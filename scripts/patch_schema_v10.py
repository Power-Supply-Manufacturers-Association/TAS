#!/usr/bin/env python3
"""
Tenth patch: strip null values from datasheetInfo top-level fields (lifetime, modelParams, factors).
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
        changed = False
        for k in list(di.keys()):
            if di[k] is None:
                del di[k]; changed = True
        if changed:
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"capacitors: {fixed} null top-level datasheetInfo fields removed")

patch_capacitors()
print("Done.")
