#!/usr/bin/env python3
"""Gate every record of any catalogue file a change touched.

WHY THIS EXISTS. `pytest tests/test_schemas.py` proves the SCHEMAS parse and cross-refer;
it never looks at data. So an autonomous fixer editing a catalogue had, in practice, no
gate between it and the data — its instructions said to run Blade Runner and JSON Schema,
and nothing checked that it had. This is that check, and it is the one Minion runs.

WHY IT VALIDATES WHOLE FILES INSTEAD OF THE DIFF. The obvious design is to gate only the
records the diff touched. It cannot work here: data/*.ndjson is git-LFS
(`*.ndjson filter=lfs` in .gitattributes), so `git diff` renders the 3-line LFS POINTER,
not the records. A commit that rewrote 1,923 capacitor rows shows as "2 insertions,
2 deletions" — the pointer's oid and size. A diff-based gate would parse that pointer text
as JSON, fail on it, and report a defect that does not exist while missing every real one.

So a touched file is validated in full: ~580 records/s, about 7 minutes for the 253,830
capacitors, 11 for the 392,346 connectors. That is slow for an edit-loop check and exactly
right for a gate standing in front of unattended writes — it cannot miss.

BOTH GATES, ALWAYS. Schema says the shape is legal; Blade Runner says the physics is
possible. Neither substitutes for the other — a units error is perfectly valid JSON. 4,415
Murata bias curves were once written 1e6 too large and every one passed CAS validation.

Exit codes: 0 clean, 1 a record failed, 2 the gate could not run — which is NOT a pass,
because a gate that cannot run is not a gate.

  changed_records_gate.py                  # files differing from HEAD
  changed_records_gate.py --base HEAD~3
  changed_records_gate.py --files capacitors.ndjson
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
PSMA = TAS.parent
sys.path.insert(0, str(TAS / "scripts"))

# The same table tests/test_data.py validates against. A touched file that is not here is
# an ERROR, never a silent skip.
# The 4th element is the wrap BladeGate rebuilds before calling tas_validator, so for the
# nested SAS families it must be the FULL path ("semiconductor", "mosfet") — a bare "mosfet"
# hands the validator {"mosfet": ...}, which has no known discriminator, and every record in
# the file comes back BLOCKED (ABT #482: mosfets.ndjson read 9936/9936 FAILED for this).
FAMILIES = {
    "mosfets.ndjson": (["semiconductor", "mosfet"], "SAS", "mosfet.json", ("semiconductor", "mosfet")),
    "diodes.ndjson": (["semiconductor", "diode"], "SAS", "diode.json", ("semiconductor", "diode")),
    "igbts.ndjson": (["semiconductor", "igbt"], "SAS", "igbt.json", ("semiconductor", "igbt")),
    "bjts.ndjson": (["semiconductor", "bjt"], "SAS", "bjt.json", ("semiconductor", "bjt")),
    "capacitors.ndjson": (["capacitor"], "CAS", "capacitor.json", "capacitor"),
    "resistors.ndjson": (["resistor"], "RAS", "resistor.json", "resistor"),
    "varistors.ndjson": (["varistor"], "RAS", "varistor.json", "varistor"),
    "magnetics.ndjson": (["magnetic"], "MAS", "magnetic.json", "magnetic"),
    "controllers.ndjson": (["controller"], "CTAS", "controller.json", "controller"),
    "analog_ics.ndjson": (["analog"], "AAS", "AAS.json", "analog"),
    "connectors.ndjson": (["connector"], "CONAS", "connector.json", "connector"),
    "timing_devices.ndjson": (["timeBase"], "TDAS", "tdas.json", "timeBase"),
    "thermistors.ndjson": (["thermistor"], "RAS", "thermistor.json", "thermistor"),
    "modules.ndjson": (["semiconductor", "module"], "SAS", "module.json", ("semiconductor", "module")),
}

# circuits.ndjson is not a part catalogue and cannot use FAMILIES: a record is a whole CIAS
# brick, not a PEAS-wrapped component, so there is no discriminator to unwrap and Blade Runner
# has nothing to validate at the record level. It gets its own three-part gate in gate_circuits().
CIRCUITS = "circuits.ndjson"

# Top-level discriminators tas_validator has a handler for (verified against the binding, not
# assumed). The PEAS-native atoms (behavioral, transmissionLine) are model primitives, not
# orderable parts — Blade Runner throws "no known component discriminator" on them BY DESIGN.
# Their physics gate is the CIAS lowering instead (see gate_circuits). Enumerating the set
# explicitly means an unexpected validator error on a KNOWN family still blocks, instead of
# being swallowed by a bare try/except.
BLADE_DISCRIMINATORS = {"magnetic", "capacitor", "resistor", "varistor", "connector",
                        "thermistor", "semiconductor", "analog", "controller", "timeBase"}
PEAS_NATIVE_ATOMS = {"behavioral", "transmissionLine"}


def build_registry():
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    by_id = {}
    for repo in ("PEAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS", "TDAS",
                 "CIAS"):
        d = PSMA / repo / "schemas"
        if not d.is_dir():
            continue
        for p in d.rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by_id[s["$id"]] = s
    return Registry().with_resources(
        [(k, Resource(contents=s, specification=DRAFT202012)) for k, s in by_id.items()])


def touched_files(base):
    p = subprocess.run(["git", "-C", str(TAS), "diff", "--name-only", base, "--", "data/"],
                       capture_output=True, text=True)
    return sorted({Path(f).name for f in p.stdout.split() if f.endswith(".ndjson")})


def unwrap(rec, path):
    cur = rec
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def gate_file(fname, reg, max_report):
    from jsonschema import Draft202012Validator
    from blade_gate import BladeGate
    disc, repo, schema_file, blade_key = FAMILIES[fname]
    schema = json.loads((PSMA / repo / "schemas" / schema_file).read_text())
    validator = Draft202012Validator(schema, registry=reg)
    gate = BladeGate(blade_key)

    path = TAS / "data" / fname
    bad = checked = reported = 0
    t0 = time.time()
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            checked += 1
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as e:
                bad += 1
                if reported < max_report:
                    print(f"    line {lineno} NOT JSON: {e}"); reported += 1
                continue
            comp = unwrap(rec, disc)
            if comp is None:
                bad += 1
                if reported < max_report:
                    print(f"    line {lineno}: missing discriminator {'.'.join(disc)}")
                    reported += 1
                continue
            ref = (comp.get("manufacturerInfo") or {}).get("reference")
            errs = sorted(validator.iter_errors(comp), key=lambda e: e.path)
            if errs:
                bad += 1
                if reported < max_report:
                    print(f"    SCHEMA {ref}: {errs[0].message[:170]}"); reported += 1
                continue
            ok, why = gate.check(comp)
            if not ok:
                bad += 1
                if reported < max_report:
                    print(f"    BLADE  {ref}: {why}"); reported += 1
            if checked % 50000 == 0:
                print(f"    …{checked} checked ({time.time() - t0:.0f}s)", flush=True)
    print(f"  {fname}: {checked} records, {bad} FAILED ({time.time() - t0:.0f}s)")
    try:
        print(f"    {gate.summary()}")
    except Exception:  # noqa: BLE001
        pass
    return bad


def gate_circuits(reg, max_report):
    """Gate data/circuits.ndjson — CIAS bricks, not PEAS-wrapped parts.

    Three checks, because no one of them subsumes the others:
      1. JSON Schema (CIAS.json) — the brick's SHAPE is legal.
      2. PyCIAS.validate_cias_structure_json — graph-level integrity JSON Schema cannot
         express: unique names, every pin/port endpoint resolves, one discriminator per
         component.
      3. Physics. For inline components carrying a catalogue discriminator that is Blade
         Runner's job. For the PEAS-native atoms it is the CIAS LOWERING: to_subckt_json
         throws on a non-square/asymmetric inductance matrix, a non-positive self
         inductance, or |k_ij| > 1 — the coupled-inductor equivalent of a units error, and
         invisible to JSON Schema (every one of those is valid JSON).

    A brick that cannot be lowered to a netlist is not a brick, so a converter throw FAILS
    the record; it is never caught and counted as a pass.
    """
    from jsonschema import Draft202012Validator
    from blade_gate import BladeGate

    for d in ("build-py", "build"):
        sys.path.insert(0, str(PSMA / "CIAS" / d))
    try:
        import PyCIAS
    except ImportError as e:                  # a gate that cannot run is not a gate
        raise RuntimeError(
            f"PyCIAS unavailable ({e}). The circuits gate needs the CIAS lowering — it is "
            "the only check that sees an unphysical inductance matrix or an unemittable "
            "brick. Build it with:\n"
            "      cmake -S ../CIAS -B ../CIAS/build-py -DCIAS_BUILD_PYBIND=ON \\\n"
            "            -DFETCHCONTENT_SOURCE_DIR_JSON=../AAS/build/_deps/json-src\n"
            "      cmake --build ../CIAS/build-py --target PyCIAS -j8") from e

    schema = json.loads((PSMA / "CIAS" / "schemas" / "CIAS.json").read_text())
    validator = Draft202012Validator(schema, registry=reg)
    conv = PyCIAS.CiasCircuitConverter(PyCIAS.CircuitSimulator.Ngspice)
    gate = BladeGate((), required=True)   # components are already PEAS-wrapped — no re-wrap

    path = TAS / "data" / CIRCUITS
    bad = checked = reported = lowered = byref = parts = 0
    t0 = time.time()
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            checked += 1
            try:
                brick = json.loads(raw)
            except json.JSONDecodeError as e:
                bad += 1
                if reported < max_report:
                    print(f"    line {lineno} NOT JSON: {e}"); reported += 1
                continue
            name = brick.get("name", f"line {lineno}")

            errs = sorted(validator.iter_errors(brick), key=lambda e: e.path)
            if errs:
                bad += 1
                if reported < max_report:
                    print(f"    SCHEMA {name}: {errs[0].message[:170]}"); reported += 1
                continue

            problems = PyCIAS.validate_cias_structure_json(brick)
            if problems:
                bad += 1
                if reported < max_report:
                    print(f"    STRUCT {name}: {problems[0][:170]}"); reported += 1
                continue

            # The converter takes INLINE PEAS documents only. A component whose `data` is a
            # URI into a catalogue file is legal CIAS but cannot be lowered without resolving
            # it, so those bricks are counted and reported — never silently passed.
            if any(isinstance(c.get("data"), str) for c in brick.get("components", [])):
                byref += 1
            else:
                try:
                    conv.to_subckt_json(brick)
                    lowered += 1
                except Exception as e:  # noqa: BLE001 — unlowerable inline brick = failed record
                    bad += 1
                    if reported < max_report:
                        print(f"    LOWER  {name}: {str(e)[:170]}"); reported += 1
                    continue

            failed_part = False
            for comp in brick.get("components", []):
                data = comp.get("data")
                if not isinstance(data, dict):
                    continue                      # a URI reference into a catalogue file —
                                                  # gated where that file is gated
                disc = set(data) & BLADE_DISCRIMINATORS
                if not disc:
                    continue                      # PEAS_NATIVE_ATOMS: covered by the lowering
                parts += 1
                ok, why = gate.check(data)
                if not ok:
                    failed_part = True
                    if reported < max_report:
                        print(f"    BLADE  {name}/{comp.get('name')}: {why}"); reported += 1
                    break
            if failed_part:
                bad += 1

            if checked % 5000 == 0:
                print(f"    …{checked} checked ({time.time() - t0:.0f}s)", flush=True)
    print(f"  {CIRCUITS}: {checked} bricks, {lowered} lowered to a netlist, "
          f"{byref} skipped (components are catalogue URIs, not inline PEAS), "
          f"{parts} inline parts blade-checked, {bad} FAILED ({time.time() - t0:.0f}s)")
    try:
        print(f"    {gate.summary()}")
    except Exception:  # noqa: BLE001
        pass
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--files", default="", help="comma-separated basenames, skips git")
    ap.add_argument("--max-report", type=int, default=10)
    a = ap.parse_args()

    files = ([f.strip() for f in a.files.split(",") if f.strip()] if a.files
             else touched_files(a.base))
    if not files:
        print("changed-records gate: no data/*.ndjson touched — nothing to check")
        return 0

    unmapped = [f for f in files if f not in FAMILIES and f != CIRCUITS]
    gateable = [f for f in files if f in FAMILIES]
    circuits = CIRCUITS in files
    for f in unmapped:
        if ".quarantine_" in f or f.endswith((".bak", ".backup.ndjson")):
            print(f"  {f}: quarantine/backup file, not gated")
        else:
            print(f"  {f}: NO FAMILY MAPPING — cannot gate, refusing to pass",
                  file=sys.stderr)
            return 2

    try:
        reg = build_registry()
    except Exception as e:  # noqa: BLE001
        print(f"changed-records gate CANNOT RUN: {e}", file=sys.stderr)
        return 2

    print(f"changed-records gate: {len(gateable) + circuits} catalogue file(s) touched — "
          f"validating in full (schema + Blade Runner)")
    bad = 0
    for f in gateable:
        try:
            bad += gate_file(f, reg, a.max_report)
        except Exception as e:  # noqa: BLE001
            print(f"  {f}: gate CANNOT RUN: {e}", file=sys.stderr)
            return 2
    if circuits:
        try:
            bad += gate_circuits(reg, a.max_report)
        except Exception as e:  # noqa: BLE001
            print(f"  {CIRCUITS}: gate CANNOT RUN: {e}", file=sys.stderr)
            return 2
    print(f"changed-records gate: {'FAILED' if bad else 'clean'} ({bad} bad records)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
