#!/usr/bin/env python3
"""ABT #389: merge a staged connector vendor into data/connectors.ndjson.

Generalises scripts/merge_jst_connectors.py to any vendor staged under
staging/<vendor>/records.ndjson by the harvest agents. Every record is re-validated
HERE rather than trusted from the harvest report:

  - CONAS JSON Schema (Draft 2020-12, registry from the sibling repos)
  - Blade Runner, which does have real connector physics — 18 checks including
    clearance breakdown, creepage-vs-clearance, DWV vs rated voltage, contact and
    insulation resistance, and current/voltage range. Anything IMPOSSIBLE is refused.
  - duplicate references against the live catalogue, so a re-run cannot double-insert.

APPENDS, never rewrites: connectors.ndjson is ~140 MB and other processes append to it.

  merge_staged_connectors.py <vendor> [--apply]
"""
import argparse
import json
import sys
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
LIVE = TAS / "data" / "connectors.ndjson"


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    psma = TAS.parent
    by = {}
    for repo in ("PEAS", "CONAS"):
        for p in (psma / repo / "schemas").rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by[s["$id"]] = s
    reg = Registry().with_resources(
        [(s["$id"], Resource(contents=s, specification=DRAFT202012)) for s in by.values()])
    return Draft202012Validator(
        json.loads((psma / "CONAS" / "schemas" / "connector.json").read_text()), registry=reg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vendor")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    staged = TAS / "staging" / a.vendor / "records.ndjson"
    if not staged.exists():
        print(f"no staged records at {staged}")
        return 2

    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    gate = BladeGate("connector")
    v = build_validator()

    existing = set()
    with LIVE.open(encoding="utf-8") as fh:
        for line in fh:
            if '"reference"' not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            ref = ((o.get("connector") or {}).get("manufacturerInfo") or {}).get("reference")
            if ref:
                existing.add(ref)

    good, dupes, bad, blocked, fams = [], 0, [], [], {}
    seen = set()
    with staged.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            c = rec.get("connector") or {}
            mi = c.get("manufacturerInfo") or {}
            ref = mi.get("reference")
            if ref in existing or ref in seen:
                dupes += 1
                continue
            seen.add(ref)
            errs = sorted(v.iter_errors(c), key=lambda e: e.path)
            if errs:
                if len(bad) < 5:
                    bad.append(f"{ref}: {errs[0].message[:120]}")
                continue
            ok, why = gate.check(c)
            if not ok:
                if len(blocked) < 5:
                    blocked.append(f"{ref}: {why}")
                continue
            fam = ((mi.get("datasheetInfo") or {}).get("familyDetails") or {}).get("family")
            fams[fam] = fams.get(fam, 0) + 1
            good.append(rec)

    print(f"{a.vendor}: {len(good)} mergeable  ({dupes} already present/duplicate, "
          f"{len(bad)} schema-invalid, {len(blocked)} Blade Runner IMPOSSIBLE)")
    print(f"  families: {dict(sorted(fams.items(), key=lambda kv: -kv[1])[:8])}")
    print(" ", gate.summary())
    for b in bad:
        print("   schema:", b)
    for b in blocked:
        print("   BLADE:", b)
    if not a.apply:
        print("\nDRY RUN — pass --apply to write")
        return 0

    with LIVE.open("a", encoding="utf-8") as fh:
        for rec in good:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"appended {len(good)} records to {LIVE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
