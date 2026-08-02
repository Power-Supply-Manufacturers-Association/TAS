#!/usr/bin/env python3
"""onsemi parametric CSV exports -> SAS (mosfet/igbt/bjt/diode[zener/esd/rect]).
MPN = 'Product Group'. Values carry a trailing ', '; '-' / '~NA~' = missing.
Auto-classifies each file by columns; routes records missing a SAS-required field to
<type>.incomplete.ndjson (librarian); skips JFETs (no SAS schema). Stamps provenance.
Run: python3 scripts/onsemi_csv_import.py
"""
import csv, json, re, datetime, os, glob, sys
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
def mohms(v):
    """'95' / 'Q1=Q2=95' / 'Q1: 62.0, Q2: 62.0' -> ohms. onsemi publishes a
    multi-channel r_DS(on) with the channel labels INLINE, and num()'s bare-number
    regex takes the '1' out of 'Q1': FDC6561AN's 95 mOhm was stored as 1 mOhm, and
    NTJD5121NT1G's 1.6 Ohm too, which is what dragged their honest gate charges
    into ABT #512's net. Strip the labels first, and refuse the cell when the
    channels disagree -- there is no single onResistance for the record then."""
    v=clean(v)
    if v is None: return None
    vals={float(x) for x in re.findall(r"[-+]?\d*\.?\d+",re.sub(r"[Qq]\d+\s*[:=]"," ",v))}
    return vals.pop()*1e-3 if len(vals)==1 else None
def gate_charge(r):
    """The row's total gate charge in coulombs, or None when the export's own
    neighbours contradict the column. "Qg Typ @ VGS = 10 V (nC)" is not always a
    TOTAL gate charge: for NTTFS4C05NTAG it reads 3 nC beside a Qgd of 5.5 nC and
    a 4.5 V Qg of 8.4 nC -- it is the gate-SOURCE charge under a total-gate-charge
    heading (ABT #512). Q_gd < Q_g, and Q_g >= 0.4*Ciss*10 V (Q_g is the integral
    of C_iss over the drive, and C_iss is quoted near its minimum, so 0.4 leaves a
    2.5x allowance). A row that fails either yields no gate charge and goes to the
    librarian; it never yields a guess."""
    qg=r.n("qg typ @ vgs = 10","qg total","qg (nc)")
    if qg is None: return None
    qgd=r.n("qgd typ")
    if qgd is not None and qgd>=qg: return None
    ciss=r.n("ciss typ")
    if ciss is not None and qg*1e-9 < 0.4*ciss*1e-12*10.0: return None
    return round(qg*1e-9,12)

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
    def exact(s,*names):
        """The named column and no other. get() matches by SUBSTRING, so asking it
        for "Type" also answers with "Package Type" or "MSL Type" -- fine for a
        numeric field nobody else is named like, fatal for the column that decides
        what the device IS (ABT #523)."""
        for nm in names:
            i=s.h.get(norm(nm))
            if i is not None and i<len(s.r): return clean(s.r[i])
        return None

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

# The columns that NAME a MOSFET's technology, by exact header. "Family" is NOT one of
# them: onsemi's EliteSiC export files it as a die generation (M1/M2/M3S/M3P), exactly as
# its SiC-diode export files D1/D2/D3 -- a generation code says nothing about the material.
MOSFET_TECH_COLS=("Silicon Family","Technology","Device Technology")

def mosfet(r,pn):
    """The technology comes from a column that NAMES it, by exact header (ABT #523).
    The diode half of this same read was the ticket: get() matches by SUBSTRING, so the
    lookup for "type" answered with "Package Type" and the device's technology was decided
    by its package name. onsemi's SiC-MOSFET exports (UF3C/UG3SC/UG4SC/NTBG, 227 rows)
    carry no technology column at all, so all of them would be asserted "Si". No column
    naming it means no technology and the row goes to the librarian -- part.technology is
    SAS-required, so a guess is the only way such a row could ever reach the catalogue."""
    pol=norm(r.get("Channel Polarity","polarity")); fam=norm(r.exact(*MOSFET_TECH_COLS))
    tech=("SiC" if "sic" in fam else "GaN" if "gan" in fam else "Si") if fam else None
    part={"partNumber":pn,"subType":"pChannel" if "p-channel" in pol else "nChannel"}
    if tech: part["technology"]=tech
    if clean(r.get("Package Type","package")): part["case"]=clean(r.get("Package Type","package"))
    el={}
    vds=r.n("v(br)dss","bvdss","blocking voltage","drain source voltage","vds(max)","vds max")
    rds=mohms(r.get("rds(on) max @ vgs = 10","rds(on) typ @ 25","rds(on) max","rds(on) typ","typical rds(on)","rds(on)","rdson"))
    idc=r.n("id max","id(peak)","id(max)","id typ","id (a)","id ")
    vth=r.n("vgs(th) max","vgs(th)","vth")
    qg=gate_charge(r)
    if vds is not None: el["drainSourceVoltage"]=vds
    if rds is not None: el["onResistance"]=rds; el["onResistanceVgs"]=10
    if idc is not None: el["continuousDrainCurrent"]=idc
    if vth is not None: el["gateThresholdVoltage"]={"maximum":vth}
    if qg is not None: el["totalGateCharge"]=qg
    if (v:=r.n("pd max","ptot")) is not None: el["powerDissipation"]=v
    miss=[k for k in("drainSourceVoltage","onResistance","continuousDrainCurrent","gateThresholdVoltage","totalGateCharge") if k not in el]
    if tech is None: miss=miss+["part.technology"]
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

# The words a diode export uses to state what the device IS, most specific first,
# with the (subType, technology) each one means. Nothing outside this vocabulary
# classifies a row -- a package name, a die generation ("D2") or a package
# configuration ("Single", "Common Cathode", "Bridge") says nothing about the
# technology and must not be read as one.
DIODE_TECH=(("sic schottky","sicSchottky","SiC"),("schottky","schottky","Si"),
            ("sbd","schottky","Si"),                     # onsemi writes RF Schottkys "RF-SBD"
            ("ultrafast","ultrafast","Si"),("ultra fast","ultrafast","Si"),
            ("ultrasoft","ultrafast","Si"),("fast recovery","fastRecovery","Si"),
            ("standard recovery","rectifier","Si"),("rectifier","rectifier","Si"))

def diode(r,pn,kind):
    """subType/technology come from a column that NAMES the device, and nothing else.
    ABT #523: this read `r.get("Type","configuration","family")`, and get() matches by
    SUBSTRING -- onsemi's SiC-diode export has no Type column (its Family is a die
    generation D1/D2/D3, its Configuration is Single/Common Cathode), so the lookup
    answered with "Package Type" and the `else` branch asserted technology "Si",
    subType "rectifier" for all 117 rows of the silicon-carbide portfolio. Kelvin's
    DiodeRow.technology IS part.subType, so a 1200 V SiC Schottky reached the
    cross-reference ranker indistinguishable from a mains rectifier and no
    technology-change note could ever fire. An export that does not state the
    technology no longer gets one guessed: the row carries neither field, and `miss`
    routes it to the librarian."""
    cfg=norm(r.exact("Type","Device Type","Diode Type","Technology"))
    hit=next((h for h in DIODE_TECH if h[0] in cfg),None)
    st,tech=(hit[1],hit[2]) if hit else (None,None)
    # zener/esd are pinned by the export's own columns (classify()), and a zener or
    # clamp junction is silicon by construction -- there is no SiC one to confuse it with.
    if kind=="zener": st,tech="zener","Si"
    elif kind=="esd": st,tech="esd","Si"
    part={"partNumber":pn}
    if st: part["subType"]=st
    if tech: part["technology"]=tech
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
    # part.technology is SAS-required and is never invented; without it the row is
    # incomplete, not silicon.
    if tech is None: miss=miss+["part.technology"]
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
    sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
    from blade_gate import BladeGate   # raises if unbuilt: a gate that cannot run is not a gate
    files=["parametrics.csv"]+[f"parametrics ({i}).csv" for i in range(1,28)]
    haves={d:load_have(d) for d in("mosfet","igbt","bjt","diode")}
    gates={d:BladeGate(("semiconductor",d)) for d in haves}
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
            # ABT #512: a heading-based mapper is one mislabelled column away from
            # writing a gate-source charge into totalGateCharge, and this converter
            # had no physics gate between it and data/*.ndjson at all.
            tag=f"{disc}.{'incomplete' if miss else 'main'}"
            if not miss:
                ok,why=gates[disc].check(rec["semiconductor"][disc])
                if not ok:
                    rec=dict(rec); rec["quarantineReason"]=f"blade runner IMPOSSIBLE: {why}"
                    tag=f"{disc}.blocked"
            buckets.setdefault(tag,[]).append((rec,miss))
    for tag,recs in buckets.items():
        with open(f"{OUT}/{tag}.ndjson","w") as fo:
            for rec,miss in recs:
                if miss: rec=dict(rec); rec["quarantineReason"]=f"incomplete onsemi; missing {','.join(miss)} ({TODAY})"
                fo.write(json.dumps(rec,ensure_ascii=False)+"\n")
    print("classification:", {f:summary[f] for f in summary})
    print("buckets:", {t:len(v) for t,v in sorted(buckets.items())})
    for d,g in gates.items(): print(f"  {d}: {g.summary()}")

if __name__=="__main__": main()
