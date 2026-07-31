#!/usr/bin/env python3
"""Adam Tech: enumerate every ORDERABLE part, then pull each part's own spec table.

Why not the category listings: /products/index/category:<id> lists ~11 000 rows of which
39 % are ordering TEMPLATES ("2PH1-XX-TA", XX = position count) rather than orderable part
numbers.  The flat index lists the ~38 800 concrete part numbers instead:

  index   GET https://app.adam-tech.com/products/index[/page:<n>]      (50 rows/page)
          row -> part number + productId (in .../api_detail_modal_view/<id>)
  detail  GET https://app.adam-tech.com/products/api_detail_modal_view/<id>
          -> {"modalContent": "<html>"} with a "Details" <table> of that part's published
             attributes, a "Listed Under" breadcrumb (its categories) and the datasheet PDF.

Writes /tmp/adamtech/index.jsonl (partNumber, id) and /tmp/adamtech/details.jsonl
(partNumber, id, categories[], attrs{}, datasheet).  Both resumable.
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


def index_page(p):
    h = get(f"{HOST}/products/index" + (f"/page:{p}" if p > 1 else ""))
    m = re.search(r'<table class="element-product-table.*?</table>', h, re.S)
    out = []
    if m:
        body = re.search(r"<tbody>(.*?)</tbody>", m.group(0), re.S)
        if body:
            for row in re.findall(r"<tr>\s*(.*?)</tr>", body.group(1), re.S):
                pid = re.search(r"api_detail_modal_view/(\d+)", row)
                pn = re.search(r'api_detail_modal_view/\d+">([^<]+)</a>', row)
                if pid and pn:
                    out.append({"partNumber": unescape(pn.group(1)).strip(), "id": pid.group(1)})
    pm = re.search(r"Showing results [\d,\-]+ on Page \d+ of (\d+)", txt(h))
    return out, int(pm.group(1)) if pm else 1


def crawl_index():
    path = f"{OUT}/index.jsonl"
    seen = set()
    if os.path.exists(path):
        for line in open(path):
            seen.add(json.loads(line)["id"])
    first, pages = index_page(1)
    print(f"flat index: {pages} pages", flush=True)
    with open(path, "a", encoding="utf-8") as fh:
        for r in first:
            if r["id"] not in seen:
                seen.add(r["id"])
                fh.write(json.dumps(r) + "\n")
        n = 0
        with ThreadPoolExecutor(max_workers=12) as ex:
            for rows, _ in ex.map(index_page, range(2, pages + 1)):
                for r in rows:
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        fh.write(json.dumps(r) + "\n")
                n += 1
                if n % 100 == 0:
                    fh.flush()
                    print(f"  index {n}/{pages-1} -> {len(seen)} parts", flush=True)
    print(f"index: {len(seen)} parts", flush=True)
    return path


DETAIL_ROW = re.compile(r"<tr>\s*<th>(.*?)</th>\s*<td>(.*?)</td>\s*</tr>", re.S)


def parse_detail(js):
    h = json.loads(js).get("modalContent") or ""
    attrs = {}
    dt = re.search(r"Details</h3>.*?<table.*?</table>", h, re.S)
    if dt:
        for k, v in DETAIL_ROW.findall(dt.group(0)):
            k, v = txt(k), txt(v)
            if k and v:
                attrs[k] = v
    cats = []
    lu = re.search(r"Listed Under</h3>.*?</ul>", h, re.S)
    if lu:
        cats = [txt(x) for x in re.findall(r"<li class=\"list-group-item\">(.*?)</li>",
                                           lu.group(0), re.S)]
    ds = re.search(r'href="(https://app\.adam-tech\.com/products/download/data_sheet/[^"]+)"', h)
    return attrs, cats, unescape(ds.group(1)) if ds else None


def crawl_details():
    idx = [json.loads(l) for l in open(f"{OUT}/index.jsonl")]
    path = f"{OUT}/details.jsonl"
    done = set()
    if os.path.exists(path):
        for line in open(path):
            try:
                done.add(json.loads(line)["id"])
            except json.JSONDecodeError:
                pass
    todo = [r for r in idx if r["id"] not in done]
    print(f"details: {len(done)} cached, {len(todo)} to fetch", flush=True)

    def one(r):
        attrs, cats, ds = parse_detail(get(f"{HOST}/products/api_detail_modal_view/{r['id']}"))
        return {"partNumber": r["partNumber"], "id": r["id"], "categories": cats,
                "attrs": attrs, "datasheet": ds}

    n = 0
    with open(path, "a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=12) as ex:
        for rec in ex.map(one, todo):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            if n % 500 == 0:
                fh.flush()
                print(f"  details {n}/{len(todo)}", flush=True)
    print("details done", flush=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    if "--details-only" not in sys.argv:
        crawl_index()
    if "--index-only" not in sys.argv:
        crawl_details()


if __name__ == "__main__":
    main()
