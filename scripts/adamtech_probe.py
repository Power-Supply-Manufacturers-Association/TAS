#!/usr/bin/env python3
"""Discover every Adam Tech LEAF product category by probing the id space.

The category tree at /categories/index/<id> is not fully linked (group pages such as
"Board to Board Connectors" list no children anywhere), so a BFS only reaches ~20 % of the
catalogue.  Leaf ids are dense small integers, and a leaf is unambiguous: GET
/products/index/category:<id> returns a <table class="element-product-table"> plus
"Showing results a-b on Page p of P".  Probe the range, keep the hits.

Writes /tmp/adamtech/leaves2.json  {id: {"title":..., "pages": n, "parts": total}}
"""
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

OUT = "/tmp/adamtech"
HOST = "https://app.adam-tech.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return ""
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1 + 2 * i)
    raise RuntimeError(f"{url}: {last}")


def probe(cid):
    h = get(f"{HOST}/products/index/category:{cid}")
    if "element-product-table" not in h:
        return cid, None
    t = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
    title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t.group(1))).strip() if t else ""
    m = re.search(r"Showing results ([\d,]+)-([\d,]+) on Page (\d+) of (\d+)",
                  re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)))
    pages = int(m.group(4)) if m else 1
    per = int(m.group(2).replace(",", "")) if m else 50
    return cid, {"title": title, "pages": pages, "perPage": per}


def main():
    os.makedirs(OUT, exist_ok=True)
    lo, hi = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (2900, 3500)
    found = {}
    path = f"{OUT}/leaves2.json"
    if os.path.exists(path):
        found = json.load(open(path))
    todo = [c for c in range(lo, hi + 1) if str(c) not in found]
    n = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for cid, info in ex.map(probe, todo):
            n += 1
            if info:
                found[str(cid)] = info
            if n % 100 == 0:
                json.dump(found, open(path, "w"))
                print(f"  probed {n}/{len(todo)} -> {len(found)} leaves", flush=True)
    json.dump(found, open(path, "w"))
    print(json.dumps({"probed": len(todo), "leaves": len(found),
                      "listing_pages": sum(v["pages"] for v in found.values())}))


if __name__ == "__main__":
    main()
