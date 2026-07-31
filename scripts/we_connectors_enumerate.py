#!/usr/bin/env python3
"""Enumerate Würth Elektronik's LIVE connector catalogue via product-line pages.

WHY THIS SURFACE. WE's category pages (e.g. .../em/connectors/board-to-board_connectors)
render only the first 100 rows of the table and have no pagination — scrolling loads
nothing, and there is no page/offset parameter (both verified on the wire). The table is
capped and the UI expects you to filter. But each row carries a `data-detail-link` to a
PRODUCT LINE page (/en/components/products/<PRODUCT_LINE>), and those render their table
IN FULL — 124 of 124, 244 of 271 — so the union of product lines is the complete catalogue.

HOW COMPLETENESS IS CHECKED. Every page publishes `data-total-article-count`, so each
product-line pull is compared against that page's OWN declared total, and the union is
compared against the connectors root total. For WR-PHD the product lines reconcile exactly
(2+31+24+60+62+9+76+62+124+65+1 = 516 = the category's stated total, once the cross-listed
DESIGNKIT_* lines are excluded — design kits are shared across categories and counted
separately). Do NOT reconcile per category by summing: a product line is linked from its
parent and sibling sidebars too, so that sum double-counts and invents mismatches.

A shortfall reported here is a claim about WE's markup and must be checked before it is
believed. Three separate "WE doesn't publish that" conclusions from this script were all
bugs in it, each found only by checking:
  - "0 of 9 rendered" on cable-assembly lines -> a \\d+ order-code regex dropping alpha
    suffixes (63901015621CAB);
  - "no parametric columns at all" -> an optional capture group after a lazy prefix,
    which never fires;
  - "WE publishes no rated current" -> data-column="I<sub>R</sub>" contains a '>', so
    <td([^>]*)> stopped inside the attribute. That is the one column CONAS needs.
Hence the HTML is cached and parsed with a real parser.

MEASURED 2026-07-31, after crawling to closure: 661 connector product lines, 659 of which
render their table in full, giving 6,427 distinct live order codes. The declared totals of
those product lines sum to 6,875 against the category tree's 9,083 minus its 2,199
design-kit articles = 6,884 — a 9-part gap, so this is the whole catalogue and not a
fraction of it.

Getting there needed the CLOSURE crawl, not just the category sidebars: a category
advertises only the product lines present among its first 100 rendered rows, which alone
reached 4,741 articles. Product-line pages link their siblings, so iterating
fetch-then-re-read closes the set — 525 -> 661 lines over 9 rounds, no parametric POST
required.

Two pages under-render against their own declared count (D-SUB-FILTER-PLASTIC-HOOD 5 of
90, WTB_WR_BHD_BOX_HEADER_2_54MM_MALE_PCB 111 of 131). That is WE's markup, verified by
counting <tr data-order-code> in the raw HTML — not a parser artifact — and those parts
are reachable through sibling product lines.

This script only MEASURES and stages the raw table. It writes nothing to data/.

  we_connectors_enumerate.py plan            -> staging/we_conn/plan.json  (product lines)
  we_connectors_enumerate.py pull            -> staging/we_conn/rows.jsonl (parsed tables)
  we_connectors_enumerate.py diff            -> what TAS is missing
"""
import argparse
import gzip
import html as htmllib
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
STAGE = TAS / "staging" / "we_conn"
PLAN = STAGE / "plan.json"
HTMLDIR = STAGE / "html"
DEAD = STAGE / "dead_links.json"
ROWS = STAGE / "rows.jsonl"
LIVE = TAS / "data" / "connectors.ndjson"

BASE = "https://www.we-online.com"
ROOT = f"{BASE}/en/components/products/em/connectors"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")

CAT_LINK = re.compile(r'"(/en/components/products/em/connectors[a-zA-Z0-9_/-]*)"')
# Product-line names may contain HYPHENS (WR-FPC_ZIF_1_00_MM). An earlier [A-Za-z0-9_]
# class made every hyphenated line invisible — the whole FPC family read as "40 parts"
# against a category declaring 602.
PL_LINK = re.compile(r'/en/components/products/([A-Z][A-Za-z0-9_-]{3,})(?:["#/])')
TOTAL = re.compile(r'data-total-article-count="(\d+)"')
# Order codes are NOT always numeric — cable assemblies use an alpha suffix
# (63901015621CAB). An earlier \d+ here silently dropped whole product lines while
# reporting "0 of 9 rendered", which read like a site cap rather than a parser bug.
ROW = re.compile(r'<tr[^>]*data-order-code="([^"]+)"[^>]*>(.*?)</tr>', re.S)
CELL = re.compile(r"<td([^>]*)>(.*?)</td>", re.S)
COL = re.compile(r'data-column="([^"]*)"')
TAG = re.compile(r"<[^>]+>")
# Allowing hyphens in product-line names (needed for WR-FPC_ZIF_1_00_MM) also lets the
# closure walk cross-links into capacitors (WCAP-CSRF), LEDs (WL-SMCW) and racks, which
# inflated the count to 12,731 "connectors". A connector product-line page always links
# back into /em/connectors; a capacitor page links it zero times. That is the membership
# test — the crawl expands only from connector pages and parses only connector pages.
IS_CONN = re.compile(r"/en/components/products/em/connectors")


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept-Language": "en"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == tries - 1:
                print(f"  FAIL {url}: {str(e)[:90]}", file=sys.stderr)
                return ""
            time.sleep(2 * (i + 1))


def text(frag):
    return htmllib.unescape(TAG.sub(" ", frag)).replace("\xa0", " ").strip()


class TableParser(HTMLParser):
    """Rows of WE's product table.

    A real parser, not a regex, because WE puts MARKUP INSIDE ATTRIBUTE VALUES —
    data-column="I<sub>R</sub>" is the rated-current column, and any `<td([^>]*)>`
    pattern stops at that inner '>' and silently loses the cell. That single column is
    the one CONAS cannot do without, so losing it quietly is the worst possible failure.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._code = None
        self._col = None
        self._buf = []
        self._cells = {}
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr" and a.get("data-order-code"):
            self._code = a["data-order-code"]
            self._cells = {}
        elif a.get("data-title") and self._code is not None:
            # The add-to-cart form inside each row carries the marketing product name
            # ("WR-SMP Cable Connectors") — the only human description in the table.
            self._cells.setdefault("_title", a["data-title"])
        elif tag == "td" and self._code is not None:
            self._col = text(a.get("data-column") or "")
            self._buf = []
            self._depth = 1
        elif self._col is not None:
            self._depth += 1

    def handle_data(self, d):
        if self._col is not None:
            self._buf.append(d)

    def handle_endtag(self, tag):
        if tag == "td" and self._col is not None:
            v = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if self._col and v:
                self._cells[self._col] = v
            self._col = None
        elif tag == "tr" and self._code is not None:
            self.rows.append((self._code, self._cells))
            self._code = None


def parse_table(h):
    """[(order_code, {column: value})] for every rendered row."""
    p = TableParser()
    p.feed(h)
    return p.rows


def cmd_plan(a):
    STAGE.mkdir(parents=True, exist_ok=True)
    root = get(ROOT)
    cats = sorted({BASE + m for m in CAT_LINK.findall(root)} | {ROOT})
    print(f"{len(cats)} connector category pages")

    plan = {}
    cat_totals = {}

    def pull(u):
        h = get(u)
        t = TOTAL.search(h)
        return u, (int(t.group(1)) if t else 0), sorted(set(PL_LINK.findall(h)))

    with ThreadPoolExecutor(max_workers=5) as ex:
        for u, total, pls in ex.map(pull, cats):
            short = u.split("/products/")[1]
            cat_totals[short] = total
            for pl in pls:
                plan.setdefault(pl, []).append(short)
            print(f"  total={total:>5}  {len(pls):>3} product lines  {short}")

    lines = sorted(plan)
    print(f"\n{len(lines)} distinct product lines "
          f"({sum(1 for p in lines if p.startswith('DESIGNKIT'))} design kits)")
    PLAN.write_text(json.dumps({"productLines": {p: plan[p] for p in lines},
                                "categoryTotals": cat_totals}, indent=1))
    print(f"wrote {PLAN}")
    return 0


def cmd_pull(a):
    """Fetch each product-line page to staging/we_conn/html/<PL>.html.gz.

    The page is cached verbatim so that fixing the parser NEVER costs another crawl —
    the first two versions of this script each had to re-fetch all 525 pages because they
    kept only the parse, and both parses turned out to be dropping data.
    """
    HTMLDIR.mkdir(parents=True, exist_ok=True)
    plan = json.loads(PLAN.read_text())
    lines = sorted(plan["productLines"])
    if a.limit:
        lines = lines[:a.limit]
    todo = [p for p in lines if not (HTMLDIR / f"{p}.html.gz").exists()]
    print(f"fetching {len(todo)} product-line pages "
          f"({len(lines) - len(todo)} already cached)")

    def pull(pl):
        h = get(f"{BASE}/en/components/products/{pl}")
        if h:
            with gzip.open(HTMLDIR / f"{pl}.html.gz", "wt", encoding="utf-8") as fh:
                fh.write(h)
        return pl, len(h)

    n = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for pl, size in ex.map(pull, todo):
            n += 1
            if not size:
                print(f"  [{n}/{len(todo)}] {pl}: EMPTY")
            elif n % 50 == 0:
                print(f"  [{n}/{len(todo)}] cached")
    print(f"cached {n} pages -> {HTMLDIR}")
    return cmd_parse(a)


def cmd_crawl(a):
    """Fetch product-line pages to CLOSURE.

    A category page advertises only the product lines present among its first 100
    rendered rows, so the sidebar crawl alone reaches ~4,741 of the ~6,900 articles the
    category tree declares. But every product-line page links its siblings, so iterating
    'fetch what is linked, then re-read what was fetched' closes the set without needing
    the filtered parametric POST at all.
    """
    HTMLDIR.mkdir(parents=True, exist_ok=True)
    plan = json.loads(PLAN.read_text())
    known = set(plan["productLines"])
    # Seed from what the ALREADY cached pages link, otherwise a resumed crawl sees
    # cached == known, exits on round 1, and reports closure it never reached.
    for p in HTMLDIR.glob("*.html.gz"):
        with gzip.open(p, "rt", encoding="utf-8", errors="replace") as fh:
            h = fh.read()
        if IS_CONN.search(h):
            known |= set(PL_LINK.findall(h))
    # Some linked names 404 (stale links in WE's own markup). They never become cached,
    # so without this they stay in `todo` and the loop spins on them forever — which is
    # exactly what the first run did, retrying one dead URL 35 times.
    dead = set(json.loads(DEAD.read_text())) if DEAD.exists() else set()
    rnd = 0
    while True:
        rnd += 1
        cached = {p.name[:-8] for p in HTMLDIR.glob("*.html.gz")}
        todo = sorted(known - cached - dead)
        if not todo:
            break
        print(f"round {rnd}: fetching {len(todo)} new product lines "
              f"({len(cached)} cached, {len(known)} known)")

        def pull(pl):
            h = get(f"{BASE}/en/components/products/{pl}")
            if h:
                with gzip.open(HTMLDIR / f"{pl}.html.gz", "wt", encoding="utf-8") as fh:
                    fh.write(h)
            return pl, h

        newly = set()
        with ThreadPoolExecutor(max_workers=4) as ex:
            for pl, h in ex.map(pull, todo):
                if not h:
                    dead.add(pl)
                elif IS_CONN.search(h):
                    newly |= set(PL_LINK.findall(h))
        DEAD.write_text(json.dumps(sorted(dead), indent=0))
        before = len(known)
        known |= newly
        print(f"  -> {len(known) - before} newly discovered product lines, "
              f"{len(dead)} dead links")
        if a.max_rounds and rnd >= a.max_rounds:
            print("  (round limit reached)")
            break

    plan["productLines"] = {p: plan["productLines"].get(p, ["<discovered>"])
                            for p in sorted(known)}
    PLAN.write_text(json.dumps(plan, indent=1))
    print(f"closure: {len(known)} product lines")
    return cmd_parse(a)


def cmd_parse(a):
    """Re-parse the cached HTML into rows.jsonl. Cheap and repeatable."""
    plan = json.loads(PLAN.read_text())
    n = foreign = 0
    with ROWS.open("w", encoding="utf-8") as out:
        for pl in sorted(plan["productLines"]):
            f = HTMLDIR / f"{pl}.html.gz"
            if not f.exists():
                continue
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                h = fh.read()
            if not IS_CONN.search(h):
                foreign += 1
                continue
            t = TOTAL.search(h)
            rows = parse_table(h)
            total = int(t.group(1)) if t else 0
            out.write(json.dumps({"productLine": pl, "declaredTotal": total,
                                  "rows": [{"orderCode": c, "cells": v} for c, v in rows]},
                                 ensure_ascii=False) + "\n")
            n += 1
            if total != len(rows):
                print(f"  {pl}: rendered {len(rows)} of {total}")
    print(f"parsed {n} connector product lines ({foreign} non-connector pages "
          f"skipped) -> {ROWS}")
    return 0


def load_rows():
    by_code = {}
    per_line = {}
    for ln in ROWS.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        per_line[r["productLine"]] = (r["declaredTotal"], len(r["rows"]))
        for row in r["rows"]:
            by_code.setdefault(row["orderCode"], {"productLines": [], "cells": {}})
            by_code[row["orderCode"]]["productLines"].append(r["productLine"])
            by_code[row["orderCode"]]["cells"].update(row["cells"])
    return by_code, per_line


def cmd_diff(a):
    by_code, per_line = load_rows()
    plan = json.loads(PLAN.read_text())

    # A product line is linked from its own category AND from parent/sibling sidebars, so
    # summing per category double-counts. The honest checks are (a) per product line,
    # rendered vs its own declared total, and (b) the connectors root total vs the union.
    short = [(pl, d, r) for pl, (d, r) in per_line.items() if d != r]
    print(f"product lines: {len(per_line)}   fully rendered: {len(per_line) - len(short)}"
          f"   short: {len(short)}")
    for pl, d, r in sorted(short, key=lambda t: t[1] - t[2], reverse=True)[:15]:
        print(f"   rendered {r:>5} of {d:>5}   {pl}")
    declared_sum = sum(d for pl, (d, _) in per_line.items() if not pl.startswith("DESIGNKIT"))
    root = plan["categoryTotals"].get("em/connectors", 0)
    print(f"\nconnectors root declares       : {root}")
    print(f"sum of product-line totals     : {declared_sum}  (design kits excluded)")

    have = set()
    with LIVE.open(encoding="utf-8") as fh:
        for line in fh:
            if "rth Elektronik" not in line:
                continue
            o = json.loads(line)
            mi = (o.get("connector") or o).get("manufacturerInfo") or {}
            if "rth" in (mi.get("name") or ""):
                have.add(mi.get("reference"))
    live = set(by_code)
    print(f"\nLIVE WE connector order codes : {len(live)}")
    print(f"TAS holds                     : {len(have)}")
    print(f"MISSING from TAS              : {len(live - have)}")
    print(f"in TAS but not live           : {len(have - live)}  (EOL or other EM categories)")
    cols = {}
    for v in by_code.values():
        for k in v["cells"]:
            cols[k] = cols.get(k, 0) + 1
    print("\ncolumns available on the staged rows:")
    for k, n in sorted(cols.items(), key=lambda kv: -kv[1])[:20]:
        print(f"   {n:>7}  {k}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    p = sub.add_parser("pull")
    p.add_argument("--limit", type=int)
    c = sub.add_parser("crawl")
    c.add_argument("--max-rounds", type=int, default=0)
    c.add_argument("--limit", type=int)
    q = sub.add_parser("parse")
    q.add_argument("--limit", type=int)
    sub.add_parser("diff")
    a = ap.parse_args()
    return {"plan": cmd_plan, "pull": cmd_pull, "crawl": cmd_crawl,
            "parse": cmd_parse, "diff": cmd_diff}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
