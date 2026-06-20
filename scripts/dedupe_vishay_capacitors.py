#!/usr/bin/env python3
"""Collapse duplicate Vishay capacitor references in data/capacitors.ndjson.

Multiple append-only import passes produced several records per Vishay
reference with conflicting (and sometimes physically impossible, e.g. 100 F,
176 C) values. JSON-Schema validation never caught this: it checks each record
independently and bounds nothing physically.

For each (manufacturer=Vishay, reference) group we keep ONE record, chosen by
the canonical TAS physics validator (validator/, the tas_validator C++/pybind11
module) — NOT a hand-rolled rule set:
  1. validator verdict   (valid; fewest IMPOSSIBLE then fewest SUSPICIOUS findings)
  2. completeness        (number of populated leaf fields)
Losers go to data/capacitors.quarantine_duplicates.ndjson with a _triage note.
Non-Vishay and singleton records are passed through untouched, in place.
"""
import json
import sys
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / 'data'
SRC = DATA / 'capacitors.ndjson'
QUAR = DATA / 'capacitors.quarantine_duplicates.ndjson'
DATE = '2026-06-20'

sys.path.insert(0, str(REPO / 'validator' / 'build'))
try:
    import tas_validator
except ImportError as e:
    raise SystemExit(
        "tas_validator not built — see validator/BUILD.md "
        "(cmake -B validator/build -G Ninja && cmake --build validator/build)"
    ) from e


def leaves(d, n=0):
    for v in d.values():
        if isinstance(v, dict):
            n = leaves(v, n)
        elif v is not None:
            n += 1
    return n


def verdict_score(rec):
    """Rank key from the canonical validator. Higher is better:
    valid first, then fewer IMPOSSIBLE, then fewer SUSPICIOUS findings."""
    try:
        v = tas_validator.validate(rec)
    except RuntimeError:
        return (-1, 0, 0)            # malformed field shape -> worst
    imp = sum(1 for f in v.findings if f.severity == 'IMPOSSIBLE')
    sus = sum(1 for f in v.findings if f.severity == 'SUSPICIOUS')
    return (1 if v.valid else 0, -imp, -sus)


def main():
    groups = defaultdict(list)   # vishay (ref) -> list of (idx, raw, cap)
    lines = []                   # (raw, kind)  kind: 'keep' | 'vishay-dup-member'
    for raw in SRC.open():
        raw = raw.rstrip('\n')
        if not raw.strip():
            continue
        rec = json.loads(raw)
        cap = rec.get('capacitor', {})
        mi = cap.get('manufacturerInfo', {})
        ref = mi.get('reference')
        idx = len(lines)
        if mi.get('name') == 'Vishay' and ref:
            groups[ref].append((idx, raw, cap))
            lines.append([raw, 'vishay', ref])
        else:
            lines.append([raw, 'keep', None])

    keep_idx = set()
    quarantined = []
    dup_refs = 0
    for ref, members in groups.items():
        if len(members) == 1:
            keep_idx.add(members[0][0])
            continue
        dup_refs += 1
        ranked = sorted(
            members,
            key=lambda m: (verdict_score({'capacitor': m[2]}), leaves(m[2])),
            reverse=True,
        )
        winner = ranked[0]
        keep_idx.add(winner[0])
        wscore = verdict_score({'capacitor': winner[2]})
        for idx, raw, cap in ranked[1:]:
            rec = json.loads(raw)
            rec['_triage'] = {'disposition': 'duplicate-reference',
                              'reason': f"duplicate Vishay reference {ref!r}; kept the copy with "
                                        f"the best tas_validator verdict + completeness "
                                        f"(kept score={wscore}, this score={verdict_score({'capacitor': cap})})",
                              'date': DATE}
            quarantined.append(json.dumps(rec, ensure_ascii=False))

    # rewrite source preserving order, dropping the quarantined Vishay members
    kept_lines = []
    for i, (raw, kind, ref) in enumerate(lines):
        if kind == 'keep' or i in keep_idx:
            kept_lines.append(raw)
    SRC.write_text('\n'.join(kept_lines) + '\n')
    with QUAR.open('a') as f:
        for q in quarantined:
            f.write(q + '\n')

    print(f"Vishay refs with duplicates: {dup_refs}")
    print(f"records kept (total file): {len(kept_lines)}")
    print(f"duplicates quarantined: {len(quarantined)} -> {QUAR.name}")


if __name__ == '__main__':
    main()
