#!/usr/bin/env python3
"""Undo 6 rows I corrupted by trusting TDK's database over the physics.

    python3 scripts/revert_overcorrected_tdk_dcr.py [--dry-run]

fix_tdk_dcr_far_below_maximum.py replaced any stored DC resistance more than 10x below
TDK's published maximum, on the reasoning that copper resistance does not vary by 10x
between typical and maximum. That reasoning is sound and the rule was still wrong for six
rows, because it assumed the vendor's number was the correct one in every disagreement.

    B82559A4322A033 / A4352A033 / A5472A033 / A5602A033 / A5682A033 / A5103A033

These are 33 x 33 x 15 mm power chokes rated 36-95 A. At the stored 0.85 mohm,
B82559A4322A033 dissipates 95^2 * 0.00085 = 7.7 W - normal for a choke that size. At TDK's
published "0.85 ohm" it dissipates 7,671 W. The stored value was right and TDK'S DATABASE
CARRIED THE UNIT ERROR. Blade Runner said so immediately: the correction took the corpus
from 8 IMPOSSIBLE findings to 16, and six of the eight new ones were rows I had just
touched.

WHY TDK'S DATABASE WAS WRONG HERE. All six sit on class_id -112 - negative, TDK's marker
for a discontinued part. That is the third time in this campaign that TDK's EOL rows have
carried bad data: the CK45 capacitors whose "+-5 %" tolerance contradicted every active
sibling (ABT #428) were class -123, and BCM605040-57N is -112. TDK's Meister database is
reliable for ACTIVE parts and demonstrably not for discontinued ones, and nothing in the
database says so - the class_id sign is the only tell.

BCM605040-57N IS NOT REVERTED, because for that row the correction was right and the
opposite field is wrong. It is 5.8 x 5.0 x 3.8 mm with 57 uH: at TDK's 0.35 ohm it is an
ordinary small inductor, and at the stored 0.00035 ohm it would be a milliohm-class power
part the size of a grain of rice. Its DC resistance stays corrected. What cannot stand is
its current: 115 A through 0.35 ohm is 4.6 kW, and TDK's database gives that 115 under a
spec whose unit table says "A". Both its saturationCurrentPeak and its unsourced
ratedCurrents are REMOVED rather than rescaled - the vendor's own record for this
discontinued part is not trustworthy enough to divide by 1000 and call it data.

The script that caused this now carries a physics guard: it refuses TDK's figure when
applying it would make Isat^2 * DCR exceed 500 W. The lesson is not "check the vendor" -
the vendor was checked - it is that a source's authority does not survive a collision with
the part's own dimensions.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "revert_overcorrected_tdk_dcr_audit.json"
TODAY = "2026-08-01"

# reference -> the value that was there before, and is correct
REVERT = {
    "B82559A4322A033": 0.00085,
    "B82559A4352A033": 0.00085,
    "B82559A5472A033": 0.0012,
    "B82559A5602A033": 0.0012,
    "B82559A5682A033": 0.0012,
    "B82559A5103A033": 0.0012,
}

STRIP_CURRENTS = {
    "BCM605040-57N": ("TDK's database gives Rated Current 115 under a spec whose unit table "
                      "says amps, which through this part's own 0.35 ohm is 4.6 kW in a "
                      "5.8 x 5.0 x 3.8 mm package. The stored saturationCurrentPeak and the "
                      "unsourced ratedCurrents are removed rather than rescaled: TDK's record "
                      "for this discontinued part (class_id -112) is not reliable enough to "
                      "divide by 1000 and call the result data"),
}

NOTE = ("DC resistance restored to the value stored before 2026-08-01: TDK's published maximum "
        "for this discontinued part (class_id -112) is a 1000x unit error, and applying it would "
        "make this 33 mm choke dissipate kilowatts at its own rated current")


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #387 follow-on correction", "date": TODAY,
             "reverted": [], "currentsRemoved": []}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"B82559A" in raw or b"BCM605040-57N" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["magnetic"]["manufacturerInfo"]
                    di = mi.get("datasheetInfo") or {}
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                ref = str(mi.get("reference") or "")
                el = di.get("electrical")
                variants = el if isinstance(el, list) else ([el] if el else [])
                touched = False

                if ref in REVERT:
                    want = REVERT[ref]
                    for e in variants:
                        if not isinstance(e, dict):
                            continue
                        d = e.get("dcResistances")
                        if isinstance(d, list) and d and isinstance(d[0], dict):
                            d[0].pop("nominal", None)
                            d[0]["maximum"] = want
                            touched = True
                        elif isinstance(e.get("dcResistance"), dict):
                            e["dcResistance"] = {"maximum": want}
                            touched = True
                    if touched:
                        di.setdefault("provenance", []).append(
                            {"source": "manual", "sourceName": NOTE, "retrievedDate": TODAY,
                             "fields": ["electrical.dcResistance"]})
                        audit["reverted"].append({"reference": ref, "restored": want})

                if ref in STRIP_CURRENTS:
                    why = STRIP_CURRENTS[ref]
                    was = {}
                    for e in variants:
                        if not isinstance(e, dict):
                            continue
                        for k in ("saturationCurrentPeak", "ratedCurrents"):
                            if k in e:
                                was[k] = e.pop(k)
                                touched = True
                    if was:
                        di.setdefault("provenance", []).append(
                            {"source": "manual", "sourceName": why, "retrievedDate": TODAY,
                             "fields": ["electrical.saturationCurrentPeak",
                                        "electrical.ratedCurrents"]})
                        audit["currentsRemoved"].append({"reference": ref, "removed": was})

                if touched:
                    line = json.dumps(rec, separators=(",", ":"),
                                      ensure_ascii=False).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"DC resistances reverted: {len(audit['reverted'])}")
    for r in audit["reverted"]:
        print(f"   {r['reference']:22} restored to {r['restored']}")
    print(f"unsupportable currents removed: {len(audit['currentsRemoved'])}")
    for r in audit["currentsRemoved"]:
        print(f"   {r['reference']:22} {r['removed']}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
