#!/usr/bin/env python3
"""Crawl the Adam Tech catalogue (app.adam-tech.com, CakePHP, plain HTML, no bot gate).

Shape of the site (all GET, no auth, no cookies needed):
  category node   https://app.adam-tech.com/categories/index/<id>
                  -> links to child nodes / leaf product listings
  leaf listing    https://app.adam-tech.com/products/index/category:<id>[/page:<n>]
                  -> <table class="element-product-table"> with a per-category
                     parametric <thead>; 50 rows per page; "Showing results a-b on
                     Page p of P"
  part detail     https://app.adam-tech.com/products/api_detail_modal_view/<productId>
                  -> {"modalContent": "<html>", "status": "success"}  (same attributes)

Writes /tmp/adamtech/rows.json  {partNumber: {category, categoryId, attrs{}, datasheet, id}}
"""
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape

OUT = "/tmp/adamtech"
HOST = "https://app.adam-tech.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
ROOTS = [3201, 3278, 3279]


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


def title(h):
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S)
    return txt(m.group(1)) if m else ""


def crawl_tree():
    """BFS the category tree; return {leafProductCategoryId: breadcrumb}."""
    seen_cat, leaves, queue = set(), {}, [(r, []) for r in ROOTS]
    while queue:
        cid, path = queue.pop(0)
        if cid in seen_cat:
            continue
        seen_cat.add(cid)
        h = get(f"{HOST}/categories/index/{cid}")
        name = title(h)
        here = path + [name]
        for m in re.finditer(r'href="https://app\.adam-tech\.com/categories/index/(\d+)', h):
            c = int(m.group(1))
            if c not in seen_cat:
                queue.append((c, here))
        for m in re.finditer(r'href="https://app\.adam-tech\.com/products/index/category:(\d+)', h):
            leaves.setdefault(int(m.group(1)), here)
        print(f"cat {cid} '{name}' -> {len(leaves)} leaves", flush=True)
    return leaves


ROW_RE = re.compile(r"<tr>\s*(.*?)</tr>", re.S)


def parse_listing(h, cid, breadcrumb):
    m = re.search(r'<table class="element-product-table.*?</table>', h, re.S)
    if not m:
        return [], 1
    tbl = m.group(0)
    head = re.search(r"<thead>(.*?)</thead>", tbl, re.S)
    cols = [txt(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", head.group(1), re.S)] if head else []
    body = re.search(r"<tbody>(.*?)</tbody>", tbl, re.S)
    out = []
    if body:
        for row in ROW_RE.findall(body.group(1)):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            if len(tds) != len(cols):
                continue
            pid = re.search(r"api_detail_modal_view/(\d+)", row)
            ds = re.search(r'href="(https://app\.adam-tech\.com/products/download/data_sheet/[^"]+)"', row)
            rec = {"categoryId": cid, "breadcrumb": breadcrumb, "id": pid.group(1) if pid else None,
                   "datasheet": unescape(ds.group(1)) if ds else None, "attrs": {}}
            for name, cell in zip(cols, tds):
                v = txt(cell)
                if name in ("Product Photo", "Datasheet / 3D Model", "Ordering", ""):
                    continue
                if name == "Part Number":
                    rec["partNumber"] = v
                elif v:
                    rec["attrs"][name] = v
            if rec.get("partNumber"):
                out.append(rec)
    pm = re.search(r"Showing results [\d\-]+ on Page (\d+) of (\d+)", txt(h))
    pages = int(pm.group(2)) if pm else 1
    return out, pages


def crawl_leaves(leaves):
    rows = {}

    def page(args):
        cid, bc, p = args
        url = f"{HOST}/products/index/category:{cid}" + (f"/page:{p}" if p > 1 else "")
        return parse_listing(get(url), cid, bc)

    for cid, bc in leaves.items():
        first, pages = page((cid, bc, 1))
        for r in first:
            rows[r["partNumber"]] = r
        if pages > 1:
            with ThreadPoolExecutor(max_workers=6) as ex:
                for rs, _ in ex.map(page, [(cid, bc, p) for p in range(2, pages + 1)]):
                    for r in rs:
                        rows[r["partNumber"]] = r
        print(f"  cat {cid} {' / '.join(bc)}: {pages} pages -> total {len(rows)}", flush=True)
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    lp = f"{OUT}/leaves.json"
    if os.path.exists(lp) and "--recrawl-tree" not in sys.argv:
        leaves = {int(k): v for k, v in json.load(open(lp)).items()}
    else:
        leaves = crawl_tree()
        json.dump(leaves, open(lp, "w"))
    print(f"{len(leaves)} leaf product categories", flush=True)
    rows = crawl_leaves(leaves)
    json.dump(rows, open(f"{OUT}/rows.json", "w"))
    print(f"{len(rows)} parts", flush=True)


if __name__ == "__main__":
    main()
