#!/usr/bin/env python3
"""WE Passive Components Access DB (Inductors table) -> MAS magnetic NDJSON.
Mirrors the existing WE magnetic shape in TAS (datasheetInfo.electrical[{subtype:inductor,...}]
+ Dummy core/coil stubs to satisfy MAS). Only NEW part numbers (not already in magnetics.ndjson).
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
#     python3 we_inductors_import.py <input> data/magnetics.ndjson
# does not append -- it OVERWRITES the live catalogue with whatever this run
# extracted, destroying every other record in it. There is no destination
# allowlist and no confirmation.
#
# If this importer is ever needed again it must be rewritten to append, to
# refuse any path under data/ that it did not create, and to source its material
# and shape fields from a real document rather than a stub.
#
# TWO MORE DEFECTS, FIXED IN THE CONVERSION BELOW 2026-09-05 (ABT #1082, #1090).
# The retirement above stands; these are fixed here so a rewrite starts from
# code that cannot re-mint them:
#
#   ABT #1082  `family` was taken from the .mdb's free-text Match Code, and the
#              campaign that ran this path defaulted it to "WE-MAPI" when the
#              row had no text. 514 rows landed as WE-MAPI, 404 of them wrongly
#              (WE-LHMI 166, WE-HCI 126, WE-HCM 77, WE-HCF 35). family is now
#              resolved from WE REDEXPERT's Series for the EXACT order code,
#              OMITTED when the code is unknown, and a Match Code that
#              contradicts REDEXPERT ABORTS the import instead of picking one.
#              The order-code PREFIX is never used: 744300 spans WE-HCM and
#              WE-HCMD, and 744310/744313/744314 each span WE-HCI and WE-HCM.
#
#   ABT #1090  henries() treated a unitless Inductance cell as microhenries, so
#              a bare "10" became 1e-05 H -- and impedance-defined parts (chip
#              beads, EMI-suppression ferrites, sleeve chokes, which quote ohms
#              and publish no inductance at all) were written as subtype
#              "inductor" carrying that constant. 29 WE-WAFB / WE-MLS / WE-PF
#              rows shipped with an identical fabricated 10 uH. A unitless cell
#              now RAISES, and an impedance-defined row is emitted as a chipBead
#              with no inductance.
# --------------------------------------------------------------------------- #
import sys as _sys
if __name__ == "__main__":
    _sys.exit(
        "REFUSING to run: we_inductors_import.py is retired (2026-09-04). It wrote a fabricated "
        "'Dummy' core stub, and it overwrites its argv destination via os.replace, "
        "so pointing it at a live data/*.ndjson destroys that catalogue. "
        "Rewrite it to append and to source real values before re-enabling."
    )

import csv, datetime, json, re, os, sys
SRC="/tmp/we_ind.csv"; OUT="/home/alf/PSMA/TAS/staging/we"; os.makedirs(OUT,exist_ok=True)
def num(s):
    if not s: return None
    m=re.search(r"[-+]?\d*\.?\d+",str(s).replace(",",".")); return float(m.group()) if m else None
def henries(s):
    """Henries from a .mdb cell. A cell with NO unit raises: assuming one is how
    a bare "10" became a fabricated 1e-05 H on 29 rows (ABT #1090)."""
    v=num(s)
    if v is None: return None
    u=str(s).lower()
    if "nh" in u: return v*1e-9
    if "mh" in u: return v*1e-3
    if "uh" in u or "µh" in u: return v*1e-6
    raise ValueError("inductance cell %r states no unit -- refusing to assume one; "
                     "fix the source column, do not guess" % (s,))
def ohms(s):
    v=num(s);
    if v is None: return None
    u=str(s).lower()
    if "mohm" in u or "mΩ" in u: return v*1e-3
    if "kohm" in u: return v*1e3
    return v
def hz(s):
    v=num(s);
    if v is None: return None
    u=str(s).lower()
    if "ghz" in u: return v*1e9
    if "mhz" in u: return v*1e6
    if "khz" in u: return v*1e3
    return v
def metres(s):
    v=num(s); return v*1e-3 if (v is not None and "mm" in str(s).lower()) else v
# ---------------------------------------------------------------------------
# family: WE REDEXPERT's Series for the EXACT order code, or nothing (ABT #1082)
# ---------------------------------------------------------------------------
# The resolver lives in the tracked guard so the importer and the guard cannot
# drift apart; the guard is also what re-checks the corpus afterwards.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_wurth_family_matches_series import load_ground_truth, DEFAULT_SNAPSHOT, verdict

_SERIES = None

def wurth_family(order_code):
    """REDEXPERT's Series for this exact order code, or None if it has none.

    None means the field is OMITTED. It never falls back to a default, to a
    description, or to an order-code prefix -- 744300 spans WE-HCM and WE-HCMD,
    and 744310/744313/744314 each span WE-HCI and WE-HCM, so a prefix rule
    mislabels real parts (ABT #1082)."""
    global _SERIES
    if _SERIES is None:
        _SERIES, _ = load_ground_truth(DEFAULT_SNAPSHOT, refresh=False, offline=False)
    return _SERIES.get(str(order_code).strip())

CORE={"functionalDescription":{"type":"twoPieceSet","material":"Dummy","shape":"Dummy","gapping":[]}}
COIL={"bobbin":"Dummy","functionalDescription":[{"name":"Dummy","numberTurns":1,"numberParallels":1,"isolationSide":"primary","wire":"Dummy"}]}

def convert(row):
    pn=(row.get("Manufacturer Part Number","") or "").strip()
    if not pn: return None
    text=(row.get("Description","")+" "+row.get("Match Code","")).lower()
    coupled = ("flyback" in text or "coupled" in text)  # flybacks ARE coupled inductors
    # An impedance-defined part (chip bead, EMI-suppression ferrite, sleeve
    # choke) publishes ohms, not henries. Writing an inductance on one is what
    # put a fabricated 10 uH on 29 WE-WAFB/WE-MLS/WE-PF rows (ABT #1090), so the
    # subtype changes and the inductance is dropped -- the impedance itself has
    # to come from that part's own datasheet, not from a column whose meaning
    # differs per family (REDEXPERT's Impedance is |Z|@100 MHz for WE-CBF/WAFB/
    # MLS but a Zmax at a part-specific frequency for WE-PF).
    bead = any(k in text for k in
               ("bead", "emi suppression", "suppression ferrite", "sleeve choke",
                "multiline", "signal line filter"))
    # read AFTER the bead test: an impedance-defined row has no inductance to
    # parse, and its Inductance cell must not be able to raise (or to be
    # rescued by a guessed unit) on a part that has no such quantity.
    L = None if bead else henries(row.get("Inductance"))
    dcr=ohms(row.get("DC Resistance"))
    if bead:
        e={"subtype":"chipBead"}
        if dcr is not None: e["dcResistance"]={"maximum":dcr}
    elif coupled:
        e={"subtype":"coupledInductor"}
        if dcr is not None: e["dcResistances"]=[{"maximum":dcr}]
    else:
        e={"subtype":"inductor"}
        if dcr is not None: e["dcResistance"]={"maximum":dcr}
    if L is not None: e["inductance"]={"nominal":L}
    if (v:=num(row.get("Saturation Current"))) is not None: e["saturationCurrentPeak"]=v
    if (v:=hz(row.get("Self Resonant Frequency"))) is not None: e["selfResonantFrequency"]=v
    if (v:=num(row.get("Rated Current"))) is not None: e["ratedCurrents"]=[v]
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
    mi={"name":"Würth Elektronik","reference":pn,"status":"production","datasheetInfo":di}
    fam = wurth_family(pn)
    stored = row.get("Match Code","").strip()
    if fam and stored and verdict(stored, fam):
        raise SystemExit(
            "IMPORT ABORTED: %s -- the .mdb Match Code %r contradicts WE REDEXPERT's "
            "Series %r for this exact order code. One of the two is wrong and this "
            "importer is not entitled to pick; resolve it before importing "
            "(ABT #1082)." % (pn, stored, fam))
    if fam: mi["family"]=fam           # absent when REDEXPERT does not know the code
    mi["datasheetUrl"]=f"https://www.we-online.com/components/products/datasheet/{pn}.pdf"
    return {"magnetic":{"manufacturerInfo":mi,"core":CORE,"coil":COIL}}

def main():
    # existing magnetics refs (only emit NEW)
    have=set()
    for l in open("/home/alf/PSMA/TAS/data/magnetics.ndjson"):
        try: mi=json.loads(l).get("magnetic",{}).get("manufacturerInfo",{})
        except: continue
        for v in (mi.get("reference"),mi.get("datasheetInfo",{}).get("part",{}).get("partNumber")):
            if v: have.add(str(v).strip())
    out=[]; seen=set()
    for row in csv.DictReader(open(SRC)):
        pn=(row.get("Manufacturer Part Number","") or "").strip()
        if not pn or pn in have or pn in seen: continue
        seen.add(pn)
        rec=convert(row)
        if rec: out.append(rec)
    with open(f"{OUT}/inductors.ndjson","w") as fo:
        for r in out: fo.write(json.dumps(r,ensure_ascii=False)+"\n")
    print(f"new WE inductors: {len(out)}")

if __name__=="__main__": main()
