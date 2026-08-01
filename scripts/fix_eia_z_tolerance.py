#!/usr/bin/env python3
"""Restore the +80/-20 % capacitance band on EIA Z-coded parts (ABT #428).

    python3 scripts/fix_eia_z_tolerance.py [--dry-run]

ABT #356 fixed 150 Vishay rows whose min/max were stored inverted, and deliberately
left the MAGNITUDE alone: the parts carry a Z tolerance code, EIA Z is +80 %/-20 %, and
not one row in the catalogue stored that. Deciding it there would have replaced a
provable swap with a guess. This is the follow-up that settles it from the datasheets.

HOW THE Z ROWS WERE FOUND. The ticket counted 377 rows by scanning part numbers for a
tolerance letter. That over-counts: a letter is only a tolerance code if it sits where
one belongs. Here the position is established by DECODING - every 3-digit run in the
part number is read as an EIA capacitance code, and the letter is only taken as the
tolerance when the code it follows evaluates to the capacitance the record actually
stores. A103Z15Y5VF5TAA -> "103" = 10 x 10^3 pF = 10 nF = the stored nominal, so the
next character, Z, is the tolerance. That yields 169 rows, all four vendors agreeing
with their own part-number grammar, instead of 377 rows found by coincidence.

WHAT EACH VENDOR'S OWN DOCUMENT SAYS.

  Vishay A series, 25 rows - document 45164 rev 17-Jan-06 ("Mono-Axial, Dipped Axial
  Multilayer Ceramic Capacitors") gives per-dielectric tolerances C0G +-5/+-10 %,
  X7R +-10/+-20 %, Y5V + 80/- 20 %, a code table reading "Z = + 80 /- 20 %", and clear
  text tables headed "+ 80 /- 20 % TOLERANCE" that NAME 23 of these 25 part numbers.

  Vishay K series, 125 rows - document 45171 rev 21-Aug-13 covers Y5V as Class 3 and
  states outright: "The tolerance codes are J = 5 %, K = 10 %, M = 20 % and
  Z = + 80 %/- 20 %."

  Samsung, 13 rows - the December 2025 MLCC catalogue gives the part-number tolerance
  table "J +-5 %, K +-10 %, M +-20 %, Z -20, +80 %" and the characteristic table
  "F = Y5V"; every one of these 13 is CL..F...Z... Samsung's own specification sheet
  for CL10F104ZB8NNNC - one of the 13 - describes it as "CAP, 100nF, 50V, -20/+80%,
  Y5V, 0603" and lists "Capacitance tolerance -20/+80%".

  TDK, 5 rows - TDK's own Meister catalogue database holds 478 CK45 parts. Every K-coded
  one is +-10 % (394 of 394) and 79 of 84 Z-coded ones are "+80,-20%". The 5 exceptions
  are exactly these 5 rows, all end-of-life entries, and EACH ONE's own active
  replacement part - same capacitance, dielectric and voltage, differing only in a
  packaging character - is recorded as "+80,-20%". The +-5 % is a vendor data error in
  five superseded rows, not a different part.

CITING AN ARCHIVED REVISION, ON PURPOSE. Vishay's live 45164 and 45171 are 2025
revisions that no longer cover Y5V at all - the dielectric was dropped from both. So the
document that proves the tolerance is the earlier revision, and citing the bare
vishay.com URL would produce a citation whose current content does not mention Y5V or
the part: exactly the dead-citation defect this campaign exists to remove, and
verify_provenance_content.py would rightly flag it. Each provenance entry therefore
names the document number AND revision, points sourceUrl at a copy that actually
contains the evidence, and records the SHA-256 of the PDF that was read, so the precise
document can be re-identified later rather than taken on trust.

WHAT IS DELIBERATELY NOT TOUCHED.

  * A334Z20Y5VF5TAA / A334Z20Y5VF5UAA. "A334Z20" appears NOWHERE in Vishay's datasheet.
    The document's Z20 parts in an F5 body are A105, A474 and A684 only, and 0.33 uF
    exists solely as A334Z15 (50 V). These two read as a real part number with its
    voltage code altered. Applying a tolerance to a part number the vendor does not
    publish would be dressing a suspect row in better evidence, so they are left as they
    are and reported.

  * C06BL851Z-5ZN-X0T (Knowles). Its datasheet specifies "Capacitance Guaranteed Minimum
    Value: 850 pF" - a GMV part with no symmetric tolerance published at all - and lists
    the X variant, not Z. There is no evidence for the stored +-5 %, and none for
    +80/-20 either. Guessing either way is the failure mode; it is reported unresolved.

Nothing here is computed from a rule of thumb: 0.8x and 1.8x follow from a tolerance the
vendor states in its own document for these exact families and codes.
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
AUDIT = REPO / "staging" / "eia_z_tolerance_audit.json"
TODAY = "2026-08-01"

# The evidence, one entry per vendor family. `match` decides membership from the part
# number alone; the decode check below still has to agree before anything is written.
EVIDENCE = [
    {
        "key": "vishay-A",
        "match": lambda p: p.startswith("A") and "Y5V" in p,
        "source": "manufacturerDatasheet",
        "sourceName": (
            "Vishay document 45164 rev 17-Jan-06, 'Mono-Axial - Dipped Axial Multilayer "
            "Ceramic Capacitors': Y5V tolerance '+ 80 /- 20 %', code table 'Z = + 80 /- 20 %', "
            "and clear-text tables headed '+ 80 /- 20 % TOLERANCE' naming this part. Archived "
            "revision - vishay.com/doc?45164 now serves a 2025 revision that no longer covers "
            "Y5V. PDF sha256 1146bb10e8677818ecc8079194a744b4ef6f11e21b8ce03cdbb2fc606a98f4f4"
        ),
        "sourceUrl": "https://www.taydaelectronics.com/datasheets/files/A-4793.PDF",
    },
    {
        "key": "vishay-K",
        "match": lambda p: p.startswith("K") and "Y5V" in p,
        "source": "manufacturerDatasheet",
        "sourceName": (
            "Vishay document 45171 rev 21-Aug-13, 'K Series Radial Leaded Multilayer Ceramic "
            "Capacitors': covers Y5V as Class 3 and states 'The tolerance codes are J = 5 %, "
            "K = 10 %, M = 20 % and Z = + 80 %/- 20 %.' Archived revision - vishay.com/doc?45171 "
            "now serves rev 05-Sep-2025 which no longer covers Y5V. PDF sha256 "
            "6e0464b329f8935e405cf1e0b85aeb66f5b3cdcab7bfa2765a93c22b126a3e7b"
        ),
        "sourceUrl": ("https://eecs.engineering.oregonstate.edu/education/"
                      "inventory_datasheets/P78-1418683447.pdf"),
    },
    {
        "key": "samsung",
        "match": lambda p: p.startswith("CL") and re.match(r"^CL\d\dF", p) is not None,
        "source": "manufacturerDatasheet",
        "sourceName": (
            "Samsung Electro-Mechanics MLCC catalogue, December 2025 (MLCC_2512.pdf): "
            "part-number tolerance table 'Z = -20, +80%' and characteristic table 'F = Y5V'. "
            "Corroborated by Samsung's specification sheet for CL10F104ZB8NNNC, one of these "
            "rows, reading 'CAP, 100nF, 50V, -20/+80%, Y5V, 0603'. PDF sha256 "
            "99f87a705209ab4e097df75e0bdaf61cb7abd162b60e88fdb376d121f952e0b7"
        ),
        "sourceUrl": ("https://product.samsungsem.com/resources/file/product-catalog/"
                      "MLCC_2512.pdf"),
    },
    {
        "key": "tdk",
        "match": lambda p: p.startswith("CK45-"),
        "source": "manufacturerDatabase",
        "sourceName": (
            "TDK Meister catalogue database (TstDB.tmdb, spec 301000010 'Tolerance for "
            "Capacitance'): across 478 CK45 parts every K-coded part is +-10% and Z-coded parts "
            "are '+80,-20%'; the 5 end-of-life rows recording +-5% are contradicted by their own "
            "active replacement parts, which are '+80,-20%' at identical capacitance, dielectric "
            "and voltage"
        ),
        "sourceUrl": "https://product.tdk.com/en/search/capacitor/ceramic/lead-disc/",
    },
]

# Part numbers the vendor's own document does NOT publish. Left untouched on purpose.
WITHHELD = {
    "A334Z20Y5VF5TAA": "'A334Z20' appears nowhere in Vishay 45164; its Z20 parts in an F5 body "
                       "are A105/A474/A684 only, and 0.33 uF exists solely as A334Z15 (50 V)",
    "A334Z20Y5VF5UAA": "'A334Z20' appears nowhere in Vishay 45164; its Z20 parts in an F5 body "
                       "are A105/A474/A684 only, and 0.33 uF exists solely as A334Z15 (50 V)",
    "C06BL851Z-5ZN-X0T": "Knowles specifies this as a guaranteed-minimum-value part ('Capacitance "
                         "Guaranteed Minimum Value 850 pF') with no symmetric tolerance published, "
                         "and the datasheet lists the X variant, not Z",
}

NOTE = ("capacitance minimum/maximum rebuilt from the vendor-stated +80/-20 % EIA Z tolerance "
        "(ABT #428)")


def z_coded(mpn, nominal):
    """True when a Z sits immediately after the EIA code that decodes to `nominal`.

    Position is what makes a letter a tolerance code. Scanning for a bare "Z" also
    matches dielectric codes, packaging letters and plain coincidence.
    """
    for i in range(len(mpn) - 3):
        s = mpn[i:i + 3]
        if not s.isdigit():
            continue
        val = int(s[:2]) * 10 ** int(s[2]) * 1e-12
        if nominal and abs(val - nominal) <= nominal * 1e-6 and mpn[i + 3].upper() == "Z":
            return True
    return False


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #428", "date": TODAY, "note": NOTE,
             "fixed": [], "withheld": [], "byEvidence": Counter()}
    seen_z = 0

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"Z" in raw and b'"capacitance"' in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["capacitor"]["manufacturerInfo"]
                    di = mi["datasheetInfo"]
                    cap = (di.get("electrical") or {}).get("capacitance") or {}
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                part = di.get("part") or {}
                mpn = str(mi.get("reference") or part.get("partNumber") or "")
                nom, mn, mx = cap.get("nominal"), cap.get("minimum"), cap.get("maximum")
                ok = (all(isinstance(v, (int, float)) and not isinstance(v, bool)
                          for v in (nom, mn, mx)) and nom > 0)
                # Only rows that STORE a band contradicting +80/-20 are in scope. A row
                # with no bounds at all is missing data, not wrong data, and filling it
                # from a code letter would be manufacturing values under this ticket's
                # cover - 79 TDK rows would have been invented that way.
                if ok and mpn and z_coded(mpn, nom) and not (
                        abs(mn - nom * 0.80) <= nom * 1e-9
                        and abs(mx - nom * 1.80) <= nom * 1e-9):
                    seen_z += 1
                    if mpn in WITHHELD:
                        audit["withheld"].append({"reference": mpn, "reason": WITHHELD[mpn],
                                                  "nominal": nom,
                                                  "leftMinimum": cap.get("minimum"),
                                                  "leftMaximum": cap.get("maximum")})
                    else:
                        ev = next((e for e in EVIDENCE if e["match"](mpn)), None)
                        if ev:
                            was = (cap.get("minimum"), cap.get("maximum"))
                            cap["minimum"] = nom * 0.80
                            cap["maximum"] = nom * 1.80
                            prov = di.setdefault("provenance", [])
                            prov.append({
                                "source": ev["source"],
                                "sourceName": ev["sourceName"],
                                "sourceUrl": ev["sourceUrl"],
                                "retrievedDate": TODAY,
                                "fields": ["electrical.capacitance.minimum",
                                           "electrical.capacitance.maximum"]})
                            audit["fixed"].append({
                                "reference": mpn, "evidence": ev["key"], "nominal": nom,
                                "wasMinimum": was[0], "wasMaximum": was[1],
                                "nowMinimum": cap["minimum"], "nowMaximum": cap["maximum"]})
                            audit["byEvidence"][ev["key"]] += 1
                            line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"Z-coded rows seen (position-verified): {seen_z}")
    print(f"repaired to +80/-20 %:                 {len(audit['fixed'])}")
    for k, v in audit["byEvidence"].most_common():
        print(f"     {v:4}  {k}")
    print(f"withheld (no vendor evidence):         {len(audit['withheld'])}")
    for w in audit["withheld"]:
        print(f"     {w['reference']:22} {w['reason'][:88]}")
    for f in audit["fixed"][:3]:
        print(f"  e.g. {f['reference']:22} nom={f['nominal']:.3g}  "
              f"min {f['wasMinimum']:.3g}->{f['nowMinimum']:.3g}  "
              f"max {f['wasMaximum']:.3g}->{f['nowMaximum']:.3g}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        audit["byEvidence"] = dict(audit["byEvidence"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
