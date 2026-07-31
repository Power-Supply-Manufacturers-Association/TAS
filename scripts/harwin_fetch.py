#!/usr/bin/env python3
"""Pull the full Harwin catalogue from its public JSON API.

Endpoints (plain curl-able, no bot protection, no auth):
  list   GET https://api.harwin.com/v1/products?page=<n>&pageSize=500
         -> {"items":[...], "filters":{...}, "page", "pageSize", "totalPages", "totalItems"}
  detail GET https://api.harwin.com/v1/products/<slug>
         -> the list item PLUS "technicalDetails", "media", "documents", "related"
            ("related.matings" is the real mating list, "documents" the datasheet PDFs)

Writes /tmp/harwin/all.json (list) and /tmp/harwin/details.json (slug -> detail).
"""
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

OUT = "/tmp/harwin"
BASE = "https://api.harwin.com/v1/products"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")


def get(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - retry transport errors only
            last = e
            time.sleep(1.0 + i * 2)
    raise RuntimeError(f"{url}: {last}")


def fetch_list():
    first = get(f"{BASE}?page=1&pageSize=500")
    items = {it["slug"]: it for it in first["items"]}
    for p in range(2, first["totalPages"] + 1):
        d = get(f"{BASE}?page={p}&pageSize=500")
        for it in d["items"]:
            items[it["slug"]] = it
        print(f"list page {p}/{first['totalPages']} -> {len(items)}", flush=True)
    assert len(items) == first["totalItems"], (len(items), first["totalItems"])
    return items


def fetch_details(slugs):
    done = {}
    path = f"{OUT}/details.json"
    if os.path.exists(path):
        done = json.load(open(path))
    todo = [s for s in slugs if s not in done]
    print(f"details: {len(done)} cached, {len(todo)} to fetch", flush=True)

    def one(s):
        return s, get(f"{BASE}/{s}")

    n = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for s, d in ex.map(one, todo):
            done[s] = d
            n += 1
            if n % 500 == 0:
                print(f"  {n}/{len(todo)}", flush=True)
                json.dump(done, open(path, "w"))
    json.dump(done, open(path, "w"))
    return done


def main():
    os.makedirs(OUT, exist_ok=True)
    items = fetch_list()
    json.dump(items, open(f"{OUT}/all.json", "w"))
    print(f"list: {len(items)} products", flush=True)
    if "--list-only" not in sys.argv:
        d = fetch_details(list(items))
        print(f"details: {len(d)}", flush=True)


if __name__ == "__main__":
    main()
