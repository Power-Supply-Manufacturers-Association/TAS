#!/usr/bin/env python3
"""ABT #285 follow-up: re-canonicalize the vendor names where majority-wins was wrong.

The first pass (fix_285_canonicalize_manufacturers.py) applied majority-spelling-wins
uniformly to minimize churn. That produced four results inconsistent with the
suffix-stripping the same pass applied everywhere else (Bourns Inc.->Bourns,
Littelfuse Inc.->Littelfuse, Wolfspeed, Inc.->Wolfspeed, Infineon Technologies->
Infineon, NXP Semiconductors->NXP), plus one brand-name error.

Applies to EVERY active catalog including magnetics. Only the string at
manufacturerInfo.name changes.

Usage:  fix_285_recanonicalize_debatable.py [--apply]      (default: dry run)
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

DATA = Path.home() / "PSMA" / "TAS" / "data"
SKIP_SUBSTR = ("quarantine", "backup", ".bak", "pending", "unmapped", "staged",
               "enumeration", "unprocessed")

# left = what is in the data now, right = corrected canonical
RECANON = {
    # brand name: the company is "Pulse Electronics"; PULSE is a distributor rendering
    "PULSE": "Pulse Electronics",
    # consistency: every other corporate suffix was stripped by the first pass
    "Microsemi Corporation": "Microsemi",
    "Murata Power Solutions Inc.": "Murata Power Solutions",
    "Taiwan Semiconductor Corporation": "Taiwan Semiconductor",
}
# NOT flipped: YAGEO. All-caps IS the official brand (as with ROHM, which the first
# pass canonicalized to all-caps without objection). Lowercasing it would introduce
# the very inconsistency this ticket is about. One line to change if wanted.


def rewrite(obj):
    hits = Counter()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "manufacturerInfo" and isinstance(v, dict):
                    nm = v.get("name")
                    if isinstance(nm, str) and nm in RECANON:
                        v["name"] = RECANON[nm]
                        hits[f"{nm} -> {RECANON[nm]}"] += 1
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    grand, per_file = Counter(), Counter()
    for path in sorted(DATA.glob("*.ndjson")):
        if any(s in path.name for s in SKIP_SUBSTR):
            continue
        out, changed = [], 0
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                s = raw.rstrip("\n")
                if not s.strip():
                    continue
                if not any(k in s for k in RECANON):
                    out.append(s)
                    continue
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError:
                    out.append(s)
                    continue
                hits = rewrite(obj)
                if hits:
                    grand.update(hits)
                    changed += 1
                    out.append(json.dumps(obj, ensure_ascii=False))
                else:
                    out.append(s)
        if changed:
            per_file[path.name] = changed
            if a.apply:
                tmp = path.with_suffix(".ndjson.tmp")
                with tmp.open("w", encoding="utf-8") as fh:
                    for line in out:
                        fh.write(line + "\n")
                os.replace(tmp, path)

    print("DRY RUN — nothing written" if not a.apply else "APPLIED")
    print(f"records rewritten: {sum(per_file.values())}\n")
    for f, n in per_file.most_common():
        print(f"  {n:6d}  {f}")
    print()
    for k, n in grand.most_common():
        print(f"  {n:6d}  {k}")
    if not a.apply:
        print("\nRe-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
