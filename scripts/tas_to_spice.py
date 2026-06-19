#!/usr/bin/env python3
# ============================================================================
# OUTDATED — targets the PRE-LEGO TAS model; needs porting before reuse.
# Since the CIAS/TAS redesign:
#   * topology.interStageCircuit            -> topology.interStageConnections
#   * stage.circuit is now a CIAS BRICK (inline or a "...?name=..." URI string)
#     with its own ports[]; brick names are LOCAL -> SPICE nodes must be
#     namespaced per stage to avoid collisions across reused bricks.
#   * stage ports: inputPort/outputPorts {type,wire} -> portBinding {port,type}
#     referencing brick ports; control is virtualControl | physicalControl.
#   * inter-stage endpoints: {component,pin} -> stage-qualified {stage,port}.
#   * simulation is now simulator-AGNOSTIC (analyses/models/stimulus),
#     not SPICE spiceModel/spiceCommand.
# See docs/schema.md and CIAS/schemas/CIAS.json for the current model.
# ============================================================================
"""TAS topology + inputs -> ngspice deck.

Topology writer. Reads a TAS ``topology`` document (stages, each owning a
CIAS brick, plus interStageConnections) and a TAS ``inputs`` document
(operating points, designRequirements) and emits a flat ngspice netlist.

Component values are resolved by following each component's ``data`` URL
to the corresponding ``TAS/data/*.ndjson`` entry. URLs with a
``?placeholder=`` query are rejected with a loud error — TAS topologies
must be bound to real components before they can be simulated.

Simulation parameters come from the TAS inputs document:

* ``Vin`` ← operatingPoints[op_index].inputVoltage
* ``Vout_<name>`` ← designRequirements.outputs[name].voltage.nominal
* ``Iout_<name>`` ← operatingPoints[op_index].outputs[name].current
* ``fsw`` ← designRequirements.switchingFrequency.nominal

Gate-drive synthesis: one independent PULSE source per switch listed in
the controller stage's ``drives``, all 50 % duty at fsw. Bridge-correct
modulation (complementary phases, dead time, phase shift) is **not**
applied — the deck preserves the converter's structural BOM but the
timing is naive. Fix by post-processing the generated PULSE sources, or
by extending this writer with a per-controller pattern argument.

Usage::

    python3 TAS/scripts/tas_to_spice.py \\
        --topology buck.tas.json --inputs buck.inputs.json --out buck.cir

API::

    from TAS.scripts.tas_to_spice import tas_to_spice
    netlist = tas_to_spice(topology, inputs, op_index=0)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlparse

TAS_ROOT = Path(__file__).resolve().parent.parent  # .../TAS
DATA_DIR = TAS_ROOT / "data"


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------


class TasToSpiceError(RuntimeError):
    """Raised on any structural problem in the TAS→SPICE pass.

    Always carries enough context (topology element name, missing field,
    URL, NDJSON file) to diagnose without re-running with a debugger.
    """


# -----------------------------------------------------------------------------
# data-URL resolution
# -----------------------------------------------------------------------------


def _parse_data_url(url: str, component_name: str) -> tuple[Path, dict[str, str]]:
    """Return ``(ndjson_path, query_params)``. Raise on placeholder URLs."""
    parsed = urlparse(url)
    if not parsed.path:
        raise TasToSpiceError(
            f"Component {component_name!r}: data URL {url!r} has no path"
        )
    ndjson = TAS_ROOT.parent / parsed.path if not parsed.path.startswith("/") else Path(parsed.path)
    # Path is TAS-relative (e.g. "TAS/data/mosfets.ndjson"); resolve from
    # the Heaviside root that contains the TAS submodule.
    if not ndjson.is_absolute():
        ndjson = (TAS_ROOT.parent / parsed.path).resolve()
    qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    if "placeholder" in qs:
        raise TasToSpiceError(
            f"Component {component_name!r}: data URL is a placeholder "
            f"({url!r}). Bind the topology to real TAS entries before "
            f"generating SPICE."
        )
    if not qs:
        raise TasToSpiceError(
            f"Component {component_name!r}: data URL {url!r} has no query "
            f"selector (need e.g. '?mpn=...' or '?id=...')"
        )
    if not ndjson.exists():
        raise TasToSpiceError(
            f"Component {component_name!r}: data URL points to {ndjson} "
            f"which does not exist"
        )
    return ndjson, qs


def _matches(entry: dict[str, Any], qs: dict[str, str]) -> bool:
    """Test whether a top-level NDJSON entry matches a URL query.

    The TAS NDJSON files wrap entries by category (``{"mosfet": {...}}``,
    ``{"capacitor": {...}}``, …). We accept query selectors against any
    nested string field — most commonly ``partNumber`` (alias ``mpn``).
    """
    # unwrap the single category-keyed object
    if len(entry) == 1 and isinstance(next(iter(entry.values())), dict):
        body = next(iter(entry.values()))
    else:
        body = entry
    # search recursively for matching field values
    for key, want in qs.items():
        if key == "mpn":
            key = "partNumber"
        if not _find_field(body, key, want):
            return False
    return True


def _find_field(obj: Any, key: str, want: str) -> bool:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and str(v) == want:
                return True
            if _find_field(v, key, want):
                return True
    elif isinstance(obj, list):
        return any(_find_field(item, key, want) for item in obj)
    return False


def _lookup(ndjson: Path, qs: dict[str, str], component_name: str) -> dict[str, Any]:
    """Linear-scan an NDJSON file for the first entry matching ``qs``."""
    with ndjson.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if _matches(entry, qs):
                return entry
    raise TasToSpiceError(
        f"Component {component_name!r}: no entry in {ndjson.name} matches "
        f"query {qs!r}"
    )


# -----------------------------------------------------------------------------
# Value extractors (one per TAS data category)
# -----------------------------------------------------------------------------


def _unwrap(entry: dict[str, Any]) -> dict[str, Any]:
    if len(entry) == 1 and isinstance(next(iter(entry.values())), dict):
        return next(iter(entry.values()))
    return entry


def _electrical(entry: dict[str, Any]) -> dict[str, Any]:
    body = _unwrap(entry)
    try:
        return body["manufacturerInfo"]["datasheetInfo"]["electrical"]
    except KeyError as exc:
        raise TasToSpiceError(
            f"NDJSON entry has no datasheetInfo.electrical block "
            f"(top key {list(entry)!r})"
        ) from exc


def _nominal(field: Any, name: str) -> float:
    if isinstance(field, dict):
        try:
            return float(field["nominal"])
        except KeyError as exc:
            raise TasToSpiceError(
                f"Field {name!r} has no 'nominal' subkey: keys={list(field)}"
            ) from exc
    return float(field)


def capacitance_of(entry: dict[str, Any]) -> float:
    return _nominal(_electrical(entry)["capacitance"], "capacitance")


def inductance_of(entry: dict[str, Any]) -> float:
    return _nominal(_electrical(entry)["inductance"], "inductance")


def resistance_of(entry: dict[str, Any]) -> float:
    return _nominal(_electrical(entry)["resistance"], "resistance")


# Magnetic components carry a per-winding inductance list. For coupled-
# inductor / transformer modelling we need a per-winding scalar.
def winding_inductances_of(entry: dict[str, Any]) -> list[float]:
    body = _unwrap(entry)
    el = body.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("electrical", {})
    windings = el.get("windings")
    if windings:
        out = []
        for w in windings:
            out.append(_nominal(w.get("inductance"), "winding.inductance"))
        return out
    # single-inductor case
    return [inductance_of(entry)]


# -----------------------------------------------------------------------------
# Topology traversal
# -----------------------------------------------------------------------------


def _index_components(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flat refdes → component dict map across all stages."""
    out: dict[str, dict[str, Any]] = {}
    for stage in topology.get("stages", []):
        for comp in stage.get("circuit", {}).get("components", []):
            name = comp.get("name")
            if not name:
                raise TasToSpiceError(
                    f"Stage {stage.get('name')!r} has a component with no 'name'"
                )
            if name in out:
                raise TasToSpiceError(f"Duplicate component name {name!r}")
            out[name] = comp
    return out


def _index_controllers(topology: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in topology.get("stages", []) if s.get("role") == "control"]


def _collect_wires(topology: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every wire (named connection) across stages + interStage.

    Each wire dict has at minimum ``name`` and ``endpoints`` (list of
    ``{component, pin}``). interStage wires may additionally have
    ``kind='externalPort'`` and ``direction`` for boundary signals.

    v2 format: stage circuit connections may include port-endpoint entries
    (``{"port": "portname"}``) that reference stage boundary ports declared in
    interStageConnections (endpoints ``{"stage": ..., "port": ...}``). These
    are resolved here: for each stage port endpoint in a circuit connection, the
    global net name assigned by interStageConnections is substituted, expanding
    the wire to cover all component pins connected via that port.
    """
    # Accept both old (interStageCircuit) and new (interStageConnections) key names.
    inter = topology.get("interStageConnections") or topology.get("interStageCircuit", [])

    # Detect v2 format: interStageConnections uses {stage, port} endpoints.
    is_v2 = any(
        "stage" in ep
        for conn in inter
        for ep in conn.get("endpoints", [])
    )

    if is_v2:
        # Build (stage_name, port_name) -> global_net_name map from interStageConnections.
        stage_port_to_net: dict[tuple[str, str], str] = {}
        for conn in inter:
            net_name = conn.get("name", "")
            for ep in conn.get("endpoints", []):
                sname = ep.get("stage")
                pname = ep.get("port")
                if sname and pname:
                    stage_port_to_net[(sname, pname)] = net_name

        wires: list[dict[str, Any]] = []
        for stage in topology.get("stages", []):
            sname = stage.get("name", "")
            for conn in stage.get("circuit", {}).get("connections", []):
                # Resolve port endpoints to global net names.
                has_port_ep = any("port" in ep and "component" not in ep for ep in conn.get("endpoints", []))
                if not has_port_ep:
                    wires.append({**conn, "_origin": f"stage:{sname}"})
                    continue
                # Determine the global net name for this connection by looking up
                # any port endpoint in the interStageConnections map.
                port_eps = [ep for ep in conn.get("endpoints", []) if "port" in ep and "component" not in ep]
                # Use the connection's own name as net name; if a port maps to an
                # interStage wire, use that global name instead (the interStage wire
                # name is what SPICE sees as the node).
                global_net = conn.get("name", "")
                for pep in port_eps:
                    pname = pep.get("port", "")
                    mapped = stage_port_to_net.get((sname, pname))
                    if mapped:
                        global_net = mapped
                        break
                # Emit a wire with only the component-pin endpoints (skip port endpoints)
                # but renamed to the global net.
                comp_eps = [ep for ep in conn.get("endpoints", []) if "component" in ep]
                if comp_eps:
                    wires.append({
                        **conn,
                        "name": global_net,
                        "endpoints": comp_eps,
                        "_origin": f"stage:{sname}",
                    })
        # Emit interStageConnections wires too (for externalPort detection etc.)
        # These have {stage, port} endpoints; the net-assignment loop will skip them.
        for conn in inter:
            wires.append({**conn, "_origin": "interStage"})
        return wires

    # v1 path: flat {component, pin} endpoints everywhere.
    wires = []
    for stage in topology.get("stages", []):
        for conn in stage.get("circuit", {}).get("connections", []):
            wires.append({**conn, "_origin": f"stage:{stage['name']}"})
    for conn in inter:
        wires.append({**conn, "_origin": "interStage"})
    return wires


# -----------------------------------------------------------------------------
# Net assignment
# -----------------------------------------------------------------------------


def _assign_nets(
    components: dict[str, dict[str, Any]],
    wires: list[dict[str, Any]],
    *,
    drives: list[dict[str, Any]] | None = None,
    senses: list[dict[str, Any]] | None = None,
) -> dict[tuple[str, str], str]:
    """Return ``(component, pin) → net_name`` for every pin in the design.

    Pins that appear in a named wire take that wire's name as their net.
    The conventional wire name ``GND`` is rewritten to SPICE node ``0``
    so callers can declare ground explicitly without manufacturing a
    bespoke net.

    Pins implied by controller ``drives`` / ``senses`` declarations are
    auto-assigned synthetic nets (``{component}_gate`` / ``{component}_{signal}``)
    when not already covered by an explicit wire. This lets the producer
    omit singleton stub wires for control/sense signals (which would
    violate the TAS schema's ``minItems: 2`` on connection.endpoints) and
    rely on the authoritative ``drives``/``senses`` declarations instead.
    The synthetic net name matches the legacy ``{Q}_gate`` convention so
    downstream gate-PULSE wiring is unchanged.

    Pins that do not appear in any wire and are not implied by
    drives/senses raise — every component pin must be explicitly
    connected.
    """
    nets: dict[tuple[str, str], str] = {}
    for w in wires:
        wname = w.get("name")
        if not wname:
            raise TasToSpiceError(f"Wire has no 'name': {w}")
        # Case-insensitive: both "GND" (from interStageConnections) and
        # "gnd" (from isolated stage circuit connections) map to SPICE node 0.
        net = "0" if wname.upper() == "GND" else wname
        for ep in w.get("endpoints", []):
            # v2 port-endpoint connections (e.g. {"port": "in"}) have no
            # "component" key — they reference a stage boundary port, not a
            # real component pin. Skip them; they have no SPICE representation.
            if "component" not in ep:
                continue
            key = (ep["component"], ep["pin"])
            if key in nets and nets[key] != net:
                raise TasToSpiceError(
                    f"Pin {key} is connected to both nets {nets[key]!r} "
                    f"and {net!r}"
                )
            nets[key] = net

    # Auto-synthesize nets for control/sense pins declared on controller
    # stages. The gate pin of a driven mosfet is "G"; the conventional
    # net name is "{component}_gate" so the existing gate-PULSE
    # emission ("V_gate_<sw> <gate_net> ...") continues to work.
    for d in (drives or []):
        comp = d.get("component")
        signal = d.get("signal", "gate")
        if comp is None:
            continue
        pin = "G" if signal == "gate" else d.get("pin", signal)
        key = (comp, pin)
        if key not in nets:
            nets[key] = f"{comp}_{signal}"
    for s in (senses or []):
        comp = s.get("component")
        signal = s.get("signal")
        pin = s.get("pin")
        if comp is None or pin is None:
            continue
        key = (comp, pin)
        if key not in nets:
            nets[key] = f"{comp}_{signal or pin}"

    # Detect dangling pins
    expected_pins = _expected_pins(components)
    dangling = expected_pins - set(nets)
    if dangling:
        raise TasToSpiceError(
            f"Pins not connected to any wire: {sorted(dangling)}. Every "
            f"component pin must appear in either a stage-internal "
            f"connection or an interStage wire."
        )
    return nets


_FIXED_PINS: dict[str, tuple[str, ...]] = {
    "mosfet":     ("D", "S", "G"),
    "diode":      ("A", "K"),
    "capacitor":  ("1", "2"),
    "resistor":   ("1", "2"),
    "terminal":   ("1",),
}


def _expected_pins(components: dict[str, dict[str, Any]]) -> set[tuple[str, str]]:
    """Enumerate all pins each component must expose."""
    out: set[tuple[str, str]] = set()
    for name, comp in components.items():
        category = _category_of(comp)
        if category == "magnetic":
            # Magnetic pin sets are derived from observed connection
            # endpoints — there is no schema-level enumeration. A
            # single-winding inductor's pins ("1", "2") and a multi-
            # winding transformer's pins ("<winding>.<idx>") are
            # discovered by _spice_lines_for_component when it walks
            # the nets dict. Integrity of the pin set is enforced at
            # emission time (single-winding requires exactly "1"/"2",
            # multi-winding requires all dotted names).
            continue
        elif category == "controller":
            continue  # controllers have no SPICE pins
        elif category == "terminal":
            # Board terminals model the external boundary; their single
            # pin appears in an externalPort connection. No SPICE element
            # is emitted (the writer treats terminals as ideal shorts to
            # the external net), so the pin set is just ("1",).
            out.add((name, "1"))
        else:
            for p in _FIXED_PINS[category]:
                out.add((name, p))
    return out


def _category_of(comp: dict[str, Any]) -> str:
    # Prefer explicit category field (set by spice_to_tas reader); fall back to
    # data URL path inference for writer-native TAS docs.
    explicit = comp.get("category")
    if explicit:
        return explicit
    url = comp.get("data", "")
    # `TAS/data/<cat>.ndjson?...`
    path = urlparse(url).path
    stem = Path(path).stem  # 'mosfets', 'capacitors', ...
    return {
        "mosfets":     "mosfet",
        "diodes":      "diode",
        "capacitors":  "capacitor",
        "resistors":   "resistor",
        "magnetics":   "magnetic",
        "controllers": "controller",
        "terminals":   "terminal",
    }.get(stem, "unknown")


# -----------------------------------------------------------------------------
# SPICE emission
# -----------------------------------------------------------------------------


def _spice_refdes(name: str, kind_letter: str) -> str:
    """Prefix ``name`` with ``kind_letter`` if not already present."""
    return name if name[:1].upper() == kind_letter.upper() else f"{kind_letter}{name}"


def _emit_component(
    name: str,
    comp: dict[str, Any],
    nets: dict[tuple[str, str], str],
    *,
    switch_model: str,
    diode_model: str,
) -> list[str]:
    """Return one or more SPICE deck lines for ``comp``."""
    category = _category_of(comp)

    if category == "controller":
        return []  # controllers have no SPICE element

    if category == "terminal":
        # Board terminals model the external boundary as an ideal short
        # to the external net. No SPICE element is emitted — the
        # terminal's single pin is bound to the externalPort net by
        # _assign_nets and that is sufficient for simulation.
        return []

    # Mosfets and diodes carry no scalar value — model name is enough
    # — so they need neither a data URL nor an inline value.
    if category in ("mosfet", "diode"):
        entry: dict[str, Any] = {}
    elif "value" in comp:
        # Inline value path: skip TAS lookup. Used by readback round-
        # trips (spice_to_tas emits TAS with values inline since the
        # SPICE deck has no part number to resolve).
        inline = float(comp["value"])
        entry = {"_inline_value": inline}
    elif category == "magnetic" and "inductances" in comp:
        # Multi-winding inline path: per-winding ``inductances`` list +
        # ``coupling`` scalar live on the component itself. Lookup is
        # skipped; the magnetic branch below reads them directly off
        # ``comp``.
        entry = {}
    else:
        url = comp.get("data")
        if not url:
            raise TasToSpiceError(
                f"Component {name!r}: needs either a 'data' URL or an "
                f"inline 'value' field — no fallback"
            )
        ndjson, qs = _parse_data_url(url, name)
        entry = _lookup(ndjson, qs, name)

    if category == "mosfet":
        nd, ns, ng = nets[(name, "D")], nets[(name, "S")], nets[(name, "G")]
        ref = _spice_refdes(name, "S")
        return [f"{ref} {nd} {ns} {ng} 0 {switch_model}"]

    if category == "diode":
        na, nk = nets[(name, "A")], nets[(name, "K")]
        ref = _spice_refdes(name, "D")
        return [f"{ref} {na} {nk} {diode_model}"]

    if category == "capacitor":
        n1, n2 = nets[(name, "1")], nets[(name, "2")]
        val = entry.get("_inline_value", None)
        if val is None:
            val = capacitance_of(entry)
        ref = _spice_refdes(name, "C")
        return [f"{ref} {n1} {n2} {val:.6e}"]

    if category == "resistor":
        n1, n2 = nets[(name, "1")], nets[(name, "2")]
        val = entry.get("_inline_value", None)
        if val is None:
            val = resistance_of(entry)
        ref = _spice_refdes(name, "R")
        return [f"{ref} {n1} {n2} {val:.6e}"]

    if category == "magnetic":
        # Derive pin set from observed connection endpoints. The
        # pin-name convention is the source of truth for winding
        # structure: dotted names ("<winding>.<idx>") => multi-winding
        # transformer; bare "1"/"2" => single-winding inductor.
        pins = sorted(p for (c, p) in nets if c == name)
        if not pins:
            raise TasToSpiceError(
                f"Magnetic {name!r}: no connections found — every "
                f"magnetic must be wired into at least one net"
            )
        dotted = [p for p in pins if "." in p]
        bare = [p for p in pins if "." not in p]
        if dotted and bare:
            raise TasToSpiceError(
                f"Magnetic {name!r}: mixed pin naming — {bare!r} "
                f"(bare) with {dotted!r} (dotted). Single-winding "
                f"uses '1'/'2'; multi-winding uses '<winding>.<idx>'"
            )
        if not dotted:
            # single-winding inductor — require both pins wired
            if set(bare) != {"1", "2"}:
                raise TasToSpiceError(
                    f"Magnetic {name!r}: single-winding requires pins "
                    f"'1' and '2', got {bare!r}"
                )
            n1, n2 = nets[(name, "1")], nets[(name, "2")]
            val = entry.get("_inline_value", None)
            if val is None:
                val = inductance_of(entry)
            ref = _spice_refdes(name, "L")
            return [f"{ref} {n1} {n2} {val:.6e}"]
        # multi-winding: one L per winding + pairwise K statements.
        # Each winding must expose pins ".1" and ".2".
        windings = sorted({p.split(".")[0] for p in dotted})
        for w in windings:
            expected = {f"{w}.1", f"{w}.2"}
            present = {p for p in dotted if p.split(".")[0] == w}
            missing = expected - present
            if missing:
                raise TasToSpiceError(
                    f"Magnetic {name!r}: winding {w!r} missing pins "
                    f"{sorted(missing)!r} — multi-winding requires "
                    f"both '.1' and '.2' on every winding"
                )
        # Inline path: spice_to_tas readback emits ``inductances`` (one
        # per winding, sorted-label order) and ``coupling`` (scalar k
        # applied to every pair) on the component itself, so no NDJSON
        # lookup is needed.
        inline_Ls = comp.get("inductances")
        inline_k = comp.get("coupling")
        if inline_Ls is not None:
            if len(inline_Ls) != len(windings):
                raise TasToSpiceError(
                    f"Magnetic {name!r}: 'inductances' has "
                    f"{len(inline_Ls)} entries but {len(windings)} "
                    f"windings derived from connection endpoints"
                )
            if inline_k is None:
                raise TasToSpiceError(
                    f"Magnetic {name!r}: 'inductances' provided but "
                    f"'coupling' is missing — no fallback"
                )
            L_values = [float(x) for x in inline_Ls]
            k_value = float(inline_k)
        else:
            if "_inline_value" in entry:
                raise TasToSpiceError(
                    f"Magnetic {name!r}: scalar inline 'value' not "
                    f"supported for multi-winding magnetics — use "
                    f"'inductances' list + 'coupling' scalar instead"
                )
            L_values = winding_inductances_of(entry)
            k_value = 0.999
            if len(L_values) < len(windings):
                raise TasToSpiceError(
                    f"Magnetic {name!r}: {len(windings)} windings declared but "
                    f"entry has only {len(L_values)} inductance values"
                )
        lines: list[str] = []
        for i, w in enumerate(windings):
            n1 = nets[(name, f"{w}.1")]
            n2 = nets[(name, f"{w}.2")]
            lines.append(f"L{name}_{w} {n1} {n2} {L_values[i]:.6e}")
        # All-pairs coupling
        k_idx = 0
        for i in range(len(windings)):
            for j in range(i + 1, len(windings)):
                k_idx += 1
                lines.append(
                    f"K{name}_{k_idx} L{name}_{windings[i]} "
                    f"L{name}_{windings[j]} {k_value:.6e}"
                )
        return lines

    raise TasToSpiceError(f"Unknown component category for {name!r}: {category!r}")


# -----------------------------------------------------------------------------
# Testbench synthesis
# -----------------------------------------------------------------------------


def _external_ports(topology: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {"input": {}, "output": {}}
    # Accept both old (interStageCircuit) and new (interStageConnections) key names.
    inter = topology.get("interStageConnections") or topology.get("interStageCircuit", [])
    for w in inter:
        if w.get("kind") != "externalPort":
            continue
        direction = w.get("direction")
        if direction not in ("input", "output"):
            raise TasToSpiceError(
                f"externalPort {w.get('name')!r} has bad direction "
                f"{direction!r}"
            )
        out[direction][w["name"]] = w
    return out


def _emit_testbench(
    topology: dict[str, Any],
    inputs: dict[str, Any],
    op_index: int,
    fsw: float,
) -> tuple[list[str], list[str]]:
    """Return ``(preamble_lines, footer_lines)`` for the testbench.

    Preamble: input voltage source, load resistors, gate-drive PULSEs.
    Footer: ``.tran`` + ``.options`` + ``.end``.
    """
    op = inputs["operatingPoints"][op_index]
    dr = inputs["designRequirements"]

    # Build name → spec maps for outputs
    out_v: dict[str, float] = {
        o["name"]: _nominal(o["voltage"], f"output {o['name']} voltage")
        for o in dr["outputs"]
    }
    out_i: dict[str, float] = {}
    for o in op.get("outputs", []):
        name = o["name"]
        if "current" in o:
            out_i[name] = float(o["current"])
        elif "power" in o:
            out_i[name] = float(o["power"]) / out_v[name]
        else:
            raise TasToSpiceError(
                f"operatingPoint output {name!r}: must specify 'current' "
                f"or 'power'"
            )

    ports = _external_ports(topology)
    if len(ports["input"]) != 1:
        raise TasToSpiceError(
            f"Expected exactly 1 input externalPort, found "
            f"{list(ports['input'])}"
        )
    input_wire = next(iter(ports["input"]))
    if len(ports["output"]) < 1:
        raise TasToSpiceError("No output externalPort found")

    vin = float(op["inputVoltage"])
    pre: list[str] = [
        f"* TAS→SPICE: op={op.get('name', op_index)!r} Vin={vin}V fsw={fsw}Hz",
        "",
        f"V_input {input_wire} 0 {vin}",
    ]
    # Load resistors for each output
    for out_name in ports["output"]:
        if out_name not in out_v or out_name not in out_i:
            raise TasToSpiceError(
                f"Output externalPort {out_name!r} not specified in TAS "
                f"inputs (need designRequirements.outputs[{out_name!r}] "
                f"and operatingPoint.outputs[{out_name!r}])"
            )
        Rload = out_v[out_name] / out_i[out_name]
        pre.append(f"R_load_{out_name} {out_name} 0 {Rload:.6e}")

    # Gate drives — one independent PULSE per switch the controllers drive.
    # If no controller stage is present (e.g. readback from spice_to_tas),
    # fall back to enumerating every mosfet component and driving each
    # independently — same naive 50% PWM. This keeps the round-trip
    # SPICE→TAS→SPICE closed even when the controller is lost.
    period = 1.0 / fsw
    pulse_w = period * 0.5
    components = _index_components(topology)
    controllers = _index_controllers(topology)
    drives = [d for ctrl in controllers for d in ctrl.get("drives", [])]
    senses = [s for ctrl in controllers for s in ctrl.get("senses", [])]
    nets = _assign_nets(components, _collect_wires(topology),
                        drives=drives, senses=senses)
    pre.append("")
    pre.append("* Gate drives (50% duty per switch — naive)")
    seen: set[str] = set()
    if controllers:
        switches_to_drive = [
            d["component"]
            for ctrl in controllers
            for d in ctrl.get("drives", [])
        ]
    else:
        switches_to_drive = [
            name for name, comp in components.items()
            if _category_of(comp) == "mosfet"
        ]
    for sw in switches_to_drive:
        if sw in seen:
            continue
        seen.add(sw)
        gate_net = nets[(sw, "G")]
        pre.append(
            f"V_gate_{sw} {gate_net} 0 PULSE(0 5 0 10n 10n "
            f"{pulse_w:.6e} {period:.6e})"
        )

    foot: list[str] = [
        "",
        ".options RELTOL=0.01 ABSTOL=1e-7 VNTOL=1e-4 ITL1=500 ITL4=500",
        f".tran {period/200:.6e} {period*20:.6e} {period*5:.6e} UIC",
        ".end",
    ]
    return pre, foot


# -----------------------------------------------------------------------------
# Top-level
# -----------------------------------------------------------------------------


_DEFAULT_SWITCH_MODEL = "SW1"
_DEFAULT_DIODE_MODEL = "DIDEAL"


def tas_to_spice(
    topology: dict[str, Any],
    inputs: dict[str, Any],
    *,
    op_index: int = 0,
    switch_model: str = _DEFAULT_SWITCH_MODEL,
    diode_model: str = _DEFAULT_DIODE_MODEL,
) -> str:
    """Emit an ngspice deck for ``topology`` at ``inputs.operatingPoints[op_index]``."""
    try:
        fsw_node = inputs["designRequirements"]["switchingFrequency"]
    except KeyError as exc:
        raise TasToSpiceError(
            "TAS inputs has no designRequirements.switchingFrequency. "
            "A nominal switching frequency is required to emit gate drives."
        ) from exc
    fsw = _nominal(fsw_node, "switchingFrequency")

    components = _index_components(topology)
    wires = _collect_wires(topology)
    controllers = _index_controllers(topology)
    drives = [d for ctrl in controllers for d in ctrl.get("drives", [])]
    senses = [s for ctrl in controllers for s in ctrl.get("senses", [])]
    nets = _assign_nets(components, wires, drives=drives, senses=senses)

    body: list[str] = []
    body.append(f".model {_DEFAULT_SWITCH_MODEL} SW VT=2.5 VH=0.5 RON=0.01 ROFF=1Meg")
    body.append(f".model {_DEFAULT_DIODE_MODEL} D(IS=1e-12 RS=0.05)")
    body.append("")
    for name, comp in components.items():
        body.extend(
            _emit_component(
                name, comp, nets,
                switch_model=switch_model,
                diode_model=diode_model,
            )
        )

    pre, foot = _emit_testbench(topology, inputs, op_index, fsw)
    title = topology.get("name") or "TAS topology"
    head = [f"* TAS → SPICE: {title}"]

    return "\n".join(head + pre + [""] + body + foot) + "\n"


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--topology", required=True, type=Path,
                        help="TAS topology JSON (matches schemas/topology.json)")
    parser.add_argument("--inputs", required=True, type=Path,
                        help="TAS inputs JSON (matches schemas/inputs.json)")
    parser.add_argument("--op-index", type=int, default=0,
                        help="Operating point index (default 0)")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output netlist path (default stdout)")
    args = parser.parse_args(argv)

    topology = json.loads(args.topology.read_text())
    inputs = json.loads(args.inputs.read_text())

    try:
        netlist = tas_to_spice(topology, inputs, op_index=args.op_index)
    except TasToSpiceError as exc:
        print(f"tas_to_spice: {exc}", file=sys.stderr)
        return 1

    if args.out:
        args.out.write_text(netlist)
        print(f"wrote {len(netlist)} bytes to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(netlist)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
