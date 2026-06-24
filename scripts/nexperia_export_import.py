#!/usr/bin/env python3
"""Nexperia parametric 'Download' exports (.xls -> libreoffice .csv) -> SAS.
Auto-classifies by columns (bjt/mosfet/diode/igbt). MPN='Type number'. NEW PNs only;
missing-required -> librarian. Stamps provenance. Pass converted CSV paths as argv.
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
    def g(s,*fr):
        for f in fr:
            fn=norm(f)
            for hn,i in s.h.items():
                if fn==hn or fn in hn:
                    if i<len(s.r) and str(s.r[i]).strip() not in ("","-"): return s.r[i]
        return None
    def n(s,*fr): return num(s.g(*fr))
def classify(cols):
    c=[norm(x) for x in cols]; has=lambda *f:any(any(x in col for col in c) for x in f)
    if has("hfe") or has("vceo") or (has("polarity") and has("vces") and not has("vge")): return "bjt"
    if has("vds","rds","vgs(th)","bvdss"): return "mosfet"
    if has("vce(sat)","vces") and has("vge"): return "igbt"
    if has("vrrm","vf","if(av)","reverse voltage"): return "diode"
    return "bjt" if has("vces") else "?"
def header_rows(path):
    rows=list(csv.reader(open(path)))
    hi=next((i for i,r in enumerate(rows) if 'type number' in [norm(c) for c in r]),None)
    if hi is None: return None,None
    return rows[hi],[r for r in rows[hi+1:] if any(c.strip() for c in r)]
def load_have(disc):
    s=set(); p=f"{DATA}/{disc}s.ndjson"
    if not os.path.exists(p): return s
    for l in open(p):
        try: mi=json.loads(l)["semiconductor"][disc]["manufacturerInfo"]
        except: continue
        for v in (mi.get("reference"),mi.get("datasheetInfo",{}).get("part",{}).get("partNumber")):
            if v: s.add(str(v).strip().upper())
    return s
def build_bjt(r,pn):
    pol=norm(r.g("Polarity"))
    part={"partNumber":pn,"subType":"pnp" if "pnp" in pol else "npn","technology":"Si"}
    if r.g("Package name","package version"): part["case"]=str(r.g("Package name","package version")).strip()
    el={}
    if (v:=r.n("VCES [max] (V)","vces","vceo")) is not None: el["collectorEmitterVoltage"]=v
    if (v:=r.n("IC [max] (mA)","ic [max]","ic ")) is not None: el["collectorCurrent"]=v*1e-3
    g={}
    if (v:=r.n("hFE [min]")) is not None: g["minimum"]=v
    if (v:=r.n("hFE [max]")) is not None: g["maximum"]=v
    if g: el["dcCurrentGain"]=g
    if (v:=r.n("Ptot [max] (mW)","ptot")) is not None: el["powerDissipation"]=v*1e-3
    if (v:=r.n("fT [min] (MHz)","ft")) is not None: el["transitionFrequency"]=v*1e6
    miss=[k for k in("collectorEmitterVoltage","collectorCurrent") if k not in el]
    return part,el,miss
BUILD={"bjt":build_bjt}
def main():
    haves={d:load_have(d) for d in("bjt","mosfet","diode","igbt")}; seen=set(); buckets={}
    for path in sys.argv[1:]:
        hdr,rows=header_rows(path)
        if not hdr: print("no header:",path); continue
        disc=classify(hdr)
        if disc not in BUILD: print(f"{path}: type {disc} not yet mapped"); continue
        for raw in rows:
            r=Row(hdr,raw); pn=(r.g("Type number") or "").strip()
            if not pn: continue
            part,el,miss=BUILD[disc](r,pn)
            if (disc,pn.upper()) in seen or pn.upper() in haves[disc]: continue
            seen.add((disc,pn.upper()))
            mi={"name":"Nexperia","reference":pn,"status":"production","datasheetInfo":{"part":part,"electrical":el,"provenance":PROV}}
            ds=r.g("Datasheet")
            if ds and str(ds).startswith("http"): mi["datasheetUrl"]=str(ds)
            buckets.setdefault(f"{disc}.{'incomplete' if miss else 'main'}",[]).append((( {"semiconductor":{disc:{"manufacturerInfo":mi}}}),miss))
    for tag,recs in buckets.items():
        with open(f"{OUT}/{tag}.ndjson","w") as fo:
            for rec,miss in recs:
                if miss: rec=dict(rec); rec["quarantineReason"]=f"incomplete Nexperia; missing {','.join(miss)} ({TODAY})"
                fo.write(json.dumps(rec,ensure_ascii=False)+"\n")
    print("buckets:",{t:len(v) for t,v in sorted(buckets.items())})
if __name__=="__main__": main()
