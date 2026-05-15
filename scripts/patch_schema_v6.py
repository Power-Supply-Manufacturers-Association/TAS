#!/usr/bin/env python3
"""
Sixth patch: comprehensive capacitor schema fix + remaining magnetic issues.
"""
import json
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"

CAS_PART_ALLOWED = {"partNumber", "series", "technology", "description", "case"}
CAS_ELEC_ALLOWED = {
    "capacitance", "capacitanceDriftLongTermPercent", "capacitanceMinimumLongTerm",
    "ratedVoltage", "voltageRatedDcMax", "dissipationFactor", "dissipationFactorFrequency",
    "leakageCurrent", "insulationResistance", "esr", "esrFrequency",
    "rippleCurrent", "rippleCurrentFrequency", "rippleCurrentTemperature",
    "rippleCurrentFrequencyPoints", "rippleCurrentTemperaturePoints",
    "thermalResistance", "_esrWarning",
}
CAS_BIZ_ALLOWED = {"packaging", "pu", "moq", "leadTime", "stock", "distribution", "priceCost"}
CAS_DS_ALLOWED = {"part", "electrical", "thermal", "mechanical", "business", "lifetime", "modelParams", "factors"}
CAS_THERMAL_ALLOWED = {"temperature", "tcc"}
MFRI_ALLOWED = {"name", "reference", "status", "datasheetUrl", "spiceModel", "datasheetInfo"}

def clean_capacitor(rec):
    cap = rec.get("capacitor", {})
    mi = cap.get("manufacturerInfo", {})

    # Clean manufacturerInfo top-level (only allowed fields)
    for k in list(mi.keys()):
        if k not in MFRI_ALLOWED:
            del mi[k]

    ds = mi.get("datasheetInfo", {})

    # Remove datasheetUrl from datasheetInfo (belongs in mi top-level, already allowed there)
    ds.pop("datasheetUrl", None)

    # Strip disallowed datasheetInfo keys
    for k in list(ds.keys()):
        if k not in CAS_DS_ALLOWED:
            del ds[k]

    # Clean part
    if "part" in ds:
        part = ds["part"]
        # Strip disallowed fields
        for k in list(part.keys()):
            if k not in CAS_PART_ALLOWED:
                del part[k]
        # Strip null values
        for k in list(part.keys()):
            if part[k] is None:
                del part[k]
        # Ensure required fields
        if "series" not in part:
            part["series"] = ""
        if "case" not in part:
            part["case"] = ""

    # Clean electrical
    if "electrical" in ds:
        e = ds["electrical"]
        # Normalize ESR capitalization
        if "ESR" in e and "esr" not in e:
            e["esr"] = e.pop("ESR")
        elif "ESR" in e:
            del e["ESR"]
        ds["electrical"] = {k: v for k, v in e.items()
                            if k in CAS_ELEC_ALLOWED and v is not None
                            and not (isinstance(v, dict) and not v)}

    # Clean thermal
    if "thermal" in ds:
        t = ds["thermal"]
        for k in list(t.keys()):
            if k not in CAS_THERMAL_ALLOWED:
                del t[k]
        if not t:  # empty thermal is not allowed (temperature required)
            del ds["thermal"]

    # Fix mechanical: ensure dimensions and shape exist
    mech = ds.get("mechanical", {})
    if not isinstance(mech, dict):
        mech = {}
    if "dimensions" not in mech:
        mech["dimensions"] = {}
    shape = mech.get("shape", {})
    if not isinstance(shape, dict):
        shape = {}
    if "assembly" not in shape:
        # Try to infer from part or use SMT as default
        shape["assembly"] = "SMT"
    if "shapeType" not in shape:
        shape["shapeType"] = "SMD"
    mech["shape"] = shape
    ds["mechanical"] = mech

    # Clean business
    if "business" in ds:
        biz = ds["business"]
        for k in list(biz.keys()):
            if k not in CAS_BIZ_ALLOWED:
                del biz[k]
        # Strip null values
        for k in list(biz.keys()):
            if biz[k] is None:
                del biz[k]

    mi["datasheetInfo"] = ds
    return rec


def patch_capacitors():
    path = DATA / "capacitors.ndjson"
    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        orig = json.dumps(rec)
        rec = clean_capacitor(rec)
        if json.dumps(rec) != orig:
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"capacitors: {fixed} entries fixed")


def patch_magnetics():
    """Strip any remaining disallowed fields from magnetics."""
    path = DATA / "magnetics.ndjson"
    MAS_ELEC_ALLOWED = {
        "inductance", "dcResistance", "ratedCurrent", "ratedVoltageDC", "ratedVoltageAC",
        "insulationTestVoltageAC", "insulationResistance", "leakageInductance", "impedancePoints",
        "maximumImpedance", "commonModeFilter", "saturationCurrentPeak", "selfResonantFrequency",
        "turnsRatio", "couplingCoefficient", "dcResistances",
    }
    MAS_MECH_ALLOWED = {"length", "width", "height", "diameter", "mounting"}
    MOUNT_MAP = {"SMT": "smt", "THT": "tht", "Pin": "pin", "Screw": "screw", "SMD": "smt"}

    out, fixed = [], 0
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        orig = json.dumps(rec)
        di = rec.get("magnetic", {}).get("manufacturerInfo", {}).get("datasheetInfo", {})
        # Clean electrical
        if "electrical" in di:
            elec = di["electrical"]
            for k in list(elec.keys()):
                if k not in MAS_ELEC_ALLOWED:
                    del elec[k]
            # Wrap scalar dcResistance
            if "dcResistance" in elec and not isinstance(elec["dcResistance"], dict):
                elec["dcResistance"] = {"nominal": elec["dcResistance"]}
        # Clean mechanical
        if "mechanical" in di:
            mech = di["mechanical"]
            for k in list(mech.keys()):
                if k not in MAS_MECH_ALLOWED:
                    del mech[k]
            if "mounting" in mech and mech["mounting"] in MOUNT_MAP:
                mech["mounting"] = MOUNT_MAP[mech["mounting"]]
        if json.dumps(rec) != orig:
            fixed += 1
        out.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n")
    print(f"magnetics: {fixed} entries fixed")


patch_capacitors()
patch_magnetics()
print("Done.")
