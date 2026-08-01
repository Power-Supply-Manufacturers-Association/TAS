#!/usr/bin/env python3
"""Give Samsung MLCC rows the dielectric Samsung's own catalogue assigns them.

    python3 scripts/fix_samsung_dielectric_code.py [--dry-run]

Found while sourcing the EIA Z tolerance for ABT #428. All 466 Samsung CL rows carry
`technology: ceramic-class-2` and no `dielectricCode` whatsoever, regardless of what the
part number says. The characteristic letter is the third field of the code and Samsung
publishes exactly what it means, in the December 2025 MLCC catalogue, section
"3 DIELECTRIC CODE", grouped by class:

    Class I  (Temperature Compensation)   C = C0G     G = X8G
    Class II (High Dielectric Constant)   A = X5R  X = X6S  W = X6T  B = X7R
                                          K = X7R(S)  Y = X7S  Z = X7T  F = Y5V
                                          M = X8M  E = X8L  J = JIS-B

So 182 rows are C0G - a Class I temperature-compensating dielectric - stored as Class II.
That is not a labelling nicety: Class I is the flat, low-loss, no-DC-bias-derating
dielectric an engineer picks for a timing or filter network, and Class II is the one that
loses most of its capacitance under bias. A part search that trusts the class field would
return C0G parts as Class II and hide them from the query that actually wants them.

THE Y5V ROWS ARE DELIBERATELY LEFT AS CLASS II. It is tempting to move them to
ceramic-class-3, since Y5V is the textbook Class III example and the corpus already files
Vishay's Y5V parts that way. But Samsung's own catalogue prints Y5V under "Class II (High
Dielectric Constant)", and this script's whole warrant is that Samsung says so. Changing
them would mean overriding the source document with a general rule - the substitution
this campaign exists to stop. The resulting inconsistency with the Vishay rows is real and
is filed as a question about the vocabulary rather than papered over here.

Both fields are written from the SAME letter in the SAME table, so a row either gets a
dielectric code Samsung defines or is left alone; no letter is guessed at.
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
AUDIT = REPO / "staging" / "samsung_dielectric_audit.json"
TODAY = "2026-08-01"

# Samsung MLCC catalogue, December 2025, section 3 "DIELECTRIC CODE".
CLASS1 = {"C": "C0G", "G": "X8G"}
CLASS2 = {"A": "X5R", "X": "X6S", "W": "X6T", "B": "X7R", "K": "X7R",
          "Y": "X7S", "Z": "X7T", "F": "Y5V", "M": "X8M", "E": "X8L", "J": "JIS-B"}

CL = re.compile(r"^CL\d{2}([A-Z])")
SOURCE_URL = "https://product.samsungsem.com/resources/file/product-catalog/MLCC_2512.pdf"
SOURCE_NAME = ("Samsung Electro-Mechanics MLCC catalogue, December 2025 (MLCC_2512.pdf), "
               "section 3 'DIELECTRIC CODE' - the characteristic letter of the part number "
               "and the class Samsung files it under")


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #428 follow-on", "date": TODAY, "source": SOURCE_NAME,
             "codeAdded": [], "technologyFixed": [], "counts": Counter()}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"Samsung" in raw and b'"CL' in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["capacitor"]["manufacturerInfo"]
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                ref = str(mi.get("reference") or "")
                m = CL.match(ref)
                if m and "Samsung" in str(mi.get("name")):
                    letter = m.group(1)
                    eia = CLASS1.get(letter) or CLASS2.get(letter)
                    if eia:
                        di = mi.setdefault("datasheetInfo", {})
                        part = di.setdefault("part", {})
                        want_tech = "ceramic-class-1" if letter in CLASS1 else "ceramic-class-2"
                        touched = []
                        if part.get("dielectricCode") != eia:
                            part["dielectricCode"] = eia
                            audit["codeAdded"].append({"reference": ref, "code": eia})
                            touched.append("dielectricCode")
                        if part.get("technology") != want_tech:
                            audit["technologyFixed"].append(
                                {"reference": ref, "was": part.get("technology"),
                                 "now": want_tech, "dielectric": eia})
                            part["technology"] = want_tech
                            touched.append("technology")
                        if touched:
                            di.setdefault("provenance", []).append({
                                "source": "manufacturerDatasheet",
                                "sourceName": SOURCE_NAME,
                                "sourceUrl": SOURCE_URL,
                                "retrievedDate": TODAY,
                                "fields": [f"part.{t}" for t in touched]})
                            audit["counts"][eia] += 1
                            line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows given a dielectric code:      {len(audit['codeAdded'])}")
    print(f"rows whose class was corrected:    {len(audit['technologyFixed'])}")
    for k, v in audit["counts"].most_common():
        print(f"     {v:5}  {k}")
    for f in audit["technologyFixed"][:4]:
        print(f"       {f['reference']:20} {f['dielectric']:6} {f['was']} -> {f['now']}")
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
