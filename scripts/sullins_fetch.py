#!/usr/bin/env python3
"""Pull the Sullins parametric catalogue (www.sullinscorp.com, WordPress + custom PHP).

The product grid is driven by ONE undocumented endpoint (no auth, no bot gate; the only
trick is that GET /products/?category=X hits a broken edge cache -> append any junk query
param to bust it):

    POST https://www.sullinscorp.com/products/?raw
         body (form-urlencoded):  filters=<tok>|<tok>&more=<offset>
         tokens: category:318 Accessories, 319 Card Edge, 320 Headers, 321 Test Sockets
                 col<NN>:<id>  (the per-column facets, ids are in the /products/ HTML)
    -> "[[<a> - <b> of <total>]][[<facet counts>]]<table rows...>"
       Exactly 25 rows per call regardless of any limit/page-size parameter tried;
       `more` is a 0-based offset.  A `?raw&count` variant returns only the header.

Catalogue sizes (2026-07-31): Headers 82,562 | Card Edge 957,112 | Test Sockets 93 |
Accessories 52.  Card Edge is Sullins' full option matrix and is 6x the size of the whole
existing TAS connector catalogue, so it is not pulled by default (--cardedge to include).

Writes /tmp/sullins/rows.jsonl (one parsed row per line) + progress.json, resumable.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape

OUT = "/tmp/sullins"
URL = "https://www.sullinscorp.com/products/?raw"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
CATS = {"320": "Headers", "321": "Test Sockets", "318": "Accessories", "319": "Card Edge"}
PAGE = 25


def post(filters, more=None, tries=4):
    # NOTE: the <thead> is only returned when `more` is ABSENT from the body.
    form = {"filters": filters}
    if more is not None:
        form["more"] = more
    body = urllib.parse.urlencode(form).encode()
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(URL, data=body, headers={
                "User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + 2 * i)
    raise RuntimeError(f"sullins {filters} more={more}: {last}")


def txt(s):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s))).replace("\xa0", " ").strip()


CELL = re.compile(r'<td class="(col\d+)"[^>]*>(.*?)</td>', re.S)
HEAD = re.compile(r'<th class="headingcolumn (col\d+)"[^>]*>(.*?)</th>', re.S)

# The <thead> is only emitted on the FIRST request of a session; later `more=` calls
# return bare <tr>s whose only column identity is the td's colNN class.  So the map is
# captured once from a real header-bearing response and cached; an unseen colNN is a
# hard error rather than a silently dropped column.
COLS = {}


def load_cols():
    path = f"{OUT}/colmap.json"
    if os.path.exists(path):
        COLS.update(json.load(open(path)))
        return
    h = post("category:320")
    m = {cid: txt(n) for cid, n in HEAD.findall(h)}
    if not m:
        raise RuntimeError("no <thead> in the first response; cannot map columns")
    COLS.update(m)
    json.dump(m, open(path, "w"), indent=1)


def parse(html_txt, category):
    total = None
    m = re.search(r"\[\[\s*[\d,]+\s*-\s*[\d,]+\s+of\s+([\d,]+)\s*\]\]", html_txt)
    if m:
        total = int(m.group(1).replace(",", ""))
    cols = COLS
    rows = []
    for row in re.findall(r"<tr>(?!<th)(.*?)</tr>", html_txt, re.S):
        pn = re.search(r"product_click\('/product/\?pn=([^']+)'\)", row)
        if not pn:
            continue
        rec = {"partNumber": unescape(pn.group(1)), "category": category, "attrs": {}}
        d = re.search(r'href="(https://drawings-pdf\.s3\.amazonaws\.com/[^"]+)"', row)
        if d:
            rec["drawing"] = d.group(1)
        for cid, cell in CELL.findall(row):
            if cid not in cols:
                raise RuntimeError(f"unmapped Sullins column {cid}")
            name = cols[cid]
            if cid == "col00":
                continue
            v = txt(cell)
            if v and v not in ("Mating Parts",):
                rec["attrs"][name] = v
        rows.append(rec)
    return rows, total


def harvest(cat_id, name, done_offsets, fh, workers=32):
    _, total = parse(post(f"category:{cat_id}"), name)
    if total is None:
        raise RuntimeError(f"no total for category {cat_id}")
    offsets = [o for o in range(0, total, PAGE) if (cat_id, o) not in done_offsets]
    print(f"category {cat_id} {name}: {total} parts, {len(offsets)} pages to fetch", flush=True)
    n = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for off, (rows, _) in zip(offsets, ex.map(
                lambda o: parse(post(f"category:{cat_id}", o), name), offsets)):
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            done_offsets.add((cat_id, off))
            n += 1
            if n % 200 == 0:
                fh.flush()
                json.dump(sorted(f"{c}:{o}" for c, o in done_offsets),
                          open(f"{OUT}/progress.json", "w"))
                print(f"  {name}: {n}/{len(offsets)} pages", flush=True)
    fh.flush()
    json.dump(sorted(f"{c}:{o}" for c, o in done_offsets), open(f"{OUT}/progress.json", "w"))


def main():
    os.makedirs(OUT, exist_ok=True)
    load_cols()
    done = set()
    pp = f"{OUT}/progress.json"
    if os.path.exists(pp):
        done = {tuple(x.split(":")) for x in json.load(open(pp))}
        done = {(c, int(o)) for c, o in done}
    order = ["321", "318", "320"] + (["319"] if "--cardedge" in sys.argv else [])
    with open(f"{OUT}/rows.jsonl", "a", encoding="utf-8") as fh:
        for cid in order:
            harvest(cid, CATS[cid], done, fh)
    print("done", flush=True)


if __name__ == "__main__":
    main()
