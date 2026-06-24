#!/usr/bin/env python3
"""Nexperia parametric 'Download' exports (.xls -> libreoffice .csv) -> SAS.
Auto-classifies per file (mosfet/diode[rect/zener/sicSchottky]/igbt/bjt). MPN='Type number'.
Duplicate column names (a 'Complement' block repeats cols) -> keep FIRST occurrence.
Missing-required -> <type>.incomplete (librarian). Provenance stamped. argv = csv paths.
"""
import csv, json, re, datetime, os, sys
DATA="/home/alf/PSMA/TAS/data"; OUT="/home/alf/PSMA/TAS/staging/nexperia"; os.makedirs(OUT,exist_ok=True)
TODAY=datetime.date.today().isoformat()
PROV=[{"source":"manufacturerParametric","sourceName":"Nexperia parametric export (Download .xls)","retrievedDate":TODAY}]
def norm(s): return re.sub(r"\s+"," ",str(s or "").lower()).strip()
def num(v):
    if v in (None,""): return None
    m=re.search(r"[-+]?\d*\.?\d+",str(v).replace(">","").replace("<","").replace(",",""))
    return float(m.group()) if m else None
class Row:
    def __init__(s,h,r):
        s.h={}; [s.h.setdefault(norm(x),i) for i,x in enumerate(h)]; s.r=r; s.hd=h
    def find(s,*fr):
        for f in fr:
            fn=norm(f)
            for hn,i in s.h.items():
                if fn==hn or fn in hn:
                    if i<len(s.r) and str(s.r[i]).strip() not in ("","-"): return s.r[i],s.hd[i]
        return None,None
    def g(s,*fr): return s.find(*fr)[0]
    def n(s,*fr): v=s.find(*fr)[0]; return num(v) if v is not None else None

def classify(cols):
    c=[norm(x) for x in cols]; has=lambda *f:any(any(x in col for col in c) for x in f)
    if has("hfe"): return "bjt"
    if has("vgsth","rdson","drain-source on-state","drain-source breakdown") or any("vds [max]" in x for x in c): return "mosfet"
    if has("vz [nom]","vz [min]","vrrm","vr [max]","vf [max]","if(av)"): return "diode"
    if has("vce [max]") or any("tsc [" in x for x in c): return "igbt"
    if has("vceo"): return "bjt"
    return "?"

def load_have(disc):
    s=set(); p=f"{DATA}/{disc}s.ndjson"
    if not os.path.exists(p): return s
    for l in open(p):
        try: mi=json.loads(l)["semiconductor"][disc]["manufacturerInfo"]
        except: continue
        for v in (mi.get("reference"),mi.get("datasheetInfo",{}).get("part",{}).get("partNumber")):
            if v: s.add(str(v).strip().upper())
    return s

def build_mosfet(r,pn,fc):
    ct=norm(r.g("Channel type")); sic=any("breakdown" in norm(x) or "at 15 v" in norm(x) or "at 18 v" in norm(x) for x in fc)
    tech="SiC" if sic else ("GaN" if pn.upper().startswith("GAN") else "Si")
    part={"partNumber":pn,"subType":"pChannel" if ct.startswith("p") else "nChannel","technology":tech}
    if r.g("Package name","package version"): part["case"]=str(r.g("Package name","package version")).strip()
    el={}
    if (v:=r.n("VDS [max] (V)","drain-source breakdown voltage")) is not None: el["drainSourceVoltage"]=v
    rv,rl=r.find("RDSon [max] @ VGS = 10","drain-source on-state resistance at 18","drain-source on-state resistance at 15","RDSon [max] @ VGS = 6","RDSon [max] @ VGS = 5","RDSon [max]")
    if rv is not None and num(rv) is not None:
        el["onResistance"]=num(rv)*1e-3
        m=re.search(r"(\d+)\s*v",norm(rl) or ""); el["onResistanceVgs"]=int(m.group(1)) if m else 10
    if (v:=r.n("ID [max] (A)","id [max]")) is not None: el["continuousDrainCurrent"]=v
    if (v:=r.n("VGSth")) is not None: el["gateThresholdVoltage"]={"nominal":v}
    if (v:=r.n("QG(tot) [typ] @ VGS = 10","QG(tot) [typ]")) is not None: el["totalGateCharge"]=round(v*1e-9,12)
    if (v:=r.n("Ptot [max] (W)")) is not None: el["powerDissipation"]=v
    miss=[k for k in("drainSourceVoltage","onResistance","continuousDrainCurrent","gateThresholdVoltage","totalGateCharge") if k not in el]
    return ("mosfet",part,el,miss)

def build_diode(r,pn,fc):
    zener = r.find("VZ [nom] (V)","vz [nom]")[0] is not None
    sic = any("vrrm" in norm(x) for x in fc) and (r.find("VF [max] (V)")[0] is not None)
    st="zener" if zener else ("sicSchottky" if sic else "rectifier")
    part={"partNumber":pn,"subType":st,"technology":"SiC" if sic else "Si"}
    if r.g("Package name"): part["case"]=str(r.g("Package name")).strip()
    el={}
    vf,vfl=r.find("VF [max] (mV)","VF [max] (V)","VF [max]")
    if vf is not None and num(vf) is not None: el["forwardVoltage"]=num(vf)*(1e-3 if "mv" in norm(vfl) else 1)
    if (v:=r.n("VRRM [max] (V)","VR [max] (V)")) is not None: el["reverseVoltage"]=v
    ifc,ifl=r.find("IF(AV) per diode","IF(AV)","@IF [max]","IF [max] (mA)","IF [max] (A)")
    if ifc is not None and num(ifc) is not None: el["forwardCurrent"]=num(ifc)*(1e-3 if "ma" in norm(ifl) else 1)
    if (v:=r.n("IFSM [max] (A)")) is not None: el["surgeCurrent"]=v
    irc,irl=r.find("IR [max] (µA)","IR [max] (nA)","IR [max]")
    if irc is not None and num(irc) is not None: el["reverseLeakageCurrent"]=num(irc)*(1e-9 if "na" in norm(irl) else 1e-6)
    if (v:=r.n("trr [max] (ns)")) is not None: el["reverseRecoveryTime"]=v*1e-9
    if (v:=r.n("Cd [typ] (pF)")) is not None: el["junctionCapacitance"]=v*1e-12
    if zener:
        bv={}
        if (v:=r.n("VZ [nom]")) is not None: bv["nominal"]=v
        if (v:=r.n("VZ [min]")) is not None: bv["minimum"]=v
        if (v:=r.n("VZ [max]")) is not None: bv["maximum"]=v
        if bv: el["breakdownVoltage"]=bv
        pv,pl=r.find("Ptot (mW)","PZSM (W)","Ptot")
        if pv is not None and num(pv) is not None: el["powerDissipation"]=num(pv)*(1e-3 if "mw" in norm(pl) else 1)
    if zener: miss=[k for k in("breakdownVoltage","powerDissipation") if k not in el]
    else: miss=[k for k in("reverseVoltage","forwardVoltage","forwardCurrent") if k not in el]
    return ("diode",part,el,miss)

def build_igbt(r,pn,fc):
    part={"partNumber":pn,"subType":"nChannel","technology":"Si"}
    if r.g("Package name"): part["case"]=str(r.g("Package name")).strip()
    el={}
    if (v:=r.n("VCE [max] (V)","vces")) is not None: el["collectorEmitterVoltage"]=v
    if (v:=r.n("IC [typ] (A)","ic [max]")) is not None: el["continuousCollectorCurrent"]=v
    if (v:=r.n("vce(sat)")) is not None: el["collectorEmitterSaturation"]=v
    miss=[k for k in("collectorEmitterVoltage","collectorEmitterSaturation","continuousCollectorCurrent") if k not in el]
    return ("igbt",part,el,miss)

def build_bjt(r,pn,fc):
    pol=norm(r.g("Polarity","channel type (e)","channel type"))
    part={"partNumber":pn,"subType":"pnp" if "pnp" in pol or pol.startswith("p") else "npn","technology":"Si"}
    if r.g("Package name"): part["case"]=str(r.g("Package name")).strip()
    el={}
    if (v:=r.n("VCES [max] (V)","VCEO [max] (V)","vces","vceo")) is not None: el["collectorEmitterVoltage"]=v
    if (v:=r.n("IC [max] (mA)","ic [max]")) is not None: el["collectorCurrent"]=v*1e-3
    g={}
    if (v:=r.n("hFE [min]")) is not None: g["minimum"]=v
    if (v:=r.n("hFE [max]")) is not None: g["maximum"]=v
    if g: el["dcCurrentGain"]=g
    if (v:=r.n("Ptot (mW)","Ptot [max] (mW)")) is not None: el["powerDissipation"]=v*1e-3
    if (v:=r.n("fT [min] (MHz)")) is not None: el["transitionFrequency"]=v*1e6
    miss=[k for k in("collectorEmitterVoltage","collectorCurrent") if k not in el]
    return ("bjt",part,el,miss)

BUILD={"mosfet":build_mosfet,"diode":build_diode,"igbt":build_igbt,"bjt":build_bjt}
def header_rows(path):
    rows=list(csv.reader(open(path)))
    hi=next((i for i,r in enumerate(rows) if 'type number' in [norm(c) for c in r]),None)
    if hi is None: return None,None
    return [c for c in rows[hi]],[r for r in rows[hi+1:] if any(c.strip() for c in r)]

def main():
    haves={d:load_have(d) for d in("bjt","mosfet","diode","igbt")}; seen=set(); buckets={}
    for path in sys.argv[1:]:
        hdr,rows=header_rows(path)
        if not hdr: continue
        disc=classify(hdr)
        if disc not in BUILD: print(f"{os.path.basename(path)}: unmapped({disc})"); continue
        for raw in rows:
            r=Row(hdr,raw); pn=(r.g("Type number") or "").strip()
            if not pn: continue
            d,part,el,miss=BUILD[disc](r,pn,hdr)
            if (d,pn.upper()) in seen or pn.upper() in haves[d]: continue
            seen.add((d,pn.upper()))
            mi={"name":"Nexperia","reference":pn,"status":"production","datasheetInfo":{"part":part,"electrical":el,"provenance":PROV}}
            ds=r.g("Datasheet")
            if ds and str(ds).startswith("http"): mi["datasheetUrl"]=str(ds)
            buckets.setdefault(f"{d}.{'incomplete' if miss else 'main'}",[]).append(({"semiconductor":{d:{"manufacturerInfo":mi}}},miss))
    for tag,recs in buckets.items():
        with open(f"{OUT}/{tag}.ndjson","w") as fo:
            for rec,miss in recs:
                if miss: rec=dict(rec); rec["quarantineReason"]=f"incomplete Nexperia; missing {','.join(miss)} ({TODAY})"
                fo.write(json.dumps(rec,ensure_ascii=False)+"\n")
    print("buckets:",{t:len(v) for t,v in sorted(buckets.items())})

if __name__=="__main__": main()
