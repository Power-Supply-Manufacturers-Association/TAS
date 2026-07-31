#!/usr/bin/env python3
"""Pull the CUI Devices / Same Sky interconnect catalogue.

CUI Devices is now Same Sky (www.cuidevices.com 301-redirects to www.sameskydevices.com).
Every leaf catalogue page is server-rendered with the COMPLETE parametric table inline --
no XHR, no auth, no bot gate, one plain GET per category:

    GET https://www.sameskydevices.com/catalog/interconnect/<group>/<leaf>

    <table> ... <thead><th>Model Number</th><th>...per-category columns...</th></thead>
    each <tr> carries dl-itemNumber="<part number>" and
    dl-itemCategories="Interconnect,Connectors,Terminal Blocks" plus a /product/... link
    and a datasheet PDF.

Writes /tmp/cui/rows.jsonl (partNumber, categories, attrs{}, productUrl, datasheet).
"""
import json
import os
import re
import time
import urllib.request
from html import unescape

OUT = "/tmp/cui"
HOST = "https://www.sameskydevices.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")


def get(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1 + 2 * i)
    raise RuntimeError(f"{url}: {last}")


def txt(s):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def leaves():
    h = get(f"{HOST}/catalog/interconnect")
    got = sorted(set(re.findall(r'href="(/catalog/interconnect/[a-z0-9\-]+/[a-z0-9\-]+)"', h)))
    return [p for p in got if "sample-kit" not in p]


def parse(h, path):
    m = re.search(r"<table[^>]*>.*?</table>", h, re.S)
    if not m:
        return []
    t = m.group(0)
    head = re.search(r"<thead.*?</thead>", t, re.S)
    cols = [txt(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", head.group(0), re.S)] if head else []
    body = re.search(r"<tbody.*?</tbody>", t, re.S)
    out = []
    if not body:
        return out
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(0), re.S):
        pn = re.search(r'dl-itemNumber="([^"]+)"', row)
        if not pn:
            continue
        cats = re.search(r'dl-itemCategories="([^"]*)"', row)
        purl = re.search(r'href="(/product/[^"]+)"', row)
        ds = re.search(r'href="([^"]*\.pdf)"', row, re.I)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        rec = {"partNumber": unescape(pn.group(1)).strip(), "catalogPath": path,
               "categories": unescape(cats.group(1)).split(",") if cats else [],
               "productUrl": HOST + purl.group(1) if purl else None,
               "datasheet": unescape(ds.group(1)) if ds else None, "attrs": {}}
        for name, cell in zip(cols, cells):
            if name in ("Model Number", "Data Sheet", "", "Buy Now"):
                continue
            v = txt(cell)
            if v and v != "-":
                rec["attrs"][name] = v
        out.append(rec)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    paths = leaves()
    print(f"{len(paths)} interconnect leaf categories", flush=True)
    n = 0
    seen = set()
    with open(f"{OUT}/rows.jsonl", "w", encoding="utf-8") as fh:
        for p in paths:
            rows = parse(get(HOST + p), p)
            k = 0
            for r in rows:
                if r["partNumber"] in seen:
                    continue
                seen.add(r["partNumber"])
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                k += 1
            n += k
            print(f"  {p}: {k} parts (total {n})", flush=True)
            time.sleep(0.3)
    print(json.dumps({"categories": len(paths), "parts": n}))


if __name__ == "__main__":
    main()
