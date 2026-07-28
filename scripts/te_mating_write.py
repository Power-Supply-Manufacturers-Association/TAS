#!/usr/bin/env python3
"""ABT #249 phase 3: write classified TE mating edges into connectors.ndjson.

Writes datasheetInfo.mating.matesWith[] = [{series, relation}] per CONAS
schemas/connector.json (closed object; required [series, relation]; relation enum
mates / intermateableStandard / mandatoryCompanion / optionalCompanion).

Rules honoured:
  * Line-patch: untouched lines are written back byte-identical; atomic replace.
  * Every patched record is validated against the CONAS connector schema BEFORE the
    file is replaced -- a record that fails is left UNPATCHED and reported, never
    written (no schema-invalid object at any stage, not even temporarily).
  * Existing matesWith entries are merged, not overwritten; a 'mates' verdict
    upgrades a previously recorded 'optionalCompanion' for the same counterpart,
    never the reverse.
  * The classifier's _why field is diagnostic only and is NOT written (the schema is
    closed; it would be an illegal extra key).

Usage: te_mating_write.py [--apply]        (default: dry run)
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

PSMA = Path.home() / "PSMA"
TAS = PSMA / "TAS"
SRC = TAS / "data" / "connectors.ndjson"
CLASSIFIED = TAS / "staging" / "te" / "te_mating_classified.json"

RANK = {"optionalCompanion": 0, "mandatoryCompanion": 1, "intermateableStandard": 2,
        "mates": 3}


def build_validator():
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


def merge(existing, incoming):
    """Merge matesWith lists, keeping the strongest relation per counterpart series."""
    by_series = {}
    for e in existing or []:
        by_series[e.get("series")] = dict(e)
    for e in incoming:
        s = e["series"]
        prev = by_series.get(s)
        if prev is None or RANK.get(e["relation"], 0) > RANK.get(prev.get("relation"), 0):
            keep = {"series": s, "relation": e["relation"]}
            if prev and "manufacturer" in prev:
                keep["manufacturer"] = prev["manufacturer"]
            if prev and "matedHeight" in prev:
                keep["matedHeight"] = prev["matedHeight"]
            by_series[s] = keep
    return sorted(by_series.values(), key=lambda x: x["series"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    classified = json.loads(CLASSIFIED.read_text())
    # strip the diagnostic _why -- the CONAS matesWith item is a closed object
    edges = {src: [{"series": m["series"], "relation": m["relation"]} for m in ms]
             for src, ms in classified.items()}

    validator = build_validator()
    stats = Counter()
    rel_written = Counter()
    invalid_samples = []

    out_lines = []
    with SRC.open("r", encoding="utf-8") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            stats["total"] += 1
            if "TE Connectivity" not in s:
                out_lines.append(s)
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                out_lines.append(s)
                continue
            conn = obj.get("connector") or obj
            mi = conn.get("manufacturerInfo") or {}
            ref = mi.get("reference")
            if mi.get("name") != "TE Connectivity" or ref not in edges:
                out_lines.append(s)
                continue

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

            for m in mating["matesWith"]:
                rel_written[m["relation"]] += 1
            stats["patched"] += 1
            out_lines.append(json.dumps(obj, ensure_ascii=False))

    print("=" * 70)
    print("DRY RUN — nothing written" if not a.apply else "APPLIED")
    print("=" * 70)
    for k in ("total", "patched", "unchanged", "rejected_invalid"):
        print(f"  {k:20} {stats[k]}")
    print("\n--- matesWith entries written, by relation ---")
    for k, v in rel_written.most_common():
        print(f"  {v:6d}  {k}")
    if invalid_samples:
        print("\n--- records LEFT UNPATCHED because the result failed CONAS validation ---")
        for x in invalid_samples:
            print(f"  {x}")

    if not a.apply:
        print("\nRe-run with --apply to write.")
        return 0

    tmp = SRC.with_suffix(".ndjson.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for line in out_lines:
            fh.write(line + "\n")
    os.replace(tmp, SRC)
    print(f"\natomically replaced {SRC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
