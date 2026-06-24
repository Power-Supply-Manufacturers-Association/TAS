#!/usr/bin/env python3
"""WE Passive 'Transformers' table -> MAS magnetic NDJSON.
Flyback transformers (WE-FB/FLY*/OL* / 'flyback') are mapped as subtype coupledInductor
(they store energy, not a true transformer); the rest as subtype transformer. NEW PNs only.
"""
import csv, json, re, os
SRC="/tmp/we_xfmr.csv"; OUT="/home/alf/PSMA/TAS/staging/we"; os.makedirs(OUT,exist_ok=True)
def num(s):
    if not s: return None
    m=re.search(r"[-+]?\d*\.?\d+",str(s).replace(",",".")); return float(m.group()) if m else None
def henries(s):
    v=num(s)
    if v is None: return None
    u=str(s).lower()
    if "nh" in u: return v*1e-9
    if "mh" in u: return v*1e-3
    return v*1e-6 if ("uh" in u or "µh" in u) else v
def metres(s):
    v=num(s); return v*1e-3 if (v is not None and "mm" in str(s).lower()) else v
def turns_ratios(s):
    parts=re.findall(r"[\d.]+", s or "")
    if len(parts)>=2:
        p0=float(parts[0])
        return [{"nominal":round(p0/float(p),6)} for p in parts[1:] if float(p)!=0]
    return None
CORE={"functionalDescription":{"type":"twoPieceSet","material":"Dummy","shape":"Dummy","gapping":[]}}
COIL={"bobbin":"Dummy","functionalDescription":[{"name":"Dummy","numberTurns":1,"numberParallels":1,"isolationSide":"primary","wire":"Dummy"}]}

def is_flyback(row):
    mc=(row.get("Match Code","") or "").upper()
    return ("flyback" in (row.get("Description","") or "").lower()
            or mc.startswith(("WE-FB","WE-FLY","WE-OL")))

def convert(row):
    pn=(row.get("Manufacturer Part Number","") or "").strip()
    if not pn: return None
    L=henries(row.get("Inductance")); tr=turns_ratios(row.get("Turns Ratio"))
    if is_flyback(row):
        e={"subtype":"coupledInductor"}
        if L is not None: e["inductance"]={"nominal":L}
        if tr: e["turnsRatios"]=tr
    else:
        e={"subtype":"transformer"}
        if L is not None: e["inductance"]={"nominal":L}
        if tr: e["turnsRatios"]=tr
        if (v:=num(row.get("Insulation Test Voltage"))) is not None: e["insulationTestVoltageAC"]=v
    part={}
    if row.get("Description","").strip(): part["description"]=row["Description"].strip()[:300]
    if row.get("Case/Size Code","").strip(): part["caseCode"]=row["Case/Size Code"].strip()
    mech={}
    for k,col in [("length","Length"),("width","Width"),("height","Height")]:
        v=metres(row.get(col))
        if v is not None: mech[k]={"nominal":round(v,9)}
    di={"part":part,"electrical":[e]}
    if mech: di["mechanical"]=mech
    tmin=num(row.get("Min Operating Temperature")); tmax=num(row.get("Max Operating Temperature"))
    if tmin is not None and tmax is not None: di["thermal"]={"operatingTemperature":{"minimum":tmin,"maximum":tmax}}
    mi={"name":"Würth Elektronik","reference":pn,"status":"production","datasheetInfo":di,
        "datasheetUrl":f"https://www.we-online.com/components/products/datasheet/{pn}.pdf"}
    if row.get("Match Code","").strip(): mi["family"]=row["Match Code"].strip()
    return {"magnetic":{"manufacturerInfo":mi,"core":CORE,"coil":COIL}}

def main():
    have=set()
    for l in open("/home/alf/PSMA/TAS/data/magnetics.ndjson"):
        try: mi=json.loads(l).get("magnetic",{}).get("manufacturerInfo",{})
        except: continue
        if mi.get("reference"): have.add(mi["reference"].strip())
    out=[]; seen=set()
    for row in csv.DictReader(open(SRC)):
        pn=(row.get("Manufacturer Part Number","") or "").strip()
        if not pn or pn in have or pn in seen: continue
        seen.add(pn); rec=convert(row)
        if rec: out.append(rec)
    with open(f"{OUT}/transformers.ndjson","w") as fo:
        for r in out: fo.write(json.dumps(r,ensure_ascii=False)+"\n")
    fb=sum(1 for r in out if r["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]["subtype"]=="coupledInductor")
    print(f"new WE transformers: {len(out)} (flyback->coupledInductor: {fb}, transformer: {len(out)-fb})")

if __name__=="__main__": main()
