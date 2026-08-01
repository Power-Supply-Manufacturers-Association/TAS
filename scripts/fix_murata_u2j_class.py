#!/usr/bin/env python3
"""File Murata's U2J leaded parts as the Class 1 dielectric Murata says they are.

    python3 scripts/fix_murata_u2j_class.py [--dry-run]

94 Murata rows carry dielectricCode U2J with technology `ceramic-class-2`. Murata's own
Reference Specification for those exact series says otherwise, twice over:

    "In case of Class 2 capacitors (Temp.Char. : X7R,X7S,X8L, etc.), applied voltage
     should be the load such as self-generated heat is within 20 C ... Since the
     self-heating is low in the Class 1 capacitors (Temp.Char.: C0G,U2J,X8G, etc.),
     the allowable power becomes extremely high compared to the Class 2 capacitors."

U2J is in Murata's Class 1 list, beside C0G; X7R is in the Class 2 list. The same sheet
gives U2J a temperature COEFFICIENT (-750 +120/-347 ppm/C from -55 to 25 C), which is a
temperature-compensating specification - Class 2 dielectrics have a capacitance-change
band instead, because they have no defined coefficient.

WHY THE DOCUMENT WAS NOT TAKEN ON ITS OWN. The Samsung case in ABT #434 taught that
vendors do not all classify alike - Samsung files Y5V under Class II where the textbooks
say Class III - so a general EIA rule is not enough to overrule a stored value. Two
independent confirmations were required here:

  * PHYSICS, from measurement already in the corpus. These rows carry Murata's own
    measured DC-bias curves. RDE7U2J943JUE1H03A holds 94.0 nF at 0 V and 91.65 nF at its
    full 630 V rating - a 2.5 % loss. A Class 2 dielectric loses 30-70 % at rated
    voltage. Whatever the label says, these parts behave like Class 1, and that evidence
    was measured, not asserted.

  * THE CORPUS ITSELF. Every other U2J row is already filed as Class 1: KEMET 213,
    Murata's own SMD parts 121, Taiyo Yuden 25. These 94 are the sole outlier, and they
    are outliers by IMPORT PATH, not by anything about the parts - they are the leaded
    RDE/RCE series from an older parametric import, while the 121 that agree are the SMD
    parts re-cited through Murata's PIM API.

All 94 are +-5 % (J) parts, a Class 1 grade, which is consistent with the rest.

THE CITATION IS REPLACED AT THE SAME TIME, because it is the reason the error survived.
These rows still point at `www.murata.com/products/productdetail?partno=...`, a scheme
that no longer resolves - the leaded RDE/RCE rows were missed by the ABT #391 re-sourcing
that fixed the SMD ones. A dead citation cannot contradict a wrong field. Each row is
re-cited to the Murata Reference Specification for its own series, which is the document
quoted above.

Only `technology` changes. The measured bias curves stay: they are real Murata data and
are what proved the point.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "capacitors.ndjson"
AUDIT = REPO / "staging" / "murata_u2j_class_audit.json"
TODAY = "2026-08-01"

DEAD_SCHEME = "www.murata.com/products/productdetail?partno="

SPECS = {
    "RDE": ("https://search.murata.co.jp/Ceramy/image/img/A01X/G101/ENG/"
            "RDE_U2J_250V-1kV_E.pdf",
            "Murata Reference Specification, RDE Series 'Leaded MLCC for Consumer "
            "Electronics & Industrial Equipment' (U2J, 250 V-1 kV): lists U2J among the "
            "Class 1 capacitors (Temp.Char.: C0G,U2J,X8G) against X7R/X7S/X8L as Class 2, "
            "and specifies U2J's temperature coefficient as -750 +120/-347 ppm/C. "
            "PDF sha256 c4fb0c922d77aa1b8200a184377a17604f9626222984f0f01d2dd091d82fb2e2"),
    "RCE": ("https://search.murata.co.jp/Ceramy/image/img/A01X/G101/ENG/"
            "RCE_U2J_250V-1kV_E.pdf",
            "Murata Reference Specification, RCE Series 'Leaded MLCC for Automotive "
            "(Powertrain/Safety)' (U2J, 250 V-1 kV): lists U2J among the Class 1 "
            "capacitors (Temp.Char.: C0G,U2J,X8G) against X7R/X7S/X8L as Class 2. "
            "PDF sha256 ed737cf078dd649d94472c4095b0d3b6d0f1c068d9b907378f49da5a8aba414f"),
}


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #434 follow-on (Murata U2J class)", "date": TODAY,
             "reclassified": [], "deadCitationsReplaced": 0, "bySeries": Counter()}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"U2J" in raw and b"urata" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["capacitor"]["manufacturerInfo"]
                    di = mi["datasheetInfo"]
                    part = di.get("part") or {}
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                ref = str(mi.get("reference") or "")
                if (str(mi.get("name")) == "Murata"
                        and str(part.get("dielectricCode") or "").upper() == "U2J"
                        and part.get("technology") == "ceramic-class-2"):
                    series = ref[:3].upper()
                    spec = SPECS.get(series)
                    if not spec:
                        # No Murata document identified for this series - leave it and say
                        # so, rather than reclassifying on the strength of the others.
                        audit.setdefault("unhandled", []).append(ref)
                        out.write(line)
                        continue
                    url, name = spec
                    part["technology"] = "ceramic-class-1"
                    prov = di.setdefault("provenance", [])
                    before = len(prov)
                    prov = [p for p in prov if DEAD_SCHEME not in str(p.get("sourceUrl", ""))]
                    audit["deadCitationsReplaced"] += before - len(prov)
                    if not any(p.get("sourceUrl") == url for p in prov):
                        prov.append({"source": "manufacturerDatasheet", "sourceName": name,
                                     "sourceUrl": url, "retrievedDate": TODAY,
                                     "fields": ["part.technology"]})
                    di["provenance"] = prov
                    audit["reclassified"].append({"reference": ref, "series": series,
                                                  "was": "ceramic-class-2",
                                                  "now": "ceramic-class-1"})
                    audit["bySeries"][series] += 1
                    line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows reclassified to ceramic-class-1: {len(audit['reclassified'])}")
    for k, v in audit["bySeries"].most_common():
        print(f"     {v:4}  {k} series")
    print(f"dead productdetail citations replaced: {audit['deadCitationsReplaced']}")
    if audit.get("unhandled"):
        print(f"LEFT ALONE (no Murata document identified): {len(audit['unhandled'])} "
              f"{audit['unhandled'][:4]}")
    for r in audit["reclassified"][:3]:
        print(f"       {r['reference']:22} {r['was']} -> {r['now']}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        audit["bySeries"] = dict(audit["bySeries"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
