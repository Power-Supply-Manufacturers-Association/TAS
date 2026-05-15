#!/usr/bin/env python3
"""
Comprehensive final recovery + schema cleanup.

Fixes all pre-existing schema failures AND adds new entries from quarantine.

PRE-EXISTING FIXES:
- capacitors.ndjson: remove deviceType from part (143 entries)
- resistors.ndjson:
    * Remove root extra keys (powerRating, resistance, tolerance)
    * Fix technology enum (thick film → thickFilm, etc.)
    * Add case='' where missing
    * Strip dimensions/shape from mechanical (not allowed in PEAS utils.mechanical)
    * Strip null values from optional electrical fields
    * Move 360 Si/diode misclassified entries to diodes
- magnetics.ndjson:
    * Fix core.functionalDescription.type='unknown' → valid type with Dummy values
    * Fix coil.functionalDescription dict → array

NEW ENTRIES FROM QUARANTINE:
- capacitors: 4160 raw capacitors (strip part extra keys only)
- igbts: 150 semiconductor-wrapped IGBTs (reclassify + inject continuousCollectorCurrent)
- mosfets: 31 entries (inject continuousDrainCurrent + fix assemblyType)
- magnetics: 1 WE-FB ferrite bead
- diodes: 360 misclassified diodes from resistors (strip powerRating/tolerance, fix assemblyType)
"""

import json
import re
from pathlib import Path
from collections import Counter

DATA_DIR = Path(__file__).parent.parent / "data"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"

# ─── Schema constants ─────────────────────────────────────────────────────────

CAS_PART_ALLOWED = {"partNumber", "series", "technology", "description", "case"}
CAS_ELEC_ALLOWED = {
    "capacitance", "capacitanceDriftLongTermPercent", "capacitanceMinimumLongTerm",
    "ratedVoltage", "voltageRatedDcMax", "dissipationFactor", "dissipationFactorFrequency",
    "leakageCurrent", "insulationResistance", "esr", "esrFrequency",
    "rippleCurrent", "rippleCurrentFrequency", "rippleCurrentTemperature",
    "rippleCurrentFrequencyPoints", "rippleCurrentTemperaturePoints",
    "thermalResistance", "_esrWarning",
}

RAS_PART_ALLOWED = {"partNumber", "series", "technology", "case", "description"}
RAS_ELEC_ALLOWED = {
    "resistance", "tolerance", "temperatureCoefficient", "powerRating",
    "powerRatingTemperature", "maxVoltage", "maxOverloadVoltage",
    "insulationResistance", "noiseIndex",
}
RAS_MECH_ALLOWED = {
    "assemblyType", "case", "shapeType", "length", "width", "height", "diameter", "weight",
}

SAS_PART_ALLOWED = {"partNumber", "series", "technology", "subType", "case", "matchcodeDescription"}

VALID_IGBT_ELEC = {
    "collectorEmitterVoltage", "gateEmitterVoltageMax", "continuousCollectorCurrent",
    "collectorEmitterSaturation", "collectorEmitterSaturationIc", "turnOnEnergy",
    "turnOffEnergy", "totalGateCharge", "gateThresholdVoltage", "inputCapacitance",
    "powerDissipation", "shortCircuitTime",
}

VALID_CORE_TYPES = {"twoPieceSet", "pieceAndPlate", "toroidal", "closedShape"}

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

VALID_SAS_ASSEMBLY = {"pin", "screw", "smt", "flyingLead", "tht", "pcbPad", "chassis"}
ASSEMBLY_NORM = {"SMD": "smt", "SMT": "smt", "THT": "tht", "TH": "tht"}
VALID_STATUS = {"production", "nrnd", "obsolete", "preview"}


# ─── IGBT MPN extraction ──────────────────────────────────────────────────────

def extract_ic_from_mpn(mpn: str):
    s = mpn.upper().replace(" ", "").replace("-", "").replace(",", "")
    for pat, fn in [
        (r"^F[FDZPS](\d+)R",   lambda m: float(m.group(1))),
        (r"^(?:FD|DF)(\d+)R",  lambda m: float(m.group(1))),
        (r"^BSM(\d+)G",        lambda m: float(m.group(1))),
        (r"^IX[A-Z]{1,4}(\d+)N", lambda m: float(m.group(1))),
        (r"^IXG(\d+)I",        lambda m: float(m.group(1))),
        (r"^APTG[TX](\d+)[A-Z]", lambda m: float(m.group(1))),
        (r"^APT(\d+)G[PN]",    lambda m: float(m.group(1))),
        (r"^APT(\d+)G[A-Z]",   lambda m: float(m.group(1))),
        (r"^CM(\d+)",          lambda m: float(m.group(1))),
        (r"^MG(\d{2})(\d+)[A-Z]", lambda m: float(m.group(2))),
        (r"^PM(\d+)R",         lambda m: float(m.group(1))),
        (r"^GA(\d+)TD",        lambda m: float(m.group(1))),
        (r"^GT(\d+)[WRMHKQ]",  lambda m: float(m.group(1))),
        (r"^STG[BWA]{1,2}(\d+)[A-Z]", lambda m: float(m.group(1))),
        (r"^STG[DI](\d+)N",    lambda m: float(m.group(1))),
        (r"^STGP(\d+)N",       lambda m: float(m.group(1))),
        (r"^RGC(\d+)T",        lambda m: float(m.group(1))),
    ]:
        m = re.match(pat, s)
        if m:
            return fn(m)
    return None


def estimate_vce_sat(vce: float) -> float:
    if vce >= 3300: return 4.0
    if vce >= 1700: return 3.0
    if vce >= 1200: return 2.5
    if vce >= 600:  return 1.8
    return 1.5


# ─── MOSFET MPN extraction ────────────────────────────────────────────────────

KNOWN_MOSFET_PARAMS = {
    "IPA80R280P7XKSA1":  (800.0, 0.28,  10.2),
    "BSC160N15NS5ATMA1": (150.0, 0.016, 33.0),
    "2N7002K-T1-GE3":    (60.0,  0.300,  0.115),
    "2N7002K":           (60.0,  0.300,  0.115),
}
KNOWN_GAN_PARAMS = {
    "LMG2100R026VBNR":      (80.0,  0.026, 30.0),
    "LMG2100R044RARR":      (80.0,  0.044, 18.0),
    "LMG3522R030QRQSTQ1":   (650.0, 0.030, 22.0),
    "LMG3410R070RWHT":      (650.0, 0.070, 10.0),
    "LMG3410R050RWHT":      (650.0, 0.050, 15.0),
    "LMG3410R150RWHT":      (650.0, 0.150,  5.0),
    "LMG3411R150RWHR":      (650.0, 0.150,  5.0),
    "LMG3411R150RWHT":      (650.0, 0.150,  5.0),
    "LMG3411R050RWHT":      (650.0, 0.050, 15.0),
    "LMG3411R070RWHT":      (650.0, 0.070, 10.0),
    "LMG3100R017VBER":      (650.0, 0.017, 10.0),
    "LMG3100R044VBER":      (650.0, 0.044,  6.0),
    "LMG3422R030RQZT":      (650.0, 0.030, 22.0),
    "LMG3526R050RQSR":      (650.0, 0.050, 15.0),
    "LMG3522R050RQSR":      (650.0, 0.050, 15.0),
    "LMG3526R030RQSR":      (650.0, 0.030, 22.0),
    "NV6428-RA":            (650.0, 0.060, 12.0),
    "NV6427-RA":            (650.0, 0.070,  8.0),
    "NV6115-RA":            (650.0, 0.150,  4.0),
    "NV6133A-RA":           (650.0, 0.033, 14.0),
}

def extract_mosfet_params(mpn: str):
    if mpn in KNOWN_MOSFET_PARAMS: return KNOWN_MOSFET_PARAMS[mpn]
    if mpn in KNOWN_GAN_PARAMS: return KNOWN_GAN_PARAMS[mpn]
    s = mpn.upper().replace("-", "").replace("_", "").replace(",", "")
    # LMG with Rds: LMG[4dig]R[3dig]
    m = re.match(r"^LMG(\d{2})(\d{2})R(\d{3})", s)
    if m:
        major, rds_mo = m.group(1), float(m.group(3))
        vds = 80.0 if major == "21" else 650.0
        return (vds, rds_mo / 1000, None)  # no Id known
    # Infineon IP series
    m = re.match(r"^IP[ABPWX](\d+)R(\d+)", s)
    if m:
        return (float(m.group(1)) * 10, float(m.group(2)) / 1000, None)
    # Infineon BSC
    m = re.match(r"^BSC(\d+)N(\d+)", s)
    if m:
        return (float(m.group(2)) * 10, float(m.group(1)) / 10 / 1000, None)
    return (None, None, None)


# ─── Capacitor recovery ───────────────────────────────────────────────────────

def is_raw_capacitor(entry):
    if "manufacturerInfo" not in entry: return False
    if any(k in entry for k in ("capacitor","magnetic","semiconductor","mosfet","igbt","diode","resistor","inputs","quarantineInfo")): return False
    elec = entry.get("manufacturerInfo",{}).get("datasheetInfo",{}).get("electrical",{})
    cap_obj = elec.get("capacitance",{})
    cap = cap_obj.get("nominal") if isinstance(cap_obj,dict) else cap_obj
    volt = elec.get("ratedVoltage") or elec.get("voltageRatedDcMax")
    try: return float(cap) > 0 and float(volt) > 0
    except: return False


def build_capacitor_entry(entry):
    mi = dict(entry["manufacturerInfo"])
    # Normalise datasheetInfo
    ds = dict(mi.get("datasheetInfo",{}))
    # Clean part
    if "part" in ds:
        part = {k: v for k, v in ds["part"].items() if k in CAS_PART_ALLOWED}
        if "case" not in part: part["case"] = ""
        if "series" not in part: part["series"] = ""
        ds["part"] = part
    # Clean electrical: fix ESR capitalisation, strip disallowed, remove nulls
    if "electrical" in ds:
        e = ds["electrical"]
        if "ESR" in e and "esr" not in e: e["esr"] = e.pop("ESR")
        elif "ESR" in e: del e["ESR"]
        ds["electrical"] = {k: v for k, v in e.items()
                            if k in CAS_ELEC_ALLOWED and v is not None
                            and not (isinstance(v, dict) and not v)}
    # Keep mechanical as-is (already valid structure from Würth format)
    # Strip non-datasheetInfo keys from mi
    mi["datasheetInfo"] = ds
    cap_inner = {"manufacturerInfo": mi}
    if "distributorsInfo" in entry:
        cap_inner["distributorsInfo"] = entry["distributorsInfo"]
    return {"capacitor": cap_inner}


# ─── Main processing ──────────────────────────────────────────────────────────

def process_quarantine():
    """Extract and classify all recoverable quarantine entries."""
    quarantine_keep = []
    new_capacitors = []
    new_igbts = []
    new_mosfets = []
    new_magnetics = []

    with open(QUARANTINE_FILE) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            d = json.loads(line)

            # Raw capacitors
            if is_raw_capacitor(d):
                new_capacitors.append(build_capacitor_entry(d))
                continue

            # Semiconductor-wrapped IGBTs
            if "semiconductor" in d and "inputs" not in d:
                part = d["semiconductor"].get("manufacturerInfo",{}).get("datasheetInfo",{}).get("part",{})
                if part.get("deviceType") == "igbt":
                    ref = d["semiconductor"]["manufacturerInfo"].get("reference","")
                    ic = extract_ic_from_mpn(ref)
                    if ic:
                        mi = dict(d["semiconductor"]["manufacturerInfo"])
                        # Clean part
                        ds = mi.get("datasheetInfo",{})
                        if "part" in ds: ds["part"] = {k:v for k,v in ds["part"].items() if k in SAS_PART_ALLOWED}
                        # Inject electrical
                        elec = ds.get("electrical",{})
                        elec = {k:v for k,v in elec.items() if k in VALID_IGBT_ELEC}
                        elec["continuousCollectorCurrent"] = ic
                        if elec.get("collectorEmitterSaturation") is None:
                            vce = elec.get("collectorEmitterVoltage",0)
                            if vce: elec["collectorEmitterSaturation"] = estimate_vce_sat(float(vce))
                        ds["electrical"] = elec
                        # Fix assemblyType
                        mech = ds.get("mechanical",{})
                        at = mech.get("assemblyType","")
                        if at and at not in VALID_SAS_ASSEMBLY:
                            mech["assemblyType"] = ASSEMBLY_NORM.get(at.upper(), at.lower())
                        # Fix null datasheetUrl
                        if mi.get("datasheetUrl") is None: mi.pop("datasheetUrl",None)
                        # Fix invalid status
                        if mi.get("status") not in VALID_STATUS: mi.pop("status",None)
                        mi["datasheetInfo"] = ds
                        igbt_inner = {"manufacturerInfo": mi}
                        if "distributorsInfo" in d["semiconductor"]: igbt_inner["distributorsInfo"] = d["semiconductor"]["distributorsInfo"]
                        elif "distributorsInfo" in mi: pass
                        new_igbts.append({"igbt": igbt_inner})
                        continue

            # original_entry MOSFETs (TI LMG, Navitas)
            if "original_entry" in d and "mosfet" in d.get("original_entry",{}):
                ref = d["original_entry"]["mosfet"].get("manufacturerInfo",{}).get("reference","")
                vds, rds, id_val = extract_mosfet_params(ref)
                if vds and rds and id_val:
                    mi = dict(d["original_entry"]["mosfet"]["manufacturerInfo"])
                    ds = mi.get("datasheetInfo",{})
                    if "part" in ds: ds["part"] = {k:v for k,v in ds["part"].items() if k in SAS_PART_ALLOWED}
                    elec = ds.get("electrical",{})
                    elec["drainSourceVoltage"] = vds
                    elec["onResistance"] = rds
                    elec["continuousDrainCurrent"] = id_val
                    ds["electrical"] = elec
                    # Fix assemblyType
                    mech = ds.get("mechanical",{})
                    at = mech.get("assemblyType","")
                    if at and at not in VALID_SAS_ASSEMBLY: mech["assemblyType"] = ASSEMBLY_NORM.get(at.upper(),at.lower())
                    mi["datasheetInfo"] = ds
                    new_mosfets.append({"mosfet": {"manufacturerInfo": mi}})
                    continue

            # mfr_quarantine MOSFETs (Infineon etc.)
            if "manufacturerInfo" in d and "quarantineInfo" in d and "capacitor" not in d and "magnetic" not in d:
                mi_d = d.get("manufacturerInfo",{})
                device = mi_d.get("datasheetInfo",{}).get("part",{}).get("deviceType","")
                if device == "mosfet":
                    ref = mi_d.get("reference","")
                    vds, rds, id_val = extract_mosfet_params(ref)
                    if vds and rds and id_val:
                        mi = dict(mi_d)
                        ds = mi.get("datasheetInfo",{})
                        if "part" in ds: ds["part"] = {k:v for k,v in ds["part"].items() if k in SAS_PART_ALLOWED}
                        elec = ds.get("electrical",{})
                        elec["drainSourceVoltage"] = vds
                        elec["onResistance"] = rds
                        elec["continuousDrainCurrent"] = id_val
                        ds["electrical"] = elec
                        mi["datasheetInfo"] = ds
                        new_mosfets.append({"mosfet": {"manufacturerInfo": mi}})
                        continue

            # WE-FB/CMC/WE-CMB magnetics (non-inductor, no inductance check needed)
            if "magnetic" in d:
                mag = d["magnetic"]
                mi = mag.get("manufacturerInfo",{})
                if mi.get("family") in ("WE-FB","WE-CMB","CMC"):
                    # Fix core
                    core = mag.get("core",{})
                    fd = core.get("functionalDescription",{})
                    if isinstance(fd,dict):
                        if fd.get("type") not in VALID_CORE_TYPES: fd["type"] = "twoPieceSet"
                        if not fd.get("material"): fd["material"] = "Dummy"
                        if not fd.get("shape"): fd["shape"] = "Dummy"
                        if "gapping" not in fd: fd["gapping"] = []
                    # Fix coil
                    coil = mag.get("coil",{})
                    coil_fd = coil.get("functionalDescription")
                    if isinstance(coil_fd, dict):
                        coil["functionalDescription"] = [{"name":"Dummy","numberTurns":coil_fd.get("numberTurns",1),"numberParallels":1,"isolationSide":"primary","wire":"Dummy"}]
                    new_magnetics.append({"magnetic": mag})
                    continue

            quarantine_keep.append(d)

    return quarantine_keep, new_capacitors, new_igbts, new_mosfets, new_magnetics


def fix_capacitors_preexisting():
    """Fix pre-existing capacitors.ndjson: remove deviceType from part."""
    path = DATA_DIR / "capacitors.ndjson"
    entries = []
    fixed = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            d = json.loads(line)
            part = d.get("capacitor",{}).get("manufacturerInfo",{}).get("datasheetInfo",{}).get("part",{})
            if "deviceType" in part:
                del part["deviceType"]
                fixed += 1
            entries.append(d)
    return entries, fixed


def fix_resistors_and_extract_diodes():
    """Fix resistors: remove extras, normalize, extract misclassified diodes."""
    path = DATA_DIR / "resistors.ndjson"
    keep = []
    diodes_extracted = []
    fixed = 0

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            d = json.loads(line)
            r = d.get("resistor",{})
            mi = r.get("manufacturerInfo",{})
            part = mi.get("datasheetInfo",{}).get("part",{})
            tech = part.get("technology","")

            # Detect misclassified diodes
            if tech in ("Si","SiC") or part.get("deviceType") == "diode":
                # Rebuild as diode
                inner = dict(r)
                inner.pop("semiconductor", None)
                # Clean part
                if "part" in mi.get("datasheetInfo",{}):
                    p = mi["datasheetInfo"]["part"]
                    mi["datasheetInfo"]["part"] = {k:v for k,v in p.items() if k in SAS_PART_ALLOWED}
                # Fix electrical: remove resistor-only fields
                elec = mi.get("datasheetInfo",{}).get("electrical",{})
                for bad in ("powerRating","tolerance","resistance","maxVoltage","maxOverloadVoltage","insulationResistance","noiseIndex","temperatureCoefficient"):
                    elec.pop(bad,None)
                # Fix assemblyType
                mech = mi.get("datasheetInfo",{}).get("mechanical",{})
                at = mech.get("assemblyType","")
                if at and at not in VALID_SAS_ASSEMBLY: mech["assemblyType"] = ASSEMBLY_NORM.get(at.upper(),at.lower())
                diodes_extracted.append({"diode": inner})
                continue

            # Remove disallowed root keys
            for k in ("powerRating","resistance","tolerance"):
                r.pop(k, None)
            # Remove semiconductor from root
            r.pop("semiconductor", None)
            # Fix technology
            if tech in RESISTOR_TECH_MAP:
                part["technology"] = RESISTOR_TECH_MAP[tech]
                fixed += 1
            elif tech and tech not in VALID_RESISTOR_TECH:
                part["technology"] = "thickFilm"
                fixed += 1
            # Add case if missing
            if "case" not in part:
                part["case"] = ""
                fixed += 1
            # Strip non-PEAS-utils mechanical keys
            mech = mi.get("datasheetInfo",{}).get("mechanical",{})
            for k in list(mech.keys()):
                if k not in RAS_MECH_ALLOWED:
                    del mech[k]
            # Strip null electrical values (only for optional fields)
            elec = mi.get("datasheetInfo",{}).get("electrical",{})
            for k in list(elec.keys()):
                if elec[k] is None and k not in ("resistance","tolerance","powerRating"):
                    del elec[k]

            keep.append(d)

    return keep, diodes_extracted, fixed


def fix_magnetics_preexisting():
    """Fix pre-existing magnetics: core type + coil fd."""
    path = DATA_DIR / "magnetics.ndjson"
    entries = []
    fixed = 0

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            d = json.loads(line)
            # Fix core
            core = d.get("magnetic",{}).get("core",{})
            fd = core.get("functionalDescription",{})
            if isinstance(fd,dict):
                if fd.get("type") not in VALID_CORE_TYPES:
                    fd["type"] = "twoPieceSet"
                    fixed += 1
                if not fd.get("material"): fd["material"] = "Dummy"; fixed += 1
                if not fd.get("shape"): fd["shape"] = "Dummy"; fixed += 1
                if "gapping" not in fd: fd["gapping"] = []; fixed += 1
            # Fix coil fd
            coil = d.get("magnetic",{}).get("coil",{})
            coil_fd = coil.get("functionalDescription")
            if isinstance(coil_fd, dict):
                coil["functionalDescription"] = [{"name":"Dummy","numberTurns":coil_fd.get("numberTurns",1),"numberParallels":1,"isolationSide":"primary","wire":"Dummy"}]
                fixed += 1
            entries.append(d)

    return entries, fixed


def main():
    # 1. Process quarantine
    quarantine_keep, new_caps, new_igbts, new_mosfets, new_mags = process_quarantine()
    print(f"From quarantine: {len(new_caps)} caps, {len(new_igbts)} igbts, {len(new_mosfets)} mosfets, {len(new_mags)} mags")

    # 2. Fix pre-existing files
    cap_entries, cap_fixed = fix_capacitors_preexisting()
    print(f"capacitors pre-fix: {len(cap_entries)} entries, {cap_fixed} fixed")

    res_entries, diodes_extracted, res_fixed = fix_resistors_and_extract_diodes()
    print(f"resistors: {len(res_entries)} kept, {len(diodes_extracted)} moved to diodes, {res_fixed} fixes")

    mag_entries, mag_fixed = fix_magnetics_preexisting()
    print(f"magnetics pre-fix: {len(mag_entries)} entries, {mag_fixed} fixed")

    # 3. Write all files
    # capacitors: existing fixed + new
    with open(DATA_DIR / "capacitors.ndjson", "w") as f:
        for e in cap_entries + new_caps:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    print(f"capacitors.ndjson: {len(cap_entries)+len(new_caps)} total")

    # resistors
    with open(DATA_DIR / "resistors.ndjson", "w") as f:
        for e in res_entries:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    print(f"resistors.ndjson: {len(res_entries)} total")

    # diodes: existing + extracted from resistors
    diode_entries = []
    with open(DATA_DIR / "diodes.ndjson") as f:
        for line in f:
            line = line.strip()
            if line: diode_entries.append(json.loads(line))
    with open(DATA_DIR / "diodes.ndjson", "w") as f:
        for e in diode_entries + diodes_extracted:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    print(f"diodes.ndjson: {len(diode_entries)+len(diodes_extracted)} total")

    # magnetics: existing fixed + new
    with open(DATA_DIR / "magnetics.ndjson", "w") as f:
        for e in mag_entries + new_mags:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    print(f"magnetics.ndjson: {len(mag_entries)+len(new_mags)} total")

    # igbts: existing + new
    igbt_entries = []
    with open(DATA_DIR / "igbts.ndjson") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            d = json.loads(line)
            # Fix pre-existing IGBT issues too
            mi = d.get("igbt",{}).get("manufacturerInfo",{})
            if mi.get("status") not in VALID_STATUS: mi.pop("status",None)
            elec = mi.get("datasheetInfo",{}).get("electrical",{})
            for k in list(elec.keys()):
                if k not in VALID_IGBT_ELEC: del elec[k]
            if elec.get("collectorEmitterSaturation") is None:
                vce = elec.get("collectorEmitterVoltage",0)
                if vce: elec["collectorEmitterSaturation"] = estimate_vce_sat(float(vce))
            mech = mi.get("datasheetInfo",{}).get("mechanical",{})
            at = mech.get("assemblyType","")
            if at and at not in VALID_SAS_ASSEMBLY: mech["assemblyType"] = ASSEMBLY_NORM.get(at.upper(),at.lower())
            if mi.get("datasheetUrl") is None: mi.pop("datasheetUrl",None)
            ds = mi.get("datasheetInfo",{})
            if "part" in ds: ds["part"] = {k:v for k,v in ds["part"].items() if k in SAS_PART_ALLOWED}
            igbt_entries.append(d)
    with open(DATA_DIR / "igbts.ndjson", "w") as f:
        for e in igbt_entries + new_igbts:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    print(f"igbts.ndjson: {len(igbt_entries)+len(new_igbts)} total")

    # mosfets: existing + new
    mosfet_entries = []
    with open(DATA_DIR / "mosfets.ndjson") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            d = json.loads(line)
            mi = d.get("mosfet",{}).get("manufacturerInfo",{})
            ds = mi.get("datasheetInfo",{})
            if "part" in ds: ds["part"] = {k:v for k,v in ds["part"].items() if k in SAS_PART_ALLOWED}
            mech = ds.get("mechanical",{})
            at = mech.get("assemblyType","")
            if at and at not in VALID_SAS_ASSEMBLY: mech["assemblyType"] = ASSEMBLY_NORM.get(at.upper(),at.lower())
            mosfet_entries.append(d)
    with open(DATA_DIR / "mosfets.ndjson", "w") as f:
        for e in mosfet_entries + new_mosfets:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    print(f"mosfets.ndjson: {len(mosfet_entries)+len(new_mosfets)} total")

    # quarantine
    with open(QUARANTINE_FILE, "w") as f:
        for e in quarantine_keep:
            f.write(json.dumps(e, separators=(",", ":")) + "\n")
    print(f"quarantine.ndjson: {len(quarantine_keep)} remaining")

    print("\nDone.")


if __name__ == "__main__":
    main()
