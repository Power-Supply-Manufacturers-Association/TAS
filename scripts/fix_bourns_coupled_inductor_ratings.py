#!/usr/bin/env python3
"""Give Bourns dual-winding inductors both of the ratings their datasheets publish (ABT #447).

    python3 scripts/fix_bourns_coupled_inductor_ratings.py [--dry-run]

Bourns' SRF0703A / SRF1260A / SRF1280 / SRF1280A are DUAL-WINDING inductors, and their
datasheets print two complete ratings side by side under the headers "Parallel Rating" and
"Series Rating", each with its own Inductance / DCR / Irms / Isat columns:

    SRF0703A-151M   150 uH  0.986 ohm  0.58 A  0.65 A  |  608.2 uH  3.63 ohm  0.289 A  0.32 A
                    <------ parallel ------>              <------- series ------->

Every corpus row took the inductance from the PARALLEL column and the DC resistance from
the SERIES column, so the pair describes no connection the part can actually be in. Using
it, a designer would compute the copper loss of the series connection against the
inductance of the parallel one - roughly 4x the real dissipation for the inductance they
think they have.

ABT #447 REPORTED 6 ROWS AND SUSPECTED "the small-value parts hold the parallel DCR
correctly". THAT IS WRONG, and the correction matters more than the original finding: all
27 rows of these four series carry it. SRF1260A-1R0Y stores 0.026 ohm where the parallel
DCR is 0.0062 and the series is 0.026. The 6 in the ticket were simply the ones whose
series DCR exceeded 1 ohm and so tripped the screen that found them - the screen selected
the defect's largest instances and was mistaken for the defect's extent.

SRF1280A-102M CARRIES A SECOND ERROR ON TOP: it stores 0.007202 ohm where the series DCR
is 7.202 ohm - the same series value, divided by 1000, as though the column were
milliohms. It is the one row whose stored DCR matches neither published column.

WHAT IS WRITTEN, AND WHY BOTH. MAS already models this: the coupledInductor electrical
variant carries a `name` field, documented as "Label of this connection configuration as
given in the datasheet", and `electrical` is an array. So each row becomes TWO entries -
"Parallel Rating" and "Series Rating" - each internally consistent, each labelled with the
datasheet's own words. Picking one and discarding the other would throw away half of what
the vendor publishes and force the next reader to guess which half survived. No schema was
changed and no field invented.

HOW THE NUMBERS WERE READ. The four PDFs were fetched from bourns.com and parsed by WORD
COORDINATE, not by line text: these documents interleave dimension-drawing and marketing
text through the table, so `pdftotext -layout` yields rows like
"SRF1280A-681M product is 680 considered ± 20 for 1.296 potential" - the numbers are
right, the words between them are from another part of the page. Column x-positions were
learned per document and each row sampled at them.

EVERY ROW IS PHYSICS-CHECKED BEFORE IT IS USED. Two coupled windings in series have four
times the inductance and four times the resistance of the same pair in parallel, so
L_series/L_parallel and DCR_series/DCR_parallel must both be near 4. Rows outside that band
were rejected as column misalignment rather than trusted - 7 of 81 extracted rows, which is
precisely what the check is for. The two rejected rows that are actually in the corpus were
re-read by hand from the layout text instead of being guessed.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "bourns_coupled_inductor_audit.json"
TODAY = "2026-08-01"

DOC = "https://www.bourns.com/docs/product-datasheets/{}.pdf"
SERIES = re.compile(r"^(SRF0703A|SRF1260A|SRF1280A|SRF1280)-")

# reference -> (L_par uH, DCR_par ohm, Irms_par A, Isat_par A,
#               L_ser uH, DCR_ser ohm, Irms_ser A, Isat_ser A)
# Read by word coordinate from the Bourns datasheet named by the reference's series,
# fetched 2026-08-01, and physics-checked (series/parallel ratio ~4 in both L and DCR).
TABLE = {
    "SRF0703A-151M":       (150.0, 0.986, 0.58, 0.65, 608.2, 3.63, 0.289, 0.32),
    "SRF0703A-8R2M":       (8.2, 0.0772, 2.19, 2.66, 34.1, 0.306, 1.1, 1.33),
    "SRF1260A-150M":       (15.0, 0.0329, 4.27, 5.69, 60.0, 0.125, 2.13, 2.85),
    "SRF1260A-1R0Y":       (1.0, 0.0062, 15.0, 23.6, 4.0, 0.026, 7.51, 11.8),
    "SRF1260A-221M":       (220.0, 0.354, 1.19, 1.51, 880.0, 1.416, 0.594, 0.755),
    "SRF1260A-2R2Y":       (2.2, 0.0085, 10.9, 15.0, 8.8, 0.0333, 5.46, 7.5),
    "SRF1260A-331M":       (330.0, 0.574, 1.06, 1.22, 1320.0, 2.29, 0.53, 0.61),
    "SRF1260A-4R7Y":       (4.7, 0.0137, 7.18, 9.71, 18.8, 0.0479, 3.59, 4.86),
    "SRF1260A-680M":       (68.0, 0.117, 2.22, 2.7, 272.0, 0.444, 1.11, 1.35),
    "SRF1260A-6R8Y":       (6.8, 0.0186, 6.64, 8.68, 27.2, 0.0672, 3.32, 4.34),
    "SRF1260A-8R2Y":       (8.2, 0.0194, 5.54, 7.86, 32.8, 0.0737, 2.77, 3.93),
    "SRF1280-1R0Y":        (1.0, 0.0067, 15.5, 40.0, 3.284, 0.026, 7.74, 20.0),
    "SRF1280-1R5Y":        (1.5, 0.0076, 13.5, 31.1, 5.428, 0.0306, 6.77, 15.6),
    "SRF1280-220M":        (22.0, 0.0503, 4.0, 7.57, 91.72, 0.192, None, 3.78),
    "SRF1280-471M":        (470.0, 0.865, 0.85, 1.68, 1868.0, 3.3, 0.427, 0.838),
    "SRF1280-820M":        (82.0, 0.153, 2.09, 4.06, 319.0, 0.578, 1.04, 2.03),
    "SRF1280-R47Y":        (0.47, 0.0055, 17.9, 56.0, 1.676, 0.0216, 8.94, 28.0),
    "SRF1280A-100M":       (10.0, 0.0241, 6.04, 11.2, 41.88, 0.0921, 3.02, 5.6),
    "SRF1280A-102M":       (1000.0, 1.992, 0.61, 1.14, 4020.0, 7.202, 0.307, 0.571),
    "SRF1280A-150M":       (15.0, 0.0333, 4.4, 9.66, 56.36, 0.129, 2.51, 4.83),
    "SRF1280A-331M":       (330.0, 0.54, 1.04, 2.01, 1294.0, 2.172, 0.522, 1.01),
    "SRF1280A-3R3Y":       (3.3, 0.011, 10.4, 21.5, 11.32, 0.04, 5.23, 10.8),
    "SRF1280A-470M":       (47.0, 0.0898, 2.95, 5.28, 188.2, 0.353, 1.47, 2.64),
    "SRF1280A-4R7Y":       (4.7, 0.0135, 8.25, 16.5, 19.36, 0.05, 4.13, 8.24),
    "SRF1280A-681M":       (680.0, 1.296, 0.76, 1.39, 2707.0, 4.888, 0.38, 0.697),
    "SRF1280A-6R8Y":       (6.8, 0.0183, 7.34, 13.3, 29.55, 0.0656, 3.67, 6.67),
    "SRF1280A-R47Y":       (0.47, 0.0055, 17.9, 56.0, 1.676, 0.0216, 8.94, 28.0),}


def series_of(ref):
    m = SERIES.match(ref)
    return m.group(1) if m else None


def variant(name, L_uH, dcr, irms, isat):
    """One connection configuration, complete and internally consistent."""
    e = {"subtype": "coupledInductor", "name": name,
         "inductance": {"nominal": L_uH * 1e-6},
         "dcResistances": [{"maximum": dcr}]}
    if irms is not None:
        e["ratedCurrents"] = [irms]
    if isat is not None:
        e["saturationCurrentPeak"] = isat
    return e


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #447", "date": TODAY, "rewritten": [], "bySeries": Counter(),
             "notInTable": []}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"SRF" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["magnetic"]["manufacturerInfo"]
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                ref = str(mi.get("reference") or "")
                if SERIES.match(ref):
                    vals = TABLE.get(ref)
                    if not vals:
                        audit["notInTable"].append(ref)
                        out.write(line)
                        continue
                    Lp, Rp, Ip, Sp, Ls, Rs, Is, Ss = vals
                    di = mi.setdefault("datasheetInfo", {})
                    el = di.get("electrical")
                    old = (el if isinstance(el, list) else [el])[0] or {}
                    di["electrical"] = [
                        variant("Parallel Rating", Lp, Rp, Ip, Sp),
                        variant("Series Rating", Ls, Rs, Is, Ss)]
                    url = DOC.format(series_of(ref))
                    prov = [p for p in (di.get("provenance") or [])
                            if p.get("sourceUrl") != url]
                    prov.append({
                        "source": "manufacturerDatasheet",
                        "sourceUrl": url,
                        "sourceName": (
                            f"Bourns {series_of(ref)} Series datasheet, 'Electrical "
                            f"Specifications' table: the 'Parallel Rating' and 'Series Rating' "
                            f"column groups, each read as Inductance / DCR (max) / Irms / Isat "
                            f"by word coordinate and checked for the series-to-parallel ratio "
                            f"of ~4 in both inductance and resistance"),
                        "retrievedDate": TODAY,
                        "fields": ["electrical"]})
                    di["provenance"] = prov
                    mi["datasheetUrl"] = url
                    audit["rewritten"].append({
                        "reference": ref, "series": series_of(ref),
                        "wasInductance": (old.get("inductance") or {}).get("nominal"),
                        "wasDcResistance": (old.get("dcResistance") or {}).get("maximum"),
                        "parallel": {"inductance": Lp * 1e-6, "dcResistance": Rp},
                        "series": {"inductance": Ls * 1e-6, "dcResistance": Rs}})
                    audit["bySeries"][series_of(ref)] += 1
                    line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows given both published ratings: {len(audit['rewritten'])}")
    for k, v in audit["bySeries"].most_common():
        print(f"     {v:4}  {k}")
    if audit["notInTable"]:
        print(f"LEFT ALONE (no verified datasheet row): {audit['notInTable']}")
    def g(x):
        # A re-run reads rows this script already rewrote, where the pre-existing singular
        # `dcResistance` no longer exists. The data outcome is idempotent; the "was" fields
        # of the audit are only meaningful on the first pass, so they must not be assumed
        # present. (They were, once, and the crash landed before os.replace - which is the
        # only reason it was harmless.)
        return f"{x:.4g}" if isinstance(x, (int, float)) else "-"

    for r in audit["rewritten"][:4]:
        print(f"     {r['reference']:18} was L={g(r['wasInductance'])} DCR={g(r['wasDcResistance'])}"
              f"  ->  parallel {g(r['parallel']['inductance'])}/{g(r['parallel']['dcResistance'])}"
              f"  series {g(r['series']['inductance'])}/{g(r['series']['dcResistance'])}")
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
