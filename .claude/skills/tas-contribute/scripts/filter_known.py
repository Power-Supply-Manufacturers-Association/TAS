#!/usr/bin/env python3
"""Drop part numbers TAS already holds (live, quarantined or denylisted) from a work list.

    python3 filter_known.py LIST UNIT_DIR [--tas-root TAS]

LIST is JSONL with at least {"mpn": ..., "manufacturer": ...} per line, or plain text with
one part number per line. Writes UNIT_DIR/input.jsonl with the parts TAS does not know yet
and prints the counts.

This is the lead filter only (adapted from the maintainers' unit builder): it normalises a
part number by removing spaces, '-', '_', '/' and upper-casing, and compares it with every
partNumber/reference/id in every data/*.ndjson (circuits and converters excluded), which
includes data/quarantine.ndjson and data/fabricated_denylist.ndjson. It is deliberately
coarse -- it may drop a real hyphen twin (Eaton 10BJ6-0A vs 10BJ60A). The authoritative
duplicate check is the candidates gate (check_candidates.py), which compares exact
(part number, manufacturer) identities.
"""
import json, os, re, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import find_tas_root, pop_tas_root_arg, data_root, is_lfs_pointer  # noqa: E402

root = find_tas_root(pop_tas_root_arg())
if len(sys.argv) != 3:
    sys.exit(__doc__)
src, unit = sys.argv[1], sys.argv[2]
norm = lambda s: re.sub(r'[\s\-_/]', '', s).upper()
PN = re.compile(rb'"(?:partNumber|reference|id)":\s*"((?:[^"\\]|\\.)*)"')
D = data_root(root) / 'data'
known = set()
for f in sorted(os.listdir(D)):
    if f.endswith('.ndjson') and f not in ('circuits.ndjson', 'converters.ndjson'):
        if is_lfs_pointer(D / f):
            sys.exit(f'data/{f} is an unfetched git-LFS pointer: run `git lfs pull` first '
                     '(a filter that cannot see the data would pass everything)')
        with open(D / f, 'rb') as fh:
            for line in fh:
                for m in PN.findall(line):
                    known.add(norm(m.decode('utf-8', 'replace')))
os.makedirs(unit, exist_ok=True)
n = collections.Counter(); seen = set()
with open(src, encoding='utf-8') as fi, open(os.path.join(unit, 'input.jsonl'), 'w', encoding='utf-8') as o:
    for line in fi:
        line = line.strip()
        if not line:
            continue
        d = json.loads(line) if line.startswith('{') else {'mpn': line, 'manufacturer': None}
        k = norm(d['mpn'])
        if k in known:
            n['already_known'] += 1; continue
        if k in seen:
            n['dup_spelling_in_list'] += 1; continue
        seen.add(k); o.write(json.dumps(d, ensure_ascii=False) + '\n'); n['new'] += 1
print(json.dumps({'unit': unit, **n}))
