#!/usr/bin/env python3
"""Crawl every Adam Tech leaf product-category listing found by scripts/adamtech_probe.py.

  GET https://app.adam-tech.com/products/index/category:<id>[/page:<n>]
      -> <table class="element-product-table"> ; 50 rows/page ; per-category <thead>
         carries that category's parametric column names.

Appends parsed rows to /tmp/adamtech/rows2.jsonl (resumable via done2.json).
"""
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape

OUT = "/tmp/adamtech"
HOST = "https://app.adam-tech.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")


def get(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + 2 * i)
    raise RuntimeError(f"{url}: {last}")


def txt(s):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s))).strip()


SKIP_COLS = {"Product Photo", "Datasheet / 3D Model", "Ordering", "Mating Parts", ""}


def parse(h, cid, title):
    m = re.search(r'<table class="element-product-table.*?</table>', h, re.S)
    if not m:
        return []
    tbl = m.group(0)
    head = re.search(r"<thead>(.*?)</thead>", tbl, re.S)
    cols = [txt(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", head.group(1), re.S)] if head else []
    body = re.search(r"<tbody>(.*?)</tbody>", tbl, re.S)
    out = []
    if not body:
        return out
    for row in re.findall(r"<tr>\s*(.*?)</tr>", body.group(1), re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(tds) != len(cols):
            continue
        pid = re.search(r"api_detail_modal_view/(\d+)", row)
        ds = re.search(r'href="(https://app\.adam-tech\.com/products/download/data_sheet/[^"]+)"', row)
        rec = {"categoryId": cid, "category": title, "id": pid.group(1) if pid else None,
               "datasheet": unescape(ds.group(1)) if ds else None, "attrs": {}}
        for name, cell in zip(cols, tds):
            v = txt(cell)
            if name in SKIP_COLS:
                continue
            if name == "Part Number":
                rec["partNumber"] = v
            elif v:
                rec["attrs"][name] = v
        if rec.get("partNumber"):
            out.append(rec)
    return out


def main():
    leaves = json.load(open(f"{OUT}/leaves2.json"))
    donep = f"{OUT}/done2.json"
    done = set(json.load(open(donep))) if os.path.exists(donep) else set()
    jobs = []
    for cid, info in leaves.items():
        for p in range(1, info["pages"] + 1):
            key = f"{cid}:{p}"
            if key not in done:
                jobs.append((cid, info["title"], p, key))
    print(f"{len(leaves)} leaves, {len(jobs)} listing pages to fetch", flush=True)

    def one(j):
        cid, title, p, key = j
        url = f"{HOST}/products/index/category:{cid}" + (f"/page:{p}" if p > 1 else "")
        return key, parse(get(url), cid, title)

    n = 0
    with open(f"{OUT}/rows2.jsonl", "a", encoding="utf-8") as fh, \
            ThreadPoolExecutor(max_workers=12) as ex:
        for key, rows in ex.map(one, jobs):
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            done.add(key)
            n += 1
            if n % 100 == 0:
                fh.flush()
                json.dump(sorted(done), open(donep, "w"))
                print(f"  {n}/{len(jobs)} pages", flush=True)
        fh.flush()
    json.dump(sorted(done), open(donep, "w"))
    print("done", flush=True)


if __name__ == "__main__":
    main()
