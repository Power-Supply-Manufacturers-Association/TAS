#!/usr/bin/env python3
"""ABT #285 — canonicalize manufacturerInfo.name spellings across every active catalog.

Same vendor under multiple spellings (ABRACON/Abracon, Murata/Murata Electronics,
PULSE/Pulse Electronics, ...) silently hides parts from any consumer that filters by
name. Majority spelling wins; only the minority spelling is rewritten.

Only the string at manufacturerInfo.name is touched -- no structural change, so no
schema risk. Untouched lines are written back byte-identical; each file is replaced
atomically. magnetics.ndjson is handled by fix_282_285_316_magnetics.py.

Usage:  fix_285_canonicalize_manufacturers.py [--apply]      (default: dry run)
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
SKIP_FILES = set()      # magnetics was handled in the combined pass; re-running is
                        # idempotent (only names present in CANON are ever rewritten)

# NOTE: the first run's normalizer stripped Inc/Corp/Ltd/GmbH but NOT LLC, so two
# real collisions survived it and were found on a second sweep with a wider suffix
# list. Kept explicit here rather than widening the heuristic, so the mapping stays
# reviewable.
CANON = {
    "Abracon LLC": "Abracon",
    "Nexperia USA Inc.": "Nexperia",
    "Panasonic Electronic Components": "Panasonic",
    "Vishay Semiconductors": "Vishay",
    "Yageo": "YAGEO",
    "Murata Electronics": "Murata",
    "ABRACON": "Abracon",
    "Bourns Inc.": "Bourns",
    "TAIYO YUDEN": "Taiyo Yuden",
    "Infineon Technologies": "Infineon",
    "PULSE": "Pulse Electronics",
    "Littelfuse Inc.": "Littelfuse",
    "Rohm": "ROHM",
    "Microchip Technology": "Microchip",
    "Wolfspeed, Inc.": "Wolfspeed",
    "Vishay Dale": "Vishay / Dale",
    "Murata Power Solutions Inc.": "Murata Power Solutions",
    "Microsemi Corporation": "Microsemi",
    "Diodes Incorporated": "Diodes Inc.",
    "Alpha and Omega Semiconductor": "Alpha and Omega",
    "Navitas Semiconductor, Inc.": "Navitas",
    "GeneSiC Semiconductor": "GeneSiC",
    "Taiwan Semiconductor Corporation": "Taiwan Semiconductor",
    "NXP Semiconductors": "NXP",
}
# This map is now the SINGLE source of truth for manufacturer canonicalization and is
# self-consistent: no key is also a value, so re-running is idempotent. An earlier
# split across two scripts held opposing directions for PULSE / Microsemi / Murata
# Power Solutions / Taiwan Semiconductor and silently reverted each other on re-run.
assert not (set(CANON) & set(CANON.values())), \
    "CANON must not map a name that is itself a canonical target (would flip-flop)"


def rewrite(obj):
    """Rewrite every manufacturerInfo.name in place. Returns hits as a Counter."""
    hits = Counter()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "manufacturerInfo" and isinstance(v, dict):
                    nm = v.get("name")
                    if isinstance(nm, str) and nm in CANON:
                        v["name"] = CANON[nm]
                        hits[f"{nm} -> {CANON[nm]}"] += 1
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return hits


def catalogs():
    for p in sorted(DATA.glob("*.ndjson")):
        if p.name in SKIP_FILES or any(s in p.name for s in SKIP_SUBSTR):
            continue
        yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    grand = Counter()
    per_file = Counter()

    for path in catalogs():
        out = []
        n_changed = 0
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                s = raw.rstrip("\n")
                if not s.strip():
                    continue
                if not any(k in s for k in CANON):      # cheap pre-filter
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
                    n_changed += 1
                    out.append(json.dumps(obj, ensure_ascii=False))
                else:
                    out.append(s)

        if n_changed:
            per_file[path.name] = n_changed
            if a.apply:
                tmp = path.with_suffix(".ndjson.tmp")
                with tmp.open("w", encoding="utf-8") as fh:
                    for line in out:
                        fh.write(line + "\n")
                os.replace(tmp, path)

    print("=" * 70)
    print("DRY RUN — nothing written" if not a.apply else "APPLIED")
    print("=" * 70)
    print(f"records rewritten: {sum(per_file.values())}\n")
    for f, n in per_file.most_common():
        print(f"  {n:6d}  {f}")
    print("\n--- by spelling ---")
    for k, n in grand.most_common():
        print(f"  {n:6d}  {k}")
    if not a.apply:
        print("\nRe-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
