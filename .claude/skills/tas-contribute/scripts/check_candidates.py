#!/usr/bin/env python3
"""Check a vendor-pull candidate batch before it may reach a live catalogue.

    python3 check_candidates.py --catalogue resistors.ndjson DIR/candidates.ndjson [--tas-root TAS]

Per row: ingest gate (identity, citation, units, schema), Blade Runner, denylist,
already-in-live-catalogue. Then the gate's batch rules over the rows that passed.
Writes DIR/accepted.ndjson, DIR/rejected.ndjson (row + reasons) and prints a summary.
Exit 0 only when the batch rules pass; read-only on data/.
"""
import argparse, collections, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import find_tas_root, pop_tas_root_arg, setup_imports  # noqa: E402
CODE = str(find_tas_root(pop_tas_root_arg()))   # the TAS checkout (code + schemas)
TAS = os.environ.get('TAS_ROOT', CODE)   # data root; overridable for tests only
setup_imports(find_tas_root(CODE))
from ingest_gate import (IngestGate, unwrap, identity_of, CATALOGUES,  # noqa: E402
                         KnownSpellings, manufacturer_key, known_label)
import tas_validator  # noqa: E402


def live_identities(cat):
    """Every (partNumber/reference, manufacturer) in the live catalogue and the quarantine.
    Identity is (part number, manufacturer): the same number from another manufacturer is
    a second source. Within one manufacturer only an exact spelling is a duplicate; one
    that differs only by '-', '_', '/' or spaces is held as a spelling twin (Eaton
    10BJ6-0A is 6.0 V, 10BJ60A is 60 V)."""
    return (KnownSpellings().add_file(f'{TAS}/data/{cat}', 'live')
            .add_file(f'{TAS}/data/quarantine.ndjson', 'quarantined'))


def denylist():
    """The fabricated-parts denylist as a KnownSpellings. An entry names an id and, when
    it carries one, a 'manufacturer'; an entry without one (every entry as of 2026-10-18)
    is compared against every manufacturer, since nothing says whose part was fabricated.
    An exact spelling is denylisted; a punctuation-only variant is held as a twin. A line
    that is not a JSON object with a string 'id' raises -- a denylist that silently loses
    entries readmits the fabrications it exists to stop."""
    out = KnownSpellings()
    path = f'{TAS}/data/fabricated_denylist.ndjson'
    with open(path, encoding='utf-8') as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            e = json.loads(line)
            if not isinstance(e, dict) or not isinstance(e.get('id'), str) or not e['id'].strip():
                raise ValueError(f'{path}:{n}: denylist entry without a string id: {line[:120]!r}')
            out.add(e['id'], e.get('manufacturer'), 'denylisted')
    return out


def unsourced_status(body):
    """True when the record states manufacturerInfo.status and no provenance entry claims it.
    A 2026-10-18 blind test found `status: "production"` on 17 of 17 records from one agent
    whose datasheets print no lifecycle at all."""
    mi = body.get('manufacturerInfo') or {}
    if not mi.get('status'):
        return False
    prov = (mi.get('datasheetInfo') or {}).get('provenance') or []
    return not any(isinstance(e, dict) and any(isinstance(f, str) and f.split('.')[-1] == 'status'
                                               for f in e.get('fields') or [])
                   for e in prov)


def inverted_ranges(node, path=''):
    """Paths of {minimum, maximum} objects whose minimum exceeds the maximum. A P-channel
    V_GS(th) printed "-1 … -3" and copied column by column reads backwards to every
    numeric consumer (2026-10-18 blind test: 4 records, the gate passed all of them)."""
    out = []
    if isinstance(node, dict):
        lo, hi = node.get('minimum'), node.get('maximum')
        if (isinstance(lo, (int, float)) and isinstance(hi, (int, float))
                and not isinstance(lo, bool) and not isinstance(hi, bool) and lo > hi):
            out.append(path or '.')
        for k, v in node.items():
            out += inverted_ranges(v, f'{path}.{k}' if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += inverted_ranges(v, f'{path}.{i}')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--catalogue', required=True)
    ap.add_argument('file')
    a = ap.parse_args()
    d = os.path.dirname(os.path.abspath(a.file))
    live, deny = live_identities(a.catalogue), denylist()
    gate = IngestGate(a.catalogue)
    disc = CATALOGUES[a.catalogue].disc
    acc, rej = [], []
    why = collections.Counter()
    seen = KnownSpellings()
    for n, line in enumerate(open(a.file), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError as e:
            rej.append({'line': n, 'reasons': [f'bad json: {e}']}); why['json'] += 1
            continue
        body = unwrap(rec, disc)
        ident = identity_of(body) if body else None
        mfr = (body.get('manufacturerInfo') or {}).get('name') if body else None
        reasons = []
        if ident and manufacturer_key(mfr) is None:
            reasons.append('no manufacturer name: identity is (part number, manufacturer)')
        elif ident:
            dv = deny.verdict(ident, mfr)
            if dv and dv[0] == 'duplicate':
                reasons.append('denylisted')
            elif dv:
                reasons.append('spelling twin of a denylisted id: differs from %s only by '
                               "'-', '_', '/' or spaces; admit only if the vendor prints both"
                               % ', '.join(known_label(x) for x in dv[1]))
            known = live.reason(ident, mfr)
            if known:
                reasons.append(known)
            else:
                variants = live.ordering_variants(ident, mfr)
                if variants:
                    reasons.append('ordering-code variant of %s: the same part in another '
                                   'packaging or finish; enrich or supersede that row instead '
                                   'of adding a second one' % ', '.join(known_label(v) for v in variants))
            inbatch = seen.verdict(ident, mfr)
            if inbatch and inbatch[0] == 'duplicate':
                reasons.append('duplicate inside batch')
            elif inbatch:
                reasons.append(seen.reason(ident, mfr))
            elif seen.ordering_variants(ident, mfr):
                reasons.append('ordering-code variant of another row in this batch')
        if body:
            for p in inverted_ranges(body):
                reasons.append(f'minimum > maximum at {p}: store the numerically smaller end as minimum')
        if body and unsourced_status(body):
            reasons.append('status without a source: no provenance entry lists manufacturerInfo.status '
                           'in its fields; a lifecycle status is a vendor statement, not a default')
        reasons += [str(r) for r in gate.offer(rec)]
        if not reasons:
            try:
                v = tas_validator.validate(rec)
                bad = [f for f in v.findings if str(f.severity).endswith('IMPOSSIBLE')]
                if bad:
                    reasons += ['Blade Runner IMPOSSIBLE: ' + f'{f.code} {f.message}' for f in bad]
            except Exception as e:
                reasons.append(f'Blade Runner error: {e}')
        if reasons:
            rej.append({'line': n, 'identity': ident, 'reasons': reasons, 'record': rec})
            for r in reasons:
                why[r.split(']')[0] + ']' if r.startswith('[') else r.split(':')[0][:60]] += 1
        else:
            acc.append(rec); seen.add(ident, mfr, 'accepted batch row')
    batch = [str(r) for r in gate.batch_refusals()]
    with open(f'{d}/accepted.ndjson', 'w') as f:
        for r in acc:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    with open(f'{d}/rejected.ndjson', 'w') as f:
        for r in rej:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print(json.dumps({'accepted': len(acc), 'rejected': len(rej),
                      'reject_reasons': why.most_common(20),
                      'batch_refusals': batch[:30], 'batch_refusal_count': len(batch)}, indent=1))
    sys.exit(1 if batch else 0)


if __name__ == '__main__':
    main()
