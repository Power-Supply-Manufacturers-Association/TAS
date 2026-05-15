#!/usr/bin/env python3
"""
Third patch pass:
1. capacitors: rename ESR -> esr, strip operatingTemperature from thermal
2. magnetics: lowercase mounting, wrap cost as {value,currency}, strip assembly/dimensions from mechanical
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
        elec = di.get("electrical", {})
        if "ESR" in elec:
            elec["esr"] = elec.pop("ESR")
            changed = True
        thermal = di.get("thermal", {})
        if "operatingTemperature" in thermal:
            del thermal["operatingTemperature"]
            changed = True
        if changed:
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"capacitors: {fixed} entries patched")

MOUNT_MAP = {"SMT": "smt", "THT": "tht", "Pin": "pin", "Screw": "screw"}

def patch_magnetics():
    path = DATA / "magnetics.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        mag = rec.get("magnetic", {})
        changed = False
        # Fix mechanical
        mech = mag.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("mechanical", {})
        for k in ("assembly", "dimensions"):
            if k in mech:
                del mech[k]; changed = True
        if "mounting" in mech and mech["mounting"] in MOUNT_MAP:
            mech["mounting"] = MOUNT_MAP[mech["mounting"]]
            changed = True
        # Fix distributorsInfo cost
        for dist in mag.get("distributorsInfo", []):
            c = dist.get("cost")
            if c is not None and not isinstance(c, dict):
                dist["cost"] = {"value": float(c), "currency": "USD"}
                changed = True
        if changed:
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"magnetics: {fixed} entries patched")

patch_capacitors()
patch_magnetics()
print("Done.")
