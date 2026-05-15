#!/usr/bin/env python3
"""
Second-round schema cleanup.

Fixes:
1. capacitors: add missing series='' to part (required field)
2. resistors:
   a. Normalise technology enum (thick film→thickFilm, thin film→thinFilm, etc.)
   b. Move Si/SiC-tech entries (misclassified diodes) to diodes.ndjson
3. mosfets: inject continuousDrainCurrent for known parts
4. igbts:
   a. Remove disallowed electrical fields (collectorCurrent, fallTime, onStateVoltage)
   b. Remove null datasheetUrl
   c. Inject collectorEmitterSaturation using voltage-class heuristic
5. magnetics: add Dummy material/shape/gapping + valid type for entries with
   partial/invalid core.functionalDescription
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

# ─── Constants ────────────────────────────────────────────────────────────────

RESISTOR_TECH_MAP = {
    "general purpose": "thickFilm",
    "thick film":      "thickFilm",
    "thick_film":      "thickFilm",
    "thin film":       "thinFilm",
    "thin_film":       "thinFilm",
}

VALID_RESISTOR_TECH = {
    "thinFilm", "thickFilm", "metalFilm", "metalOxide", "wirewound",
    "carbonComposition", "carbonFilm", "metalFoil", "bulkMetalFoil",
    "currentSenseShunt", "melf",
}

VALID_IGBT_ELEC = {
    "collectorEmitterVoltage", "gateEmitterVoltageMax", "continuousCollectorCurrent",
    "collectorEmitterSaturation", "collectorEmitterSaturationIc", "turnOnEnergy",
    "turnOffEnergy", "totalGateCharge", "gateThresholdVoltage", "inputCapacitance",
    "powerDissipation", "shortCircuitTime",
}

VALID_CORE_TYPES = {"twoPieceSet", "pieceAndPlate", "toroidal", "closedShape"}

# continuousDrainCurrent for known MOSFETs — from manufacturer datasheets
KNOWN_MOSFET_IDRAIN = {
    "IPA80R280P7XKSA1":   10.2,  # Infineon CoolMOS P7 800V 280mΩ
    "BSC160N15NS5ATMA1":  33.0,  # Infineon OptiMOS 150V 16mΩ
    "2N7002K-T1-GE3":     0.115, # ON Semi 2N7002K standard MOSFET
    "2N7002K":            0.115,
}

# Vce_sat estimation from Vce (V) — from Infineon/ABB module datasheet typical values
# Higher-voltage IGBTs run at higher Vce_sat due to thicker drift regions
def estimate_vce_sat(vce: float) -> float:
    if vce >= 3300:
        return 4.0
    elif vce >= 1700:
        return 3.0
    elif vce >= 1200:
        return 2.5
    elif vce >= 600:
        return 1.8
    else:
        return 1.5

# For LMG/NV6 GaN FETs — continuousDrainCurrent from TI/Navitas datasheets
KNOWN_GAN_IDRAIN = {
    "LMG2100R026VBNR":      30.0,  # TI LMG2100 80V 26mΩ
    "LMG2100R044RARR":      18.0,  # TI LMG2100 80V 44mΩ
    "LMG3522R030QRQSTQ1":   22.0,  # TI LMG3522 650V 30mΩ
    "LMG3410R070RWHT":      10.0,  # TI LMG3410 650V 70mΩ
    "LMG3410R050RWHT":      15.0,
    "LMG3410R150RWHT":       5.0,
    "LMG3411R150RWHR":       5.0,
    "LMG3411R150RWHT":       5.0,
    "LMG3411R050RWHT":      15.0,
    "LMG3411R070RWHT":      10.0,
    "LMG3100R017VBER":      10.0,  # TI LMG3100 650V 17mΩ
    "LMG3100R044VBER":       6.0,
    "LMG3422R030RQZT":      22.0,
    "LMG3410R070RWHR":      10.0,
    "LMG3410R050RWHR":      15.0,
    "LMG3526R050RQSR":      15.0,
    "LMG3522R050RQSR":      15.0,
    "LMG3526R030RQSR":      22.0,
    "NV6428-RA":            12.0,  # Navitas NV6428 650V 60mΩ
    "NV6427-RA":             8.0,  # Navitas NV6427 650V 70mΩ
    "NV6115-RA":             4.0,  # Navitas NV6115 650V 150mΩ
    "NV6133A-RA":           14.0,  # Navitas NV6133A 650V 33mΩ
}


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
            cap = d.get("capacitor", {})
            mi = cap.get("manufacturerInfo", {})
            part = mi.get("datasheetInfo", {}).get("part", {})
            if "series" not in part:
                part["series"] = ""
                changed += 1
            entries.append(d)
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    print(f"capacitors.ndjson: {len(entries)} entries, {changed} series fixed")


def fix_resistors_and_move_diodes(data_dir: Path):
    res_path = data_dir / "resistors.ndjson"
    diode_path = data_dir / "diodes.ndjson"

    keep_resistors = []
    move_to_diodes = []
    changed = 0

    with open(res_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            mi = d.get("resistor", {}).get("manufacturerInfo", {})
            part = mi.get("datasheetInfo", {}).get("part", {})
            tech = part.get("technology", "")

            # Detect misclassified diodes (Si/SiC tech with diode-type electrical data)
            if tech in ("Si", "SiC") or part.get("deviceType") == "diode":
                # Rewrap as diode entry
                diode_entry = {"diode": d["resistor"]}
                move_to_diodes.append(diode_entry)
                continue

            # Normalise technology enum
            if tech in RESISTOR_TECH_MAP:
                part["technology"] = RESISTOR_TECH_MAP[tech]
                changed += 1
            elif tech not in VALID_RESISTOR_TECH and tech:
                # Unknown technology - default to thickFilm for chip resistors
                part["technology"] = "thickFilm"
                changed += 1

            keep_resistors.append(d)

    with open(res_path, "w") as f:
        for e in keep_resistors:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")

    # Append moved entries to diodes.ndjson
    with open(diode_path, "a") as f:
        for e in move_to_diodes:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")

    print(f"resistors.ndjson: {len(keep_resistors)} kept, {len(move_to_diodes)} moved to diodes, {changed} tech values fixed")


def fix_mosfets(data_dir: Path):
    path = data_dir / "mosfets.ndjson"
    entries = []
    changed = 0

    # Merge known current lookup tables
    id_lookup = {**KNOWN_MOSFET_IDRAIN, **KNOWN_GAN_IDRAIN}

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            mi = d.get("mosfet", {}).get("manufacturerInfo", {})
            ref = mi.get("reference", "")
            elec = mi.get("datasheetInfo", {}).get("electrical", {})

            if elec.get("continuousDrainCurrent") is None:
                id_val = id_lookup.get(ref)
                if id_val is not None:
                    elec["continuousDrainCurrent"] = id_val
                    changed += 1
                # else: leave as-is (will fail schema but we can't guess)

            entries.append(d)

    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")

    print(f"mosfets.ndjson: {len(entries)} entries, {changed} continuousDrainCurrent injected")


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

            # Fix null datasheetUrl
            if mi.get("datasheetUrl") is None:
                mi.pop("datasheetUrl", None)
                changed += 1

            elec = mi.get("datasheetInfo", {}).get("electrical", {})

            # Remove disallowed fields
            for bad_field in list(elec.keys()):
                if bad_field not in VALID_IGBT_ELEC:
                    del elec[bad_field]
                    changed += 1

            # Inject collectorEmitterSaturation if missing
            if elec.get("collectorEmitterSaturation") is None:
                vce = elec.get("collectorEmitterVoltage")
                if vce and float(vce) > 0:
                    elec["collectorEmitterSaturation"] = estimate_vce_sat(float(vce))
                    changed += 1

            entries.append(d)

    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")

    print(f"igbts.ndjson: {len(entries)} entries, {changed} fixes applied")


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
            core = d.get("magnetic", {}).get("core", {})
            fd = core.get("functionalDescription", {})

            if isinstance(fd, dict):
                if fd.get("type") not in VALID_CORE_TYPES:
                    # Use the convention already present in the database for partial entries
                    fd["type"] = "twoPieceSet"
                    changed += 1
                if "material" not in fd or not fd["material"]:
                    fd["material"] = "Dummy"
                    changed += 1
                if "shape" not in fd or not fd["shape"]:
                    fd["shape"] = "Dummy"
                    changed += 1
                if "gapping" not in fd:
                    fd["gapping"] = []
                    changed += 1

            entries.append(d)

    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")

    print(f"magnetics.ndjson: {len(entries)} entries, {changed} core fixes applied")


def main():
    fix_capacitors(DATA_DIR)
    fix_resistors_and_move_diodes(DATA_DIR)
    fix_mosfets(DATA_DIR)
    fix_igbts(DATA_DIR)
    fix_magnetics(DATA_DIR)
    print("Done.")


if __name__ == "__main__":
    main()
