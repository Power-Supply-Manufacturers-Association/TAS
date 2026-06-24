#!/usr/bin/env python3
"""Convert scraped Würth Elektronik connector series tables (.playwright-mcp/we_*.json)
-> CONAS connector NDJSON. WE series pages render a product table (DOM); each row's cells
are 'label+value' concatenated, parsed here by regex. Family classified from the series URL.
"""
import json, re, glob, os
MCP="/home/alf/PSMA/.playwright-mcp"; OUT="/home/alf/PSMA/TAS/staging/we"; os.makedirs(OUT,exist_ok=True)

def num(s):
    if not s: return None
    m=re.search(r"[-+]?\d*\.?\d+",s.replace(",",".")); return float(m.group()) if m else None

def family_from_url(u):
    u=u.lower()
    if "board-to-board" in u: return ("boardToBoard",{})
    if "coax" in u: return (None,"rf connector without characteristicImpedance")
    if "wr-dsub" in u or "dsub" in u: return ("dataInterface",{"interfaceStandard":"D-Sub"})
    if "wr-usb" in u or "usb" in u: return ("dataInterface",{"interfaceStandard":"USB"})
    if "wr-mj" in u or "modular_jack" in u: return ("dataInterface",{"interfaceStandard":"Modular Jack"})
    if "wr-crd" in u or "card" in u: return ("cardEdge",{})
    if "wire-to-board" in u or "wire_to_board" in u: return ("wireToBoard",{})
    return ("wireToBoard",{})  # default for WE connectors

def polarity(typ):
    t=(typ or "").lower()
    if "socket" in t or "receptacle" in t or "female" in t: return "female"
    if "header" in t or "pin" in t or "plug" in t or "male" in t or "tht" in t: return "male"
    return None

STATUS={"active":"production","obsolete":"obsolete","not recommended":"nrnd","end of life":"obsolete"}

def cell_with(cells, *frags):
    # WE table cells are self-labeling ("Rated Current3 A") and data rows are shifted
    # vs the header, so match on the cell's own embedded label, not the column index.
    for c in cells:
        cl=c.lower()
        if any(fr in cl for fr in frags): return c
    return None

def convert(row, headers, url):
    oc=row.get("oc")
    if not oc: return None,["partNumber"]
    cells=row["cells"]
    cur=num(cell_with(cells,"rated current"))
    volt=num(cell_with(cells,"working voltage"))
    pins=num(cell_with(cells,"pins"))
    typ=cell_with(cells,"type")
    statc=cell_with(cells,"status") or ""
    tempc=cell_with(cells,"operating temperature") or ""
    fam,extra=family_from_url(url)
    part={"partNumber":oc}
    pol=polarity(typ)
    if pol: part["matingPolarity"]=pol
    e={}
    if cur is not None: e["ratedCurrentPerContact"]=cur
    if volt is not None: e["ratedVoltage"]=volt
    mech={}
    if pins and pins>=1: mech["positions"]=int(pins)
    di={"part":part,"electrical":e,"mechanical":mech}
    tm=re.findall(r"[-+]?\d+",tempc)
    if len(tm)>=2: di["environmental"]={"operatingTemperature":{"minimum":float(tm[0]),"maximum":float(tm[1])}}
    if isinstance(fam,str): fd={"family":fam}; fd.update(extra if isinstance(extra,dict) else {}); di["familyDetails"]=fd
    st="production"
    sl=statc.lower()
    for k,v in STATUS.items():
        if k in sl: st=v; break
    mi={"name":"Würth Elektronik","reference":oc,"status":st,
        "datasheetUrl":f"https://www.we-online.com/components/products/datasheet/{oc}.pdf",
        "datasheetInfo":di}
    rec={"connector":{"manufacturerInfo":mi}}
    missing=[]
    if cur is None: missing.append("ratedCurrentPerContact")
    if fam is None: missing.append(extra)
    return rec,missing

def main():
    mains=[]; inc=[]; seen=set()
    for fn in sorted(glob.glob(f"{MCP}/we_*.json")):
        d=json.load(open(fn)); headers=d["headers"]; url=d.get("url","")
        for row in d["rows"]:
            oc=row.get("oc")
            if not oc or oc in seen: continue
            seen.add(oc)
            rec,missing=convert(row,headers,url)
            if rec is None: continue
            if missing:
                rec["quarantineReason"]="incomplete WE connector; missing: "+"; ".join(map(str,missing))+" (2026-06-24)"
                inc.append(rec)
            else: mains.append(rec)
    for nm,recs in [("connectors.main",mains),("connectors.incomplete",inc)]:
        with open(f"{OUT}/{nm}.ndjson","w") as fo:
            for r in recs: fo.write(json.dumps(r,ensure_ascii=False)+"\n")
    print(json.dumps({"main":len(mains),"incomplete":len(inc)}))

if __name__=="__main__": main()
