#!/usr/bin/env python3
"""ABT #249: harvest Molex mating relations into datasheetInfo.mating.matesWith[].

Plain curl-able JSON API — no browser, no Akamai, unlike TE:
  GET https://search.molex.com/api/search/productdetails?q=<PART_NUMBER>
    -> products[0].matesWithUseWith.matesWithSeries = [{seriesId, seriesName}, ...]
       products[0].physical.matedHeight            = ["17.64mm"]

WHY relation='mates' IS SOURCED HERE, NOT INFERRED: Molex publishes an explicit
"Mates With / Use With" relation. That is stronger evidence than the gender-verified
inference used for TE (where the vendor only said "Compatible"), so these are recorded
as 'mates' directly on the vendor's authority.

*** PER-PART, NOT PER-SERIES. *** Tempting to make ~347 calls (one per series in our
catalogue) instead of 94,255 — but the relation VARIES WITHIN a series by circuit
count. Measured on series 43045:
    430450412 (4 circuits)  -> 6 counterpart series, matedHeight 17.64mm
    430450800 (8 circuits)  -> 6 counterpart series, matedHeight 10.29mm
    430451200 (12 circuits) -> only 2 counterpart series
Collapsing to series level would have invented 4 mating relations for every 12-circuit
part. Do not "optimise" this back.

Staging-first: fetch writes JSONL; a separate --apply pass line-patches
connectors.ndjson with per-record CONAS validation.

Usage:
  molex_mating_harvest.py fetch [--limit N] [--delay 0.25]
  molex_mating_harvest.py write [--apply]
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

TAS = Path.home() / "PSMA" / "TAS"
SRC = TAS / "data" / "connectors.ndjson"
STAGE = TAS / "staging" / "molex"
OUT = STAGE / "mating.jsonl"
DONE = STAGE / "mating_done.json"
LOG = STAGE / "mating_harvest.log"
STOP = STAGE / "STOP"

API = "https://search.molex.com/api/search/productdetails"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def molex_parts():
    out = []
    with SRC.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or "Molex" not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = o.get("connector") or o
            mi = c.get("manufacturerInfo") or {}
            if mi.get("name") != "Molex":
                continue
            ds = mi.get("datasheetInfo") or {}
            if ((ds.get("mating") or {}).get("matesWith")):
                continue                          # already has relations
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            if pn:
                out.append(pn)
    return out


def parse_mm(v):
    """['17.64mm'] -> 0.01764 (metres). None when absent/unparseable."""
    if isinstance(v, list):
        v = v[0] if v else None
    if not isinstance(v, str):
        return None
    m = re.search(r"([\d.]+)\s*mm", v)
    return float(m.group(1)) * 1e-3 if m else None


def fetch(sess, pn, timeout=45):
    try:
        r = sess.get(API, params={"q": pn}, timeout=timeout)
    except Exception as e:
        return None, f"net: {str(e)[:60]}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        d = r.json()
    except Exception:
        return None, "unparseable json"
    prods = d.get("products") or []
    if not prods:
        return None, "no products"
    p = prods[0]
    series = ((p.get("matesWithUseWith") or {}).get("matesWithSeries")) or []
    if not series:
        return None, "no matesWithSeries"
    height = parse_mm((p.get("physical") or {}).get("matedHeight"))
    entries = []
    for s in series:
        sid = str(s.get("seriesId") or "").strip()
        if not sid:
            continue
        e = {"series": sid, "relation": "mates"}
        if height and height > 0:
            e["matedHeight"] = height
        entries.append(e)
    return (entries or None), (None if entries else "no usable series")


def cmd_fetch(a):
    STAGE.mkdir(parents=True, exist_ok=True)
    if STOP.exists():
        STOP.unlink()
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    parts = [p for p in molex_parts() if p not in done]
    if a.limit:
        parts = parts[:a.limit]
    log(f"FETCH start: {len(parts)} Molex parts without mating ({len(done)} done)")

    sess = requests.Session()
    ok = fail = 0
    errs = {}
    buf = []
    for i, pn in enumerate(parts, 1):
        if STOP.exists():
            log("STOP file -- exiting cleanly")
            break
        entries, err = fetch(sess, pn)
        if err:
            fail += 1
            errs[err] = errs.get(err, 0) + 1
            done.add(pn)          # a definitive "no relations" is a real answer
        else:
            ok += 1
            buf.append({"partNumber": pn, "matesWith": entries})
            done.add(pn)
        if len(buf) >= 25 or i == len(parts):
            with OUT.open("a", encoding="utf-8") as fh:
                for r in buf:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            buf.clear()
            DONE.write_text(json.dumps(sorted(done)))
        if i % 200 == 0:
            log(f"  {i}/{len(parts)} ok={ok} fail={fail} {dict(list(errs.items())[:3])}")
        time.sleep(a.delay)
    if buf:
        with OUT.open("a", encoding="utf-8") as fh:
            for r in buf:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        DONE.write_text(json.dumps(sorted(done)))
    log(f"FETCH end: ok={ok} fail={fail} errors={errs}")
    return 0


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    PSMA = Path.home() / "PSMA"
    by = {}
    for repo in ("PEAS", "CONAS"):
        for p in (PSMA / repo / "schemas").rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by[s["$id"]] = s
    res = [Resource(contents=s, specification=DRAFT202012) for s in by.values()]
    reg = Registry().with_resources([(r.contents["$id"], r) for r in res])
    return Draft202012Validator(
        json.loads((PSMA / "CONAS" / "schemas" / "connector.json").read_text()), registry=reg)


def cmd_write(a):
    rel = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rel[r["partNumber"]] = r["matesWith"]
    log(f"WRITE: {len(rel)} Molex parts with harvested relations")
    if not rel:
        return 0
    v = build_validator()
    patched = rejected = 0
    bad = []
    out_lines = []
    with SRC.open(encoding="utf-8") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            if "Molex" not in s:
                out_lines.append(s)
                continue
            obj = json.loads(s)
            c = obj.get("connector") or obj
            mi = c.get("manufacturerInfo") or {}
            ds = mi.get("datasheetInfo") or {}
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            if mi.get("name") != "Molex" or pn not in rel:
                out_lines.append(s)
                continue
            mating = ds.setdefault("mating", {})
            if mating.get("matesWith"):
                out_lines.append(s)
                continue
            mating["matesWith"] = rel[pn]
            errs = sorted(v.iter_errors(c), key=lambda x: x.path)
            if errs:
                rejected += 1
                if len(bad) < 5:
                    bad.append(f"{pn}: {errs[0].message[:140]}")
                out_lines.append(s)
                continue
            patched += 1
            out_lines.append(json.dumps(obj, ensure_ascii=False))
    log(f"WRITE {'APPLIED' if a.apply else 'DRY RUN'}: patched={patched} rejected={rejected}")
    for b in bad:
        log(f"  rejected: {b}")
    if a.apply and patched:
        tmp = SRC.with_suffix(".ndjson.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for line in out_lines:
                fh.write(line + "\n")
        os.replace(tmp, SRC)
        log(f"atomically replaced {SRC}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch"); f.add_argument("--limit", type=int); f.add_argument("--delay", type=float, default=0.25)
    w = sub.add_parser("write"); w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return cmd_fetch(a) if a.cmd == "fetch" else cmd_write(a)


if __name__ == "__main__":
    sys.exit(main())
