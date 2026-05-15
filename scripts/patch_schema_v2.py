#!/usr/bin/env python3
"""
Patch remaining schema violations after recover_and_fix_all.py:
1. capacitors.ndjson: add series='' where missing
2. resistors.ndjson: remove priceCost from business
3. magnetics.ndjson: add bobbin='Dummy' to coils missing it
4. diodes.ndjson: remove leakageCurrent (-> not allowed), _needsVerification*, _verificationNote*
"""
import json
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"

def patch_capacitors():
    path = DATA / "capacitors.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        body = rec.get("capacitor", {})
        part = body.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("part", {})
        if "series" not in part:
            part["series"] = ""
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"capacitors: {fixed} series fields added")

def patch_resistors():
    path = DATA / "resistors.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        body = rec.get("resistor", {})
        biz = body.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("business", {})
        if "priceCost" in biz:
            del biz["priceCost"]
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"resistors: {fixed} priceCost fields removed")

def patch_magnetics():
    path = DATA / "magnetics.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        coil = rec.get("magnetic", {}).get("coil", None)
        if isinstance(coil, dict) and "bobbin" not in coil:
            coil["bobbin"] = "Dummy"
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"magnetics: {fixed} bobbin fields added")

def patch_diodes():
    path = DATA / "diodes.ndjson"
    BAD_ELEC = {"leakageCurrent"}
    BAD_DS = {"_needsVerification", "_verificationNotes", "_verificationNote"}
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        di = rec.get("diode", {}).get("manufacturerInfo", {}).get("datasheetInfo", {})
        changed = False
        for k in BAD_DS:
            if k in di:
                del di[k]; changed = True
        elec = di.get("electrical", {})
        for k in BAD_ELEC:
            if k in elec:
                del elec[k]; changed = True
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
