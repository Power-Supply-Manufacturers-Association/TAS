#!/usr/bin/env python3
"""WE Passive 'Resistors' table -> RAS resistor NDJSON (NEW PNs only)."""
import csv, json, re
SRC="/tmp/we_res.csv"; OUT="/home/alf/PSMA/TAS/staging/we"
def num(s):
    if not s: return None
    m=re.search(r"[-+]?\d*\.?\d+",str(s).replace(",",".")); return float(m.group()) if m else None
def watts(s):
    v=num(s)
    if v is None: return None
    u=str(s).lower()
    if "mw" in u: return v*1e-3
    if "kw" in u: return v*1e3
    return v
def ohms(s):
    v=num(s)
    if v is None: return None
    u=str(s).lower()
    if "mohm" in u or "mΩ" in u: return v*1e-3
    if "kohm" in u: return v*1e3
    if "mohm" in u: return v*1e6
    if "gohm" in u: return v*1e9
    return v
TECH=[("thick film","thickFilm"),("thin film","thinFilm"),("metal foil","metalFoil"),
      ("metal strip","metalFoil"),("metal film","metalFilm"),("metal oxide","metalOxide"),
      ("current sense","currentSenseShunt"),("shunt","currentSenseShunt"),
      ("wirewound","wirewound"),("wire wound","wirewound"),("melf","melf"),("carbon","carbonFilm")]
def tech_of(cat):
    c=(cat or "").lower()
    for frag,t in TECH:
        if frag in c: return t
    return "thickFilm"
def metres(s):
    v=num(s); return v*1e-3 if (v is not None and "mm" in str(s).lower()) else v
def main():
    have=set()
    for l in open("/home/alf/PSMA/TAS/data/resistors.ndjson"):
        try: mi=json.loads(l).get("resistor",{}).get("manufacturerInfo",{})
        except: continue
        for v in (mi.get("reference"),mi.get("datasheetInfo",{}).get("part",{}).get("partNumber")):
            if v: have.add(str(v).strip())
    out=[]; seen=set()
    for row in csv.DictReader(open(SRC)):
        pn=(row.get("Manufacturer Part Number","") or "").strip()
        if not pn or pn in have or pn in seen: continue
        seen.add(pn)
        part={"partNumber":pn,"technology":tech_of(row.get("Category"))}
        if row.get("Match Code","").strip(): part["series"]=row["Match Code"].strip()
        if row.get("Case/Size Code","").strip(): part["case"]=row["Case/Size Code"].strip()
        if row.get("Description","").strip(): part["matchcodeDescription"]=row["Description"].strip()[:200]
        rv=ohms(row.get("Resistance"))
        if rv is None or rv<=0: continue  # skip 0-ohm jumpers / blanks
        e={"resistance":{"nominal":rv}}
        tol=num(row.get("Resistance Tolerance"))
        if tol is not None: e["tolerance"]=abs(tol)/100.0
        if (v:=watts(row.get("Rated Power"))) is not None: e["powerRating"]=v
        if (v:=num(row.get("Temperature Coefficient of Resistance"))) is not None: e["temperatureCoefficient"]=v
        di={"part":part,"electrical":e}
        tmin=num(row.get("Min Operating Temperature")); tmax=num(row.get("Max Operating Temperature"))
        if tmin is not None and tmax is not None: di["thermal"]={"operatingTemperature":{"minimum":tmin,"maximum":tmax}}
        mech={}
        for k,col in [("length","Length"),("width","Width"),("height","Height")]:
            v=metres(row.get(col))
            if v is not None: mech[k]={"nominal":round(v,9)}
        if mech: di["mechanical"]={"dimensions":mech} if False else mech
        mi={"name":"Würth Elektronik","reference":pn,"status":"production","datasheetInfo":di,
            "datasheetUrl":f"https://www.we-online.com/components/products/datasheet/{pn}.pdf"}
        out.append({"resistor":{"manufacturerInfo":mi}})
    open(f"{OUT}/resistors.ndjson","w").write("\n".join(json.dumps(r,ensure_ascii=False) for r in out)+("\n" if out else ""))
    print("new WE resistors:",len(out))
if __name__=="__main__": main()
