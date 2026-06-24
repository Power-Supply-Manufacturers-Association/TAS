#!/usr/bin/env python3
"""Infineon parametric Finder xlsx exports -> SAS (igbt / diode / esd-diode).
Robust to exact column names (normalized fuzzy matching). A record missing a
SAS-required field is routed to <type>.incomplete.ndjson (librarian), never faked.
Stamps provenance. NEW part numbers only (dedup vs the live catalog).

Run:  python3 scripts/infineon_sas_finders_import.py
"""
import openpyxl, json, re, datetime, os
DL="/mnt/c/Users/Alfonso/Downloads"
OUT="/home/alf/PSMA/TAS/staging/infineon"; os.makedirs(OUT,exist_ok=True)
DATA="/home/alf/PSMA/TAS/data"
TODAY=datetime.date.today().isoformat()

def norm(s): return re.sub(r"\s+"," ",str(s or "").lower()).strip()
def val(s):
    if s is None: return None
    m=re.search(r"[-+]?\d*\.?\d+",str(s).replace(",",".")); return float(m.group()) if m else None
def ohms(s):
    v=val(s); u=norm(s)
    if v is None: return None
    if "mω" in u or "mohm" in u or "mΩ" in u: return v*1e-3
    if "kω" in u or "kohm" in u: return v*1e3
    return v
def charge(s):
    v=val(s); u=norm(s)
    if v is None: return None
    if "nc" in u: return v*1e-9
    if "uc" in u or "µc" in u: return v*1e-6
    if "pc" in u: return v*1e-12
    return v
def farads(s):
    v=val(s); u=norm(s)
    if v is None: return None
    if "pf" in u: return v*1e-12
    if "nf" in u: return v*1e-9
    return v

class Row:
    def __init__(self,hdr,r):
        self.h={norm(h):i for i,h in enumerate(hdr)}; self.r=r; self.hdr=hdr
    def get(self,*cands, prefer_max=True):
        # match a column whose normalized header CONTAINS a candidate fragment; prefer 'max'
        best=None
        for frag in cands:
            f=norm(frag); hits=[i for hn,i in self.h.items() if f in hn]
            if not hits: continue
            if prefer_max:
                mx=[i for i in hits if "max" in norm(self.hdr[i])]
                hits=mx or hits
            best=hits[0]; break
        if best is None: return None
        v=self.r[best] if best<len(self.r) else None
        return v if v not in ("",None) else None

def load_refs(path,disc):
    s=set()
    if not os.path.exists(path): return s
    for l in open(path):
        try: mi=json.loads(l)["semiconductor"][disc]["manufacturerInfo"]
        except: continue
        for v in (mi.get("reference"),mi.get("datasheetInfo",{}).get("part",{}).get("partNumber")):
            if v: u=str(v).strip().upper(); s.add(u); s.add(u.replace(" ",""))
    return s

def diode_subtype(tech,cfg):
    t=norm(tech)+" "+norm(cfg)
    if "sic" in t and "schottky" in t: return "sicSchottky"
    if "schottky" in t: return "schottky"
    if "ultrafast" in t or "ultra fast" in t: return "ultrafast"
    if "fast" in t: return "fastRecovery"
    return "rectifier"

def emit(rows_iter, build, disc, srcname, tag=None):
    tag=tag or f"{disc}s"
    main=[]; inc=[]
    have=load_refs(f"{DATA}/{disc}s.ndjson",disc); seen=set()
    for row in rows_iter:
        pn=row.get("Part number","partnumber")
        pn=re.sub(r"\s+"," ",str(pn)).strip() if pn else None
        if not pn: continue
        key=pn.upper(); opn=str(row.get("OPN") or "").strip().upper()
        if key in have or key.replace(" ","") in have or (opn and opn in have) or key in seen: continue
        seen.add(key)
        rec,missing = build(row,pn)
        if rec is None: continue
        prov=[{"source":"manufacturerParametric","sourceName":srcname,"retrievedDate":TODAY}]
        ds=row.get("Datasheet link","datasheet") or row.get("Part link","part link")
        if ds and str(ds).startswith("http"):
            prov[0]["sourceUrl"]=str(ds); rec["manufacturerInfo"]["datasheetUrl"]=str(ds)
        rec["manufacturerInfo"]["datasheetInfo"]["provenance"]=prov
        wrapped={"semiconductor":{disc:rec}}
        if missing:
            wrapped["quarantineReason"]=f"incomplete Infineon {disc}; missing {missing} ({TODAY})"
            inc.append(wrapped)
        else: main.append(wrapped)
    for nm,recs in [(f"{tag}.main",main),(f"{tag}.incomplete",inc)]:
        with open(f"{OUT}/{nm}.ndjson","w") as fo:
            for r in recs: fo.write(json.dumps(r,ensure_ascii=False)+"\n")
    print(f"{disc}: main={len(main)} incomplete={len(inc)}  (src {srcname})")

def build_igbt(row,pn):
    vce=val(row.get("Voltage Class","vce","vces"))
    ic=val(row.get("IC (@","ic max","collector current","ic("))
    vcesat=val(row.get("VCE(sat)","vce sat","vcesat","saturation voltage"))
    part={"partNumber":pn,"subType":"nChannel","technology":"Si"}
    if row.get("Technology"): part["series"]=re.sub("[™]","",str(row.get("Technology"))).strip()
    if row.get("Package","package name","infineon package"): part["case"]=str(row.get("Package","package name","infineon package")).strip()
    el={}
    if vce is not None: el["collectorEmitterVoltage"]=vce
    if ic is not None: el["continuousCollectorCurrent"]=ic
    if vcesat is not None: el["collectorEmitterSaturation"]=vcesat
    if (v:=val(row.get("Ptot","power"))) is not None: el["powerDissipation"]=v
    if (v:=charge(row.get("QG","gate charge"))) is not None: el["totalGateCharge"]=v
    missing=[k for k in ("collectorEmitterVoltage","collectorEmitterSaturation","continuousCollectorCurrent") if k not in el]
    rec={"manufacturerInfo":{"name":"Infineon","reference":pn,"status":"production","datasheetInfo":{"part":part,"electrical":el}}}
    return rec, (",".join(missing) if missing else None)

def build_diode(row,pn):
    st=diode_subtype(row.get("Technology"),row.get("Configuration"))
    part={"partNumber":pn,"subType":st,"technology":"SiC" if st=="sicSchottky" or "sic" in norm(row.get("Technology")) else "Si"}
    if row.get("Technology"): part["series"]=re.sub("[™]","",str(row.get("Technology"))).strip()
    if row.get("Package","package name"): part["case"]=str(row.get("Package","package name")).strip()
    el={}
    if (v:=val(row.get("VRRM","reverse voltage","voltage class","vr max","repetitive peak reverse"))) is not None: el["reverseVoltage"]=v
    if (v:=val(row.get("VF","forward voltage"))) is not None: el["forwardVoltage"]=v
    if (v:=val(row.get("IF (av)","ifav","if max","if(av)","forward current","if "))) is not None: el["forwardCurrent"]=v
    if (v:=val(row.get("IFSM","surge current"))) is not None: el["surgeCurrent"]=v
    if (v:=val(row.get("IR","reverse leakage","ir max"))) is not None: el["reverseLeakageCurrent"]=v
    missing=[k for k in ("reverseVoltage","forwardVoltage","forwardCurrent") if k not in el]
    rec={"manufacturerInfo":{"name":"Infineon","reference":pn,"status":"production","datasheetInfo":{"part":part,"electrical":el}}}
    return rec, (",".join(missing) if missing else None)

def build_esd(row,pn):
    part={"partNumber":pn,"subType":"esd","technology":"Si"}
    if row.get("Package","infineon package"): part["case"]=str(row.get("Package","infineon package")).strip()
    el={}
    if (v:=val(row.get("VWM","vrwm","working voltage","standoff"))) is not None: el["standoffVoltage"]=v
    if (v:=val(row.get("VESD","esd"))) is not None: el["esdVoltageContact"]=v
    if (v:=val(row.get("Vcl","clamping"))) is not None: el["clampingVoltage"]=v
    if (v:=val(row.get("IPP","peak pulse current"))) is not None: el["peakPulseCurrent"]=v
    if (v:=farads(row.get("CL","capacitance"))) is not None: el["junctionCapacitance"]=v
    pulse_ok = any(k in el for k in ("peakPulseCurrent","esdVoltageContact"))
    missing=[]
    if "standoffVoltage" not in el: missing.append("standoffVoltage")
    if not pulse_ok: missing.append("pulseRating")
    rec={"manufacturerInfo":{"name":"Infineon","reference":pn,"status":"production","datasheetInfo":{"part":part,"electrical":el}}}
    return rec, (",".join(missing) if missing else None)

def rows_of(fname):
    wb=openpyxl.load_workbook(f"{DL}/{fname}",read_only=True); ws=wb.active
    data=list(ws.iter_rows(values_only=True)); hdr=data[0]
    for r in data[1:]: yield Row(hdr,r)

if __name__=="__main__":
    emit(rows_of("IGBT Discrete Finder.xlsx"), build_igbt, "igbt", "Infineon IGBT Discrete Finder (xlsx export)", tag="igbt")
    emit(rows_of("Diode Rectifier Finder.xlsx"), build_diode, "diode", "Infineon Diode Rectifier Finder (xlsx export)", tag="diode_rect")
    emit(rows_of("ESD Protection Finder.xlsx"), build_esd, "diode", "Infineon ESD Protection Finder (xlsx export)", tag="diode_esd")
