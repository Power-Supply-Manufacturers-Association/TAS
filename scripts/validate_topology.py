#!/usr/bin/env python3
"""Referential-integrity validation for CIAS bricks and TAS topologies.

JSON Schema (draft 2020-12) validates structure but cannot cross-check names:
that a portBinding names a port the brick actually declares, that a
{stage, component} reference resolves, that a declared port is ever wired, etc.
This module does that pass.

Usage:
    python3 scripts/validate_topology.py                 # examples/ + data/circuits.ndjson
    python3 scripts/validate_topology.py path/to/tas.json ...

Exit code is non-zero if any integrity error is found. Bricks referenced by URI
string (not inlined) are opaque here; their port/component checks are skipped
(reported as an informational note, never silently).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# CIAS brick integrity
# ---------------------------------------------------------------------------

def validate_cias_brick(brick: dict, where: str = "") -> list[str]:
    """Return a list of integrity errors for one inline CIAS brick."""
    errs: list[str] = []
    p = f"{where}: " if where else ""

    ports = [pt["name"] for pt in brick.get("ports", [])]
    comps = [c["name"] for c in brick.get("components", [])]
    conns = [c["name"] for c in brick.get("connections", [])]

    for label, names in (("port", ports), ("component", comps), ("connection", conns)):
        seen = set()
        for n in names:
            if n in seen:
                errs.append(f"{p}duplicate {label} name {n!r}")
            seen.add(n)

    port_set, comp_set = set(ports), set(comps)
    exposed: set[str] = set()
    for conn in brick.get("connections", []):
        for ep in conn.get("endpoints", []):
            if "component" in ep:
                if ep["component"] not in comp_set:
                    errs.append(f"{p}connection {conn['name']!r} endpoint references "
                                f"unknown component {ep['component']!r}")
            elif "port" in ep:
                if ep["port"] not in port_set:
                    errs.append(f"{p}connection {conn['name']!r} endpoint references "
                                f"undeclared port {ep['port']!r}")
                exposed.add(ep["port"])

    for pt in ports:
        if pt not in exposed:
            errs.append(f"{p}port {pt!r} is declared but never wired by any connection")

    return errs


# ---------------------------------------------------------------------------
# TAS topology integrity
# ---------------------------------------------------------------------------

def _brick_of(stage: dict):
    """Inline brick dict, or None if the stage references a brick by URI."""
    c = stage.get("circuit")
    return c if isinstance(c, dict) else None


def validate_tas(doc: dict) -> tuple[list[str], list[str]]:
    """Return (errors, notes) for a full TAS document."""
    errs: list[str] = []
    notes: list[str] = []
    topo = doc.get("topology", {})
    stages = topo.get("stages", [])

    # stage names unique
    stage_names = [s["name"] for s in stages]
    if len(stage_names) != len(set(stage_names)):
        errs.append(f"duplicate stage name(s): {sorted(_dups(stage_names))}")

    # per-stage brick maps (ports/components) for cross-checking, None if URI
    brick_ports: dict[str, set | None] = {}
    brick_comps: dict[str, set | None] = {}
    for s in stages:
        name = s["name"]
        b = _brick_of(s)
        if b is None:
            brick_ports[name] = None
            brick_comps[name] = None
            if "circuit" in s:
                notes.append(f"stage {name!r} references a brick by URI "
                             f"({s['circuit']!r}); port/component checks skipped")
            continue
        errs += validate_cias_brick(b, where=f"stage {name!r} brick")
        brick_ports[name] = {pt["name"] for pt in b.get("ports", [])}
        brick_comps[name] = {c["name"] for c in b.get("components", [])}

        # portBindings must name ports the brick declares
        bindings = []
        if "inputPort" in s:
            bindings.append(s["inputPort"])
        if "outputPort" in s:
            bindings.append(s["outputPort"])
        bindings += s.get("outputPorts", [])
        bindings += s.get("ports", [])   # physicalControl
        for bd in bindings:
            if bd["port"] not in brick_ports[name]:
                errs.append(f"stage {name!r} binds port {bd['port']!r} "
                            f"not declared by its brick")

    # inter-stage connections
    inter = topo.get("interStageConnections", [])
    inter_names = [c["name"] for c in inter]
    if len(inter_names) != len(set(inter_names)):
        errs.append(f"duplicate inter-stage net name(s): {sorted(_dups(inter_names))}")

    def check_stage_port(stage_name, port_name, ctx):
        if stage_name not in stage_names:
            errs.append(f"{ctx}: unknown stage {stage_name!r}")
            return
        ports = brick_ports.get(stage_name)
        if ports is not None and port_name not in ports:
            errs.append(f"{ctx}: stage {stage_name!r} has no port {port_name!r}")

    def check_stage_comp(stage_name, comp_name, ctx):
        if stage_name not in stage_names:
            errs.append(f"{ctx}: unknown stage {stage_name!r}")
            return
        comps = brick_comps.get(stage_name)
        if comps is not None and comp_name not in comps:
            errs.append(f"{ctx}: stage {stage_name!r} has no component {comp_name!r}")

    for c in inter:
        for ep in c.get("endpoints", []):
            check_stage_port(ep["stage"], ep["port"], f"inter-stage net {c['name']!r}")

    # control senses/drives, simulation overrides/stimulus
    for s in stages:
        if s.get("controlImplementation") == "virtual":
            for sense in s.get("senses", []):
                if "net" in sense and sense["net"] not in inter_names:
                    errs.append(f"stage {s['name']!r} senses unknown net {sense['net']!r}")
                if "component" in sense:
                    check_stage_comp(sense["stage"], sense["component"],
                                     f"stage {s['name']!r} sense")
            for drv in s.get("drives", []):
                check_stage_comp(drv["stage"], drv["component"],
                                 f"stage {s['name']!r} drive")

    sim = doc.get("simulation", {})
    for ov in sim.get("overrides", []):
        check_stage_comp(ov["stage"], ov["component"], "simulation override")
    for st in sim.get("stimulus", []):
        check_stage_comp(st["stage"], st["component"], "simulation stimulus")

    return errs, notes


def _dups(items):
    seen, dups = set(), set()
    for i in items:
        if i in seen:
            dups.add(i)
        seen.add(i)
    return dups


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_targets():
    targets = sorted((REPO / "examples").glob("*.json"))
    return [("tas", p) for p in targets]


def main(argv: list[str]) -> int:
    rc = 0
    if argv:
        items = [("tas", Path(a)) for a in argv]
    else:
        items = _default_targets()
        # also check every brick in the library
        lib = REPO / "data" / "circuits.ndjson"
        if lib.exists():
            for ln, line in enumerate(lib.read_text().splitlines(), 1):
                line = line.strip()
                if not line or line.startswith("version https"):
                    continue
                brick = json.loads(line)
                errs = validate_cias_brick(brick, where=f"circuits.ndjson:{ln} ({brick.get('name')})")
                if errs:
                    rc = 1
                    print(f"FAIL brick {brick.get('name')!r}:")
                    for e in errs:
                        print(f"  - {e}")
                else:
                    print(f"OK   brick {brick.get('name')!r}")

    for _, path in items:
        doc = json.loads(path.read_text())
        errs, notes = validate_tas(doc)
        for n in notes:
            print(f"NOTE {path.name}: {n}")
        if errs:
            rc = 1
            print(f"FAIL {path.name}: {len(errs)} integrity error(s)")
            for e in errs:
                print(f"  - {e}")
        else:
            print(f"OK   {path.name}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
