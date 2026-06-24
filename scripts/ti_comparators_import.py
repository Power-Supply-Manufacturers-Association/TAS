#!/usr/bin/env python3
"""Convert TI comparator parametric data (.playwright-mcp/ti_comparators.json) -> AAS NDJSON.

Source: ti.com/selectionmodel result-list destinationId=50002. base values are SI
(propagation delay in s, Vos in V, Iq in A). Maps to AAS comparator discriminator.
Appended to analog_ics.ndjson. Validated by JSON Schema + Blade Runner (CMP_* checks).
"""
import json, re, os

SRC = "/home/alf/PSMA/.playwright-mcp/ti_comparators.json"
OUT = "/home/alf/PSMA/TAS/staging/ti"; os.makedirs(OUT, exist_ok=True)

def f(v):
    if v is None or isinstance(v, list): return None
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(v)); return float(m.group()) if m else None

STATUS = {"ACTIVE": "production", "PREVIEW": "preview", "NRND": "nrnd", "OBSOLETE": "obsolete", "PRE_RELEASE": "preview"}

def output_stage(v):
    s = " ".join(v).lower() if isinstance(v, list) else str(v or "").lower()
    if "push" in s: return "pushPull"
    if "complementary" in s: return "complementary"
    if "open-drain" in s or "open drain" in s: return "openDrain"
    if "open-collector" in s or "open collector" in s: return "openCollector"
    return None

def ctype(feats):
    s = " ".join(feats).lower() if isinstance(feats, list) else str(feats or "").lower()
    if "window" in s: return "window"
    if "high speed" in s or "high-speed" in s: return "highSpeed"
    if "low power" in s or "low-power" in s or "nanopower" in s or "micropower" in s: return "lowPower"
    if "precision" in s: return "precision"
    return None

def convert(r):
    P = r["params"]; g = P.get; gpn = r["gpn"]
    tpd = f(g("Propagation delay time"))
    if tpd is None:
        return None  # propagationDelay is required by AAS comparator
    e = {"propagationDelay": tpd}
    if (v := f(g("Number of channels"))) is not None: e["numberOfChannels"] = int(v)
    if (v := f(g("Vos (offset voltage at 25°C)max"))) is not None: e["inputOffsetVoltage"] = v
    if (v := f(g("Input bias current (±)max"))) is not None: e["inputBiasCurrent"] = v
    lo, hi = f(g("VICRmin")), f(g("VICRmax"))
    cm = {}
    if lo is not None: cm["minimum"] = lo
    if hi is not None: cm["maximum"] = hi
    if cm: e["commonModeVoltageRange"] = cm
    if (os_ := output_stage(g("Output type"))): e["outputStage"] = os_
    if (ct := ctype(g("Features"))): e["type"] = ct
    supply = {}
    if (v := f(g("Vsmin"))) is not None: supply["minimumSupplyVoltage"] = v
    if (v := f(g("Vsmax"))) is not None: supply["maximumSupplyVoltage"] = v
    if (v := f(g("Iq per channeltyp"))) is not None: supply["quiescentCurrentPerChannel"] = v
    if supply: e["supply"] = supply
    part = {"partNumber": gpn}
    pkg = g("Package type")
    if pkg: part["package"] = pkg[0] if isinstance(pkg, list) else pkg
    mi = {"name": "Texas Instruments", "reference": gpn,
          "status": STATUS.get(r.get("status"), "production"),
          "datasheetUrl": f"https://www.ti.com/lit/gpn/{gpn}",
          "datasheetInfo": {"part": part, "electrical": e}}
    return {"comparator": {"manufacturerInfo": mi}}

def main():
    recs = json.load(open(SRC))["records"]
    out = [c for c in (convert(r) for r in recs if r.get("gpn")) if c]
    with open(f"{OUT}/comparators.ndjson", "w") as fo:
        for r in out: fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"converted {len(out)} / {len(recs)} TI comparators")

if __name__ == "__main__": main()
