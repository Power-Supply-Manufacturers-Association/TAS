#!/usr/bin/env python3
"""Infineon 'MOSFET Finder' xlsx export -> SAS mosfet NDJSON. Complete records
(VDS, Id, RDS(on), VGS(th), Qg all present). NEW part numbers only; stamps provenance.
"""
import openpyxl, json, re, datetime, sys
SRC="/mnt/c/Users/Alfonso/Downloads/MOSFET Finder.xlsx"
OUT="/home/alf/PSMA/TAS/staging/infineon"; import os; os.makedirs(OUT,exist_ok=True)
TODAY=datetime.date.today().isoformat()
def val(s):
    if s is None: return None
    m=re.search(r"[-+]?\d*\.?\d+",str(s).replace(",",".")); return float(m.group()) if m else None
def ohms(s):
    v=val(s)
    if v is None: return None
    u=str(s).lower()
    if "mω" in u or "mohm" in u or "mΩ" in u: return v*1e-3
    if "kω" in u or "kohm" in u: return v*1e3
    return v
def charge(s):
    v=val(s)
    if v is None: return None
    u=str(s).lower()
    if "nc" in u: return v*1e-9
    if "µc" in u or "uc" in u: return v*1e-6
    return v
def tech_of(s):
    s=(s or "").lower()
    if "gan" in s: return "GaN"
    if "sic" in s: return "SiC"
    return "Si"

def load_have():
    have=set()
    for l in open("/home/alf/PSMA/TAS/data/mosfets.ndjson"):
        try: mi=json.loads(l).get("semiconductor",{}).get("mosfet",{}).get("manufacturerInfo",{})
        except: continue
        for v in (mi.get("reference"),mi.get("datasheetInfo",{}).get("part",{}).get("partNumber")):
            if v:
                u=str(v).strip().upper(); have.add(u); have.add(u.replace(" ",""))
    return have

def main():
    wb=openpyxl.load_workbook(SRC,read_only=True); ws=wb.active
    rows=list(ws.iter_rows(values_only=True)); hdr=[str(c) for c in rows[0]]
    H={h:i for i,h in enumerate(hdr)}
    def g(r,name): return r[H[name]] if name in H and H[name]<len(r) else None
    have=load_have(); out=[]; seen=set()
    for r in rows[1:]:
        pn=g(r,"Part number")
        pn=re.sub(r"\s+"," ",str(pn)).strip() if pn else None
        if not pn: continue
        opn=(str(g(r,"OPN")).strip() if g(r,"OPN") else "")
        key=pn.upper()
        if key in have or key.replace(" ","") in have or (opn and opn.upper() in have) or key in seen: continue
        seen.add(key)
        vds=val(g(r,"VDS max")); idc=val(g(r,"ID  (@25°C) max")); rds=ohms(g(r,"RDS (on) (@10V) max"))
        thmin=val(g(r,"VGS(th) min")); thmax=val(g(r,"VGS(th) max")); thtyp=val(g(r,"VGS(th)")); qg=charge(g(r,"QG (typ @10V)"))
        # require the SAS-mandatory five
        if None in (vds,idc,rds,qg) or (thmin is None and thmax is None and thtyp is None): continue
        gth={}
        if thmin is not None: gth["minimum"]=thmin
        if thtyp is not None: gth["nominal"]=thtyp
        if thmax is not None: gth["maximum"]=thmax
        pol=(str(g(r,"Polarity") or "")).strip().upper()
        part={"partNumber":pn,"technology":tech_of(g(r,"Technology")),
              "subType":"pChannel" if pol=="P" else "nChannel"}
        ser=re.sub(r"[™™]","",str(g(r,"Technology") or "")).strip()
        if ser: part["series"]=ser
        case=g(r,"Package name") or g(r,"Infineon package")
        if case: part["case"]=str(case).strip()
        el={"drainSourceVoltage":vds,"continuousDrainCurrent":idc,"onResistance":rds,
            "onResistanceVgs":10,"gateThresholdVoltage":gth,"totalGateCharge":round(qg,12)}
        if (q45:=charge(g(r,"QG (typ @4.5V)"))) is not None: pass
        ds=g(r,"Datasheet link") or g(r,"Part link")
        prov=[{"source":"manufacturerParametric","sourceName":"Infineon MOSFET Finder (xlsx export)","retrievedDate":TODAY}]
        if ds and str(ds).startswith("http"): prov[0]["sourceUrl"]=str(ds)
        mi={"name":"Infineon","reference":pn,"status":"production",
            "datasheetInfo":{"part":part,"electrical":el,"provenance":prov}}
        if ds and str(ds).startswith("http"): mi["datasheetUrl"]=str(ds)
        out.append({"semiconductor":{"mosfet":{"manufacturerInfo":mi}}})
    with open(f"{OUT}/mosfets.ndjson","w") as fo:
        for o in out: fo.write(json.dumps(o,ensure_ascii=False)+"\n")
    print(f"new Infineon MOSFETs: {len(out)}")

if __name__=="__main__": main()
