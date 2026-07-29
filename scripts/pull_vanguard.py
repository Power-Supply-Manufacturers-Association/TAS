#!/usr/bin/env python3
"""Vendor-direct pull of the Vanguard Electronics catalog (ABT #351 class B).

    python3 scripts/pull_vanguard.py out.json [--limit N]

Vanguard's store at ve1.com is WordPress/WooCommerce, and its Store API is public
and ungated — plain HTTP, no browser needed:

    https://www.ve1.com/wp-json/wc/store/v1/products?per_page=100&page=N

which is the same source the corpus rows cite ("Vanguard Electronics WooCommerce
API (ve1)"). Each product carries its spec table as WooCommerce attributes:

    Series, Inductance (uH), DCR Max (OHMS), DC Current Max (A), Q min,
    Test Freq (MHz), SRF min (GHz), Operating Temperature, ...

THE UNIT TRAP THIS PULL EXISTS TO EXPOSE

The attribute is labelled "DC Current Max (A)" and R50029-NT-J carries 170. Read
as amps that part — 1.5 uH, 1.7 ohm DCR — would dissipate 49 kW. Vanguard's OWN
datasheet for the series (C50000-Series_COTS-Chip-Inductors_RevF.pdf) states
"Current Rating (mA): 85 to 630". The value is right; the website's unit label is
wrong, and the corpus faithfully reproduced the mislabel.

So this pull captures the attributes VERBATIM, including the "(A)" label. It does
not silently rescale anything. Deciding the true unit is a per-series question
answered by that series' datasheet, and the datasheet URL is captured with each
product so the check is reproducible.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

API = "https://www.ve1.com/wp-json/wc/store/v1/products"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")
PER_PAGE = 100


def fetch(url: str) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read(), dict(r.headers)


def main(argv: list[str]) -> int:
    if not argv:
        sys.exit(__doc__)
    out = argv[0]
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None

    _, headers = fetch(f"{API}?per_page=1")
    total = int(headers.get("x-wp-total", 0))
    pages = (total + PER_PAGE - 1) // PER_PAGE
    if limit:
        pages = min(pages, (limit + PER_PAGE - 1) // PER_PAGE)
    print(f"catalog: {total} products, {pages} pages of {PER_PAGE}")

    rows, seen = [], set()
    for page in range(1, pages + 1):
        body, _ = fetch(f"{API}?per_page={PER_PAGE}&page={page}")
        batch = json.loads(body)
        if not batch:
            print(f"page {page}: empty — stopping")
            break
        for p in batch:
            sku = p.get("sku") or p.get("name")
            if not sku or sku in seen:
                continue
            seen.add(sku)
            attrs = {}
            for a in p.get("attributes", []):
                terms = [t.get("name", "") for t in a.get("terms", [])]
                attrs[a.get("name", "?")] = terms[0] if len(terms) == 1 else terms
            rows.append({"sku": sku, "name": p.get("name"), "permalink": p.get("permalink"),
                         "categories": [c.get("name") for c in p.get("categories", [])],
                         "attributes": attrs})
        if page % 10 == 0 or page == pages:
            print(f"  page {page}/{pages}: {len(rows)} products")
        time.sleep(0.4)                      # polite to the vendor

    with open(out, "w") as f:
        json.dump({"source": API, "total": total, "products": rows}, f)
    print(f"\nsaved {len(rows)} products -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
