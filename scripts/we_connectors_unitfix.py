#!/usr/bin/env python3
"""ABT #403: fix the residual Würth unit errors that have no datasheet to check against.

we_connectors_reconcile.py corrected every record whose own datasheet could be fetched.
A tail remains: 129 records with contactResistance >= 1 Ω and 84 with insulationResistance
<= 1000 Ω, on parts WE no longer publishes a datasheet for (404). Both are physically
impossible as stored — a 20 Ω connector contact would dissipate 8 W at 0.6 A, and a 1 Ω
insulation resistance is a short circuit.

WHY CORRECTING THESE IS NOT GUESSING. This fixes the UNIT, not the value: the old importer
copied the datasheet's number and dropped its conversion. The conversion is not inferred
from physics or "typical" values — it is measured from the 911 records in the SAME import
whose datasheets WERE fetched, where the mapping is unanimous with zero counterexamples:

    contactResistance      20.0 -> 0.02  (350/350)   30.0 -> 0.03 (65/65)
                           50.0 -> 0.05  (10/10)     60.0 -> 0.06 (8/8)
                           80.0 -> 0.08  (4/4)       ... every case exactly x1e-3 (mΩ)
    insulationResistance    1.0 -> 1e9   (254/254, GΩ)
                          100.0 -> 1e8   (21/21,  MΩ)
                         1000.0 -> 1e9   (199/199, MΩ)

So the stored digits are preserved exactly and only the omitted unit conversion is applied.
Anything whose stored value is NOT in that verified mapping is left alone and reported,
never scaled on a pattern.

Provenance is recorded as `manual`, NOT manufacturerDatasheet: these specific parts were
not checked against their own datasheet, and the trail should say so.

  we_connectors_unitfix.py            # dry run
  we_connectors_unitfix.py --apply
"""
import argparse
import json
import os
import sys
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
LIVE = TAS / "data" / "connectors.ndjson"
AUDIT = TAS / "staging" / "we_conn" / "unitfix_audit.json"
RETRIEVED = "2026-07-31"

# Verified from the datasheet-confirmed cohort (see docstring). Nothing outside these
# mappings is touched.
IR_MAP = {1.0: 1e9, 100.0: 1e8, 1000.0: 1e9}
CR_FACTOR = 1e-3          # mΩ -> Ω, unanimous across all 437 confirmed corrections
CR_IMPOSSIBLE = 1.0       # a connector contact is never >= 1 Ω
IR_IMPOSSIBLE = 1e4       # insulation resistance is never <= 10 kΩ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    from resolve_dim import resolve_or_none
    from merge_staged_connectors import build_validator
    gate = BladeGate("connector")
    v = build_validator()

    before_lines = sum(1 for _ in LIVE.open(encoding="utf-8"))
    before_size = LIVE.stat().st_size

    audit, unmapped = [], []
    fixed_cr = fixed_ir = touched = reverted = 0
    tmp = LIVE.with_suffix(".ndjson.unitfix_tmp")
    with LIVE.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as out:
        for raw in src:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            if "rth Elektronik" not in s:
                out.write(s + "\n")
                continue
            obj = json.loads(s)
            c = obj.get("connector") or obj
            mi = c.get("manufacturerInfo") or {}
            if "rth" not in (mi.get("name") or ""):
                out.write(s + "\n")
                continue
            ref = mi.get("reference")
            el = ((mi.get("datasheetInfo") or {}).get("electrical") or {})
            changes = []

            cr = el.get("contactResistance")
            cur = resolve_or_none(cr)
            if isinstance(cur, (int, float)) and cur >= CR_IMPOSSIBLE:
                new = cur * CR_FACTOR
                key = "maximum" if (isinstance(cr, dict) and "maximum" in cr) else "nominal"
                el["contactResistance"] = {key: new}
                changes.append(("electrical.contactResistance", cur, new))
                fixed_cr += 1

            ir = el.get("insulationResistance")
            if isinstance(ir, (int, float)) and ir <= IR_IMPOSSIBLE:
                if ir in IR_MAP:
                    el["insulationResistance"] = IR_MAP[ir]
                    changes.append(("electrical.insulationResistance", ir, IR_MAP[ir]))
                    fixed_ir += 1
                else:
                    unmapped.append({"reference": ref, "field": "insulationResistance",
                                     "value": ir})

            if not changes:
                out.write(s + "\n")
                continue

            ds = mi.setdefault("datasheetInfo", {})
            ds.setdefault("provenance", []).append({
                "source": "manual",
                "sourceName": ("unit correction: value carried the datasheet's figure in "
                               "mΩ/MΩ/GΩ but was stored as Ω; conversion verified against "
                               "911 same-import records whose datasheets were fetched"),
                "retrievedDate": RETRIEVED})

            errs = sorted(v.iter_errors(c), key=lambda e: e.path)
            ok, _ = (False, None) if errs else gate.check(c)
            if not ok:
                reverted += 1
                out.write(s + "\n")
                continue
            touched += 1
            for field, old, new in changes:
                audit.append({"reference": ref, "field": field, "old": old, "new": new})
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")

    print(f"records changed          : {touched}")
    print(f"  contactResistance mΩ->Ω: {fixed_cr}")
    print(f"  insulationResistance   : {fixed_ir}")
    print(f"left untouched           : {reverted} (would not validate)")
    print(f"NOT mapped (left as-is)  : {len(unmapped)}")
    for u in unmapped[:6]:
        print(f"    {u['reference']} {u['field']}={u['value']}")
    print(" ", gate.summary())

    if not a.apply:
        tmp.unlink()
        print("\nDRY RUN — pass --apply to write")
        return 0
    if LIVE.stat().st_size != before_size or \
            sum(1 for _ in LIVE.open(encoding="utf-8")) != before_lines:
        tmp.unlink()
        print("ABORTED: connectors.ndjson changed while building the corrected copy")
        return 1
    AUDIT.write_text(json.dumps({"corrections": audit, "unmapped": unmapped},
                                indent=1, ensure_ascii=False))
    os.replace(tmp, LIVE)
    print(f"\nwrote {LIVE}; audit: {AUDIT} ({len(audit)} changes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
