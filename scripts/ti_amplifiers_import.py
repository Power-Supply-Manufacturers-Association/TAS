#!/usr/bin/env python3
"""Generic TI amplifier-family -> AAS converter (instrumentation/difference/PGA).

Shares amplifierCommon mapping with the op-amp importer; handles TI param-name
variants across sub-categories (CMRRmin/typ, Iqtyp/Iq per channeltyp, Vos typ/max).
Reads .playwright-mcp/ti_<type>.json, writes staging/ti/<type>.ndjson.
"""
import json, re, os, sys

MCP = "/home/alf/PSMA/.playwright-mcp"
OUT = "/home/alf/PSMA/TAS/staging/ti"; os.makedirs(OUT, exist_ok=True)

def f(v):
    if v is None or isinstance(v, list): return None
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(v)); return float(m.group()) if m else None

STATUS = {"ACTIVE": "production", "PREVIEW": "preview", "NRND": "nrnd", "OBSOLETE": "obsolete", "PRE_RELEASE": "preview"}

def first_present(P, keys):
    for k in keys:
        if P.get(k) is not None: return P.get(k)
    return None

def convert(r, disc):
    P = r["params"]; gpn = r["gpn"]
    if not gpn: return None
    e = {}
    if (v := f(P.get("Number of channels"))) is not None: e["numberOfChannels"] = int(v)
    if (v := f(first_present(P, ["Vos (offset voltage at 25°C)max", "Vos (offset voltage at 25°C)typ",
                                 "Input offset voltage (±)max", "Vos (offset voltage)max"]))) is not None:
        e["inputOffsetVoltage"] = v
    if (v := f(P.get("Input bias current (±)max"))) is not None: e["inputBiasCurrent"] = v
    if (v := f(first_present(P, ["CMRRtyp", "CMRRmin"]))) is not None: e["commonModeRejectionRatio"] = v
    if (v := f(P.get("Slew ratetyp"))) is not None: e["slewRate"] = round(v * 1e6, 6)
    if (v := f(P.get("Vn at 1 kHztyp"))) is not None:
        e["voltageNoiseDensity"] = v; e["voltageNoiseDensityFrequency"] = 1000
    supply = {}
    if (v := f(first_present(P, ["Vsmin", "Total supply voltage (+5 V = 5, ±5 V = 10)min"]))) is not None:
        supply["minimumSupplyVoltage"] = v
    if (v := f(first_present(P, ["Vsmax", "Total supply voltage (+5 V = 5, ±5 V = 10)max"]))) is not None:
        supply["maximumSupplyVoltage"] = v
    if (v := f(first_present(P, ["Iq per channeltyp", "Iqtyp"]))) is not None:
        supply["quiescentCurrentPerChannel"] = v
    if supply: e["supply"] = supply
    # type-specific gain (V/V)
    if disc == "instrumentationAmplifier":
        if (v := f(P.get("Voltage gainmin"))) is not None: e["minimumGain"] = v
        if (v := f(P.get("Voltage gainmax"))) is not None: e["maximumGain"] = v
    elif disc == "differenceAmplifier":
        if (v := f(first_present(P, ["Voltage gain", "Voltage gainmax"]))) is not None: e["gain"] = v
        ge = f(P.get("Gain error (±)max"))
        if ge is not None: e["gainError"] = ge / 100.0 if ge > 1 else ge  # % -> fraction if needed

    part = {"partNumber": gpn}
    mi = {"name": "Texas Instruments", "reference": gpn,
          "status": STATUS.get(r.get("status"), "production"),
          "datasheetUrl": f"https://www.ti.com/lit/gpn/{gpn}",
          "datasheetInfo": {"part": part, "electrical": e}}
    return {disc: {"manufacturerInfo": mi}}

def main():
    for disc in ["instrumentationAmplifier", "differenceAmplifier", "programmableGainAmplifier"]:
        recs = json.load(open(f"{MCP}/ti_{disc}.json"))["records"]
        out = [c for c in (convert(r, disc) for r in recs) if c]
        with open(f"{OUT}/{disc}.ndjson", "w") as fo:
            for r in out: fo.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{disc}: {len(out)}")

if __name__ == "__main__": main()
