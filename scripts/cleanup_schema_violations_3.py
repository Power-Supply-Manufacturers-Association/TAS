#!/usr/bin/env python3
"""
Third-round schema cleanup.

Comprehensive fix for all remaining schema violations.

1. capacitors.ndjson:
   - Strip all extra/invalid fields from datasheetInfo (keep only part + electrical)
   - Remove disallowed part fields (keep only: partNumber, series, technology, case)
   - Remove disallowed electrical fields (keep only CAS-allowed ones)
   - Fix capitalisation: ESR → esr
   - Add case='' if missing
   - Remove null values from electrical (keeps the dict clean for optional fields)
   - Remove entire datasheetInfo sub-keys that have invalid data (mechanical, thermal, etc.)

2. resistors.ndjson:
   - Add case='' for entries missing case in part

3. diodes.ndjson:
   - Strip 'semiconductor' from inside diode wrapper
   - Strip 'deviceType' from part

4. igbts.ndjson:
   - Normalise assemblyType: 'SMD' → 'smt', 'THT' → 'tht'

5. magnetics.ndjson:
   - Fix coil.functionalDescription from dict to array (wrap in array with required fields)
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

# ─── CAS capacitor electrical allowed fields ──────────────────────────────────

CAS_ELEC_ALLOWED = {
    "capacitance", "capacitanceDriftLongTermPercent", "capacitanceMinimumLongTerm",
    "ratedVoltage", "voltageRatedDcMax", "dissipationFactor", "dissipationFactorFrequency",
    "leakageCurrent", "insulationResistance", "esr", "esrFrequency",
    "rippleCurrent", "rippleCurrentFrequency", "rippleCurrentTemperature",
    "rippleCurrentFrequencyPoints", "rippleCurrentTemperaturePoints",
    "thermalResistance", "_esrWarning",
}

CAS_PART_ALLOWED = {"partNumber", "series", "technology", "description", "case"}

SAS_PART_ALLOWED = {"partNumber", "series", "technology", "subType", "case", "matchcodeDescription"}

ASSEMBLY_TYPE_MAP = {
    "SMD": "smt",
    "THT": "tht",
    "SMT": "smt",
    "TH": "tht",
}

VALID_ASSEMBLY_TYPES = {"pin", "screw", "smt", "flyingLead", "tht", "pcbPad", "chassis"}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def strip_nulls(d: dict) -> dict:
    """Remove keys with None values from a dict (shallow)."""
    return {k: v for k, v in d.items() if v is not None}


def clean_electrical_cap(elec: dict) -> dict:
    """Strip disallowed/null electrical fields for CAS capacitor."""
    # Rename ESR → esr
    if "ESR" in elec and "esr" not in elec:
        elec["esr"] = elec.pop("ESR")
    elif "ESR" in elec:
        del elec["ESR"]

    # Keep only allowed fields, remove nulls
    cleaned = {}
    for k, v in elec.items():
        if k not in CAS_ELEC_ALLOWED:
            continue
        if v is None:
            continue
        # rippleCurrentFrequencyPoints/rippleCurrentTemperaturePoints should be object
        # if they're empty dicts, skip them
        if isinstance(v, dict) and not v:
            continue
        cleaned[k] = v
    return cleaned


# ─── File processors ──────────────────────────────────────────────────────────

def fix_capacitors(data_dir: Path):
    path = data_dir / "capacitors.ndjson"
    entries = []
    changed = 0

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            before = json.dumps(d, sort_keys=True)

            cap = d.get("capacitor", {})
            mi = cap.get("manufacturerInfo", {})
            ds = mi.get("datasheetInfo", {})

            # Strip disallowed datasheetInfo keys (only part and electrical are safe)
            safe_ds_keys = {"part", "electrical"}
            for k in list(ds.keys()):
                if k not in safe_ds_keys:
                    del ds[k]

            # Clean part
            if "part" in ds:
                part = ds["part"]
                ds["part"] = {k: v for k, v in part.items() if k in CAS_PART_ALLOWED}
                if "case" not in ds["part"]:
                    ds["part"]["case"] = ""
                if "series" not in ds["part"]:
                    ds["part"]["series"] = ""

            # Clean electrical
            if "electrical" in ds:
                ds["electrical"] = clean_electrical_cap(ds["electrical"])

            after = json.dumps(d, sort_keys=True)
            if before != after:
                changed += 1
            entries.append(d)

    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")

    print(f"capacitors.ndjson: {len(entries)} entries, {changed} modified")


def fix_resistors(data_dir: Path):
    path = data_dir / "resistors.ndjson"
    entries = []
    changed = 0

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            part = d.get("resistor", {}).get("manufacturerInfo", {}).get("datasheetInfo", {}).get("part", {})
            if "case" not in part:
                part["case"] = ""
                changed += 1
            entries.append(d)

    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")

    print(f"resistors.ndjson: {len(entries)} entries, {changed} case fields added")


def fix_diodes(data_dir: Path):
    path = data_dir / "diodes.ndjson"
    entries = []
    changed = 0

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            diode = d.get("diode", {})

            # Remove semiconductor from diode inner keys
            if "semiconductor" in diode:
                del diode["semiconductor"]
                changed += 1

            # Strip deviceType from part
            part = diode.get("manufacturerInfo", {}).get("datasheetInfo", {}).get("part", {})
            if "deviceType" in part:
                del part["deviceType"]
                changed += 1

            entries.append(d)

    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")

    print(f"diodes.ndjson: {len(entries)} entries, {changed} fixes applied")


def fix_igbts(data_dir: Path):
    path = data_dir / "igbts.ndjson"
    entries = []
    changed = 0

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            mi = d.get("igbt", {}).get("manufacturerInfo", {})
            mech = mi.get("datasheetInfo", {}).get("mechanical", {})
            at = mech.get("assemblyType", "")
            if at and at not in VALID_ASSEMBLY_TYPES:
                normalized = ASSEMBLY_TYPE_MAP.get(at.upper(), at.lower())
                if normalized in VALID_ASSEMBLY_TYPES:
                    mech["assemblyType"] = normalized
                    changed += 1

            entries.append(d)

    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")

    print(f"igbts.ndjson: {len(entries)} entries, {changed} assemblyType fixed")


def fix_magnetics(data_dir: Path):
    path = data_dir / "magnetics.ndjson"
    entries = []
    changed = 0

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            coil = d.get("magnetic", {}).get("coil", {})
            fd = coil.get("functionalDescription")

            # If it's a dict (wrong type), wrap in array with required fields
            if isinstance(fd, dict):
                winding = {
                    "name": "Dummy",
                    "numberTurns": fd.get("numberTurns", 1),
                    "numberParallels": fd.get("numberParallels", 1),
                    "isolationSide": fd.get("isolationSide", "primary"),
                    "wire": fd.get("wire", "Dummy"),
                }
                coil["functionalDescription"] = [winding]
                changed += 1

            entries.append(d)

    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")

    print(f"magnetics.ndjson: {len(entries)} entries, {changed} coil.fd fixed")


def main():
    fix_capacitors(DATA_DIR)
    fix_resistors(DATA_DIR)
    fix_diodes(DATA_DIR)
    fix_igbts(DATA_DIR)
    fix_magnetics(DATA_DIR)
    print("Done.")


if __name__ == "__main__":
    main()
