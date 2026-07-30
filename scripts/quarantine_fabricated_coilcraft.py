#!/usr/bin/env python3
"""Quarantine the fourth fabrication batch: 195 invented Coilcraft magnetics.

    python3 scripts/quarantine_fabricated_coilcraft.py [--dry-run]

WHAT THESE ARE. 195 rows across 17 Coilcraft family names, carrying exactly one
provenance entry whose sourceUrl has the shape

    https://www.coilcraft.com/en-us/products/power-inductors/<family>/

That URL scheme does not exist at Coilcraft. Real product URLs are deep and
category-bearing — /en-us/products/power/shielded-inductors/ferrite-drum/lps/lps4018/
returns a 235 KB page titled "LPS4018 Series Low Profile Shielded Power Inductors".
All 17 fabricated URLs instead return one BYTE-IDENTICAL 176,381-byte page titled
"Power Inductors" — the generic category landing page every unknown path falls
through to. The provenance cites a page that was never a product page.

WHY THIS IS FABRICATION AND NOT A SOURCING ERROR. Two of the 17 family names are
real and publish datasheets (EPL2010, EPL3015), so they can be checked field by
field. Against coilcraft.com/pdfs/epl2010.pdf:

    corpus  EPL2010-100ML   1.00 uH    30 mOhm    8.0 A    5.0 x 2.5 x 2.0 mm
    real    EPL2010-102ML   1.0  uH   119 mOhm    1.36 A   2.0 x 2.0 x 1.0 mm

Every field is wrong, including the part number — the real series encodes value as
three digits plus a multiplier (102 = 1.0 uH), not as the corpus's 100/1000 scheme.
The corpus's five lowest-inductance EPL2010 parts have no counterpart at all; the
real series starts at 0.18 uH. EPL3015 tells the same story (real series starts at
0.90 uH, 55 mOhm, 2.45 A).

The generator's fingerprint is visible in the numbers themselves. Across the 195
rows the DC resistance is a single constant per henry — median 30.0 mOhm/uH, p10
27.0, p90 42.4 — and it is the SAME constant for a 4012 and a 6349 package. Real
inductors do the opposite: a bigger core reaching the same inductance needs fewer
turns of thicker wire, so DCR falls with size. The other 4,134 genuine Coilcraft
rows in this catalogue have a median of 5.7 mOhm/uH spread across 1.0 to 62. The
saturation-to-rated ratio is likewise pinned near 1.23 (sd 0.17) where the real
rows scatter over 0.09 to 8.0 (sd 0.52). Inductances follow a clean E12 ladder
identical in all six power families.

HOW IT SURFACED. The rows were flagged by MAG_DISS_DENSITY — DCR * I_rated^2 per
unit of package surface — at up to 8.9 W/cm2, which is what an invented current
paired with an invented DCR produces. The first hypothesis was that only the
DIMENSIONS were wrong (a case code misread as inches), and repairing them was
attempted; it failed, because the derived vendor package made the density WORSE.
That failure is what exposed the rows as invented rather than mis-measured. A
"repair" here would have laundered fabricated data into looking sourced.

This is the fourth fabrication batch found in these catalogues (after ABT #247 and
the phase2-5 generators of ABT #256) and the most convincing: real family names,
a plausible MPN suffix, and a URL that reads correctly at a glance. The earlier
batches announced themselves with codes like Coi000u08050001_50, which is why
scripts/check_no_fabricated_parts.py — keyed to MPN templates — cannot see these.
That guard is extended alongside this quarantine.

Quarantine, never delete: rows move to data/magnetics.quarantine_fabricated.ndjson
with a _validatorQuarantine block, matching the shape used for the fabricated
capacitors of ABT #256.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
QUAR = REPO / "data" / "magnetics.quarantine_fabricated.ndjson"
AUDIT = REPO / "staging" / "coilcraft_fabricated_quarantine_audit.json"

# The URL scheme Coilcraft does not use. Real ones carry a category path:
#   /en-us/products/power/<category>/<subcategory>/<family>/
FAKE_URL = re.compile(
    r"^https?://(?:www\.)?coilcraft\.com/en-us/products/power-inductors/[a-z0-9]+/?$", re.I)

REASON = (
    "fabricated part - provenance cites https://www.coilcraft.com/en-us/products/"
    "power-inductors/<family>/, a URL scheme Coilcraft does not use; all 17 such URLs "
    "return the same 176,381-byte generic 'Power Inductors' category page, never a "
    "product page. Checked field-by-field against the two families that are real and "
    "publish datasheets: corpus EPL2010-100ML claims 1.0uH/30mOhm/8.0A in 5.0x2.5x2.0mm "
    "where the real EPL2010-102ML is 1.0uH/119mOhm/1.36A in 2.0x2.0x1.0mm, and the "
    "corpus's five lowest-inductance parts do not exist (the real series starts at "
    "0.18uH). Generator fingerprint: DC resistance is one constant per henry "
    "(median 30.0 mOhm/uH, p10 27.0, p90 42.4) identical across five different package "
    "sizes, against 5.7 mOhm/uH spread over 1.0-62 for the 4,134 genuine Coilcraft rows; "
    "Isat/Irated pinned at 1.23 (sd 0.17) vs 1.13 (sd 0.52) genuine; E12 inductance "
    "ladder repeated across families. Surfaced via MAG_DISS_DENSITY up to 8.9 W/cm2 "
    "(ABT #351). Fourth fabrication batch, after ABT #247 and the phase2-5 generators "
    "of ABT #256."
)
CODES = ["GEN_FABRICATED_PROVENANCE_URL", "GEN_FABRICATED_DCR_CONSTANT"]
DATE = "2026-07-30"


def is_fabricated(rec) -> bool:
    try:
        di = rec["magnetic"]["manufacturerInfo"]["datasheetInfo"]
    except Exception:
        return False
    prov = di.get("provenance") or []
    if len(prov) != 1 or not isinstance(prov[0], dict):
        return False
    return bool(FAKE_URL.match(str(prov[0].get("sourceUrl", ""))))


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    taken, kept = [], 0
    out_q = []

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            rec = None
            if b"power-inductors/" in raw:
                try:
                    rec = json.loads(raw)
                except Exception:
                    rec = None
            if rec is not None and is_fabricated(rec):
                info = rec["magnetic"]["manufacturerInfo"]
                ref = str(info.get("reference"))
                url = rec["magnetic"]["manufacturerInfo"]["datasheetInfo"]["provenance"][0].get("sourceUrl")
                rec["_validatorQuarantine"] = {
                    "date": DATE, "reason": REASON, "codes": CODES,
                    "messages": [f"provenance sourceUrl {url} is not a Coilcraft product page"],
                }
                out_q.append(json.dumps(rec, separators=(",", ":")))
                taken.append({"reference": ref, "manufacturer": str(info.get("name")), "sourceUrl": url})
            else:
                out.write(raw)
                kept += 1
        out.flush()
        os.fsync(out.fileno())

    from collections import Counter
    fams = Counter(re.sub(r"[-_].*$", "", t["reference"]) for t in taken)
    print(f"quarantining {len(taken)} rows, keeping {kept}")
    for f, n in sorted(fams.items()):
        print(f"  {n:4}  {f}")

    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing moved")
        return 0

    with open(QUAR, "a", encoding="utf-8") as q:
        for line in out_q:
            q.write(line + "\n")
        q.flush()
        os.fsync(q.fileno())
    os.replace(tmp, DATA)
    AUDIT.write_text(json.dumps(
        {"ticket": "ABT #351 (fourth fabrication batch)", "date": DATE,
         "quarantineFile": QUAR.name, "reason": REASON, "codes": CODES,
         "families": dict(sorted(fams.items())), "rows": taken}, indent=1))
    print(f"\nappended {len(out_q)} rows -> {QUAR}")
    print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
