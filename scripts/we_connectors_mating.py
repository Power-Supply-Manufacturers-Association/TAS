#!/usr/bin/env python3
"""Extract Würth Elektronik connector MATING relations from the cached catalogue HTML.

WHY RE-PARSE THE HTML. staging/we_conn/rows.jsonl already carries a "Mates with" column
for 1,865 rows, but it is unusable: the table parser in we_connectors_enumerate.py
flattens a cell to its concatenated text, so a part with two counterparts reads
"Mates with6240402162262404021722" — two order codes fused, and Würth order codes are
NOT fixed width (62403421722 is 11 digits, 624034213322 is 12), so the string cannot be
segmented back apart without guessing.

The markup does keep them separate. Each counterpart is its own anchor:

    <td data-column="Mates with">...
      <a class="wecProductTableBody__designKit"
         href="/en/components/products/<PRODUCT_LINE>#<orderCode>">624008213322</a>

so one anchor per counterpart, carrying the counterpart's order code twice (link text and
href fragment) plus its product line. That is parsed here directly from the cached
staging/we_conn/html/*.html.gz — no network access, the crawl is already on disk.

"Use with" is the accessory column (crimp tools, IDC presses, strain reliefs) and names
SERIES in prose ("WR-BHD 2.54 mm Male"), not order codes. It is extracted separately and
NOT written: a crimping tool is not part of a mated set, and CONAS's companion relations
describe electrical mating partners.

  we_connectors_mating.py extract   -> staging/we_conn/mating.json
  we_connectors_mating.py write     -> patch data/connectors.ndjson (dry run)
  we_connectors_mating.py write --apply
"""
import argparse
import gzip
import json
import os
import re
import sys
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
PSMA = TAS.parent
STAGE = TAS / "staging" / "we_conn"
HTMLDIR = STAGE / "html"
OUT = STAGE / "mating.json"
AUDIT = STAGE / "mating_audit.json"
LIVE = TAS / "data" / "connectors.ndjson"

MFR = "Würth Elektronik"
# href="/en/components/products/<PRODUCT_LINE>#<orderCode>"
HREF = re.compile(r"/en/components/products/([^#\"?]+)#([A-Za-z0-9_.-]+)")


class MatingParser(HTMLParser):
    """Per row: the anchors inside the 'Mates with' / 'Use with' cells.

    Mirrors we_connectors_enumerate.TableParser (a real parser, because WE puts markup
    inside attribute values — data-column="I<sub>R</sub>"), but keeps the anchors of a
    cell as a LIST instead of collapsing the cell to text, which is the whole point.
    """

    WANTED = {"Mates with", "Use with"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []          # [(order_code, {column: [(product_line, code, text)]})]
        self._code = None
        self._col = None
        self._cells = None
        self._href = None
        self._atext = None
        self._ctext = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr" and a.get("data-order-code"):
            self._code = a["data-order-code"]
            self._cells = defaultdict(list)
        elif tag == "td" and self._code is not None:
            col = (a.get("data-column") or "").strip()
            self._col = col if col in self.WANTED else None
            self._href = None
            self._atext = None
            self._ctext = [] if self._col else None
        elif tag == "a" and self._col is not None:
            m = HREF.search(a.get("href") or "")
            self._href = (m.group(1), m.group(2)) if m else None
            self._atext = []

    def handle_data(self, d):
        if self._atext is not None:
            self._atext.append(d)
        if self._ctext is not None:
            self._ctext.append(d)

    def handle_endtag(self, tag):
        if tag == "a" and self._col is not None and self._atext is not None:
            txt = re.sub(r"\s+", " ", "".join(self._atext)).strip()
            pl, cp = self._href if self._href else (None, None)
            if cp or txt:
                self._cells[self._col].append((pl, cp, txt))
            self._href = None
            self._atext = None
        elif tag == "td" and self._col is not None:
            # A cell with no anchor at all: WE either prints a dash (no counterpart) or
            # the mobile-label prose. Record it so a silent parse regression is visible
            # as "text but no anchor" instead of just a smaller edge count.
            if not self._cells[self._col]:
                txt = re.sub(r"\s+", " ", "".join(self._ctext or ())).strip()
                txt = txt[len(self._col):].strip() if txt.startswith(self._col) else txt
                self._cells[self._col].append((None, None, txt))
            self._col = None
            self._ctext = None
        elif tag == "tr" and self._code is not None:
            self.rows.append((self._code, dict(self._cells)))
            self._code = None
            self._cells = None


def cmd_extract(a):
    files = sorted(HTMLDIR.glob("*.html.gz"))
    if not files:
        sys.exit(f"no cached HTML in {HTMLDIR} — run we_connectors_enumerate.py pull")

    mates = defaultdict(dict)       # order_code -> {counterpart: product_line}
    usewith = defaultdict(set)      # order_code -> {prose series}
    stats = Counter()
    text_only = Counter()

    for f in files:
        h = gzip.decompress(f.read_bytes()).decode("utf-8", "replace")
        p = MatingParser()
        p.feed(h)
        for code, cells in p.rows:
            stats["rows"] += 1
            for pl, cp, txt in cells.get("Mates with", []):
                if cp is None:
                    # A dash means WE publishes no counterpart for this part.
                    if txt not in ("–", "-", "—", ""):
                        text_only[txt] += 1
                    continue
                if cp == code:
                    stats["self_reference_dropped"] += 1
                    continue
                if txt and txt != cp:
                    stats["link_text_mismatch"] += 1
                mates[code][cp] = pl
            for pl, cp, txt in cells.get("Use with", []):
                if txt and txt not in ("–", "-", "—"):
                    usewith[code].add(txt)

    stats["parts_with_mates"] = len(mates)
    stats["edges"] = sum(len(v) for v in mates.values())
    stats["parts_with_usewith"] = len(usewith)

    # Is the relation symmetric in WE's own data? A mates B should imply B mates A.
    rev = defaultdict(set)
    for src, cps in mates.items():
        for cp in cps:
            rev[cp].add(src)
    stats["symmetric_edges"] = sum(
        1 for src, cps in mates.items() for cp in cps if src in mates.get(cp, {}))
    stats["distinct_counterparts"] = len(rev)

    OUT.write_text(json.dumps(
        {c: {"mates": v, "useWith": sorted(usewith.get(c, ()))}
         for c, v in sorted(mates.items())}, ensure_ascii=False, indent=1))

    print(f"{len(files)} cached product-line pages")
    for k, v in stats.most_common():
        print(f"  {k:36} {v}")
    if text_only:
        print("\n  cells with text but no anchor (NOT written):")
        for t, n in text_only.most_common(10):
            print(f"    {n:5d}  {t[:80]}")
    print(f"\nwrote {OUT}")
    return 0


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    by_id = {}
    for repo in ("PEAS", "CONAS"):
        d = PSMA / repo / "schemas"
        if not d.is_dir():
            continue
        for p in d.rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by_id[s["$id"]] = s
    res = [Resource(contents=s, specification=DRAFT202012) for s in by_id.values()]
    reg = Registry().with_resources([(r.contents["$id"], r) for r in res])
    schema = json.loads((PSMA / "CONAS" / "schemas" / "connector.json").read_text())
    return Draft202012Validator(schema, registry=reg)


RANK = {"optionalCompanion": 0, "mandatoryCompanion": 1, "intermateableStandard": 2,
        "mates": 3}


def merge(existing, incoming):
    """Merge matesWith lists, keeping the strongest relation per counterpart."""
    by_series = {e.get("series"): dict(e) for e in (existing or [])}
    for e in incoming:
        prev = by_series.get(e["series"])
        if prev is None or RANK.get(e["relation"], 0) > RANK.get(prev.get("relation"), 0):
            keep = {"series": e["series"], "relation": e["relation"]}
            for carry in ("manufacturer", "matedHeight"):
                if prev and carry in prev:
                    keep[carry] = prev[carry]
            by_series[e["series"]] = keep
    return sorted(by_series.values(), key=lambda x: x["series"])


def cmd_write(a):
    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    gate = BladeGate("connector")   # schema alone cannot catch a units error

    harvest = json.loads(OUT.read_text())
    edges = {c: [{"series": cp, "relation": "mates"} for cp in sorted(v["mates"])]
             for c, v in harvest.items() if v["mates"]}

    validator = build_validator()
    stats = Counter()
    unmatched = set(edges)
    invalid_samples = []
    counterpart_unknown = Counter()
    known = set()

    out_lines = []
    with LIVE.open("r", encoding="utf-8") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            stats["total"] += 1
            if MFR not in s:
                out_lines.append(s)
                continue
            obj = json.loads(s)
            conn = obj.get("connector") or obj
            mi = conn.get("manufacturerInfo") or {}
            if mi.get("name") != MFR:
                out_lines.append(s)
                continue
            ref = mi.get("reference")
            known.add(ref)
            if ref not in edges:
                stats["we_without_mating_data"] += 1
                out_lines.append(s)
                continue
            unmatched.discard(ref)

            ds = mi.setdefault("datasheetInfo", {})
            mating = ds.setdefault("mating", {})
            before = json.dumps(mating.get("matesWith"), sort_keys=True)
            mating["matesWith"] = merge(mating.get("matesWith"), edges[ref])
            if json.dumps(mating["matesWith"], sort_keys=True) == before:
                stats["unchanged"] += 1
                out_lines.append(s)
                continue

            errs = sorted(validator.iter_errors(conn), key=lambda e: e.path)
            if errs:
                stats["rejected_invalid"] += 1
                if len(invalid_samples) < 5:
                    invalid_samples.append(f"{ref}: {errs[0].message[:160]}")
                out_lines.append(s)          # leave the ORIGINAL line untouched
                continue
            ok_bl, why_bl = gate.check(conn)
            if not ok_bl:
                stats["rejected_blade"] += 1
                if len(invalid_samples) < 5:
                    invalid_samples.append(f"{ref}: BLADE {why_bl}")
                out_lines.append(s)
                continue

            stats["patched"] += 1
            stats["edges_written"] += len(edges[ref])
            out_lines.append(json.dumps(obj, ensure_ascii=False))

    for ref in edges:
        for e in edges[ref]:
            if e["series"] not in known:
                counterpart_unknown[e["series"]] += 1

    print("=" * 72)
    print("DRY RUN — nothing written" if not a.apply else "APPLIED")
    print("=" * 72)
    for k in ("total", "patched", "edges_written", "unchanged", "we_without_mating_data",
              "rejected_invalid", "rejected_blade"):
        print(f"  {k:26} {stats[k]}")
    print(f"  {'harvested parts':26} {len(edges)}")
    print(f"  {'harvested, not in TAS':26} {len(unmatched)}")
    print(f"  {'counterparts not in TAS':26} {len(counterpart_unknown)}")
    if invalid_samples:
        print("\n--- LEFT UNPATCHED (result failed a gate) ---")
        for x in invalid_samples:
            print(f"  {x}")

    AUDIT.write_text(json.dumps({
        "stats": dict(stats),
        "harvested_parts_absent_from_tas": sorted(unmatched),
        "counterparts_absent_from_tas": sorted(counterpart_unknown),
        "left_unpatched": invalid_samples,
    }, ensure_ascii=False, indent=1))
    print(f"\naudit -> {AUDIT}")

    if not a.apply:
        print("\nRe-run with --apply to write.")
        return 0

    tmp = LIVE.with_suffix(".ndjson.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for line in out_lines:
            fh.write(line + "\n")
    os.replace(tmp, LIVE)
    print(f"atomically replaced {LIVE}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("extract")
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return {"extract": cmd_extract, "write": cmd_write}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
