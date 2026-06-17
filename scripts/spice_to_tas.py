#!/usr/bin/env python3
# ============================================================================
# OUTDATED — emits the PRE-LEGO TAS model; needs porting before reuse.
# It produces stages with inline circuit.components/connections and an
# `interStageCircuit`. The current model instead is: stage.circuit = a CIAS
# brick (with ports[]), `interStageConnections` with stage-qualified
# {stage,port} endpoints, portBinding{port,type}, virtual/physical control,
# and an agnostic `simulation` block. See docs/schema.md + CIAS/schemas/CIAS.json.
# ============================================================================
"""SPICE -> TAS topology reader.

Inverse of ``tas_to_spice.py``. Parses an ngspice deck (the kind
emitted by either MKF's ``generate_ngspice_circuit`` or this repo's
``tas_to_spice.py``) and returns a TAS topology document.

Scope (May 2026)
----------------
* Handles **single-cell non-isolated** topologies: any deck with no
  ``K`` mutual-inductance statements is treated as one ``switchingCell``
  stage. This covers buck / boost / cuk / sepic / zeta /
  four-switch-buck-boost.

* Handles **isolated** topologies (decks with ``K`` statements) by
  collapsing every mutually-coupled inductor group into a single
  multi-winding ``magnetic`` component carrying inline
  ``inductances`` + ``coupling`` fields. Stage role inference
  (inverter / isolation / outputRectifier split) is **not** performed
  — every real-BOM element (transformer included) lands in one
  ``switchingCell`` stage. This is enough for SPICE↔TAS↔SPICE round-
  trip fingerprint validation; a proper role-inference pass is
  deferred until it's needed by an agent.

  Winding labels are assigned by deck-declaration order: the
  first-declared inductor in a coupling group becomes ``pri``, the
  rest become ``sec0``, ``sec1``, …. All pairwise K-statements in a
  group must carry the same coupling coefficient (within 1e-6) — any
  mismatch raises, since the writer emits a single scalar ``k`` to
  all pairs.

* Components carry an inline ``value`` field (SI units: V, A, Ω, F, H)
  *instead of* a ``data:`` URL. The writer was extended in parallel to
  accept ``value`` as an alternative to ``data`` so a deck can survive
  a round-trip ``SPICE → TAS → SPICE`` without needing a TAS NDJSON
  lookup. ``data`` URLs are not synthesised — silently fabricating a
  part number would violate the project's no-fallback rule.

* The reader **cannot recover the controller stage** (the original SPICE
  deck describes only the bridge + gate drives; there is no
  ``compensator`` element to read back). Decks read in here will have
  no ``control`` stage. Re-emitting via ``tas_to_spice`` therefore
  needs the gate-drive list to be derived from the switches present
  rather than from ``controller.drives`` — the writer already
  falls back to "one PULSE per switch" when there is no controller.

Testbench classification
------------------------
Elements removed before topology synthesis:

* ``V_input`` (or any voltage source named ``Vin`` / ``V_in``) — sets the
  input externalPort.
* ``R_load_<name>`` — sets the corresponding output externalPort.
* ``V_gate_<Q>`` / ``Vpwm`` / ``Vpwm_<tag>`` — gate-drive sources; each
  produces a ``<Q>_gate`` interStage wire.
* ``.model`` cards and ``.tran`` / ``.options`` / ``.end`` control cards.

Anything left over is a real BOM element and must be classifiable as
mosfet / diode / capacitor / resistor / magnetic (inductor). Unknown
refdeses raise — silent drops would corrupt the decomposition.

CLI::

    python3 TAS/scripts/spice_to_tas.py deck.cir [--out topology.json]

API::

    from TAS.scripts.spice_to_tas import spice_to_tas
    tas = spice_to_tas(deck_text)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Vendored parser sits next to this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spice_parser import SpiceDeck, SpiceElement, parse_spice  # noqa: E402


# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------


class SpiceToTasError(RuntimeError):
    """Raised on any structural problem reading SPICE into TAS."""


# -----------------------------------------------------------------------------
# Testbench classification
# -----------------------------------------------------------------------------


# Voltage-source refdeses that always belong to the testbench. Matched
# case-insensitively against the full refdes.
_INPUT_VSRC_RE = re.compile(r"^V(_?in(put)?|dc_supply)$", re.IGNORECASE)
_LOAD_R_RE = re.compile(r"^R_load_(?P<port>.+)$", re.IGNORECASE)
_GATE_VSRC_RE = re.compile(
    r"^V(_gate_|pwm(_)?)(?P<switch>.+)$", re.IGNORECASE
)
# Sense / ammeter sources MKF inserts for waveform probing.
_SENSE_VSRC_RE = re.compile(
    r"^V(.*_sense|pri_sense|l_sense|sense_)", re.IGNORECASE
)
# Snubber R/C MKF adds across bridge legs.
_SNUBBER_RE = re.compile(r"^[RC](snub|sn)_", re.IGNORECASE)
# Bleeder resistors MKF adds across clamp / bus caps. ``Rbal_*`` covers
# the LLC half-bridge midpoint balancers. The leading R is followed
# *immediately* by the keyword (no underscore) — this deliberately
# excludes ``R_bal_*`` and ``R_bleed_*`` which the TAS→SPICE writer
# emits for real LLC bus-balance / clamp components round-tripped from
# a TAS doc (TAS names use snake_case with the ``R_`` prefix).
_BLEEDER_RE = re.compile(r"^R(bleed|clamp|bal)_", re.IGNORECASE)


def _classify_voltage_source(refdes: str) -> str:
    """Return one of: ``"input"``, ``"gate:<Q>"``, ``"sense"``, ``"real"``."""
    if _INPUT_VSRC_RE.match(refdes):
        return "input"
    m = _GATE_VSRC_RE.match(refdes)
    if m:
        sw = m.group("switch")
        # Vpwm_HI → "HI"; common prefix on bridge gates.
        return f"gate:{sw}"
    if _SENSE_VSRC_RE.match(refdes):
        return "sense"
    raise SpiceToTasError(
        f"Voltage source {refdes!r}: not recognised as input "
        f"(V_input/Vin/Vdc_supply), gate drive (V_gate_*/Vpwm_*), or "
        f"sense (V*_sense). Treating an unknown V source as real BOM "
        f"would corrupt the decomposition."
    )


def _classify_resistor(refdes: str) -> tuple[str, str | None]:
    """Return ``(kind, port_name)`` where kind ∈ {load, snubber, bleeder, real}."""
    m = _LOAD_R_RE.match(refdes)
    if m:
        return "load", m.group("port")
    if _SNUBBER_RE.match(refdes):
        return "snubber", None
    if _BLEEDER_RE.match(refdes):
        return "bleeder", None
    return "real", None


def _classify_capacitor(refdes: str) -> str:
    if _SNUBBER_RE.match(refdes):
        return "snubber"
    return "real"


# -----------------------------------------------------------------------------
# Refdes → TAS component name
# -----------------------------------------------------------------------------


def _tas_name(el: SpiceElement) -> str:
    """Strip the SPICE prefix added by the writer to recover the TAS name."""
    ref = el.refdes
    if el.kind == "switch":
        # SQ1 → Q1, SQ_HI → Q_HI. If the refdes already starts with Q
        # (some MKF decks), keep it.
        if ref[:1].upper() == "S" and len(ref) > 1 and ref[1:2].upper() != "_":
            return ref[1:]
        return ref
    # All other kinds: TAS name already starts with the correct prefix
    # (D for diodes, C for capacitors, R for resistors, L/T for magnetics),
    # so the SPICE refdes IS the TAS name.
    return ref


# -----------------------------------------------------------------------------
# Value extraction
# -----------------------------------------------------------------------------


_NUMBER_RE = re.compile(r"^[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_SPICE_SUFFIX = {
    "t": 1e12, "g": 1e9, "meg": 1e6, "x": 1e6, "k": 1e3,
    "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
}


def _parse_value(s: str) -> float:
    """Parse a SPICE numeric value with optional engineering suffix.

    Examples: ``"1.46e-07"`` → 1.46e-7, ``"100k"`` → 1e5, ``"1u IC=12"``
    → 1e-6 (trailing IC=… clause stripped).
    """
    s = s.strip()
    m = _NUMBER_RE.match(s)
    if not m:
        raise SpiceToTasError(f"Cannot parse SPICE value {s!r}")
    base = float(m.group(0))
    tail = s[m.end():].lstrip().lower()
    # Match longest suffix first ("meg" before "m").
    for suf in sorted(_SPICE_SUFFIX, key=len, reverse=True):
        if tail.startswith(suf):
            base *= _SPICE_SUFFIX[suf]
            break
    return base


# -----------------------------------------------------------------------------
# Component synthesis
# -----------------------------------------------------------------------------


def _component_for(el: SpiceElement) -> tuple[dict[str, Any], dict[tuple[str, str], str]]:
    """Build the TAS component dict + a pin→node map for ``el``.

    Returns
    -------
    component:
        Dict with ``name``, ``category``, ``value`` (or ``windings`` for
        multi-winding magnetics — not handled in MVP).
    pin_to_node:
        Map ``(tas_name, pin)`` → SPICE node, used to build wires later.
    """
    name = _tas_name(el)

    if el.kind == "switch":
        nd, ns, ng = el.nodes[0], el.nodes[1], el.nodes[2]
        comp = {"name": name, "category": "mosfet"}
        pins = {
            (name, "D"): nd,
            (name, "S"): ns,
            (name, "G"): ng,
        }
        return comp, pins

    if el.kind == "diode":
        na, nk = el.nodes
        comp = {"name": name, "category": "diode"}
        pins = {(name, "A"): na, (name, "K"): nk}
        return comp, pins

    if el.kind == "capacitor":
        n1, n2 = el.nodes
        val = _parse_value(el.value or "")
        comp = {"name": name, "category": "capacitor", "value": val}
        pins = {(name, "1"): n1, (name, "2"): n2}
        return comp, pins

    if el.kind == "resistor":
        n1, n2 = el.nodes
        val = _parse_value(el.value or "")
        comp = {"name": name, "category": "resistor", "value": val}
        pins = {(name, "1"): n1, (name, "2"): n2}
        return comp, pins

    if el.kind == "inductor":
        n1, n2 = el.nodes
        val = _parse_value(el.value or "")
        comp = {"name": name, "category": "magnetic", "value": val}
        pins = {(name, "1"): n1, (name, "2"): n2}
        return comp, pins

    raise SpiceToTasError(
        f"Element {el.refdes!r}: unsupported kind {el.kind!r} for TAS readback"
    )


# -----------------------------------------------------------------------------
# Wire synthesis
# -----------------------------------------------------------------------------


def _wires_from_nodes(
    pin_to_node: dict[tuple[str, str], str],
    input_node: str,
    output_ports: dict[str, str],     # port_name → node
    gate_nodes: dict[str, str],       # switch_name → gate_node
) -> list[dict[str, Any]]:
    """Group pins by SPICE node into interStage wires.

    * Pins on ``input_node`` form the ``Vin`` externalPort.
    * Pins on each ``output_ports[name]`` form a ``<name>`` externalPort.
    * Pins on node ``"0"`` form the ``GND`` wire.
    * Pins on each ``gate_nodes[<Q>]`` form a ``<Q>_gate`` wire.
    * Any remaining node touched by ≥ 2 component pins forms an
      internal wire named after the SPICE node.

    Component pins on a singleton node (i.e., touched by only one
    real-BOM pin) imply a dangling element — raise, because the deck
    must be electrically complete.
    """
    # Invert: node → list of (component, pin).
    node_pins: dict[str, list[tuple[str, str]]] = {}
    for (c, p), node in pin_to_node.items():
        node_pins.setdefault(node, []).append((c, p))

    wires: list[dict[str, Any]] = []
    consumed: set[str] = set()

    # ── input externalPort ────────────────────────────────────────────
    eps = sorted(node_pins.get(input_node, []))
    if not eps:
        raise SpiceToTasError(
            f"Input node {input_node!r} has no component pins on it — "
            f"V_input is dangling"
        )
    wires.append({
        "name": "Vin",
        "kind": "externalPort",
        "direction": "input",
        "endpoints": [{"component": c, "pin": p} for c, p in eps],
    })
    consumed.add(input_node)

    # ── output externalPorts ──────────────────────────────────────────
    for port_name, node in output_ports.items():
        eps = sorted(node_pins.get(node, []))
        if not eps:
            raise SpiceToTasError(
                f"Output port {port_name!r}: node {node!r} has no "
                f"component pins — R_load_{port_name} is dangling"
            )
        wires.append({
            "name": port_name,
            "kind": "externalPort",
            "direction": "output",
            "endpoints": [{"component": c, "pin": p} for c, p in eps],
        })
        consumed.add(node)

    # ── GND wire ──────────────────────────────────────────────────────
    eps = sorted(node_pins.get("0", []))
    if eps:
        wires.append({
            "name": "GND",
            "kind": "wire",
            "endpoints": [{"component": c, "pin": p} for c, p in eps],
        })
    consumed.add("0")

    # ── gate wires ────────────────────────────────────────────────────
    for switch_name, gate_node in gate_nodes.items():
        eps = sorted(node_pins.get(gate_node, []))
        if not eps:
            # Gate sources can drive a node with no other component pins
            # if the switch is somehow missing — that's a deck bug.
            raise SpiceToTasError(
                f"Gate driver V_gate_{switch_name}: node {gate_node!r} "
                f"has no component pins (no switch sees this gate)"
            )
        wires.append({
            "name": f"{switch_name}_gate",
            "kind": "wire",
            "endpoints": [{"component": c, "pin": p} for c, p in eps],
        })
        consumed.add(gate_node)

    # ── remaining internal wires ──────────────────────────────────────
    for node, eps in sorted(node_pins.items()):
        if node in consumed:
            continue
        eps = sorted(eps)
        if len(eps) < 2:
            raise SpiceToTasError(
                f"Internal node {node!r} touched by only one component pin "
                f"{eps[0]!r}: dangling element"
            )
        wires.append({
            "name": node,
            "kind": "wire",
            "endpoints": [{"component": c, "pin": p} for c, p in eps],
        })

    return wires


# -----------------------------------------------------------------------------
# Coupling groups (K-statements → multi-winding transformers)
# -----------------------------------------------------------------------------


def _build_coupling_groups(
    deck: SpiceDeck,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Group inductors tied by K-statements into transformer descriptors.

    Returns
    -------
    groups:
        List of group dicts, one per transformer. Each carries::

            {
                "name":         "T1",            # synthesised TAS name
                "inductors":    [SpiceElement],  # in deck-declaration order
                "coupling":     0.999,           # scalar k, common to all pairs
                "k_refdeses":   ["K1", "K2", ...],  # for diagnostics
            }

    coupled_to_group:
        Map inductor refdes → index into ``groups``. Inductors NOT in
        this map are plain single-winding magnetics and should be
        emitted by the regular per-element path.
    """
    couplings = deck.of_kind("coupling")
    if not couplings:
        return [], {}

    # Union-find over inductor refdeses.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Verify every K-referenced inductor exists, and union pairs.
    inductor_refs = {ind.refdes for ind in deck.of_kind("inductor")}
    k_to_pair: dict[str, tuple[str, str, float]] = {}
    for k in couplings:
        a, b = k.nodes
        if a not in inductor_refs or b not in inductor_refs:
            raise SpiceToTasError(
                f"K-statement {k.refdes!r} references inductors {a!r}, "
                f"{b!r} but at least one is missing from the deck"
            )
        try:
            kval = _parse_value(k.value or "")
        except SpiceToTasError as exc:
            raise SpiceToTasError(
                f"K-statement {k.refdes!r}: cannot parse coupling "
                f"{k.value!r} ({exc})"
            ) from None
        k_to_pair[k.refdes] = (a, b, kval)
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        union(a, b)

    # Bucket inductors by group root, in deck-declaration order.
    inductors_in_order = deck.of_kind("inductor")
    by_root: dict[str, list[SpiceElement]] = {}
    for ind in inductors_in_order:
        if ind.refdes not in parent:
            continue  # not coupled — stays a plain inductor
        by_root.setdefault(find(ind.refdes), []).append(ind)

    # Bucket K-statements by group root to assert uniform coupling.
    k_by_root: dict[str, list[tuple[str, str, float]]] = {}
    k_refs_by_root: dict[str, list[str]] = {}
    for kref, (a, _, _) in k_to_pair.items():
        r = find(a)
        k_by_root.setdefault(r, []).append(k_to_pair[kref])
        k_refs_by_root.setdefault(r, []).append(kref)

    groups: list[dict[str, Any]] = []
    coupled_to_group: dict[str, int] = {}
    # Iterate groups in the deck-declaration order of their first inductor.
    seen_roots: list[str] = []
    for ind in inductors_in_order:
        r = parent.get(ind.refdes)
        if r is None or r in seen_roots:
            continue
        seen_roots.append(r)

    for idx, root in enumerate(seen_roots):
        members = by_root[root]
        ks = k_by_root[root]
        # Sanity: every pair within the group must be present and equal.
        n = len(members)
        expected_pairs = n * (n - 1) // 2
        if len(ks) != expected_pairs:
            raise SpiceToTasError(
                f"Coupling group rooted at {root!r} has {n} windings "
                f"({[m.refdes for m in members]}) but {len(ks)} K-pairs "
                f"({k_refs_by_root[root]}) — expected {expected_pairs} "
                f"(all-pairs). Partial coupling is not representable in "
                f"the TAS magnetic schema."
            )
        k_values = [k for (_, _, k) in ks]
        k_min, k_max = min(k_values), max(k_values)
        if k_max - k_min > 1e-6:
            raise SpiceToTasError(
                f"Coupling group rooted at {root!r} has non-uniform "
                f"coupling: min={k_min}, max={k_max}. TAS multi-winding "
                f"magnetics use one scalar k for every pair — no fallback."
            )
        name = f"T{idx + 1}"
        groups.append({
            "name": name,
            "inductors": members,
            "coupling": k_values[0],
            "k_refdeses": k_refs_by_root[root],
        })
        for m in members:
            coupled_to_group[m.refdes] = idx

    return groups, coupled_to_group


def _transformer_component(
    group: dict[str, Any],
) -> tuple[dict[str, Any], dict[tuple[str, str], str]]:
    """Build the TAS magnetic component + pin map for a coupling group.

    Winding labels: first-declared inductor → ``pri``; subsequent ones →
    ``sec0``, ``sec1``, …. Winding pins ``.1`` / ``.2`` map to the
    inductor's first / second SPICE node respectively.
    """
    name = group["name"]
    inductors: list[SpiceElement] = group["inductors"]
    coupling = float(group["coupling"])

    labels = ["pri"] + [f"sec{i}" for i in range(len(inductors) - 1)]
    # The writer sorts winding labels alphabetically before emitting. So
    # we must order ``inductances`` to match the sorted-label order, not
    # the natural pri/sec0/sec1 order. Build (label, L, n1, n2) tuples
    # and sort by label.
    tuples = []
    for label, ind in zip(labels, inductors, strict=True):
        L = _parse_value(ind.value or "")
        n1, n2 = ind.nodes
        tuples.append((label, L, n1, n2))
    tuples.sort(key=lambda t: t[0])

    pin_map: dict[tuple[str, str], str] = {}
    inductances: list[float] = []
    for label, L, n1, n2 in tuples:
        pin_map[(name, f"{label}.1")] = n1
        pin_map[(name, f"{label}.2")] = n2
        inductances.append(L)

    # No 'pins' field: writer/consumers derive the pin set from observed
    # connection endpoints. The pin-name convention is preserved in
    # pin_map (and thus in the synthesised wires).
    comp = {
        "name": name,
        "category": "magnetic",
        "inductances": inductances,
        "coupling": coupling,
    }
    return comp, pin_map


# -----------------------------------------------------------------------------


def spice_to_tas(deck_text: str) -> dict[str, Any]:
    """Read an ngspice deck into a TAS topology document.

    See module docstring for scope and limitations.
    """
    deck = parse_spice(deck_text)

    # ── coupling groups → multi-winding transformers ─────────────────
    # Each K statement ties two inductors into a coupling group; the
    # closure of these ties (transitive) is one transformer. Inductors
    # NOT in any K group remain plain single-winding magnetics.
    coupling_groups, coupled_to_group = _build_coupling_groups(deck)

    # ── partition voltage sources ────────────────────────────────────
    input_node: str | None = None
    gate_nodes: dict[str, str] = {}
    for v in deck.of_kind("voltage_source"):
        cls = _classify_voltage_source(v.refdes)
        if cls == "input":
            if input_node is not None:
                raise SpiceToTasError(
                    f"Multiple input voltage sources: already saw "
                    f"{input_node!r}, then {v.refdes} on {v.nodes[0]!r}"
                )
            # V_input <node+> 0 <value>
            if v.nodes[1] != "0":
                raise SpiceToTasError(
                    f"V_input {v.refdes}: expected n- == 0, got {v.nodes[1]!r}"
                )
            input_node = v.nodes[0]
        elif cls.startswith("gate:"):
            sw = cls.split(":", 1)[1]
            gate_nodes[sw] = v.nodes[0]
        # sense sources contribute nothing

    if input_node is None:
        raise SpiceToTasError(
            "No input voltage source (V_input / Vin / Vdc_supply) found"
        )

    # ── partition resistors ──────────────────────────────────────────
    output_ports: dict[str, str] = {}
    real_resistors: list[SpiceElement] = []
    for r in deck.of_kind("resistor"):
        kind, port = _classify_resistor(r.refdes)
        if kind == "load":
            assert port is not None
            # R_load_<port>  <port_node> 0  <value>
            if r.nodes[1] != "0":
                raise SpiceToTasError(
                    f"R_load_{port}: expected n- == 0, got {r.nodes[1]!r}"
                )
            output_ports[port] = r.nodes[0]
        elif kind in ("snubber", "bleeder"):
            continue  # testbench
        else:
            real_resistors.append(r)

    if not output_ports:
        raise SpiceToTasError(
            "No load resistors (R_load_*) found — cannot identify output ports"
        )

    # ── collect real BOM elements ────────────────────────────────────
    # Inductors that participate in a coupling group are NOT emitted
    # individually; they are subsumed into a multi-winding transformer
    # component below.
    real_elements: list[SpiceElement] = []
    real_elements.extend(deck.of_kind("switch"))
    real_elements.extend(deck.of_kind("diode"))
    for ind in deck.of_kind("inductor"):
        if ind.refdes not in coupled_to_group:
            real_elements.append(ind)
    real_elements.extend(real_resistors)
    for c in deck.of_kind("capacitor"):
        if _classify_capacitor(c.refdes) == "real":
            real_elements.append(c)

    # ── build components + pin map ───────────────────────────────────
    components: list[dict[str, Any]] = []
    pin_to_node: dict[tuple[str, str], str] = {}
    for el in real_elements:
        comp, pins = _component_for(el)
        components.append(comp)
        # Sanity: catch duplicate TAS names from naming collisions.
        for key in pins:
            if key in pin_to_node:
                raise SpiceToTasError(
                    f"Duplicate component pin {key!r} — two SPICE refdeses "
                    f"map to the same TAS name"
                )
        pin_to_node.update(pins)

    # Multi-winding transformers (one per coupling group).
    for group in coupling_groups:
        comp, pins = _transformer_component(group)
        components.append(comp)
        for key in pins:
            if key in pin_to_node:
                raise SpiceToTasError(
                    f"Transformer {comp['name']!r}: duplicate pin "
                    f"{key!r} collides with another component"
                )
        pin_to_node.update(pins)

    # Cross-check: every gate net we saw on a V_gate source must
    # actually be driven into a switch's G pin.
    for sw, gnode in gate_nodes.items():
        if not any(
            pin == "G" and node == gnode
            for (_, pin), node in pin_to_node.items()
        ):
            raise SpiceToTasError(
                f"Gate driver V_gate_{sw} targets node {gnode!r} but no "
                f"switch.G pin is on that node"
            )

    # ── synthesise wires ─────────────────────────────────────────────
    wires = _wires_from_nodes(pin_to_node, input_node, output_ports, gate_nodes)

    # ── assemble single switchingCell stage ──────────────────────────
    stage = {
        "name": "power_stage",
        "role": "switchingCell",
        "inputPort":  {"type": "dcBus",    "wire": "Vin"},
        "outputPorts": [
            {"type": "dcOutput", "wire": name} for name in output_ports
        ],
        "circuit": {
            "components": components,
            "connections": [],  # all wires hoisted to interStageCircuit
        },
    }

    return {"stages": [stage], "interStageCircuit": wires}


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read an ngspice deck into a TAS topology document."
    )
    parser.add_argument("deck", type=Path, help="ngspice deck (.cir)")
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output TAS topology JSON path (default: stdout)",
    )
    args = parser.parse_args(argv)

    tas = spice_to_tas(args.deck.read_text())
    rendered = json.dumps(tas, indent=2)
    if args.out is None:
        print(rendered)
    else:
        args.out.write_text(rendered + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
