#!/usr/bin/env python3
"""
Post-recovery schema cleanup script.

Fixes additionalProperties violations introduced by quarantine recovery:

1. capacitors.ndjson:
   - Remove disallowed part fields: useInDcTool, internalViewOnly, deviceType,
     dataCompleteness, matchcodeDescription

2. resistors.ndjson:
   - Remove disallowed root-level fields: powerRating, resistance, tolerance
     (these already exist inside datasheetInfo.electrical — they're duplicates)

3. mosfets.ndjson:
   - Remove disallowed part field: deviceType

4. igbts.ndjson:
   - Remove disallowed part field: deviceType
   - Fix invalid status value: 'unknown' → remove the field (not in enum)

5. magnetics.ndjson:
   - Remove invalid core.functionalDescription.type: 'unknown'
     (not in enum ['twoPieceSet', 'pieceAndPlate', 'toroidal', 'closedShape'])
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

# Allowed fields per schema
CAP_PART_ALLOWED = {"partNumber", "series", "technology", "description", "case"}
SAS_PART_ALLOWED = {"partNumber", "series", "technology", "subType", "case", "matchcodeDescription"}
VALID_STATUS = {"production", "nrnd", "obsolete", "preview"}
VALID_CORE_TYPES = {"twoPieceSet", "pieceAndPlate", "toroidal", "closedShape"}


def clean_dict_keys(d: dict, allowed: set) -> dict:
    return {k: v for k, v in d.items() if k in allowed}


def fix_capacitor(entry: dict) -> dict:
    cap = entry.get("capacitor", {})
    mi = cap.get("manufacturerInfo", {})
    ds = mi.get("datasheetInfo", {})
    if "part" in ds:
        ds["part"] = clean_dict_keys(ds["part"], CAP_PART_ALLOWED)
    return entry


def fix_resistor(entry: dict) -> dict:
    r = entry.get("resistor", {})
    # Remove extra root-level fields (only manufacturerInfo and distributorsInfo allowed)
    for k in ("powerRating", "resistance", "tolerance"):
        r.pop(k, None)
    return entry


def fix_mosfet(entry: dict) -> dict:
    mi = entry.get("mosfet", {}).get("manufacturerInfo", {})
    ds = mi.get("datasheetInfo", {})
    if "part" in ds:
        ds["part"] = clean_dict_keys(ds["part"], SAS_PART_ALLOWED)
    return entry


def fix_igbt(entry: dict) -> dict:
    mi = entry.get("igbt", {}).get("manufacturerInfo", {})
    # Fix invalid status
    if mi.get("status") not in VALID_STATUS:
        mi.pop("status", None)
    ds = mi.get("datasheetInfo", {})
    if "part" in ds:
        ds["part"] = clean_dict_keys(ds["part"], SAS_PART_ALLOWED)
    return entry


def fix_magnetic(entry: dict) -> dict:
    core = entry.get("magnetic", {}).get("core", {})
    fd = core.get("functionalDescription", {})
    if isinstance(fd, dict) and fd.get("type") not in VALID_CORE_TYPES:
        fd.pop("type", None)
    return entry


def process_file(path: Path, fix_fn):
    entries = []
    changed = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            before = json.dumps(d, sort_keys=True)
            d = fix_fn(d)
            after = json.dumps(d, sort_keys=True)
            if before != after:
                changed += 1
            entries.append(d)

    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    print(f"{path.name}: {len(entries)} entries, {changed} modified")


def main():
    process_file(DATA_DIR / "capacitors.ndjson", fix_capacitor)
    process_file(DATA_DIR / "resistors.ndjson", fix_resistor)
    process_file(DATA_DIR / "mosfets.ndjson", fix_mosfet)
    process_file(DATA_DIR / "igbts.ndjson", fix_igbt)
    process_file(DATA_DIR / "magnetics.ndjson", fix_magnetic)
    print("Done.")


if __name__ == "__main__":
    main()
