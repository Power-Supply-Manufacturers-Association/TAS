#!/usr/bin/env python3
"""Pull Murata's real MLCC catalogue, to re-identify corrupted part numbers (ABT #391).

    python3 scripts/enumerate_murata_catalog.py CATEGORY OUT.jsonl [--page-size N]

WHY A BULK PULL. 3,793 Murata part numbers in the catalogue are unknown to Murata's
own resolver. They are not all wrong in the same way, so there is no mechanical
correction to apply:

    GRM32ER72A105KA35L   one character out — the real part is GRM32CR72A105KA35L,
                         which DigiKey suggested and Murata's API then confirmed
    GRM31CR71H106KA12L   no single-character substitution resolves
    GRM18R60J100KAE      14 chars where a real code is 17-18; no single-character
                         insertion resolves either

Guessing an identity is worse than admitting one is unknown — a wrong part number
silently reassigns a record to a different component. So instead of patching
strings, this pulls the REAL catalogue (41,683 parts for ceramicCapacitorSMD) with
each part's parameters, so a corrupted row can be matched against actual Murata
parts on what it claims to BE — capacitance, voltage, case size — rather than on
what its part number looks like.

A match is only usable when it is UNIQUE and the parameters agree. Where several
real parts fit, or none do, the row stays unidentified and goes to a human. That
distinction is the entire point: re-identification is evidence-led, and ambiguity
is reported rather than resolved by preference.

PACING. The endpoint is slow and honest about it — roughly 0.12 s per part however
the pages are sized, and pageSize 2000 returns a 504. It runs at 500 per page with
a pause between pages, appends each page as it lands, and skips pages already
present so an interrupted run resumes instead of starting over.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
API = "https://pimapi.murata.com/public/api/pim/v1/products/search"
PAGE_PAUSE = 1.5

# The itemInfoList ids that are actually POPULATED, verified against a full dump of
# one part rather than guessed from plausible names. A first pass kept "size" and
# "continuousVoltage" — both come back empty for every part, which silently reduced
# the matcher downstream to capacitance-only and let a 1206 part match a 1210. The
# real fields are sizeCodeInInch and ratedVoltage.
#
# productionStatus and alternativeProducts are kept deliberately: a part marked
# plannedDiscontinue explains why a citation went stale, and Murata's OWN named
# replacement is better evidence than any similarity match this repo could compute.
KEEP = {"partNumWithPackageCode", "partNum", "capacitance", "capacitanceTolerance",
        "sizeCodeInInch", "sizeCodeInMm", "ratedVoltage", "ratedVoltageDc",
        "tempChara", "thickness", "length", "width", "series",
        "productionStatus", "alternativeProducts"}


def fetch_page(cat, page, size):
    body = json.dumps({"searchCondClass": 3, "page": page, "pageSize": size,
                       "productCategoryId": cat, "languageRegion": "en-global",
                       "series": "", "sortKey": "", "valSearchCondList": [],
                       "rangeValSearchCondList": [], "dateRangeSearchCondList": []}).encode()
    req = urllib.request.Request(API, data=body, method="POST",
                                 headers={"User-Agent": UA, "Accept": "application/json",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def flatten(entry):
    out = {}
    for item in entry.get("itemInfoList", []):
        key = item["id"].strip()
        if key not in KEEP:
            continue
        vs = item.get("valueList") or []
        if vs:
            out[key] = {"value": str(vs[0].get("value", "")).strip(),
                        "unit": str(vs[0].get("unit", "")).strip()}
    return out


def main(argv):
    cat = argv[0]
    out_path = Path(argv[1])
    size = int(argv[argv.index("--page-size") + 1]) if "--page-size" in argv else 500

    done_pages = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            try:
                done_pages.add(json.loads(line)["_page"])
            except Exception:
                pass

    first = fetch_page(cat, 1, 1)
    total = first.get("totalNum") or 0
    pages = (total + size - 1) // size
    print(f"{cat}: {total} parts, {pages} pages of {size}, {len(done_pages)} already pulled")

    fh = out_path.open("a", encoding="utf-8")
    got = 0
    for page in range(1, pages + 1):
        if page in done_pages:
            continue
        for attempt in range(3):
            try:
                d = fetch_page(cat, page, size)
                break
            except Exception as e:                                # noqa: BLE001
                print(f"  page {page} attempt {attempt+1}: {type(e).__name__}")
                time.sleep(5 * (attempt + 1))
        else:
            print(f"  page {page} FAILED — left for a resume run")
            continue
        rows = d.get("productSearchResult") or []
        for e in rows:
            fh.write(json.dumps({"_page": page, **flatten(e)}) + "\n")
        fh.flush()
        got += len(rows)
        print(f"  page {page}/{pages}  +{len(rows)}  ({got} this run)")
        time.sleep(PAGE_PAUSE)
    fh.close()
    print(f"done: {got} parts appended -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
