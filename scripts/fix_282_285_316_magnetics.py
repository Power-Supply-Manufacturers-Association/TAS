#!/usr/bin/env python3
"""One atomic pass over magnetics.ndjson applying three approved fixes.

ABT #316 (critical) — quarantine 1,232 FABRICATED Coilcraft "transformer" rows.
  Evidence: 11 descriptions x exactly 112 rows; 924/1232 have a part.description
  inductance contradicting the record's own electrical inductance on a
  x0.5/x1/x1.5/x2 grid; no manufacturerInfo.reference, no name, no
  saturationCurrentPeak on any of them. -> magnetics.quarantine_fabricated.ndjson

ABT #282 — Blade Runner MAG_SUBTYPE_MISMATCH, the 548 non-Coilcraft hits.
  Rows whose remaining fields are all legal on the target MAS variant are FLIPPED
  (with dcResistance -> dcResistances where the target requires the plural form).
  Rows carrying a field the target variant cannot hold (saturationCurrent* on a
  transformer, inductance on a chipBead) are QUARANTINED, per the approved rule
  "quarantine any row whose data does not support the target variant" — the data
  is preserved, never silently dropped. -> magnetics.quarantine_subtype_mismatch.ndjson

ABT #285 — canonicalize manufacturerInfo.name spellings (majority spelling wins).

Line-patch semantics: every untouched line is written back BYTE-IDENTICAL; only
targeted rows are re-serialized. Flipped rows are validated against the MAS
magnetic schema BEFORE being written; a row that fails validation is quarantined
rather than written (never emit a schema-invalid object).

Usage:  fix_282_285_316_magnetics.py [--apply]     (default: dry run)
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

PSMA = Path.home() / "PSMA"
DATA = PSMA / "TAS" / "data"
SRC = DATA / "magnetics.ndjson"
Q_FAB = DATA / "magnetics.quarantine_fabricated.ndjson"
Q_SUB = DATA / "magnetics.quarantine_subtype_mismatch.ndjson"

# --- ABT #285: canonical manufacturer spellings (majority spelling wins) -------
CANON = {
    "Panasonic Electronic Components": "Panasonic",
    "Vishay Semiconductors": "Vishay",
    "Yageo": "YAGEO",
    "Murata Electronics": "Murata",
    "ABRACON": "Abracon",
    "Bourns Inc.": "Bourns",
    "TAIYO YUDEN": "Taiyo Yuden",
    "Infineon Technologies": "Infineon",
    "Pulse Electronics": "PULSE",
    "Littelfuse Inc.": "Littelfuse",
    "Rohm": "ROHM",
    "Microchip Technology": "Microchip",
    "Wolfspeed, Inc.": "Wolfspeed",
    "Vishay Dale": "Vishay / Dale",
    "Murata Power Solutions": "Murata Power Solutions Inc.",
    "Microsemi": "Microsemi Corporation",
    "Diodes Incorporated": "Diodes Inc.",
    "Alpha and Omega Semiconductor": "Alpha and Omega",
    "Navitas Semiconductor, Inc.": "Navitas",
    "GeneSiC Semiconductor": "GeneSiC",
    "Taiwan Semiconductor": "Taiwan Semiconductor Corporation",
    "NXP Semiconductors": "NXP",
}

# --- ABT #282: MAS electrical variant property sets (from MAS/schemas/magnetic.json)
ALLOWED = {
    "transformer": {
        "couplingCoefficient", "dcResistances", "inductance", "insulationResistance",
        "insulationTestVoltageAC", "leakageInductance", "name", "numberTurns",
        "ratedCurrentPoints", "ratedCurrents", "ratedVoltageAC", "ratedVoltageDC",
        "selfResonantFrequency", "subtype", "turnsRatios"},
    "chipBead": {
        "dcResistance", "impedancePoints", "impedanceTolerance", "name",
        "numberPulsesPoints", "numberTurns", "pulsePoints", "ratedCurrentPoints",
        "ratedCurrents", "reactancePoints", "resistancePoints", "selfResonantFrequency",
        "subtype"},
}
# Field mapping, not just renaming: the inductor variant's `dcResistance` is a single
# dimensionWithTolerance, while the transformer variant's `dcResistances` is an ARRAY
# ("DC resistance per winding"). A single-winding DCR becomes a one-element array.
RENAME = {"transformer": {"dcResistance": ("dcResistances", lambda v: [v])}}

FAB_REASON = (
    "ABT #316: synthesized record, not a real Coilcraft part — 11 distinct "
    "part.description values x exactly 112 rows each (1232 total); 924/1232 have a "
    "description inductance contradicting the record's own electrical inductance on a "
    "x0.5/x1/x1.5/x2 grid; manufacturerInfo.reference, magnetic.name and "
    "saturationCurrentPeak null on every row. provenance claimed 'Coilcraft parametric "
    "API (scraped JSON)' 2026-06-22. Same failure class as ABT #247."
)


def build_validator():
    by_id = {}
    for repo in ("PEAS", "MAS"):
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
    resources = [Resource(contents=s, specification=DRAFT202012) for s in by_id.values()]
    registry = Registry().with_resources([(r.contents["$id"], r) for r in resources])
    schema = json.loads((PSMA / "MAS" / "schemas" / "magnetic.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def is_fabricated_coilcraft(mag):
    """The ABT #316 signature. Deliberately narrow: every clause must hold."""
    mi = mag.get("manufacturerInfo") or {}
    if mi.get("name") != "Coilcraft":
        return False
    if mi.get("reference") is not None or mag.get("name") is not None:
        return False
    ds = mi.get("datasheetInfo") or {}
    desc = (ds.get("part") or {}).get("description") or ""
    if not re.fullmatch(r"Transformer \d+µH", desc):
        return False
    el = ds.get("electrical") or []
    return all(e.get("saturationCurrentPeak") is None for e in el if isinstance(e, dict))


def target_subtype(mag):
    """Blade Runner's MAG_SUBTYPE_MISMATCH rule, verbatim (validator/src/magnetics.cpp)."""
    ds = (mag.get("manufacturerInfo") or {}).get("datasheetInfo") or {}
    d = (ds.get("part") or {}).get("description")
    if not isinstance(d, str):
        return None
    desc = d.lower()
    if ("common mode" in desc or "common-mode" in desc) and ("choke" in desc or "filter" in desc):
        expected = "commonModeChoke"
    elif "bead" in desc:
        expected = "chipBead"
    elif "transformer" in desc:
        expected = "transformer"
    else:
        return None
    el = ds.get("electrical") or []
    for e in el:
        if isinstance(e, dict) and e.get("subtype") == expected:
            return None          # already coherent -> not a finding
    return expected


def try_flip(mag, expected):
    """Return (new_mag, None) if every field is legal on the target, else (None, reason)."""
    ds = mag["manufacturerInfo"]["datasheetInfo"]
    allowed = ALLOWED.get(expected)
    if allowed is None:
        return None, f"no property set known for target variant {expected}"
    ren = RENAME.get(expected, {})
    new_el = []
    for e in ds.get("electrical") or []:
        if not isinstance(e, dict):
            return None, "non-object electrical entry"
        out = {}
        for k, v in e.items():
            k2, xform = ren.get(k, (k, None))
            if k2 not in allowed:
                return None, f"field '{k}' is not permitted on variant '{expected}'"
            out[k2] = xform(v) if xform else v
        out["subtype"] = expected
        new_el.append(out)
    mag = json.loads(json.dumps(mag))
    mag["manufacturerInfo"]["datasheetInfo"]["electrical"] = new_el
    return mag, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    a = ap.parse_args()

    validator = build_validator()
    stats = Counter()
    canon_hits = Counter()
    flip_by = Counter()
    quar_reasons = Counter()

    tmp = SRC.with_suffix(".ndjson.tmp")
    fab_out, sub_out, keep_out = [], [], []

    with SRC.open("r", encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.rstrip("\n")
            if not stripped.strip():
                continue
            stats["total"] += 1

            # cheap pre-filter: only parse lines that could possibly be affected
            touched_name = any(k in stripped for k in CANON)
            if not touched_name and "Coilcraft" not in stripped and "subtype" not in stripped:
                keep_out.append(stripped)
                continue

            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                stats["unparseable_kept"] += 1
                keep_out.append(stripped)
                continue

            mag = obj.get("magnetic")
            if mag is None:
                keep_out.append(stripped)
                continue

            changed = False

            # --- ABT #316 --------------------------------------------------
            if is_fabricated_coilcraft(mag):
                obj["quarantineReason"] = FAB_REASON
                fab_out.append(json.dumps(obj, ensure_ascii=False))
                stats["quarantined_fabricated_316"] += 1
                continue

            # --- ABT #285 --------------------------------------------------
            mi = mag.get("manufacturerInfo") or {}
            nm = mi.get("name")
            if isinstance(nm, str) and nm in CANON:
                mi["name"] = CANON[nm]
                canon_hits[f"{nm} -> {CANON[nm]}"] += 1
                stats["canonicalized_285"] += 1
                changed = True

            # --- ABT #282 --------------------------------------------------
            expected = target_subtype(mag)
            if expected is not None:
                flipped, reason = try_flip(mag, expected)
                if flipped is None:
                    obj["quarantineReason"] = (
                        f"ABT #282: description names a {expected} but the record's data "
                        f"does not support that MAS variant — {reason}. Quarantined rather "
                        f"than dropping the value (approved 2026-07-27)."
                    )
                    sub_out.append(json.dumps(obj, ensure_ascii=False))
                    quar_reasons[f"{expected}: {reason}"] += 1
                    stats["quarantined_subtype_282"] += 1
                    continue
                errs = sorted(validator.iter_errors(flipped), key=lambda e: e.path)
                if errs:
                    obj["quarantineReason"] = (
                        f"ABT #282: flip to '{expected}' failed MAS validation: "
                        f"{errs[0].message[:200]}"
                    )
                    sub_out.append(json.dumps(obj, ensure_ascii=False))
                    quar_reasons[f"{expected}: MAS-invalid after flip"] += 1
                    stats["quarantined_invalid_282"] += 1
                    continue
                obj["magnetic"] = flipped
                flip_by[expected] += 1
                stats["flipped_282"] += 1
                changed = True

            keep_out.append(json.dumps(obj, ensure_ascii=False) if changed else stripped)

    print("=" * 74)
    print(f"{'DRY RUN — nothing written' if not a.apply else 'APPLYING'}")
    print("=" * 74)
    for k in ("total", "quarantined_fabricated_316", "flipped_282",
              "quarantined_subtype_282", "quarantined_invalid_282",
              "canonicalized_285", "unparseable_kept"):
        print(f"  {k:32} {stats[k]}")
    print(f"  {'rows retained in magnetics.ndjson':32} {len(keep_out)}")
    print("\n--- #282 flips by target variant ---")
    for k, v in flip_by.most_common():
        print(f"  {v:6d}  {k}")
    print("\n--- #282 quarantine reasons ---")
    for k, v in quar_reasons.most_common():
        print(f"  {v:6d}  {k}")
    print("\n--- #285 canonicalizations (magnetics only) ---")
    for k, v in canon_hits.most_common():
        print(f"  {v:6d}  {k}")

    if not a.apply:
        print("\nRe-run with --apply to write.")
        return 0

    with tmp.open("w", encoding="utf-8") as fh:
        for line in keep_out:
            fh.write(line + "\n")
    for path, rows in ((Q_FAB, fab_out), (Q_SUB, sub_out)):
        if rows:
            with path.open("a", encoding="utf-8") as fh:
                for line in rows:
                    fh.write(line + "\n")
            print(f"appended {len(rows)} rows -> {path.name}")
    os.replace(tmp, SRC)
    print(f"atomically replaced {SRC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
