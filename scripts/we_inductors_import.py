#!/usr/bin/env python3
"""WE Passive Components Access DB (Inductors table) -> MAS magnetic NDJSON.
Mirrors the existing WE magnetic shape in TAS (datasheetInfo.electrical[{subtype:inductor,...}]
+ Dummy core/coil stubs to satisfy MAS). Only NEW part numbers (not already in magnetics.ndjson).
"""
import csv, datetime, json, re, os
SRC="/tmp/we_ind.csv"; OUT="/home/alf/PSMA/TAS/staging/we"; os.makedirs(OUT,exist_ok=True)
def num(s):
    if not s: return None
    m=re.search(r"[-+]?\d*\.?\d+",str(s).replace(",",".")); return float(m.group()) if m else None
def henries(s):
    v=num(s);
    if v is None: return None
    u=str(s).lower()
    if "nh" in u: return v*1e-9
    if "mh" in u: return v*1e-3
    if "uh" in u or "µh" in u: return v*1e-6
    return v*1e-6  # WE inductance default uH
def ohms(s):
    v=num(s);
    if v is None: return None
    u=str(s).lower()
    if "mohm" in u or "mΩ" in u: return v*1e-3
    if "kohm" in u: return v*1e3
    return v
def hz(s):
    v=num(s);
    if v is None: return None
    u=str(s).lower()
    if "ghz" in u: return v*1e9
    if "mhz" in u: return v*1e6
    if "khz" in u: return v*1e3
    return v
def metres(s):
    v=num(s); return v*1e-3 if (v is not None and "mm" in str(s).lower()) else v
CORE={"functionalDescription":{"type":"twoPieceSet","material":"Dummy","shape":"Dummy","gapping":[]}}
COIL={"bobbin":"Dummy","functionalDescription":[{"name":"Dummy","numberTurns":1,"numberParallels":1,"isolationSide":"primary","wire":"Dummy"}]}

def convert(row):
    pn=(row.get("Manufacturer Part Number","") or "").strip()
    if not pn: return None
    L=henries(row.get("Inductance"))
    text=(row.get("Description","")+" "+row.get("Match Code","")).lower()
    coupled = ("flyback" in text or "coupled" in text)  # flybacks ARE coupled inductors
    dcr=ohms(row.get("DC Resistance"))
    if coupled:
        e={"subtype":"coupledInductor"}
        if dcr is not None: e["dcResistances"]=[{"maximum":dcr}]
    else:
        e={"subtype":"inductor"}
        if dcr is not None: e["dcResistance"]={"maximum":dcr}
    if L is not None: e["inductance"]={"nominal":L}
    if (v:=num(row.get("Saturation Current"))) is not None: e["saturationCurrentPeak"]=v
    if (v:=hz(row.get("Self Resonant Frequency"))) is not None: e["selfResonantFrequency"]=v
    if (v:=num(row.get("Rated Current"))) is not None: e["ratedCurrents"]=[v]
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
    di["provenance"]=[{"source":"manufacturerDatabase","sourceName":"WE - Passive Components.mdb","retrievedDate":datetime.date.today().isoformat()}]
    mi={"name":"Würth Elektronik","reference":pn,"status":"production","datasheetInfo":di}
    if row.get("Match Code","").strip(): mi["family"]=row["Match Code"].strip()
    mi["datasheetUrl"]=f"https://www.we-online.com/components/products/datasheet/{pn}.pdf"
    return {"magnetic":{"manufacturerInfo":mi,"core":CORE,"coil":COIL}}

def main():
    # existing magnetics refs (only emit NEW)
    have=set()
    for l in open("/home/alf/PSMA/TAS/data/magnetics.ndjson"):
        try: mi=json.loads(l).get("magnetic",{}).get("manufacturerInfo",{})
        except: continue
        for v in (mi.get("reference"),mi.get("datasheetInfo",{}).get("part",{}).get("partNumber")):
            if v: have.add(str(v).strip())
    out=[]; seen=set()
    for row in csv.DictReader(open(SRC)):
        pn=(row.get("Manufacturer Part Number","") or "").strip()
        if not pn or pn in have or pn in seen: continue
        seen.add(pn)
        rec=convert(row)
        if rec: out.append(rec)
    with open(f"{OUT}/inductors.ndjson","w") as fo:
        for r in out: fo.write(json.dumps(r,ensure_ascii=False)+"\n")
    print(f"new WE inductors: {len(out)}")

if __name__=="__main__": main()
