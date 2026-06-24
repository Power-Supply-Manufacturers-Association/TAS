#!/usr/bin/env python3
"""Infineon Gate Driver + PMIC Finder xlsx -> CTAS controller NDJSON.
Gate drivers -> function.category=gateDriver (+ gateDrive/isolation blocks).
PMIC (Infineon Optireg safety SBCs, e.g. TLF35584) -> category=supervisor (identity only).
NEW part numbers only; stamps provenance. -> staging/infineon/controllers_*.ndjson
"""
import openpyxl, json, re, datetime, os
DL="/mnt/c/Users/Alfonso/Downloads"; OUT="/home/alf/PSMA/TAS/staging/infineon"; os.makedirs(OUT,exist_ok=True)
DATA="/home/alf/PSMA/TAS/data/controllers.ndjson"; TODAY=datetime.date.today().isoformat()
def norm(s): return re.sub(r"\s+"," ",str(s or "").lower()).strip()
def val(s):
    if s is None: return None
    m=re.search(r"[-+]?\d*\.?\d+",str(s).replace(",",".")); return float(m.group()) if m else None
def seconds(s):
    v=val(s); u=norm(s)
    if v is None: return None
    if "ns" in u: return v*1e-9
    if "µs" in u or "us" in u: return v*1e-6
    if "ps" in u: return v*1e-12
    return v
def chan_config(cfg):
    c=norm(cfg)
    if "," in c: return None
    if c=="high-side": return "singleHigh"
    if c=="low-side": return "singleLow"
    if "half-bridge"==c or "high-side and low-side"==c: return "halfBridge"
    return None
def isolation(iso):
    c=norm(iso)
    if "reinforced" in c: return "reinforced"
    if "galvanic" in c and "basic" in c: return "basic"
    if "galvanic" in c and "functional" in c: return "functional"
    if "double" in c: return "double"
    return None  # levelshift / non-isolated -> not galvanically isolated

def load_have():
    have=set()
    for l in open(DATA):
        try: mi=json.loads(l).get("controller",{}).get("manufacturerInfo",{})
        except: continue
        for v in (mi.get("reference"),mi.get("datasheetInfo",{}).get("part",{}).get("partNumber")):
            if v: u=str(v).strip().upper(); have.add(u); have.add(u.replace(" ",""))
    return have

def rows_of(fname):
    wb=openpyxl.load_workbook(f"{DL}/{fname}",read_only=True); ws=wb.active
    data=list(ws.iter_rows(values_only=True)); hdr=[str(c) for c in data[0]]; H={h:i for i,h in enumerate(hdr)}
    for r in data[1:]:
        yield {h:(r[H[h]] if H[h]<len(r) else None) for h in hdr}

def common(row, category, srcname):
    pn=re.sub(r"\s+"," ",str(row.get("Part number") or "")).strip()
    if not pn: return None
    part={"partNumber":pn,"deviceType":"controller"}
    if row.get("Infineon package"): part["case"]=str(row["Infineon package"]).strip()
    func={"category":category}
    ds=row.get("Datasheet link") or row.get("Part link")
    prov=[{"source":"manufacturerParametric","sourceName":srcname,"retrievedDate":TODAY}]
    if ds and str(ds).startswith("http"): prov[0]["sourceUrl"]=str(ds)
    di={"part":part,"function":func,"provenance":prov}
    mi={"name":"Infineon","reference":pn,"status":"production","datasheetInfo":di}
    if ds and str(ds).startswith("http"): mi["datasheetUrl"]=str(ds)
    return pn, {"controller":{"manufacturerInfo":mi}}, part, func, di

def build_gd(row):
    r=common(row,"gateDriver","Infineon Gate Driver Finder (xlsx export)")
    if not r: return None,None
    pn,rec,part,func,di=r
    if row.get("Switch Type"): part["technology"]=str(row["Switch Type"]).strip()[:60]
    ch=val(row.get("Channels"))
    if ch and ch>=1: func["channelCount"]=int(ch)
    iso=isolation(row.get("Isolation Type"))
    if iso: func["isolation"]="primaryToSecondary"
    else: func["isolation"]="none"
    gd={}
    if (v:=val(row.get("Output Current (Source)"))) is not None: gd["sourceCurrentPeak"]=v
    if (v:=val(row.get("Output Current (Sink)"))) is not None: gd["sinkCurrentPeak"]=v
    if (v:=seconds(row.get("Turn On Propagation Delay"))) is not None: gd["propagationDelay"]=v
    cc=chan_config(row.get("Configuration"))
    if cc: gd["channelConfiguration"]=cc
    el={}
    if gd: el["gateDrive"]=gd
    if iso: el["isolation"]={"isolationType":iso}
    if el: di["electrical"]=el
    return pn,rec

def build_pmic(row):
    r=common(row,"supervisor","Infineon PMIC Finder (xlsx export) [Optireg SBC->supervisor]")
    if not r: return None,None
    return r[0], r[1]

def emit(fname, build, tag):
    have=load_have(); main=[]; seen=set()
    for row in rows_of(fname):
        pn,rec=build(row)
        if not pn: continue
        k=pn.upper()
        if k in have or k.replace(" ","") in have or k in seen: continue
        seen.add(k); main.append(rec)
    with open(f"{OUT}/{tag}.ndjson","w") as fo:
        for r in main: fo.write(json.dumps(r,ensure_ascii=False)+"\n")
    print(f"{tag}: new={len(main)}")

if __name__=="__main__":
    emit("Gate Driver Finder.xlsx", build_gd, "controllers_gd")
    emit("PMIC Finder.xlsx", build_pmic, "controllers_pmic")
