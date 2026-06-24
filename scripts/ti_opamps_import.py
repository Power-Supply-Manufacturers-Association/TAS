#!/usr/bin/env python3
"""Convert TI op-amp parametric data (.playwright-mcp/ti_opamps.json) -> AAS NDJSON.

Source: ti.com/selectionmodel/api/gpn/result-list?destinationId=1293 (op-amps),
pulled via the Playwright MCP browser. TI gives every value in SI `base` units
(GBW in Hz, Vos in V, Iq in A) EXCEPT slew rate which is V/µs (×1e6 -> V/s).
Maps to the AAS operationalAmplifier discriminator. No Blade Runner for AAS
(not a PEAS physical child) — JSON Schema is the gate.
"""
import json, re

SRC = "/home/alf/PSMA/.playwright-mcp/ti_opamps.json"
OUT = "/home/alf/PSMA/TAS/staging/ti"
import os; os.makedirs(OUT, exist_ok=True)

def f(v):
    if v is None or isinstance(v, list): return None
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(v))
    return float(m.group()) if m else None

STATUS = {"ACTIVE": "production", "PREVIEW": "preview", "NRND": "nrnd",
          "OBSOLETE": "obsolete", "PRE_RELEASE": "preview"}
INPUT_STAGE = {"cmos": "CMOS", "bipolar": "bipolar", "jfet": "JFET", "bicmos": "BiCMOS"}

def convert(r):
    P = r["params"]
    def g(k): return P.get(k)
    def gs(k):
        v = P.get(k)
        if isinstance(v, list): v = v[0] if v else None
        return (v or "") if isinstance(v, str) else ""
    gpn = r["gpn"]
    e = {}
    if (v := f(g("Number of channels"))) is not None: e["numberOfChannels"] = int(v)
    if (v := f(g("Vos (offset voltage at 25°C)max"))) is not None: e["inputOffsetVoltage"] = v
    if (v := f(g("Offset drifttyp"))) is not None: e["inputOffsetVoltageDrift"] = v
    if (v := f(g("Input bias currenttyp"))) is not None: e["inputBiasCurrent"] = v
    if (v := f(g("CMRRtyp"))) is not None: e["commonModeRejectionRatio"] = v
    if (v := f(g("Slew ratetyp"))) is not None: e["slewRate"] = round(v * 1e6, 6)  # V/µs -> V/s
    if (v := f(g("Vn at 1 kHztyp"))) is not None:
        e["voltageNoiseDensity"] = v; e["voltageNoiseDensityFrequency"] = 1000
    if (v := f(g("GBWtyp"))) is not None: e["gainBandwidthProduct"] = v
    if (v := f(g("Iouttyp"))) is not None: e["outputCurrent"] = v
    arch = gs("Architecture").lower()
    if arch in INPUT_STAGE: e["inputStage"] = INPUT_STAGE[arch]
    feats = g("Features")
    if isinstance(feats, list) and any("zero drift" in str(x).lower() for x in feats):
        e["architecture"] = "zeroDrift"
    rr = gs("Rail-to-rail").lower()
    if rr:
        e["railToRailInput"] = ("in" in rr)
        e["railToRailOutput"] = ("out" in rr)
    supply = {}
    if (v := f(g("Total supply voltage (+5 V = 5, ±5 V = 10)min"))) is not None: supply["minimumSupplyVoltage"] = v
    if (v := f(g("Total supply voltage (+5 V = 5, ±5 V = 10)max"))) is not None: supply["maximumSupplyVoltage"] = v
    if (v := f(g("Iq per channeltyp"))) is not None: supply["quiescentCurrentPerChannel"] = v
    if supply: e["supply"] = supply

    part = {"partNumber": gpn}
    pkg = g("Package type")
    if pkg: part["package"] = pkg[0] if isinstance(pkg,list) else pkg
    if gs("Rating").lower() == "automotive": part["qualification"] = "Automotive"

    mi = {"name": "Texas Instruments", "reference": gpn,
          "status": STATUS.get(r.get("status"), "production"),
          "datasheetUrl": f"https://www.ti.com/lit/gpn/{gpn}",
          "datasheetInfo": {"part": part, "electrical": e}}
    return {"operationalAmplifier": {"manufacturerInfo": mi}}

def main():
    recs = json.load(open(SRC))["records"]
    out = [convert(r) for r in recs if r.get("gpn")]
    with open(f"{OUT}/opamps.ndjson", "w") as fo:
        for r in out: fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"converted {len(out)} TI op-amps -> {OUT}/opamps.ndjson")

if __name__ == "__main__": main()
