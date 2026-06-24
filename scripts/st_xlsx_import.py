#!/usr/bin/env python3
"""STMicroelectronics 'ProductsList' xlsx exports -> SAS (mosfet/igbt/bjt).
ST format: blank preamble, a category-title row, then header (MPN='Part Number').
Device type from the title; RDS(on) unit (Ω vs mΩ) read PER-HEADER. ST MOSFET exports
carry no VGS(th) -> those route to mosfets.quarantine_incomplete (librarian, no fabrication).
Run: python3 scripts/st_xlsx_import.py
"""
import openpyxl, json, re, datetime, os
DL="/mnt/c/Users/Alfonso/Downloads"; OUT="/home/alf/PSMA/TAS/staging/st"; os.makedirs(OUT,exist_ok=True)
DATA="/home/alf/PSMA/TAS/data"; TODAY=datetime.date.today().isoformat()
PROV=[{"source":"manufacturerParametric","sourceName":"STMicroelectronics parametric export (xlsx)","retrievedDate":TODAY}]
def norm(s): return re.sub(r"\s+"," ",str(s or "").lower()).strip()
def clean(v):
    if v is None: return None
    s=str(v).strip()
    if s in ("","-","~NA~","NA","N/A"): return None
    return s
def num(v):
    s=clean(v)
    if s is None: return None
    m=re.search(r"[-+]?\d*\.?\d+",str(s).replace("±","").replace(",",""))
    return float(m.group()) if m else None
def status_of(s):
    s=norm(s)
    if "obsolet" in s or "nrnd" in s or "not recommended" in s or "last" in s: return "obsolete"
    return "production"

class Row:
    def __init__(s,hdr,r): s.hdr=hdr; s.r=r
    def find(s,*frags,prefer=None):
        idxs=[]
        for fr in frags:
            f=norm(fr); hits=[i for i,h in enumerate(s.hdr) if f in norm(h)]
            if hits: idxs=hits; break
        if not idxs: return (None,None)
        if prefer:
            pf=[i for i in idxs if norm(prefer) in norm(s.hdr[i])]; idxs=pf or idxs
        i=idxs[0]
        return (clean(s.r[i]) if i<len(s.r) else None, s.hdr[i])
    def val(s,*f,**k): v,_=s.find(*f,**k); return num(v) if v is not None else None

def classify(title):
    t=norm(title)
    if "mosfet" in t or "powergan" in t: return ("mosfet","SiC" if "sic" in t else ("GaN" if "gan" in t else "Si"),"pChannel" if "p-channel" in t else "nChannel")
    if "igbt" in t: return ("igbt","Si","nChannel")
    if "bipolar" in t or "darlington" in t or "transistor" in t and "power" in t: return ("bjt","Si",None)
    return (None,None,None)

def header_and_title(ws):
    rows=list(ws.iter_rows(values_only=True))
    hi=next((i for i,r in enumerate(rows) if sum(1 for c in r if c is not None)>=5),None)
    if hi is None: return None,None,[]
    title=None
    for j in range(hi-1,-1,-1):
        nz=[c for c in rows[j] if c]
        if len(nz)==1: title=str(nz[0]).strip(); break
    hdr=[("" if c is None else str(c)) for c in rows[hi]]
    return title,[r for r in rows[hi+1:] if any(c is not None for c in r)],hdr

def load_have(disc):
    s=set(); p=f"{DATA}/{disc}s.ndjson"
    if not os.path.exists(p): return s
    for l in open(p):
        try: mi=json.loads(l)["semiconductor"][disc]["manufacturerInfo"]
        except: continue
        for v in (mi.get("reference"),mi.get("datasheetInfo",{}).get("part",{}).get("partNumber")):
            if v: s.add(str(v).strip().upper())
    return s

def build_mosfet(r,pn,tech,sub):
    part={"partNumber":pn,"subType":sub,"technology":tech}
    if clean(r.find("Package")[0]): part["case"]=clean(r.find("Package")[0])
    el={}
    if (v:=r.val("vdss")) is not None: el["drainSourceVoltage"]=v
    rv,rl=r.find("rds(on)",prefer="10v")
    if rv is not None:
        x=num(rv)
        if x is not None: el["onResistance"]=x*1e-3 if "mω" in norm(rl) or "mohm" in norm(rl) else x; el["onResistanceVgs"]=10
    if (v:=r.val("drain current")) is not None: el["continuousDrainCurrent"]=v
    if (v:=r.val("qg")) is not None: el["totalGateCharge"]=round(v*1e-9,12)
    if (v:=r.val("ptot")) is not None: el["powerDissipation"]=v
    miss=[k for k in("drainSourceVoltage","onResistance","continuousDrainCurrent","gateThresholdVoltage","totalGateCharge") if k not in el]
    return part,el,miss

def build_igbt(r,pn,tech,sub):
    part={"partNumber":pn,"subType":"nChannel","technology":"Si"}
    if clean(r.find("Package")[0]): part["case"]=clean(r.find("Package")[0])
    el={}
    if (v:=r.val("vces")) is not None: el["collectorEmitterVoltage"]=v
    if (v:=r.val("ic (a) (@ tc=25","ic (a) (@ tc=100","ic (a)")) is not None: el["continuousCollectorCurrent"]=v
    if (v:=r.val("vce(sat)")) is not None: el["collectorEmitterSaturation"]=v
    if (v:=r.val("qg")) is not None: el["totalGateCharge"]=round(v*1e-9,12)
    if (v:=r.val("eon")) is not None: el["turnOnEnergy"]=v*1e-3
    if (v:=r.val("eoff")) is not None: el["turnOffEnergy"]=v*1e-3
    if (v:=r.val("ptot")) is not None: el["powerDissipation"]=v
    miss=[k for k in("collectorEmitterVoltage","collectorEmitterSaturation","continuousCollectorCurrent") if k not in el]
    return part,el,miss

def build_bjt(r,pn,tech,sub):
    pol=norm(r.find("Transistor Polarity")[0])
    part={"partNumber":pn,"subType":"pnp" if "pnp" in pol else "npn","technology":"Si"}
    if clean(r.find("Package")[0]): part["case"]=clean(r.find("Package")[0])
    el={}
    if (v:=r.val("collector-emitter voltage")) is not None: el["collectorEmitterVoltage"]=v
    if (v:=r.val("collector current")) is not None: el["collectorCurrent"]=v
    g={}
    if (v:=r.val("dc current gain min")) is not None: g["minimum"]=v
    if (v:=r.val("dc current gain max")) is not None: g["maximum"]=v
    if g: el["dcCurrentGain"]=g
    if (v:=r.val("vce(sat)")) is not None: el["saturationVoltage"]=v
    if (v:=r.val("ptot")) is not None: el["powerDissipation"]=v
    miss=[k for k in("collectorEmitterVoltage","collectorCurrent") if k not in el]
    return part,el,miss

BUILD={"mosfet":build_mosfet,"igbt":build_igbt,"bjt":build_bjt}
def main():
    files=["ProductsList.xlsx"]+[f"ProductsList ({i}).xlsx" for i in range(1,15)]
    haves={d:load_have(d) for d in("mosfet","igbt","bjt")}; seen=set(); buckets={}; summ={}
    for f in files:
        p=f"{DL}/{f}"
        if not os.path.exists(p): continue
        wb=openpyxl.load_workbook(p,read_only=True); ws=wb.active
        title,rows,hdr=header_and_title(ws); wb.close()
        disc,tech,sub=classify(title); summ[f]=(disc,title,len(rows))
        if not disc: continue
        for raw in rows:
            r=Row(hdr,raw); pn=clean(r.find("part number")[0])
            if not pn: continue
            part,el,miss=BUILD[disc](r,pn,tech,sub)
            key=(disc,pn.upper())
            if key in seen or pn.upper() in haves[disc]: continue
            seen.add(key)
            mi={"name":"STMicroelectronics","reference":pn,"status":status_of(r.find("Marketing Status")[0]),
                "datasheetInfo":{"part":part,"electrical":el,"provenance":PROV}}
            rec={"semiconductor":{disc:{"manufacturerInfo":mi}}}
            buckets.setdefault(f"{disc}.{'incomplete' if miss else 'main'}",[]).append((rec,miss))
    for tag,recs in buckets.items():
        with open(f"{OUT}/{tag}.ndjson","w") as fo:
            for rec,miss in recs:
                if miss: rec=dict(rec); rec["quarantineReason"]=f"incomplete ST; missing {','.join(miss)} ({TODAY})"
                fo.write(json.dumps(rec,ensure_ascii=False)+"\n")
    print("files:",{f:(summ[f][0],summ[f][2]) for f in summ})
    print("buckets:",{t:len(v) for t,v in sorted(buckets.items())})

if __name__=="__main__": main()
