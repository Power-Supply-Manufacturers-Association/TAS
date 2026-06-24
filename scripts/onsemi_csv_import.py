#!/usr/bin/env python3
"""onsemi parametric CSV exports -> SAS (mosfet/igbt/bjt/diode[zener/esd/rect]).
MPN = 'Product Group'. Values carry a trailing ', '; '-' / '~NA~' = missing.
Auto-classifies each file by columns; routes records missing a SAS-required field to
<type>.incomplete.ndjson (librarian); skips JFETs (no SAS schema). Stamps provenance.
Run: python3 scripts/onsemi_csv_import.py
"""
import csv, json, re, datetime, os, glob
DL="/mnt/c/Users/Alfonso/Downloads"; OUT="/home/alf/PSMA/TAS/staging/onsemi"; os.makedirs(OUT,exist_ok=True)
DATA="/home/alf/PSMA/TAS/data"; TODAY=datetime.date.today().isoformat()
PROV=[{"source":"manufacturerParametric","sourceName":"onsemi parametric export (CSV)","retrievedDate":TODAY}]
def norm(s): return re.sub(r"\s+"," ",str(s or "").lower()).strip()
def clean(v):
    if v is None: return None
    s=str(v).strip().rstrip(",").strip()
    if s in ("","-","~NA~","NA","N/A"): return None
    return s
def num(v):
    s=clean(v)
    if s is None: return None
    m=re.search(r"[-+]?\d*\.?\d+",s.replace("±","").replace(",",""))
    return float(m.group()) if m else None
def status_of(s):
    s=norm(s)
    if "obsolet" in s or "last shipment" in s or "lifetime" in s: return "obsolete"
    return "production"

class Row:
    def __init__(s,hdr,r): s.h={norm(h):i for i,h in enumerate(hdr)}; s.r=r; s.hdr=hdr
    def get(s,*frags,prefer_max=True):
        best=None
        for fr in frags:
            f=norm(fr); hits=[i for hn,i in s.h.items() if f in hn]
            if not hits: continue
            if prefer_max:
                mx=[i for i in hits if "max" in norm(s.hdr[i])]; hits=mx or hits
            best=hits[0]; break
        return clean(s.r[best]) if best is not None and best<len(s.r) else None
    def n(s,*frags,**k):
        v=s.get(*frags,**k); return num(v) if v is not None else None

def classify(cols):
    c=[norm(x) for x in cols]; has=lambda *f:any(any(x in col for col in c) for x in f)
    if has("idss","v(br)gss"): return "jfet"
    if has("hfe","vcbo","vebo","vceo(sus)"): return "bjt"
    if has("vce(sat)","vcesat") and has("v(br)ces","vces"): return "igbt"
    if has("vgs(th)","rds(on)","rds (on)","rdson","bvdss","v(br)dss","drain source","vds(max)","blocking voltage"): return "mosfet"
    if has("vz typ","vz min","vz max") or any(col=="vz typ (v)" for col in c): return "zener"
    if has("vrwm") or has("interface") or has("number of lines") or has("ppk"): return "esd"
    if has("vrrm","vr min","vr max","vfm","io(rec)","if(ave)") or any(col.startswith("vf") for col in c): return "rect"
    return "?"

def mosfet(r,pn):
    pol=norm(r.get("Channel Polarity","polarity")); fam=norm(r.get("Silicon Family","family","type"))
    tech="SiC" if "sic" in fam else ("GaN" if "gan" in fam else "Si")
    part={"partNumber":pn,"subType":"pChannel" if "p-channel" in pol else "nChannel","technology":tech}
    if clean(r.get("Package Type","package")): part["case"]=clean(r.get("Package Type","package"))
    el={}
    vds=r.n("v(br)dss","bvdss","blocking voltage","drain source voltage","vds(max)","vds max")
    rds=r.n("rds(on) max @ vgs = 10","rds(on) typ @ 25","rds(on) max","rds(on) typ","typical rds(on)","rds(on)","rdson")
    idc=r.n("id max","id(peak)","id(max)","id typ","id (a)","id ")
    vth=r.n("vgs(th) max","vgs(th)","vth")
    qg=r.n("qg typ @ vgs = 10","qg total","qg (nc)","qoss typ","qg")
    if vds is not None: el["drainSourceVoltage"]=vds
    if rds is not None: el["onResistance"]=rds*1e-3; el["onResistanceVgs"]=10
    if idc is not None: el["continuousDrainCurrent"]=idc
    if vth is not None: el["gateThresholdVoltage"]={"maximum":vth}
    if qg is not None: el["totalGateCharge"]=round(qg*1e-9,12)
    if (v:=r.n("pd max","ptot")) is not None: el["powerDissipation"]=v
    miss=[k for k in("drainSourceVoltage","onResistance","continuousDrainCurrent","gateThresholdVoltage","totalGateCharge") if k not in el]
    return ("mosfet",part,el,miss)

def igbt(r,pn):
    part={"partNumber":pn,"subType":"nChannel","technology":"Si"}
    if clean(r.get("Package Type","package")): part["case"]=clean(r.get("Package Type"))
    el={}
    if (v:=r.n("v(br)ces","vces","vce ")) is not None: el["collectorEmitterVoltage"]=v
    if (v:=r.n("ic max","ic cont","ic continuous","ic (a)")) is not None: el["continuousCollectorCurrent"]=v
    if (v:=r.n("vce(sat)","vcesat")) is not None: el["collectorEmitterSaturation"]=v
    if (v:=r.n("gate charge","qg")) is not None: el["totalGateCharge"]=round(v*1e-9,12)
    if (v:=r.n("eon")) is not None: el["turnOnEnergy"]=v*1e-3
    if (v:=r.n("eoff")) is not None: el["turnOffEnergy"]=v*1e-3
    if (v:=r.n("pd max","ptot")) is not None: el["powerDissipation"]=v
    miss=[k for k in("collectorEmitterVoltage","collectorEmitterSaturation","continuousCollectorCurrent") if k not in el]
    return ("igbt",part,el,miss)

def bjt(r,pn):
    pol=norm(r.get("Polarity","channel polarity"))
    part={"partNumber":pn,"subType":"pnp" if "pnp" in pol else "npn","technology":"Si"}
    if clean(r.get("Package Type")): part["case"]=clean(r.get("Package Type"))
    el={}
    if (v:=r.n("vceo(sus)","v(br)ceo","vceo")) is not None: el["collectorEmitterVoltage"]=v
    if (v:=r.n("ic continuous","ic cont","ic (a)")) is not None: el["collectorCurrent"]=v
    if (v:=r.n("vcbo")) is not None: el["collectorBaseVoltage"]=v
    if (v:=r.n("hfe min","hfe")) is not None:
        el["dcCurrentGain"]={"minimum":v}
        if (vx:=r.n("hfe max")) is not None: el["dcCurrentGain"]["maximum"]=vx
    if (v:=r.n("vce(sat)","vcesat")) is not None: el["saturationVoltage"]=v
    if (v:=r.n("ft min","ft")) is not None: el["transitionFrequency"]=v*1e6
    if (v:=r.n("ptm max","pd max")) is not None: el["powerDissipation"]=v
    miss=[k for k in("collectorEmitterVoltage","collectorCurrent") if k not in el]
    return ("bjt",part,el,miss)

def diode(r,pn,kind):
    cfg=norm(r.get("Type","configuration","family"))
    if kind=="zener": st="zener"
    elif kind=="esd": st="esd"
    elif "sic" in cfg and "schottky" in cfg: st="sicSchottky"
    elif "schottky" in cfg: st="schottky"
    elif "ultrafast" in cfg or "ultra fast" in cfg: st="ultrafast"
    elif "fast" in cfg: st="fastRecovery"
    else: st="rectifier"
    part={"partNumber":pn,"subType":st,"technology":"SiC" if "sic" in cfg else "Si"}
    if clean(r.get("Package Type")): part["case"]=clean(r.get("Package Type"))
    el={}
    if (v:=r.n("vrrm","vr min","vr max","reverse voltage")) is not None: el["reverseVoltage"]=v
    if (v:=r.n("vf (max)","vfm","vf max","vf typ","vf ")) is not None: el["forwardVoltage"]=v
    if (v:=r.n("if(ave)","io(rec)","if max","if (a)")) is not None: el["forwardCurrent"]=v
    if (v:=r.n("ifsm")) is not None: el["surgeCurrent"]=v
    irc=r.get("ir (max)","irm","ir max","ir ");
    if irc is not None:
        iv=num(irc); u=norm(irc)
        if iv is not None: el["reverseLeakageCurrent"]=iv*(1e-6 if ("µa" in u or "ua" in u) else (1e-3 if "ma" in u else 1e-6))
    if (v:=r.n("trr")) is not None: el["reverseRecoveryTime"]=v*1e-9
    if (v:=r.n("cd max","cj","c max")) is not None: el["junctionCapacitance"]=v*1e-12
    if (v:=r.n("vz typ","vz")) is not None: el["breakdownVoltage"]={"nominal":v}
    if (v:=r.n("p max","pd max")) is not None: el["powerDissipation"]=v
    if (v:=r.n("vrwm")) is not None: el["standoffVoltage"]=v
    if (v:=r.n("ppk")) is not None: el["peakPulsePower"]=v
    if (v:=r.n("v(br) min","v(br)")) is not None and st in ("esd","zener","tvs"): el.setdefault("breakdownVoltage",{"minimum":v})
    if st=="zener": miss=[k for k in("breakdownVoltage","powerDissipation") if k not in el]
    elif st=="esd":
        miss=([ "standoffVoltage"] if "standoffVoltage" not in el else [])+(["pulseRating"] if not any(k in el for k in("peakPulseCurrent","peakPulsePower","esdVoltageContact")) else [])
    else: miss=[k for k in("reverseVoltage","forwardVoltage","forwardCurrent") if k not in el]
    return ("diode",part,el,miss)

def load_have(disc):
    have=set()
    p=f"{DATA}/{disc}s.ndjson"
    if not os.path.exists(p): return have
    for l in open(p):
        try: mi=json.loads(l)["semiconductor"][disc]["manufacturerInfo"]
        except: continue
        for v in (mi.get("reference"),mi.get("datasheetInfo",{}).get("part",{}).get("partNumber")):
            if v: have.add(str(v).strip().upper())
    return have

def main():
    files=["parametrics.csv"]+[f"parametrics ({i}).csv" for i in range(1,28)]
    haves={d:load_have(d) for d in("mosfet","igbt","bjt","diode")}
    buckets={}  # tag -> list
    seen=set(); summary={}
    for f in files:
        p=f"{DL}/{f}"
        if not os.path.exists(p): continue
        with open(p,encoding="utf-8-sig") as fh:
            rd=csv.reader(fh); hdr=next(rd); rows=list(rd)
        kind=classify(hdr); summary[f]=(kind,len(rows))
        if kind in("jfet","?"): continue
        for raw in rows:
            r=Row(hdr,raw); pn=clean(r.get("Product Group","product group"))
            if not pn: continue
            if kind=="mosfet": disc,part,el,miss=mosfet(r,pn)
            elif kind=="igbt": disc,part,el,miss=igbt(r,pn)
            elif kind=="bjt": disc,part,el,miss=bjt(r,pn)
            else: disc,part,el,miss=diode(r,pn,kind)
            key=(disc,pn.upper())
            if key in seen or pn.upper() in haves[disc]: continue
            seen.add(key)
            mi={"name":"onsemi","reference":pn,"status":status_of(r.get("Status","status")),
                "datasheetInfo":{"part":part,"electrical":el,"provenance":PROV}}
            rec={"semiconductor":{disc:{"manufacturerInfo":mi}}}
            tag=f"{disc}.{'incomplete' if miss else 'main'}"
            buckets.setdefault(tag,[]).append((rec,miss))
    for tag,recs in buckets.items():
        with open(f"{OUT}/{tag}.ndjson","w") as fo:
            for rec,miss in recs:
                if miss: rec=dict(rec); rec["quarantineReason"]=f"incomplete onsemi; missing {','.join(miss)} ({TODAY})"
                fo.write(json.dumps(rec,ensure_ascii=False)+"\n")
    print("classification:", {f:summary[f] for f in summary})
    print("buckets:", {t:len(v) for t,v in sorted(buckets.items())})

if __name__=="__main__": main()
