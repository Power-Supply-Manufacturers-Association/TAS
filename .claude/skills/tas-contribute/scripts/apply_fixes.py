#!/usr/bin/env python3
"""Apply a clean-up fix manifest to ONE live catalogue: flock, verified backup, stream rewrite, os.replace.

    python3 apply_fixes.py mosfets.ndjson FIXES.jsonl [--dry-run] [--tas-root TAS]

Fix lines (format: references/submission-format.md), rows matched by normalised identity + manufacturer name:
  op set       path (dotted, from the record root), old (must equal the live value), new, provenance (optional entry to append)
  op unset     path, old
  op quarantine  reason      -> row leaves the live file, appended to quarantine.ndjson with quarantineReason
  op delete    reason        -> fabricated row, removed outright
A set/unset whose record would gain an ingest-gate finding or a Blade Runner IMPOSSIBLE finding it did not
already have is refused and logged.
"""
import fcntl, hashlib, json, os, re, shutil, sys, time, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import find_tas_root, pop_tas_root_arg, setup_imports  # noqa: E402
CODE = str(find_tas_root(pop_tas_root_arg()))   # the TAS checkout (code + schemas)
TAS = os.environ.get('TAS_ROOT', CODE)   # data root; overridable for tests only
setup_imports(find_tas_root(CODE))
from ingest_gate import IngestGate, CATALOGUES, unwrap, identity_of  # noqa: E402
import tas_validator  # noqa: E402  Blade Runner


def impossible(rec):
    v = tas_validator.validate_json(json.dumps(rec))
    return {'BladeRunner ' + f.code for f in v.findings if str(f.severity).endswith('IMPOSSIBLE')}

norm = lambda s: re.sub(r'[\s\-_/]', '', s or '').upper()
key = lambda pn, mfr: (norm(pn), (mfr or '').strip().lower())
MISSING = object()


def walk(rec, path):
    parts = [int(p) if p.isdigit() else p for p in path.split('.')]
    cur = rec
    for p in parts[:-1]:
        cur = cur[p]
    return cur, parts[-1]


def get(rec, path):
    try:
        c, k = walk(rec, path)
        return c[k]
    except (KeyError, IndexError, TypeError):
        return MISSING


def rel(path):
    """field path relative to datasheetInfo, the live convention ('electrical.energyAbsorption')"""
    return path.split('datasheetInfo.', 1)[1] if 'datasheetInfo.' in path else path


def apply(rec, body, f):
    op = f['op']
    if op in ('quarantine', 'delete'):
        return op
    cur = get(rec, f['path'])
    if cur is MISSING and op == 'unset':
        return 'already absent'
    if cur is MISSING and 'old' in f and f['old'] is not None:
        return 'path missing'
    if 'old' in f and cur is not MISSING and cur != f['old']:
        return 'old value differs'
    if op == 'set':
        try:
            c, k = walk(rec, f['path'])
        except (KeyError, IndexError, TypeError):
            return 'path missing'
        c[k] = f['new']
    elif op == 'unset':
        c, k = walk(rec, f['path'])
        del c[k]
    else:
        return 'bad op'
    # the old entries' claim on this field is void now; the new entry (if any) carries it
    prov = body['manufacturerInfo'].setdefault('datasheetInfo', {}).setdefault('provenance', [])
    field = rel(f['path'])
    for e in prov:
        if 'fields' in e and any(rel(x) == field for x in e['fields']):
            e['fields'] = [x for x in e['fields'] if rel(x) != field]
            if not e['fields']:
                del e['fields']
    pv = f.get('provenance')
    if pv:
        pv = dict(pv)
        if 'fields' in pv:
            pv['fields'] = [rel(x) for x in pv['fields']]
        # same document, same verification -> one entry: union the fields instead of appending a twin
        same = next((e for e in prov if ident(e) == ident(pv)), None)
        if same is None:
            prov.append(pv)
        elif 'fields' in pv:
            same['fields'] = sorted(set(same.get('fields', [])) | set(pv['fields']))
    return None


IDENT_KEYS = ('source', 'sourceName', 'sourceUrl', 'retrievedDate', 'verification', 'verificationDate', 'verificationMethod')


def ident(e):
    return tuple(e.get(k) for k in IDENT_KEYS)


def main():
    cat, ff = sys.argv[1], sys.argv[2]; dry = '--dry-run' in sys.argv
    disc = CATALOGUES[cat].disc
    fixes = collections.defaultdict(list)
    for l in open(ff):
        if l.strip():
            f = json.loads(l)
            if f.get('catalogue', cat) == cat:
                fixes[key(f['pn'], f['mfr'])].append(f)
    live = f'{TAS}/data/{cat}'; qfile = f'{TAS}/data/quarantine.ndjson'
    # key() drops '-', '_', '/' and spaces, so different parts can share one (Eaton 10BJ6-0A vs 10BJ60A).
    # Where the live catalogue has more than one spelling under a key, a fix applies only to the row whose
    # identity matches its pn exactly (case-insensitive); one that matches no spelling exactly is refused.
    spellings = collections.defaultdict(set)
    with open(live, 'rb') as fh:
        for raw in fh:
            if raw.strip():
                b = unwrap(json.loads(raw), disc)
                if b:
                    k = key(identity_of(b), b.get('manufacturerInfo', {}).get('name'))
                    if k in fixes:
                        spellings[k].add((identity_of(b) or '').strip().upper())
    gate = IngestGate(cat)
    log = open(ff + f'.{cat}.applied.log', 'w'); st = collections.Counter(); used = set(); qrows = []
    with open(live + '.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        if not dry:
            os.makedirs(f'{TAS}/.backups', exist_ok=True)
            bak = f'{TAS}/.backups/{cat}.{time.strftime("%Y%m%dT%H%M%S")}.{time.time_ns() % 10**9:09d}'; shutil.copy2(live, bak)
            assert hashlib.sha256(open(bak, 'rb').read()).hexdigest() == hashlib.sha256(open(live, 'rb').read()).hexdigest()
        tmp = live + '.fixtmp'; n = 0; removed = 0
        with open(live, 'rb') as fh, open(tmp, 'wb') as o:
            for raw in fh:
                n += 1
                line = raw.decode('utf-8')
                if not line.strip(): o.write(raw); continue
                rec = json.loads(line); body = unwrap(rec, disc)
                k = key(identity_of(body), (body or {}).get('manufacturerInfo', {}).get('name')) if body else None
                fs = fixes.get(k) if body else None
                if fs and len(spellings[k]) > 1:
                    exact = (identity_of(body) or '').strip().upper()
                    fs = [f for f in fs if f['pn'].strip().upper() == exact]
                    for f in fixes[k]:
                        if f['pn'].strip().upper() not in spellings[k] and (f['pn'], 'ambiguous') not in used:
                            used.add((f['pn'], 'ambiguous')); st['refused_ambiguous_key'] += 1
                            log.write(json.dumps({'pn': f['pn'], 'result': 'ambiguous key', 'spellings': sorted(spellings[k])}) + '\n')
                if not fs: o.write(raw); continue
                before = json.dumps(rec, sort_keys=True)
                pre = {str(x) for x in gate.offer(json.loads(line))} | impossible(json.loads(line))
                gone = None
                for f in fs:
                    used.add(key(f['pn'], f['mfr']))
                    r = apply(rec, body, f)
                    if r in ('quarantine', 'delete'):
                        gone = (r, f); break
                    st[r or ('applied_' + f['op'])] += 1
                    if r: log.write(json.dumps({'pn': f['pn'], 'path': f.get('path'), 'result': r}) + '\n')
                if gone:
                    r, f = gone; removed += 1; st[r] += 1
                    if r == 'quarantine':
                        q = json.loads(line); q['quarantineReason'] = f['reason']; q['_quarantineSource'] = [cat]
                        qrows.append(json.dumps(q, ensure_ascii=False) + '\n')
                    log.write(json.dumps({'pn': f['pn'], 'result': r, 'reason': f.get('reason')}) + '\n')
                    continue
                if json.dumps(rec, sort_keys=True) == before: o.write(raw); continue
                new = [x for x in [str(y) for y in gate.offer(rec)] + sorted(impossible(rec)) if x not in pre]
                if new:
                    st['refused_by_gate'] += 1; o.write(raw)
                    log.write(json.dumps({'pn': identity_of(body), 'result': 'gate', 'why': new}) + '\n')
                    continue
                compact = '":' in line and '": ' not in line
                o.write((json.dumps(rec, ensure_ascii=False, separators=(',', ':') if compact else (', ', ': ')) + '\n').encode())
                st['rows_changed'] += 1
        st['unmatched_rows'] = len(set(fixes) - used)
        for k in set(fixes) - used:
            log.write(json.dumps({'pn': fixes[k][0]['pn'], 'result': 'unmatched'}) + '\n')
        assert sum(1 for _ in open(tmp, 'rb')) == n - removed, 'line count changed'
        if dry:
            os.remove(tmp)
        else:
            if qrows:
                with open(qfile + '.lock', 'w') as ql:
                    fcntl.flock(ql, fcntl.LOCK_EX)
                    qb = f'{TAS}/.backups/quarantine.ndjson.{time.strftime("%Y%m%dT%H%M%S")}.{time.time_ns() % 10**9:09d}'; shutil.copy2(qfile, qb)
                    with open(qfile, 'rb+') as q:
                        q.seek(0, 2)
                        if q.tell():
                            q.seek(-1, 2)
                            if q.read(1) != b'\n': sys.exit('quarantine lacks trailing newline')
                        q.write(''.join(qrows).encode()); q.flush(); os.fsync(q.fileno())
            os.replace(tmp, live)
    print(json.dumps({'catalogue': cat, 'dry_run': dry, 'removed': removed, **st}))


if __name__ == '__main__':
    main()
