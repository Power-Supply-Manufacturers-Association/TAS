#!/usr/bin/env python3
"""
Recover semiconductor-wrapped IGBT entries from quarantine.

These entries have the old structure:
  {"semiconductor": {"manufacturerInfo": {...}}, "manufacturerInfo": {...}, "quarantineInfo": {...}}

They need to be transformed to:
  {"igbt": {"manufacturerInfo": {...}}}

The validator requires:
  - collectorEmitterVoltage > 0   (already present in all entries)
  - continuousCollectorCurrent > 0  (extracted from MPN using manufacturer conventions)

Current extraction sources:
  - Infineon: FF/FZ/FP/FS/FD/DF/BSM naming conventions (current encoded directly)
  - IXYS: IXGT/IXYX/IXBH/IXBX/IXBY/IXG etc. (current before 'N' for voltage)
  - Microsemi/APT: APTGX/APTGT/APT standalone conventions
  - Mitsubishi: CM/MG module families
  - Powerex: PM family
  - Fuji: GA module family
  - Toshiba: GT family
  - ST: STGB/STGW/STGA/STGD/STGI/STGP/STGI families

Entries where current cannot be reliably extracted stay in quarantine.
"""

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"
IGBTS_FILE = DATA_DIR / "igbts.ndjson"


def extract_ic_from_mpn(mpn: str) -> float | None:
    """
    Extract continuousCollectorCurrent (Ic) in Amps from IGBT MPN.
    Returns float or None if pattern not recognised.
    All patterns are based on published manufacturer naming conventions.
    """
    s = mpn.upper().replace(" ", "").replace("-", "").replace(",", "")

    # Infineon single/dual modules: F[F/Z/P/S][current]R  (FF, FZ, FP, FS series)
    # e.g. FF1200R17IP5 = 1200A, FZ1800R17 = 1800A
    m = re.match(r"^F[FDZPS](\d+)R", s)
    if m:
        return float(m.group(1))

    # Infineon FD/DF (dual) modules: FD[current]R or DF[current]R
    m = re.match(r"^(?:FD|DF)(\d+)R", s)
    if m:
        return float(m.group(1))

    # Infineon BSM module: BSM[current]G[A/B/C]
    m = re.match(r"^BSM(\d+)G", s)
    if m:
        return float(m.group(1))

    # IXYS IGBT discretes/modules with N for voltage: IX[chars][current]N
    # e.g. IXGT16N170A=16A, IXYX50N170=50A, IXBH42N170=42A, IXYH25N250=25A
    m = re.match(r"^IX[A-Z]{1,4}(\d+)N", s)
    if m:
        return float(m.group(1))

    # IXYS IXG series: IXG[current]I[voltage]  e.g. IXG50I4500KN=50A
    m = re.match(r"^IXG(\d+)I", s)
    if m:
        return float(m.group(1))

    # Microsemi/APT modules: APTG[TX][current][A/H/SK/U/DU/DA]
    # e.g. APTGX300A170=300A, APTGT150H170=150A
    m = re.match(r"^APTG[TX](\d+)[A-Z]", s)
    if m:
        return float(m.group(1))

    # Microsemi/APT discrete: APT[current]G[P/N][voltage/10]
    # e.g. APT15GP90BG=15A, APT80GA90S=80A
    m = re.match(r"^APT(\d+)G[PN]", s)
    if m:
        return float(m.group(1))

    # APT discrete alternate: APT[current]G[A-Z]
    m = re.match(r"^APT(\d+)G[A-Z]", s)
    if m:
        return float(m.group(1))

    # Mitsubishi CM module: CM[current]DU/EXS/HA/...
    m = re.match(r"^CM(\d+)", s)
    if m:
        return float(m.group(1))

    # Mitsubishi MG module: MG[voltage_2digits][current][type]
    # e.g. MG17100S-BN4MM = voltage=1700V (17*100), current=100A
    # MG1750S = 1700V / 50A
    m = re.match(r"^MG(\d{2})(\d+)[A-Z]", s)
    if m:
        return float(m.group(2))

    # Powerex/Mitsubishi PM module: PM[current]RSA
    m = re.match(r"^PM(\d+)R", s)
    if m:
        return float(m.group(1))

    # Fuji GA module: GA[current]TD[voltage/100]  e.g. GA400TD25S=400A
    m = re.match(r"^GA(\d+)TD", s)
    if m:
        return float(m.group(1))

    # Toshiba GT series: GT[current][W/R/H/M/K/Q][R/T/...][voltage_digits]
    # e.g. GT40WR21=40A (600V), W=600V voltage class
    m = re.match(r"^GT(\d+)[WRMHKQ]", s)
    if m:
        return float(m.group(1))

    # ST IGBT: STGB/STGW/STGA + [current]N  e.g. STGB8NC60=8A, STGWA30IH=30A
    m = re.match(r"^STG[BWA]{1,2}(\d+)[A-Z]", s)
    if m:
        return float(m.group(1))

    # ST IGBT: STGD[current]N or STGI[current]N
    m = re.match(r"^STG[DI](\d+)N", s)
    if m:
        return float(m.group(1))

    # ST discrete: STGP[current]N
    m = re.match(r"^STGP(\d+)N", s)
    if m:
        return float(m.group(1))

    # Renesas/Fuji RGC: RGC[current]T
    m = re.match(r"^RGC(\d+)T", s)
    if m:
        return float(m.group(1))

    return None


def is_semiconductor_igbt(entry):
    if "semiconductor" not in entry or "inputs" in entry:
        return False
    part = (
        entry["semiconductor"]
        .get("manufacturerInfo", {})
        .get("datasheetInfo", {})
        .get("part", {})
    )
    return part.get("deviceType") == "igbt"


def build_igbt_entry(entry, ic: float) -> dict:
    """Transform semiconductor-wrapped entry to igbt schema."""
    sem = entry["semiconductor"]
    mi = sem["manufacturerInfo"].copy()

    # Inject continuousCollectorCurrent
    if "datasheetInfo" not in mi:
        mi["datasheetInfo"] = {}
    if "electrical" not in mi["datasheetInfo"]:
        mi["datasheetInfo"]["electrical"] = {}
    mi["datasheetInfo"]["electrical"]["continuousCollectorCurrent"] = ic

    igbt_inner = {"manufacturerInfo": mi}
    if "distributorsInfo" in sem:
        igbt_inner["distributorsInfo"] = sem["distributorsInfo"]
    elif "distributorsInfo" in entry:
        igbt_inner["distributorsInfo"] = entry["distributorsInfo"]

    return {"igbt": igbt_inner}


def main():
    quarantine_keep = []
    recovered = []
    skipped_no_ic = []

    with open(QUARANTINE_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if not is_semiconductor_igbt(d):
                quarantine_keep.append(d)
                continue

            ref = d["semiconductor"]["manufacturerInfo"].get("reference", "")
            ic = extract_ic_from_mpn(ref)
            if ic is None:
                quarantine_keep.append(d)
                skipped_no_ic.append(ref)
            else:
                recovered.append(build_igbt_entry(d, ic))

    print(f"IGBTs recovered: {len(recovered)}")
    print(f"IGBTs not extractable (staying in quarantine): {len(skipped_no_ic)}")
    for r in skipped_no_ic:
        print(f"  {r}")
    print(f"Quarantine entries remaining: {len(quarantine_keep)}")

    with open(IGBTS_FILE, "a") as f:
        for entry in recovered:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    with open(QUARANTINE_FILE, "w") as f:
        for entry in quarantine_keep:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    print("Done.")


if __name__ == "__main__":
    main()
