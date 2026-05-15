#!/usr/bin/env python3
"""
Eighth patch: strip None values from thermal.temperature dimensionWithTolerance objects in capacitors.
"""
import json
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"

def strip_nulls(d):
    """Recursively strip None values from dict."""
    if isinstance(d, dict):
        return {k: strip_nulls(v) for k, v in d.items() if v is not None}
    if isinstance(d, list):
        return [strip_nulls(x) for x in d]
    return d

def patch_capacitors():
    path = DATA / "capacitors.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        di = rec.get("capacitor", {}).get("manufacturerInfo", {}).get("datasheetInfo", {})
        thermal = di.get("thermal")
        if thermal:
            new_thermal = strip_nulls(thermal)
            if new_thermal != thermal:
                di["thermal"] = new_thermal
                fixed += 1
            # Remove thermal if empty after stripping
            if not di.get("thermal"):
                del di["thermal"]
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"capacitors: {fixed} thermal objects cleaned")

patch_capacitors()
print("Done.")
