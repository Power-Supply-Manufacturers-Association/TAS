#!/usr/bin/env python3
"""Correct the Vishay A-series Y5V citation, and the two rows it wrongly condemned.

    python3 scripts/fix_vishay_a_series_z_citation.py [--dry-run]

ABT #428 sourced the +80/-20 tolerance for these parts from Vishay document 45164
revision 17-Jan-06 and WITHHELD two rows, A334Z20Y5VF5TAA and A334Z20Y5VF5UAA, because
"A334Z20" appeared nowhere in it. ABT #433 was raised asking whether they were fabricated.

THEY ARE REAL, AND THE REASONING THAT CONDEMNED THEM WAS A MISREADING OF THE ORDERING KEY
PRINTED IN THAT SAME DOCUMENT. Its ORDERING INFORMATION table reads:

    PRODUCT   CAPACITANCE   CAP. TOL.   SIZE CODE                TEMP.  RATED VOLTAGE
    A = Mono- two digits    J = +-5 %   15 = 3.8 (0.15") max.    C0G    F = 50 VDC
    Axial     + multiplier  K = +-10 %  20 = 5.0 (0.20") max.    X7R    H = 100 VDC

So the "15"/"20" field is the BODY SIZE in inches, and the voltage is the "F"/"H" that
follows the dielectric. A334Z15 and A334Z20 are both 50 V parts differing in package
length; "the voltage code was changed from 15 to 20" describes nothing that exists. The
supporting claim that the corpus "also holds A334Z15Y5VF5TAA/UAA" was false as well - it
holds no A334Z15 row at all, so there was no original for these to be a permutation of.

WHAT ACTUALLY HAPPENED: Vishay moved 0.33 uF/50 V Y5V from the size-15 body to the size-20
body between revisions. Revision 20-Aug-13 of the same document 45164 lists

    330 000 pF    A334Z20Y5VF5###    (- for the 100 V column)
    Notes: Tolerance is + 80 %/- 20 %
           # 13th, 14th and 15th digits are packaging code: Reel = TAA; Ammo = UAA

which expands to exactly these two part numbers. The 2006 revision was simply seven years
stale on that line. An absent part number is evidence about a REVISION, not about a part -
the revisions in between have to be checked before calling anything fabricated.

TWO CHANGES, both from that 2013 revision:

1. The two withheld rows get the +80/-20 band the rest of the family already has.

2. All 25 A-series Y5V Z rows are re-cited to revision 20-Aug-13 instead of 17-Jan-06.
   This is a strict improvement in the provenance itself, not churn. The 2006 citation
   named 23 of 25 and was only reachable through a distributor's re-host
   (taydaelectronics.com); the 2013 revision names 25 of 25 and is available as a capture
   of VISHAY'S OWN server, so the chain no longer depends on a third party. Vishay's live
   URL still cannot be cited: Y5V was dropped from document 45164 entirely at the
   16-Oct-2023 revision, so vishay.com/doc?45164 today mentions neither the dielectric nor
   the parts.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "capacitors.ndjson"
AUDIT = REPO / "staging" / "vishay_a_series_z_citation_audit.json"
TODAY = "2026-08-01"

A_SERIES_Z = re.compile(r"^A\d{3}Z\d{2}Y5V", re.I)

NEW_URL = ("https://web.archive.org/web/20140820104712if_/"
           "http://www.vishay.com/docs/45164/aseries.pdf")
NEW_NAME = (
    "Vishay document 45164 rev 20-Aug-13, 'A Series - Axial Leaded Multilayer Ceramic "
    "Capacitors for General Purpose': the Y5V table names this part (via the documented "
    "packaging wildcard '### = TAA reel / UAA ammo') and notes 'Tolerance is + 80 %/- 20 %'. "
    "Capture of Vishay's own server; the live vishay.com/doc?45164 is the 16-Oct-2023 "
    "revision, which dropped Y5V entirely. PDF sha256 "
    "b356cf604cd2eb03abd3029ce43e38dcccd8011eff38b0189b2b15a3229dd61f")

# The provenance entry ABT #428 wrote, which this replaces.
OLD_MARKER = "rev 17-Jan-06"


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #433 / ABT #428 correction", "date": TODAY,
             "sourceUrl": NEW_URL, "toleranceFixed": [], "reCited": [],
             "counts": Counter()}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"Y5V" in raw and b"ishay" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["capacitor"]["manufacturerInfo"]
                    di = mi["datasheetInfo"]
                    cap = (di.get("electrical") or {}).get("capacitance") or {}
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                ref = str(mi.get("reference") or "")
                if A_SERIES_Z.match(ref) and "Vishay" in str(mi.get("name")):
                    touched = False
                    nom = cap.get("nominal")
                    if isinstance(nom, (int, float)) and not isinstance(nom, bool) and nom > 0:
                        want_min, want_max = nom * 0.80, nom * 1.80
                        if abs((cap.get("maximum") or 0) - want_max) > nom * 1e-9:
                            audit["toleranceFixed"].append(
                                {"reference": ref, "nominal": nom,
                                 "wasMinimum": cap.get("minimum"),
                                 "wasMaximum": cap.get("maximum"),
                                 "nowMinimum": want_min, "nowMaximum": want_max})
                            cap["minimum"], cap["maximum"] = want_min, want_max
                            touched = True
                    prov = di.setdefault("provenance", [])
                    kept = [p for p in prov if OLD_MARKER not in str(p.get("sourceName", ""))]
                    if len(kept) != len(prov):
                        audit["counts"]["replacedOldCitation"] += 1
                    if not any(NEW_URL == p.get("sourceUrl") for p in kept):
                        kept.append({"source": "manufacturerDatasheet", "sourceName": NEW_NAME,
                                     "sourceUrl": NEW_URL, "retrievedDate": TODAY,
                                     "fields": ["electrical.capacitance.minimum",
                                                "electrical.capacitance.maximum"]})
                        audit["reCited"].append(ref)
                        touched = True
                    di["provenance"] = kept
                    if touched:
                        audit["counts"]["rows"] += 1
                        line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"A-series Y5V Z rows touched:  {audit['counts']['rows']}")
    print(f"  tolerance corrected:        {len(audit['toleranceFixed'])}")
    print(f"  re-cited to rev 20-Aug-13:  {len(audit['reCited'])}")
    print(f"  old 17-Jan-06 citation replaced on: {audit['counts']['replacedOldCitation']}")
    for f in audit["toleranceFixed"]:
        print(f"     {f['reference']:22} nom={f['nominal']:.4g}  "
              f"max {f['wasMaximum']:.4g} -> {f['nowMaximum']:.4g}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        audit["counts"] = dict(audit["counts"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
