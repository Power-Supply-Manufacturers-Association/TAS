#!/usr/bin/env python3
"""
Recover raw-format capacitor entries from quarantine.

These entries have the structure:
  {
    "manufacturerInfo": {...},
    "distributorsInfo": [...],
    "quarantineReason": "...",
    "quarantineSource": "..."
  }

They need to be wrapped as:
  {
    "capacitor": {
      "manufacturerInfo": {...},
      "distributorsInfo": [...]
    }
  }

Only entries with both capacitance and ratedVoltage > 0 are recovered.
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"
CAPACITORS_FILE = DATA_DIR / "capacitors.ndjson"

QUARANTINE_ONLY_KEYS = {"quarantineReason", "quarantineSource", "quarantineInfo"}


def positive(val):
    try:
        return float(val) > 0
    except (TypeError, ValueError):
        return False


def get_cap_and_voltage(entry):
    elec = (
        entry.get("manufacturerInfo", {})
        .get("datasheetInfo", {})
        .get("electrical", {})
    )
    cap_obj = elec.get("capacitance", {})
    if isinstance(cap_obj, dict):
        cap = cap_obj.get("nominal") or cap_obj.get("minimum")
    else:
        cap = cap_obj
    voltage = elec.get("ratedVoltage") or elec.get("voltageRatedDcMax")
    return cap, voltage


def is_raw_capacitor(entry):
    """Entry has manufacturerInfo at root with capacitance, no capacitor/magnetic/semiconductor wrapper."""
    if "manufacturerInfo" not in entry:
        return False
    if any(k in entry for k in ("capacitor", "magnetic", "semiconductor", "mosfet", "igbt", "diode", "resistor", "inputs", "quarantineInfo")):
        return False
    cap, voltage = get_cap_and_voltage(entry)
    return positive(cap) and positive(voltage)


def build_capacitor_entry(entry):
    """Transform raw entry into proper capacitor schema."""
    cap_inner = {"manufacturerInfo": entry["manufacturerInfo"]}

    # Move distributorsInfo inside if present at root
    if "distributorsInfo" in entry:
        cap_inner["distributorsInfo"] = entry["distributorsInfo"]
    elif "distributorsInfo" in entry.get("manufacturerInfo", {}):
        # Already inside — leave it
        pass

    return {"capacitor": cap_inner}


def main():
    quarantine_keep = []
    recovered = []

    with open(QUARANTINE_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if is_raw_capacitor(d):
                recovered.append(build_capacitor_entry(d))
            else:
                quarantine_keep.append(d)

    print(f"Raw capacitors recovered: {len(recovered)}")
    print(f"Quarantine entries remaining: {len(quarantine_keep)}")

    # Append to capacitors.ndjson
    with open(CAPACITORS_FILE, "a") as f:
        for entry in recovered:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    # Rewrite quarantine
    with open(QUARANTINE_FILE, "w") as f:
        for entry in quarantine_keep:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    print("Done.")


if __name__ == "__main__":
    main()
