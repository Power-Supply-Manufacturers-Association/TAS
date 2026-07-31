#!/usr/bin/env python3
"""Harvest Sullins mating relations from the parametric grid's mating-link column.

WHY THIS EXISTS. Sullins publishes mating on every Headers row, and the original pull
threw it away. sullins_fetch.py parses each cell to TEXT, and the mating cell's text is
the constant label "Mating Parts" — so the parser has an explicit

    if v and v not in ("Mating Parts",):

that drops it. All the information is in the anchor's href, which text extraction
discards. Same failure mode as the Würth "Mates with" column: a cell whose value lives in
markup, flattened to a useless string.

WHAT THE LINK CONTAINS. The href is Sullins' own definition of what mates with the part:

  /?s=category:Headers gender:female gender:dual_female pitch[mm]:2.54mm
      positions/contacts:3/6 rows:2 matingthickness[mm]:&nbsp;
      plating[contactsurface]:pcs-gold product_number:SFH mated:1
      &mated_for=SBH11-NBPC-D03-RA-BK

`mated_for` is the part itself and `product_number:` is the counterpart SERIES STEM —
exactly what CONAS matesWith.series is specified to hold ("counterpart series or
part-number stem"). The remaining tokens are the parametric constraints Sullins applies
within that series (same pitch, matching gender, matching position count).

`product_number:*` means Sullins constrains the counterpart parametrically but names no
series. That is NOT written: a wildcard is not a counterpart, and the alternative —
executing the search per part to enumerate matches — would be inference dressed as
sourcing, on top of tens of thousands of extra requests.

Card Edge (category 319) is not pulled: it is Sullins' 957k-row option matrix and TAS
carries none of it.

  sullins_mating_harvest.py sample          # 3 pages per category, prints what it finds
  sullins_mating_harvest.py pull            # -> staging/sullins/mating.jsonl (resumable)
  sullins_mating_harvest.py write [--apply] # -> data/connectors.ndjson
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
PSMA = TAS.parent
STAGE = TAS / "staging" / "sullins"
OUT = STAGE / "mating.jsonl"
PROGRESS = STAGE / "mating_progress.json"
AUDIT = STAGE / "mating_audit.json"
LIVE = TAS / "data" / "connectors.ndjson"
MFR = "Sullins Connector Solutions"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sullins_fetch import PAGE, parse, post  # noqa: E402

# Categories TAS actually carries. 319 (Card Edge) is deliberately absent.
CATS = {"320": "Headers", "321": "Test Sockets", "318": "Accessories"}

ROW = re.compile(r"<tr>(?!<th)(.*?)</tr>", re.S)
PN = re.compile(r"product_click\('/product/\?pn=([^']+)'\)")
MATING = re.compile(r'<a class="mating-link" href="([^"]+)"')
TOTAL = re.compile(r"\[\[\s*[\d,]+\s*-\s*[\d,]+\s+of\s+([\d,]+)\s*\]\]")


def parse_link(href):
    """-> (source_part, counterpart_series_stem, constraint_tokens) or None."""
    h = unescape(href)
    m = re.search(r"[?&]mated_for=([^&]+)", h)
    src = m.group(1) if m else None
    s = re.search(r"[?&]s=([^&]*)", h)
    if not s or not src:
        return None
    toks = s.group(1).split(" ")
    stem = None
    keep = []
    for t in toks:
        if t.startswith("product_number:"):
            stem = t.split(":", 1)[1]
        elif t and not t.startswith(("mated:", "category:")):
            keep.append(t)
    return src, stem, keep


def parse_mating(html_txt, category):
    out = []
    for row in ROW.findall(html_txt):
        pn = PN.search(row)
        link = MATING.search(row)
        if not pn or not link:
            continue
        got = parse_link(link.group(1))
        if not got:
            continue
        src, stem, keep = got
        out.append({"partNumber": unescape(pn.group(1)), "category": category,
                    "matedFor": src, "series": stem, "constraints": keep})
    t = TOTAL.search(html_txt)
    return out, (int(t.group(1).replace(",", "")) if t else None)


def cmd_sample(a):
    for cid, name in CATS.items():
        rows, total = parse_mating(post(f"category:{cid}"), name)
        print(f"\n=== {name} (category {cid}, {total} parts) ===")
        stems = Counter(r["series"] for r in rows)
        print(f"  {len(rows)} of 25 rows carry a mating link; stems: {dict(stems)}")
        for r in rows[:3]:
            print(f"   {r['partNumber']:<28} -> series={r['series']!r} "
                  f"constraints={' '.join(r['constraints'])[:90]}")
    return 0


def cmd_pull(a):
    STAGE.mkdir(parents=True, exist_ok=True)
    done = set()
    if PROGRESS.exists():
        done = {tuple(x.split("|")) for x in json.loads(PROGRESS.read_text())}
        done = {(c, int(o)) for c, o in done}

    with OUT.open("a", encoding="utf-8") as fh:
        for cid, name in CATS.items():
            _, total = parse_mating(post(f"category:{cid}"), name)
            if total is None:
                sys.exit(f"no total for category {cid}")
            offsets = [o for o in range(0, total, PAGE) if (cid, o) not in done]
            print(f"{name}: {total} parts, {len(offsets)} pages to fetch", flush=True)
            n = 0
            with ThreadPoolExecutor(max_workers=a.workers) as ex:
                for off, (rows, _) in zip(offsets, ex.map(
                        lambda o: parse_mating(post(f"category:{cid}", o), name),
                        offsets)):
                    for r in rows:
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                    done.add((cid, off))
                    n += 1
                    if n % 200 == 0:
                        fh.flush()
                        PROGRESS.write_text(json.dumps(
                            sorted(f"{c}|{o}" for c, o in done)))
                        print(f"  {name}: {n}/{len(offsets)} pages", flush=True)
            fh.flush()
            PROGRESS.write_text(json.dumps(sorted(f"{c}|{o}" for c, o in done)))
    print("pull complete", flush=True)
    return 0


def cmd_write(a):
    from we_connectors_mating import build_validator, merge
    from blade_gate import BladeGate
    gate = BladeGate("connector")
    validator = build_validator()

    edges = {}
    skipped_wildcard = 0
    seen = 0
    with OUT.open(encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            r = json.loads(ln)
            seen += 1
            stem = r.get("series")
            if not stem or stem == "*":
                skipped_wildcard += 1
                continue
            edges.setdefault(r["partNumber"], set()).add(stem)
    edges = {k: [{"series": s, "relation": "mates"} for s in sorted(v)]
             for k, v in edges.items()}
    print(f"{seen} harvested rows -> {len(edges)} parts with a named counterpart series "
          f"({skipped_wildcard} rows name no series and are skipped)")

    stats = Counter()
    rejected = []
    tmp = LIVE.with_suffix(".ndjson.smating_tmp")
    with LIVE.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as out:
        for raw in src:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            stats["total"] += 1
            if MFR not in s:
                out.write(s + "\n")
                continue
            obj = json.loads(s)
            c = obj.get("connector") or obj
            mi = c.get("manufacturerInfo") or {}
            ref = mi.get("reference")
            if mi.get("name") != MFR or ref not in edges:
                out.write(s + "\n")
                continue
            ds = mi.setdefault("datasheetInfo", {})
            mating = ds.setdefault("mating", {})
            before = json.dumps(mating.get("matesWith"), sort_keys=True)
            mating["matesWith"] = merge(mating.get("matesWith"), edges[ref])
            if json.dumps(mating["matesWith"], sort_keys=True) == before:
                stats["unchanged"] += 1
                out.write(s + "\n")
                continue
            errs = sorted(validator.iter_errors(c), key=lambda e: e.path)
            if errs:
                stats["rejected_invalid"] += 1
                if len(rejected) < 5:
                    rejected.append(f"{ref}: {errs[0].message[:150]}")
                out.write(s + "\n")
                continue
            ok, why = gate.check(c)
            if not ok:
                stats["rejected_blade"] += 1
                if len(rejected) < 5:
                    rejected.append(f"{ref}: BLADE {why}")
                out.write(s + "\n")
                continue
            stats["patched"] += 1
            stats["edges_written"] += len(edges[ref])
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")

    if a.apply:
        os.replace(tmp, LIVE)
    else:
        tmp.unlink()
    print("APPLIED" if a.apply else "DRY RUN — nothing written")
    for k in ("total", "patched", "edges_written", "unchanged",
              "rejected_invalid", "rejected_blade"):
        print(f"  {k:20} {stats[k]}")
    if rejected:
        print("  -- left unpatched --")
        for r in rejected:
            print(f"     {r}")
    AUDIT.write_text(json.dumps({"stats": dict(stats),
                                 "rows_without_named_series": skipped_wildcard,
                                 "rejected": rejected}, indent=1))
    if not a.apply:
        print("Re-run with --apply to write.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    p = sub.add_parser("pull")
    p.add_argument("--workers", type=int, default=24)
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return {"sample": cmd_sample, "pull": cmd_pull, "write": cmd_write}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
