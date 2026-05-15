#!/usr/bin/env python3
"""
Fifth patch pass:
1. capacitors: remove empty thermal objects (temperature required when thermal present)
2. magnetics: strip componentSubType and packageSize from part
"""
import json
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"

def patch_capacitors():
    path = DATA / "capacitors.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        di = rec.get("capacitor", {}).get("manufacturerInfo", {}).get("datasheetInfo", {})
        if "thermal" in di and not di["thermal"]:
            del di["thermal"]
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"capacitors: {fixed} empty thermal objects removed")

def patch_magnetics():
    path = DATA / "magnetics.ndjson"
    out, fixed = [], 0
    STRIP = {"componentSubType", "packageSize"}
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        part = rec.get("magnetic", {}).get("manufacturerInfo", {}).get("datasheetInfo", {}).get("part", {})
        changed = False
        for k in STRIP:
            if k in part:
                del part[k]; changed = True
        if changed:
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"magnetics: {fixed} part fields stripped")

patch_capacitors()
patch_magnetics()
print("Done.")
