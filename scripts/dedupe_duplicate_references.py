#!/usr/bin/env python3
"""Collapse duplicate (manufacturer, reference) records in any TAS part library.

Append-only imports stacked multiple, often conflicting, records under the same
manufacturer part number (Vishay capacitors/magnetics/diodes, STMicro igbts,
...). JSON-Schema validation never caught this (records are validated
independently; nothing checks uniqueness).

For each (manufacturer, reference) group with >1 record, keep ONE — chosen by
the canonical TAS physics validator (validator/ tas_validator C++/pybind11
module): best verdict (valid; fewest IMPOSSIBLE then SUSPICIOUS findings) then
completeness. Losers go to <file>.quarantine_duplicates.ndjson with a _triage
note. Records without a manufacturer+reference, and singletons, pass through
untouched and in place. Files with no duplicates are left byte-for-byte alone.

Usage: python scripts/dedupe_duplicate_references.py [file_basename ...]
       (default: all active part libraries)
"""
import json
import sys
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / 'data'
DEFAULT_FILES = ['magnetics', 'capacitors', 'resistors', 'varistors',
                 'diodes', 'mosfets', 'igbts', 'bjts', 'controllers',
                 'analog_ics', 'thermistors']

sys.path.insert(0, str(REPO / 'validator' / 'build'))
DATE = '2026-07-31'
try:
    import tas_validator
except ImportError as e:
    raise SystemExit("tas_validator not built — see validator/BUILD.md") from e


def manufacturer_info(rec):
    """Find manufacturerInfo through the 1-level (capacitor/magnetic/...) or
    2-level (semiconductor/{mosfet,diode,igbt}) discriminator wrap."""
    if not isinstance(rec, dict):
        return {}
    # Annotation keys sit ALONGSIDE the discriminator (_triage, _validatorQuarantine).
    # A strict len(rec) == 1 test treats an annotated record as unreadable and passes
    # it through unkeyed, which is how two annotated Vishay mosfets stayed duplicated
    # while the file reported "no duplicates".
    body_keys = [k for k in rec if not k.startswith('_')]
    if len(body_keys) != 1:
        return {}
    body = rec[body_keys[0]]
    if not isinstance(body, dict):
        return {}
    if isinstance(body.get('manufacturerInfo'), dict):
        return body['manufacturerInfo']
    for v in body.values():
        if isinstance(v, dict) and isinstance(v.get('manufacturerInfo'), dict):
            return v['manufacturerInfo']
    return {}


def part_number(mi):
    """The part number, from EITHER field the catalogues use.

    manufacturerInfo.reference is the usual home, but a large share of records carry
    it ONLY as datasheetInfo.part.partNumber. Keying on `reference` alone made this
    script see 69 duplicates corpus-wide when there were 16,741: every TDK capacitor
    duplicate — 16,671 of them, two imports of the same parts, one via the catalogue
    PDF and one via product.tdk.com — was invisible, because those rows have no
    `reference` at all and were passed through as unkeyed.

    Same blind spot that let 17,183 fabricated records past a reference-keyed guard in
    ABT #256. Any code that identifies a part must read both fields.
    """
    r = mi.get('reference')
    if r:
        return str(r)
    part = (mi.get('datasheetInfo') or {}).get('part') or {}
    pn = part.get('partNumber')
    return str(pn) if pn else None


def leaves(d, n=0):
    for v in d.values():
        if isinstance(v, dict):
            n = leaves(v, n)
        elif v is not None:
            n += 1
    return n


def verdict_score(rec):
    try:
        v = tas_validator.validate(rec)
    except RuntimeError:
        return (-1, 0, 0)
    imp = sum(1 for f in v.findings if f.severity == 'IMPOSSIBLE')
    sus = sum(1 for f in v.findings if f.severity == 'SUSPICIOUS')
    return (1 if v.valid else 0, -imp, -sus)


def dedupe_file(name):
    src = DATA / f'{name}.ndjson'
    if not src.exists():
        return
    lines = []                       # (raw, key_or_None)
    groups = defaultdict(list)       # (mfr, ref) -> [idx, ...]
    for raw in src.open():
        raw = raw.rstrip('\n')
        if not raw.strip():
            continue
        rec = json.loads(raw)
        mi = manufacturer_info(rec)
        key = (mi.get('name'), part_number(mi))
        idx = len(lines)
        if key[0] and key[1]:
            groups[key].append(idx)
            lines.append([raw, key])
        else:
            lines.append([raw, None])

    dup_keys = {k: v for k, v in groups.items() if len(v) > 1}
    if not dup_keys:
        print(f"{name}: no duplicates")
        return

    keep = set()
    quarantined = []
    for key, idxs in groups.items():
        if len(idxs) == 1:
            keep.add(idxs[0])
            continue
        ranked = sorted(
            idxs,
            key=lambda i: (verdict_score(json.loads(lines[i][0])),
                           leaves(json.loads(lines[i][0]))),
            reverse=True,
        )
        keep.add(ranked[0])
        wscore = verdict_score(json.loads(lines[ranked[0]][0]))
        for i in ranked[1:]:
            rec = json.loads(lines[i][0])
            rec['_triage'] = {
                'disposition': 'duplicate-reference',
                'reason': f"duplicate {key[0]} reference {key[1]!r}; kept the copy with the "
                          f"best tas_validator verdict + completeness (kept score={wscore})",
                'date': DATE,
            }
            quarantined.append(json.dumps(rec, ensure_ascii=False))

    kept_lines = [raw for i, (raw, key) in enumerate(lines) if key is None or i in keep]
    src.write_text('\n'.join(kept_lines) + '\n')
    quar = DATA / f'{name}.quarantine_duplicates.ndjson'
    with quar.open('a') as f:
        for q in quarantined:
            f.write(q + '\n')
    print(f"{name}: {len(dup_keys)} dup refs collapsed, {len(quarantined)} quarantined "
          f"-> {quar.name}; file {len(lines)} -> {len(kept_lines)} records")


def main():
    files = sys.argv[1:] or DEFAULT_FILES
    for name in files:
        dedupe_file(name)


if __name__ == '__main__':
    main()
