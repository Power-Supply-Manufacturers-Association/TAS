#!/usr/bin/env python3
"""Quarantine records the canonical validator marks IMPOSSIBLE via a magnitude
check (CAP_MAGNITUDE / MAG_L_MAGNITUDE) — capacitance/inductance values that are
unit errors (e.g. an EIA code "100" stored as 100 F, or 3700 H for a small
choke). These are singletons, not duplicates, so the dedup pass missed them.

Movers go to <file>.quarantine_invalid_physics.ndjson with a _triage note
carrying the offending finding. Only IMPOSSIBLE magnitude findings are acted on;
SUSPICIOUS values are left in place for human review.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / 'data'
DATE = '2026-06-21'
MAG_CODES = {'CAP_MAGNITUDE', 'MAG_L_MAGNITUDE'}
FILES = ['capacitors', 'magnetics']

sys.path.insert(0, str(REPO / 'validator' / 'build'))
try:
    import tas_validator
except ImportError as e:
    raise SystemExit("tas_validator not built — see validator/BUILD.md") from e


def offending(rec):
    """Return the IMPOSSIBLE magnitude finding (code, message) or None."""
    try:
        v = tas_validator.validate(rec)
    except RuntimeError:
        return None
    for f in v.findings:
        if f.code in MAG_CODES and f.severity == 'IMPOSSIBLE':
            return (f.code, f.message)
    return None


def process(name):
    src = DATA / f'{name}.ndjson'
    if not src.exists():
        return
    keep, moved = [], []
    for raw in src.open():
        raw = raw.rstrip('\n')
        if not raw.strip():
            continue
        rec = json.loads(raw)
        hit = offending(rec)
        if hit:
            rec['_triage'] = {'disposition': 'invalid-physics',
                              'reason': f"{hit[0]}: {hit[1]}",
                              'date': DATE}
            moved.append(json.dumps(rec, ensure_ascii=False))
        else:
            keep.append(raw)
    if not moved:
        print(f"{name}: no magnitude-impossible records")
        return
    src.write_text('\n'.join(keep) + '\n')
    quar = DATA / f'{name}.quarantine_invalid_physics.ndjson'
    with quar.open('a') as f:
        for m in moved:
            f.write(m + '\n')
    print(f"{name}: quarantined {len(moved)} magnitude-impossible records "
          f"-> {quar.name}; file -> {len(keep)} records")


def main():
    for name in FILES:
        process(name)


if __name__ == '__main__':
    main()
