#!/usr/bin/env python3
"""Sweep the TAS catalog through the validator and summarise verdicts.

    cd TAS/validator && cmake -B build -G Ninja && cmake --build build
    python3 sweep.py                 # all data/*.ndjson
    python3 sweep.py magnetics       # one family
    TAS_VALIDATOR_LIMIT=2000 python3 sweep.py

Prints, per file: records, INVALID (>=1 IMPOSSIBLE), SUSPICIOUS-only, malformed,
and the top firing check codes. A healthy catalog should be mostly clean; an
"everything invalid" result almost always means a units/path bug, not bad data.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "build"))
import tas_validator  # noqa: E402

DATA = HERE.parent / "data"
ALL = ["magnetics", "capacitors", "resistors", "diodes", "mosfets", "igbts"]
LIMIT = int(os.environ.get("TAS_VALIDATOR_LIMIT", "0"))  # 0 = no limit


def sweep(name):
    path = DATA / f"{name}.ndjson"
    if not path.exists():
        print(f"  {name}: (missing)")
        return
    n = invalid = suspicious = malformed = 0
    codes = Counter()
    with open(path) as f:
        for i, line in enumerate(f):
            if LIMIT and i >= LIMIT:
                break
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                v = tas_validator.validate(json.loads(line))
            except RuntimeError:
                malformed += 1
                continue
            except ValueError:
                malformed += 1
                continue
            for fnd in v.findings:
                codes[f"{fnd.code}/{fnd.severity}"] += 1
            if not v.valid:
                invalid += 1
            elif v.findings:
                suspicious += 1
    top = ", ".join(f"{c}={k}" for c, k in codes.most_common(6))
    print(f"  {name:11s} n={n:<7d} invalid={invalid:<6d} suspicious={suspicious:<6d} "
          f"malformed={malformed:<6d}")
    if top:
        print(f"               top: {top}")


def main():
    families = sys.argv[1:] or ALL
    print(f"TAS validator sweep (limit={LIMIT or 'none'})")
    for name in families:
        sweep(name)


if __name__ == "__main__":
    main()
