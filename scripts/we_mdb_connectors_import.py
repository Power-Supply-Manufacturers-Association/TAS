#!/usr/bin/env python3
"""Convert the WE Electromechanical Components Access DB (Connectors table) -> CONAS NDJSON.
Source: /tmp/we_connectors.csv (mdb-export of 'WE - Electromechanical Components.mdb').
The complete WE connector catalog (4,142 parts) with rated current/voltage/pins/pitch/temp/
impedance/contact-R. Relaxed CONAS: partNumber + family + ratedCurrentPerContact required.
"""
import csv, json, re, os
SRC="/tmp/we_connectors.csv"; OUT="/home/alf/PSMA/TAS/staging/we"; os.makedirs(OUT,exist_ok=True)

def num(s):
    if not s: return None
    m=re.search(r"[-+]?\d*\.?\d+",s.replace(",",".")); return float(m.group()) if m else None
def ohms(s):
    v=num(s)
    if v is None: return None
    sl=s.lower()
    if "mohm" in sl or "m ohm" in sl or "mΩ" in sl: return v*1e-3
    if "kohm" in sl: return v*1e3
    if "mohm" in sl: return v*1e6  # (megaohm rare here; mOhm handled above)
    return v
def metres(s):
    v=num(s)
    if v is None: return None
    return v*1e-3 if ("mm" in (s or "").lower()) else v

FAMILY=[("terminal block","terminalBlock"),("board-to-board","boardToBoard"),
        ("wire-to-board","wireToBoard"),("fpc","fpcFfc"),("coaxial","rf"),
        ("circular","circular"),("input/output","dataInterface"),
        ("dc power jack","power"),("led","wireToBoard")]
def family_of(cat):
    c=(cat or "").lower()
    for frag,fam in FAMILY:
        if frag in c: return fam
    return None  # e.g. 'Fuse Holder' -> no connector family

IFACE=["USB","HDMI","DisplayPort","RJ45","RJ-45","Ethernet","D-Sub","FAKRA","SMA","BNC","MMCX","MCX","SMB"]
def interface_std(row):
    t=" ".join([row.get("Interface Type",""),row.get("Type",""),row.get("Description","")])
    for tok in IFACE:
        if tok.lower() in t.lower(): return tok
    return row.get("Interface Type","").strip() or row.get("Match Code","").strip() or None

def polarity(row):
    g=(row.get("Gender","") or "").lower()
    if "female" in g: return "female"
    if "male" in g: return "male"
    if "hermaphro" in g or "unisex" in g: return "hermaphroditic"
    ty=(row.get("Type","")+row.get("Connector Type","")).lower()
    if "socket" in ty or "receptacle" in ty or "jack" in ty: return "female"
    if "header" in ty or "plug" in ty or "pin" in ty or "tab" in ty: return "male"
    return None

def convert(row):
    pn=(row.get("Manufacturer Part Number","") or "").strip()
    if not pn: return None,["partNumber"]
    cat=row.get("Category","")
    fam=family_of(cat)
    cur=num(row.get("Rated Current"))
    volt=num(row.get("Working Voltage"))
    part={"partNumber":pn}
    pol=polarity(row)
    if pol: part["matingPolarity"]=pol
    if row.get("Description","").strip(): part["description"]=row["Description"].strip()[:1000]
    if row.get("Match Code","").strip(): part["series"]=row["Match Code"].strip()
    e={}
    if cur is not None: e["ratedCurrentPerContact"]=cur
    if volt is not None: e["ratedVoltage"]=volt
    if (v:=ohms(row.get("Contact Resistance"))) is not None: e["contactResistance"]={"nominal":v}
    if (v:=ohms(row.get("Insulation Resistance"))) is not None: e["insulationResistance"]=v
    if (v:=num(row.get("Withstanding Voltage"))) is not None: e["dielectricWithstandingVoltage"]=v
    mech={}
    if (v:=num(row.get("Number of Pins"))) and v>=1: mech["positions"]=int(v)
    if (v:=num(row.get("Number of Rows"))) and v>=1: mech["rows"]=int(v)
    if (v:=metres(row.get("Pitch"))) is not None and v>0: mech["pitch"]=round(v,9)
    di={"part":part,"electrical":e,"mechanical":mech}
    tmin=num(row.get("Min Operating Temperature")); tmax=num(row.get("Max Operating Temperature"))
    env={}
    if tmin is not None and tmax is not None: env["operatingTemperature"]={"minimum":tmin,"maximum":tmax}
    ip=(row.get("Ingress Protection Code","") or "").strip()
    if re.match(r"^IP[0-6X][0-9X]",ip): env["ipRating"]=ip[:5]
    if env: di["environmental"]=env
    extra_missing=None
    if fam=="rf":
        imp=num(row.get("Impedance"))
        if imp is not None: di["familyDetails"]={"family":"rf","characteristicImpedance":imp}
        else: fam=None; extra_missing="rf connector without characteristicImpedance"
    elif fam=="dataInterface":
        std=interface_std(row)
        if std: di["familyDetails"]={"family":"dataInterface","interfaceStandard":std}
        else: fam=None; extra_missing="dataInterface without interfaceStandard"
    elif fam is not None:
        di["familyDetails"]={"family":fam}
    mi={"name":"Würth Elektronik","reference":pn,"status":"production","datasheetInfo":di}
    url=(row.get("ComponentLink2URL","") or "").strip()
    if url.startswith("http"): mi["datasheetUrl"]=url
    rec={"connector":{"manufacturerInfo":mi}}
    missing=[]
    if cur is None: missing.append("ratedCurrentPerContact")
    if fam is None: missing.append(extra_missing or f"unmapped category: {cat}")
    return rec,missing

def main():
    rows=list(csv.DictReader(open(SRC)))
    mains=[]; inc=[]; seen=set()
    for row in rows:
        pn=(row.get("Manufacturer Part Number","") or "").strip()
        if not pn or pn in seen: continue
        seen.add(pn)
        rec,missing=convert(row)
        if rec is None: continue
        if missing:
            rec["quarantineReason"]="incomplete WE connector (MDB); missing: "+"; ".join(map(str,missing))+" (2026-06-24)"
            inc.append(rec)
        else: mains.append(rec)
    for nm,recs in [("mdb.main",mains),("mdb.incomplete",inc)]:
        with open(f"{OUT}/{nm}.ndjson","w") as fo:
            for r in recs: fo.write(json.dumps(r,ensure_ascii=False)+"\n")
    print(json.dumps({"rows":len(rows),"main":len(mains),"incomplete":len(inc)}))

if __name__=="__main__": main()
