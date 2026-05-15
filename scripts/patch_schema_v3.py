#!/usr/bin/env python3
"""
Second patch pass:
1. capacitors: add case='' where missing
2. resistors: strip modelParams and factors from datasheetInfo
3. magnetics: wrap scalar dcResistance as {nominal: value}
4. diodes: remove 'package' from mechanical, 'note' from electrical
"""
import json
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"

def patch_capacitors():
    path = DATA / "capacitors.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        part = rec.get("capacitor", {}).get("manufacturerInfo", {}).get("datasheetInfo", {}).get("part", {})
        if "case" not in part:
            part["case"] = ""
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"capacitors: {fixed} case fields added")

def patch_resistors():
    path = DATA / "resistors.ndjson"
    out, fixed = [], 0
    STRIP = {"modelParams", "factors"}
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        di = rec.get("resistor", {}).get("manufacturerInfo", {}).get("datasheetInfo", {})
        changed = False
        for k in STRIP:
            if k in di:
                del di[k]; changed = True
        if changed:
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"resistors: {fixed} entries patched (modelParams/factors stripped)")

def patch_magnetics():
    path = DATA / "magnetics.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        elec = rec.get("magnetic", {}).get("manufacturerInfo", {}).get("datasheetInfo", {}).get("electrical", {})
        if "dcResistance" in elec and not isinstance(elec["dcResistance"], dict):
            elec["dcResistance"] = {"nominal": elec["dcResistance"]}
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"magnetics: {fixed} dcResistance wrapped as object")

def patch_diodes():
    path = DATA / "diodes.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        di = rec.get("diode", {}).get("manufacturerInfo", {}).get("datasheetInfo", {})
        changed = False
        mech = di.get("mechanical", {})
        if "package" in mech:
            del mech["package"]; changed = True
        elec = di.get("electrical", {})
        if "note" in elec:
            del elec["note"]; changed = True
        if changed:
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"diodes: {fixed} entries patched")

patch_capacitors()
patch_resistors()
patch_magnetics()
patch_diodes()
print("Done.")
