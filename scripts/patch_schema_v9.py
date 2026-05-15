#!/usr/bin/env python3
"""
Ninth patch: remove thermal if temperature object is empty (fails anyOf on dimensionWithTolerance).
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
        thermal = di.get("thermal", {})
        temp = thermal.get("temperature", None)
        if isinstance(temp, dict) and not temp:
            # temperature is {} - remove thermal entirely
            del di["thermal"]
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"capacitors: {fixed} empty-temperature thermals removed")

patch_capacitors()
print("Done.")
