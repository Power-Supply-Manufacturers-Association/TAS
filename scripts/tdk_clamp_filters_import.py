#!/usr/bin/env python3
"""Build MAS `cableCore` records for TDK ZCAT clamp filters (ferrite core with
case — snap-on clamp-on cores) from their MEASURED impedance curves.

Source: product.tdk.com clamp-filter graph API — POST /pdc_api/.../info/graph
(body graph_kind[0]=3011&pid[]=<pid>, header X-Requested-With: XMLHttpRequest) ->
graph.graph_kind_3011[0].data = measured |Z|(f) [[freq_Hz, Z_ohm], ...] (156 pts,
1 MHz-495 MHz). Full measured curves (like WE) — used verbatim.

    python3 tdk_clamp_filters_import.py --apply
"""
import json, re, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent; DATA=HERE.parent/"data"
RAW=DATA/"tdk_parts.jsonl"; OUT=DATA/"tdk_cable_cores.ndjson"; APPLY="--apply" in sys.argv

def cable_max_m(s):
    if not s: return None
    m=re.search(r'([\d.]+)\s*mm', s)  # "Φ8mm" or "7mm (W) x 4mm (T)"
    return float(m.group(1))/1000.0 if m else None

def build(rec):
    pn=rec["part_no"]; curve=rec.get("curve") or []
    pts=[{"impedance":{"magnitude":float(z)},"frequency":float(f)} for f,z in sorted(curve) if f>0 and z>0]
    if len(pts)<5: return None
    z100=next((p["impedance"]["magnitude"] for p in pts if abs(p["frequency"]-1e8)<2e6), None)
    cm=cable_max_m(rec.get("cableSize"))
    url=f"https://product.tdk.com/en/search/emc/emc/clamp/info?part_no={pn}"
    electrical={"subtype":"cableCore","numberTurns":1,"impedancePoints":pts,"mountingForm":"snapOn"}
    if cm and cm>0: electrical["maximumCableOuterDiameter"]=cm
    ptype=rec.get("productType") or ""
    desc="TDK ZCAT clamp filter (snap-on"+((", "+ptype) if ptype else "")+")"
    if rec.get("cableSize"): desc+=f", cable {rec['cableSize']}"
    if z100: desc+=f", |Z|≈{round(z100)} Ω @100 MHz (measured), 1 turn"
    return {"magnetic":{"manufacturerInfo":{
        "name":"TDK","reference":pn,"status":"production","family":"ZCAT Clamp Filter","datasheetUrl":url,
        "datasheetInfo":{
            "part":{"description":desc,"material":"Ferrite","shielded":False},
            "electrical":[electrical],
            "provenance":[{"source":"manufacturerParametric",
                           "sourceName":"TDK Product Center clamp-filter impedance graph (measured)",
                           "sourceUrl":url,"retrievedDate":"2026-07-28"}]}}}}

def main():
    recs=[json.loads(l) for l in open(RAW) if l.strip()]
    built,seen=[],set()
    for r in recs:
        pn=r.get("part_no")
        if not pn or pn in seen: continue
        o=build(r)
        if o: seen.add(pn); built.append(o)
    from collections import Counter
    print(f"{'APPLYING' if APPLY else 'DRY'} — TDK ZCAT: built {len(built)} of {len(recs)}")
    if built:
        e=built[0]["magnetic"]["manufacturerInfo"]; print("  sample:",e["reference"],"—",e["datasheetInfo"]["part"]["description"])
    if APPLY:
        with open(OUT,"w") as f:
            for o in built: f.write(json.dumps(o)+"\n")
        print(f"  wrote {len(built)} to {OUT}")

if __name__=="__main__": main()
