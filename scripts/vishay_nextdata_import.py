#!/usr/bin/env python3
"""Vishay __NEXT_DATA__ webtableResults exports -> SAS. Each input JSON = {colmap, rows}
(saved from the Vishay parametric grid via the headed MCP). Label-based mapping so one
converter handles mosfet/diode/igbt. MPN='Series'. Missing-required -> librarian. argv=json files.
"""
import json, re, datetime, os, sys
DATA="/home/alf/PSMA/TAS/data"; OUT="/home/alf/PSMA/TAS/staging/vishay"; os.makedirs(OUT,exist_ok=True)
TODAY=datetime.date.today().isoformat()
PROV=[{"source":"manufacturerParametric","sourceName":"Vishay parametric (__NEXT_DATA__ webtable)","retrievedDate":TODAY}]
def num(v):
    if v in (None,""): return None
    if isinstance(v,(int,float)): return float(v)
    m=re.search(r"[-+]?\d*\.?\d+",str(v).replace(",","")); return float(m.group()) if m else None
def norm(s): return re.sub(r"\s+"," ",str(s or "").lower()).strip()
class L:
    def __init__(s,row,cm): s.d={norm(cm.get(k,k)):v for k,v in row.items()}; s.cm=cm
    def g(s,*labels):
        for lb in labels:
            n=norm(lb)
            for k,v in s.d.items():
                if n==k or n in k:
                    if v not in (None,"","-"): return v
        return None
    def n(s,*labels): return num(s.g(*labels))
def classify(cm):
    labs=" ".join(cm.values()).lower()
    if "vds" in labs and ("rds(on)" in labs or "vth" in labs): return "mosfet"
    if "vceo" in labs or "hfe" in labs: return "bjt"
    if "vce" in labs and "ic" in labs: return "igbt"
    if "vr" in labs or "vrrm" in labs or "if(av)" in labs or "vf" in labs: return "diode"
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
def build_mosfet(r,pn):
    ch=norm(r.g("Channel"))
    part={"partNumber":pn,"subType":"pChannel" if ch.startswith("p") else "nChannel","technology":"Si"}
    if r.g("Package"): part["case"]=str(r.g("Package")).strip()
    el={}
    if (v:=r.n("VDS(V)","VDS")) is not None: el["drainSourceVoltage"]=v
    for lab,vgs in [("rDS(on) at 10 V",10),("rDS(on) at 7.5 V",7),("rDS(on) at 6 V",6),("rDS(on) at 4.5 V",4),("rDS(on) at 2.5 V",2)]:
        rv=r.n(lab)
        if rv is not None: el["onResistance"]=rv; el["onResistanceVgs"]=vgs; break
    if (v:=r.n("Drain current (max.)(A)","Drain current")) is not None: el["continuousDrainCurrent"]=v
    if (v:=r.n("Vth V(V)","Vth")) is not None: el["gateThresholdVoltage"]={"nominal":v}
    if (v:=r.n("Qg at 10 V(nC)","Total gate charge")) is not None: el["totalGateCharge"]=round(v*1e-9,12)
    if (v:=r.n("Power dissipation (max.)(W)")) is not None: el["powerDissipation"]=v
    miss=[k for k in("drainSourceVoltage","onResistance","continuousDrainCurrent","gateThresholdVoltage","totalGateCharge") if k not in el]
    return ("mosfet",part,el,miss)
def build_diode(r,pn):
    part={"partNumber":pn,"subType":"rectifier","technology":"Si"}
    if r.g("Package"): part["case"]=str(r.g("Package")).strip()
    el={}
    if (v:=r.n("VRRM","VR","Repetitive peak reverse voltage")) is not None: el["reverseVoltage"]=v
    if (v:=r.n("VF","Forward voltage")) is not None: el["forwardVoltage"]=v
    if (v:=r.n("IF(AV)","IF","Forward current","Average forward current")) is not None: el["forwardCurrent"]=v
    miss=[k for k in("reverseVoltage","forwardVoltage","forwardCurrent") if k not in el]
    return ("diode",part,el,miss)
BUILD={"mosfet":build_mosfet,"diode":build_diode}
def main():
    haves={d:load_have(d) for d in("mosfet","diode","igbt","bjt")}; seen=set(); buckets={}
    for path in sys.argv[1:]:
        d=json.load(open(path)); cm=d["colmap"]; rows=d["rows"]
        disc=classify(cm)
        if disc not in BUILD: print(f"{os.path.basename(path)}: unmapped({disc})"); continue
        for row in rows:
            r=L(row,cm); pn=(r.g("Series") or "").strip()
            if not pn: continue
            dd,part,el,miss=BUILD[disc](r,pn)
            if (dd,pn.upper()) in seen or pn.upper() in haves[dd]: continue
            seen.add((dd,pn.upper()))
            mi={"name":"Vishay","reference":pn,"status":"production","datasheetInfo":{"part":part,"electrical":el,"provenance":PROV}}
            fn=row.get("FILE_NAME")
            if fn: mi["datasheetUrl"]=f"https://www.vishay.com/doc?{fn}"
            buckets.setdefault(f"{dd}.{'incomplete' if miss else 'main'}",[]).append(({"semiconductor":{dd:{"manufacturerInfo":mi}}},miss))
    for tag,recs in buckets.items():
        with open(f"{OUT}/{tag}.ndjson","w") as fo:
            for rec,miss in recs:
                if miss: rec=dict(rec); rec["quarantineReason"]=f"incomplete Vishay; missing {','.join(miss)} ({TODAY})"
                fo.write(json.dumps(rec,ensure_ascii=False)+"\n")
    print("buckets:",{t:len(v) for t,v in sorted(buckets.items())})
if __name__=="__main__": main()
