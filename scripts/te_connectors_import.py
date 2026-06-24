#!/usr/bin/env python3
"""Convert TE Connectivity compact records (.playwright-mcp/te_*.json) -> CONAS connector NDJSON.

Source: api.te.com search API (pulled via the Playwright MCP browser, Akamai-gated).
Relaxed CONAS (2026-06-24): required = partNumber + familyDetails.family +
electrical.ratedCurrentPerContact; matingPolarity + ratedVoltage optional.
"""
import glob, json, os, re

SRC = "/home/alf/PSMA/.playwright-mcp"
OUT = "/home/alf/PSMA/TAS/staging/te"
os.makedirs(OUT, exist_ok=True)

def num(s):
    if s is None: return None
    m = re.search(r"[-+]?\d*\.?\d+", str(s).replace(",", "."))
    return float(m.group()) if m else None

def mating_polarity(r):
    ht = (r.get("housingType") or "").lower()
    if "female" in ht: return "female"
    if "male" in ht: return "male"
    ct = (r.get("contactType") or "").lower()
    if ct in ("pin", "tab", "blade", "plug"): return "male"
    if ct in ("socket", "receptacle", "jack"): return "female"
    return None

IFACE = ["USB4","USB-C","USB","HDMI","DisplayPort","Thunderbolt","QSFP","SFP","PCIe",
         "PCI Express","SATA","SAS","Ethernet","RJ45","RJ-45","FAKRA","M.2","DIMM","FireWire"]
def interface_standard(r):
    t = (r.get("desc") or "") + " " + (r.get("system") or "")
    for tok in IFACE:
        if tok.lower() in t.lower(): return tok
    return (r.get("system") or "").strip() or None

def map_family(r):
    crumb = (r.get("crumb") or "").lower()
    system = (r.get("system") or "").lower()
    blob = crumb + " " + system
    def has(*xs): return any(x in blob for x in xs)
    if has("backshell","accessor","tool","cap","cover","gland","contact only","strain relief"):
        # accessories: only drop if clearly non-connector; keep generic otherwise
        if has("backshell","accessor","tool","gland"): return None, "non-connector accessory"
    if has("rf","coax","fakra"): return None, "rf connector without characteristicImpedance"
    if has("ffc","fpc","flex"): return "fpcFfc", {}
    if has("card edge","memory module","dimm","socket for ic"): return "cardEdge", {}
    if has("circular","m8","m12","circular"): return "circular", {}
    if has("terminal block","barrier","din rail"): return "terminalBlock", {}
    if has("board-to-board","mezzanine","backplane"): return "boardToBoard", {}
    if has("header","receptacle"): return "pinHeaderSocket", {}
    if has("usb","hdmi","ethernet","i/o","io connector","sfp","qsfp","modular jack","fiber","data"):
        std = interface_standard(r)
        return ("dataInterface", {"interfaceStandard": std}) if std else (None, "dataInterface without interfaceStandard")
    if has("power","busbar","high current","high power"): return "power", {}
    if has("wire-to-wire","wire-to-board","wire to","housing","crimp","terminal","poke","quick"):
        return "wireToBoard", {}
    if has("pcb"): return "pinHeaderSocket", {}
    if system: return "wireToBoard", {}
    return None, f"unmapped: {r.get('crumb')}"

STATUS = {"active":"production","obsolete":"obsolete","not recommended":"nrnd","end of life":"obsolete"}

def convert(r):
    pn = (r.get("pn") or "").strip()
    if not pn: return None, ["partNumber"]
    cur = num(r.get("current"))
    volt = num(r.get("voltage"))
    pol = mating_polarity(r)
    family, extra = map_family(r)

    part = {"partNumber": pn}
    if pol: part["matingPolarity"] = pol
    if r.get("desc"): part["description"] = r["desc"][:1000]
    if r.get("brand"): part["series"] = r["brand"]

    electrical = {}
    if cur is not None: electrical["ratedCurrentPerContact"] = cur
    if volt is not None: electrical["ratedVoltage"] = volt

    mechanical = {}
    pos = num(r.get("positions"))
    if pos and pos >= 1: mechanical["positions"] = int(pos)
    pitch = num(r.get("pitch"))
    if pitch and pitch > 0: mechanical["pitch"] = round(pitch*1e-3, 9)

    di = {"part": part, "electrical": electrical, "mechanical": mechanical}
    if family is not None:
        fd = {"family": family}; fd.update(extra); di["familyDetails"] = fd
    t = r.get("temp")
    if t:
        m = re.findall(r"[-+]?\d+", t)
        if len(m) >= 2: di["environmental"] = {"operatingTemperature": {"minimum": float(m[0]), "maximum": float(m[1])}}

    mi = {"name": "TE Connectivity", "reference": pn,
          "status": STATUS.get((r.get("status") or "active").lower(), "production"),
          "datasheetInfo": di}
    if r.get("brand"): mi["family"] = r["brand"]
    if r.get("desc"): mi["description"] = r["desc"][:1000]
    ds = r.get("datasheet")
    if ds and str(ds).startswith("http"): mi["datasheetUrl"] = ds
    rec = {"connector": {"manufacturerInfo": mi}}

    missing = []
    if cur is None: missing.append("ratedCurrentPerContact")
    if family is None: missing.append(extra)
    return rec, missing

def main():
    seen=set(); mains=[]; inc=[]; n=dup=0
    for f in sorted(glob.glob(f"{SRC}/te_*.json")):
        d = json.load(open(f))
        for r in d.get("records", []):
            n+=1; pn=(r.get("pn") or "").strip()
            if not pn or pn in seen: dup+=1; continue
            seen.add(pn)
            rec, missing = convert(r)
            if rec is None: continue
            if missing:
                rec["quarantineReason"]="incomplete TE data; missing: "+"; ".join(map(str,missing))+" (2026-06-24)"
                inc.append(rec)
            else: mains.append(rec)
    for name,recs in [("connectors.main",mains),("connectors.incomplete",inc)]:
        with open(f"{OUT}/{name}.ndjson","w") as fo:
            for x in recs: fo.write(json.dumps(x,ensure_ascii=False)+"\n")
    print(json.dumps({"rows":n,"dup":dup,"main":len(mains),"incomplete":len(inc)},indent=2))

if __name__=="__main__": main()
