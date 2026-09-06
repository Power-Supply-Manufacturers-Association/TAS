#!/usr/bin/env python3
"""WE Passive 'Transformers' table -> MAS magnetic NDJSON.
Flyback transformers (WE-FB/FLY*/OL* / 'flyback') are mapped as subtype coupledInductor
(they store energy, not a true transformer); the rest as subtype transformer. NEW PNs only.
"""

# --------------------------------------------------------------------------- #
# RETIRED 2026-09-04. This script is one of the eight importers that wrote the
# hardcoded core stub {"material": "Dummy", "shape": "Dummy"} onto 21,507
# magnetics rows -- a fabricated value that sat in the catalogue for months and
# that MKF reads to compute saturation and losses. Those stubs were removed on
# 2026-09-04 and the real materials re-sourced from vendor data.
#
# It is retired for a second, sharper reason found by an adversarial review the
# same day: it takes its DESTINATION from argv and finishes with os.replace, so
#     python3 we_transformers_import.py <input> data/magnetics.ndjson
# does not append -- it OVERWRITES the live catalogue with whatever this run
# extracted, destroying every other record in it. There is no destination
# allowlist and no confirmation.
#
# If this importer is ever needed again it must be rewritten to append, to
# refuse any path under data/ that it did not create, and to source its material
# and shape fields from a real document rather than a stub.
#
# ALSO FIXED 2026-09-05 (ABT #1082, #1090), so a rewrite starts from code that
# cannot re-mint those defects:
#   * `family` came from the .mdb's free-text Match Code (the campaign that ran
#     this path defaulted it to "WE-MAPI" when a row had no text -- 404 rows
#     landed in the wrong family). It is now WE REDEXPERT's Series for the EXACT
#     order code, OMITTED when the code is unknown, and a Match Code that
#     contradicts REDEXPERT aborts the import. Never the order-code prefix:
#     744300 spans WE-HCM/WE-HCMD, 744310/744313/744314 span WE-HCI/WE-HCM.
#   * henries() returned a UNITLESS cell as if it were already henries, so a
#     bare "10" became 10 H. It now raises rather than assume a unit.
# --------------------------------------------------------------------------- #
import sys as _sys
if __name__ == "__main__":
    _sys.exit(
        "REFUSING to run: we_transformers_import.py is retired (2026-09-04). It wrote a fabricated "
        "'Dummy' core stub, and it overwrites its argv destination via os.replace, "
        "so pointing it at a live data/*.ndjson destroys that catalogue. "
        "Rewrite it to append and to source real values before re-enabling."
    )

import csv, datetime, json, re, os, sys
SRC="/tmp/we_xfmr.csv"; OUT="/home/alf/PSMA/TAS/staging/we"; os.makedirs(OUT,exist_ok=True)
def num(s):
    if not s: return None
    m=re.search(r"[-+]?\d*\.?\d+",str(s).replace(",",".")); return float(m.group()) if m else None
def henries(s):
    """Henries from a .mdb cell. A cell with NO unit raises: assuming one is how
    fabricated inductances reach the catalogue (ABT #1090)."""
    v=num(s)
    if v is None: return None
    u=str(s).lower()
    if "nh" in u: return v*1e-9
    if "mh" in u: return v*1e-3
    if "uh" in u or "µh" in u: return v*1e-6
    raise ValueError("inductance cell %r states no unit -- refusing to assume one; "
                     "fix the source column, do not guess" % (s,))
def metres(s):
    v=num(s); return v*1e-3 if (v is not None and "mm" in str(s).lower()) else v
def turns_ratios(s):
    parts=re.findall(r"[\d.]+", s or "")
    if len(parts)>=2:
        p0=float(parts[0])
        return [{"nominal":round(p0/float(p),6)} for p in parts[1:] if float(p)!=0]
    return None
# family: WE REDEXPERT's Series for the EXACT order code, or nothing (ABT #1082).
# The resolver lives in the tracked guard so importer and guard cannot drift.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_wurth_family_matches_series import load_ground_truth, DEFAULT_SNAPSHOT, verdict
# THE GATE (2026-09-06): a candidate row is checked BEFORE it can enter the
# catalogue, and a refusal aborts the import instead of degrading the row.
from ingest_gate import IngestGate, IngestRefused

_SERIES = None

def wurth_family(order_code):
    global _SERIES
    if _SERIES is None:
        _SERIES, _ = load_ground_truth(DEFAULT_SNAPSHOT, refresh=False, offline=False)
    return _SERIES.get(str(order_code).strip())

CORE={"functionalDescription":{"type":"twoPieceSet","material":"Dummy","shape":"Dummy","gapping":[]}}
COIL={"bobbin":"Dummy","functionalDescription":[{"name":"Dummy","numberTurns":1,"numberParallels":1,"isolationSide":"primary","wire":"Dummy"}]}

def is_flyback(row):
    mc=(row.get("Match Code","") or "").upper()
    return ("flyback" in (row.get("Description","") or "").lower()
            or mc.startswith(("WE-FB","WE-FLY","WE-OL")))

def convert(row):
    pn=(row.get("Manufacturer Part Number","") or "").strip()
    if not pn: return None
    L=henries(row.get("Inductance")); tr=turns_ratios(row.get("Turns Ratio"))
    if is_flyback(row):
        e={"subtype":"coupledInductor"}
        if L is not None: e["inductance"]={"nominal":L}
        if tr: e["turnsRatios"]=tr
    else:
        e={"subtype":"transformer"}
        if L is not None: e["inductance"]={"nominal":L}
        if tr: e["turnsRatios"]=tr
        if (v:=num(row.get("Insulation Test Voltage"))) is not None: e["insulationTestVoltageAC"]=v
    part={}
    if row.get("Description","").strip(): part["description"]=row["Description"].strip()[:300]
    if row.get("Case/Size Code","").strip(): part["caseCode"]=row["Case/Size Code"].strip()
    mech={}
    for k,col in [("length","Length"),("width","Width"),("height","Height")]:
        v=metres(row.get(col))
        if v is not None: mech[k]={"nominal":round(v,9)}
    di={"part":part,"electrical":[e]}
    if mech: di["mechanical"]=mech
    tmin=num(row.get("Min Operating Temperature")); tmax=num(row.get("Max Operating Temperature"))
    if tmin is not None and tmax is not None: di["thermal"]={"operatingTemperature":{"minimum":tmin,"maximum":tmax}}
    di["provenance"]=[{"source":"manufacturerDatabase","sourceName":"WE - Passive Components.mdb","retrievedDate":datetime.date.today().isoformat()}]
    mi={"name":"Würth Elektronik","reference":pn,"status":"production","datasheetInfo":di,
        "datasheetUrl":f"https://www.we-online.com/components/products/datasheet/{pn}.pdf"}
    fam = wurth_family(pn)
    stored = row.get("Match Code","").strip()
    if fam and stored and verdict(stored, fam):
        raise SystemExit(
            "IMPORT ABORTED: %s -- the .mdb Match Code %r contradicts WE REDEXPERT's "
            "Series %r for this exact order code. One of the two is wrong and this "
            "importer is not entitled to pick; resolve it before importing "
            "(ABT #1082)." % (pn, stored, fam))
    if fam: mi["family"]=fam           # absent when REDEXPERT does not know the code
    return {"magnetic":{"manufacturerInfo":mi,"core":CORE,"coil":COIL}}

def main():
    gate = IngestGate("magnetics.ndjson")
    have=set()
    for l in open("/home/alf/PSMA/TAS/data/magnetics.ndjson"):
        try: mi=json.loads(l).get("magnetic",{}).get("manufacturerInfo",{})
        except: continue
        if mi.get("reference"): have.add(mi["reference"].strip())
    out=[]; seen=set()
    for row in csv.DictReader(open(SRC)):
        pn=(row.get("Manufacturer Part Number","") or "").strip()
        if not pn or pn in have or pn in seen: continue
        seen.add(pn); rec=convert(row)
        if rec:
            gate.admit(rec)     # raises IngestRefused; the import stops here
            out.append(rec)
    # batch rules over the whole candidate set, before a byte is written
    gate.close()
    with open(f"{OUT}/transformers.ndjson","w") as fo:
        for r in out: fo.write(json.dumps(r,ensure_ascii=False)+"\n")
    fb=sum(1 for r in out if r["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]["subtype"]=="coupledInductor")
    print(f"new WE transformers: {len(out)} (flyback->coupledInductor: {fb}, transformer: {len(out)-fb})")

if __name__=="__main__": main()
