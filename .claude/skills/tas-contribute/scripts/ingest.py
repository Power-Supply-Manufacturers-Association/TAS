#!/usr/bin/env python3
"""Append a gated accepted.ndjson to a live catalogue: flock, backup, re-check duplicates
under the lock, append, verify prefix bytes unchanged and line count = old + new.

    python3 ingest.py CATALOGUE ACCEPTED.ndjson [OLD=NEW ...] [--allow-twins FILE] [--tas-root TAS]

Identity is (partNumber, manufacturer name) -- the same number from another manufacturer is a
second source and is appended. Duplicates under the lock: a partNumber whose exact spelling
(case-insensitive) is already live, or earlier in this file, FROM THE SAME MANUFACTURER is
skipped (skipped_dup_under_lock). A partNumber that differs from a same-manufacturer
live/earlier spelling only by '-', '_', '/' or spaces is a SPELLING TWIN (Eaton 10BJ6-0A is
6.0 V, 10BJ60A is 60 V): it is never skipped silently and never appended unasked. Unless every twin is listed (exact spelling, one per line, '#' comments) in the
--allow-twins file -- which says someone checked that the vendor prints both spellings --
the whole ingest is refused and nothing is written. A row naming no manufacturer refuses the
whole ingest (it cannot be compared). Manufacturer names compare per ingest_gate.manufacturer_key
(case-insensitive, trimmed, plus its explicit alias table), after any OLD=NEW renames.
"""
import fcntl, hashlib, json, os, shutil, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import find_tas_root, pop_tas_root_arg, setup_imports  # noqa: E402
CODE = str(find_tas_root(pop_tas_root_arg()))   # the TAS checkout (code + schemas)
TAS = os.environ.get('TAS_ROOT', CODE)   # data root; overridable for tests only
setup_imports(find_tas_root(CODE))
from ingest_gate import KnownSpellings, exact_spelling, twin_reason, manufacturer_key  # noqa: E402

args = sys.argv[1:]
allow_file = None
if '--allow-twins' in args:
    i = args.index('--allow-twins')
    if i + 1 >= len(args):
        sys.exit('--allow-twins needs a FILE')
    allow_file = args[i + 1]
    del args[i:i + 2]
cat, src = args[0], args[1]
rename = dict(a.split('=', 1) for a in args[2:])  # manufacturer name normalisation
allowed = set()
if allow_file:
    for l in open(allow_file, encoding='utf-8'):
        l = l.split('#', 1)[0].strip()
        if l:
            allowed.add(exact_spelling(l))
live = f'{TAS}/data/{cat}'
with open(live + '.lock', 'w') as lk:
    fcntl.flock(lk, fcntl.LOCK_EX)
    size = os.path.getsize(live)
    h = hashlib.sha256(); n_old = 0
    with open(live, 'rb') as f:
        for line in f:
            h.update(line); n_old += 1
    pre = h.hexdigest()
    ids = KnownSpellings().add_file(live, 'live', fields=('partNumber',))
    rows, skipped, twins, allowed_used = [], 0, [], set()
    for l in open(src):
        r = json.loads(l)
        body = next(iter(r.values()))
        if 'manufacturerInfo' not in body: body = next(iter(body.values()))
        mi = body['manufacturerInfo']
        if mi.get('name') in rename: mi['name'] = rename[mi['name']]
        pn = mi['datasheetInfo']['part']['partNumber']
        if manufacturer_key(mi.get('name')) is None:
            sys.exit('INGEST REFUSED -- %s names no manufacturer; identity is (partNumber, '
                     'manufacturer); nothing was written' % pn)
        v = ids.verdict(pn, mi['name'])
        if v and v[0] == 'duplicate': skipped += 1; continue
        if v:
            if exact_spelling(pn) not in allowed:
                twins.append((pn, twin_reason(v[1]))); continue
            allowed_used.add(exact_spelling(pn))
        ids.add(pn, mi['name'], 'earlier row of this file'); rows.append(json.dumps(r, ensure_ascii=False) + '\n')
    if twins:
        sys.stderr.write('INGEST REFUSED -- %d spelling twin(s) not in an --allow-twins file; '
                         'nothing was written:\n' % len(twins))
        for pn, why in twins:
            sys.stderr.write('  %s: %s\n' % (pn, why))
        sys.exit(2)
    os.makedirs(f'{TAS}/.backups', exist_ok=True)
    bak = f'{TAS}/.backups/{cat}.{time.strftime("%Y%m%dT%H%M%S")}.{time.time_ns() % 10**9:09d}'
    shutil.copy2(live, bak)
    assert hashlib.sha256(open(bak, 'rb').read()).hexdigest() == pre, 'backup mismatch'
    with open(live, 'rb+') as f:
        f.seek(0, 2)
        if size:
            f.seek(size - 1)
            if f.read(1) != b'\n': sys.exit('live file lacks trailing newline')
        f.seek(0, 2); f.write(''.join(rows).encode()); f.flush(); os.fsync(f.fileno())
    h2 = hashlib.sha256(); n_new = 0
    with open(live, 'rb') as f:
        h2.update(f.read(size))
        for _ in f: pass
    n_new = sum(1 for _ in open(live, 'rb'))
    assert h2.hexdigest() == pre, 'PREFIX CHANGED'
    assert n_new == n_old + len(rows), (n_new, n_old, len(rows))
    print(json.dumps({'catalogue': cat, 'before': n_old, 'appended': len(rows),
                      'skipped_dup_under_lock': skipped, 'admitted_allowed_twins': len(allowed_used),
                      'unused_allow_twins': sorted(allowed - allowed_used),
                      'after': n_new, 'backup': bak}))
