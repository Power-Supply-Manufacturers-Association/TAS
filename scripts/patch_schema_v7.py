#!/usr/bin/env python3
"""
Seventh patch:
1. capacitors: restore technology='' where it was null (required string)
2. magnetics: strip componentType/series from part; strip assembly from business
"""
import json
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"

MAS_PART_ALLOWED = {
    "partNumber", "description", "matchCode", "family", "caseCode",
    "windingStyle", "material", "numberOfWindings", "automotive", "shielded", "insulationGrade",
}
MAS_BIZ_ALLOWED = {"packaging"}

def patch_capacitors():
    path = DATA / "capacitors.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        part = rec.get("capacitor", {}).get("manufacturerInfo", {}).get("datasheetInfo", {}).get("part", {})
        if "technology" not in part:
            part["technology"] = ""
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"capacitors: {fixed} technology fields restored")

def patch_magnetics():
    path = DATA / "magnetics.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        di = rec.get("magnetic", {}).get("manufacturerInfo", {}).get("datasheetInfo", {})
        changed = False
        part = di.get("part", {})
        for k in list(part.keys()):
            if k not in MAS_PART_ALLOWED:
                del part[k]; changed = True
        biz = di.get("business", {})
        for k in list(biz.keys()):
            if k not in MAS_BIZ_ALLOWED:
                del biz[k]; changed = True
        if changed:
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"magnetics: {fixed} entries fixed")

patch_capacitors()
patch_magnetics()
print("Done.")
