#!/usr/bin/env python3
"""The two numbers that track the component database: QUALITY and COVERAGE.

    python3 scripts/db_metrics.py [--spec scripts/metrics_spec.json] [--no-history] [--limit N]
                                  [--workers 4]

QUALITY (0-100 %). Every live part gets a score; a catalogue's score is the mean over its
live parts plus its quarantined rows and rows that do not unwrap to a part (each scores 0);
the total is the mean over all of them (by part count). A part's score is a weighted
checklist:

  existence  page/datasheet proves the exact part exists
  traced     each present core field is covered by a source it was read from
  fields     core datasheet fields present (a field Blade Runner flags, or a known
             placeholder value, counts -1; floored at 0)
  curves     expected datasheet curves present (weight moves to the others when a
             type has no expected curves)
  url        the part cites a URL (manufacturerInfo.datasheetUrl or a provenance
             sourceUrl), scored by the catalogue's measured alive rate (rolling sample in
             staging/metrics/url_alive.json; unmeasured = 0)
  valid      schema-valid and no Blade Runner finding (IMPOSSIBLE 0, SUSPICIOUS 0.5)

Evidence credit (Alf, 2026-10-17): exact part + values read = 1.0; series datasheet
(seriesConfirmed) = 0.6; existence confirmed without values = existence 1.0 / traced 0;
unverified, inferred, disproven or citation-only = 0; a distributor source counts 0.8x.

The arithmetic is C++ (validator/metrics, Python module `tas_metrics`, linked against Blade
Runner; the same code builds for the browser with emscripten). This script only does what
C++ cannot: JSON-Schema (draft 2020-12) validation through scripts/ingest_gate.py, cached
per line by content hash so a nightly run re-validates only rows that changed, and the
report.

COVERAGE (0-100 %). Distinct live parts per catalogue divided by the estimated number of
in-scope parts the Datasheet Archive serves (current + obsolete; entries it lists but never
serves are excluded, Alf 2026-10-17), from staging/coverage/coverage_estimate.json. Both sides
key a part on (canonical manufacturer, normalised mpn), the canonical name coming from
scripts/manufacturer_aliases.json. Quarantined parts are not counted as covered.

Outputs docs/metrics/QUALITY.md (overwritten) and appends one line per run to
docs/metrics/history.ndjson.
"""
import argparse, concurrent.futures as cf, datetime, hashlib, json, multiprocessing, os, sys
TAS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TAS, 'scripts'))
sys.path.insert(0, os.path.join(TAS, 'validator', 'build'))
import tas_metrics  # noqa: E402  C++ scorer
from ingest_gate import CATALOGUES, PROTEUS, prune_empty, unwrap, validator_for  # noqa: E402

CACHE = os.path.join(TAS, 'staging', 'metrics', 'schema_cache')
KEY = 16  # bytes of blake2b per line


def schema_fingerprint():
    """Changes whenever any sibling schema or the gate's own pruning code changes."""
    h = hashlib.sha256()
    for repo in sorted(os.listdir(PROTEUS)):
        root = os.path.join(PROTEUS, repo, 'schemas')
        if not os.path.isdir(root):
            continue
        for dp, _, fs in sorted(os.walk(root)):
            for f in sorted(fs):
                if f.endswith('.json'):
                    p = os.path.join(dp, f)
                    h.update(p.encode()); h.update(open(p, 'rb').read())
    h.update(open(os.path.join(TAS, 'scripts', 'ingest_gate.py'), 'rb').read())
    return h.digest()[:KEY]


def load_cache(cat, fp):
    p = os.path.join(CACHE, cat + '.bin')
    if not os.path.exists(p):
        return {}
    b = open(p, 'rb').read()
    if b[:KEY] != fp:
        return {}
    return {b[i:i + KEY]: b[i + KEY] for i in range(KEY, len(b), KEY + 1)}


def save_cache(cat, fp, seen):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, cat + '.bin')
    with open(p + '.tmp', 'wb') as o:
        o.write(fp)
        for k, v in seen.items():
            o.write(k); o.write(bytes([v]))
    os.replace(p + '.tmp', p)


_W = {}


def _validate(job):
    """worker: schema verdicts (1 ok / 0 invalid) for a batch of raw lines"""
    cat, lines = job
    C = CATALOGUES[cat]
    out = []
    for raw in lines:
        try:
            got = unwrap(json.loads(raw), C.disc, want_kind=True)
        except ValueError:
            out.append(0); continue
        if got is None:
            out.append(1); continue  # not a part body: the scorer counts it as malformed
        body, kind = got
        v = _W.get((cat, kind))
        if v is None:
            v = _W[(cat, kind)] = validator_for(C, kind)
        out.append(0 if v is None or next(v.iter_errors(prune_empty(body)), None) is not None else 1)
    return out


def schema_mask(fh, cat, limit, pool, fp):
    """one byte per line of the open file: b'0' schema-invalid, b'1' valid or unchecked"""
    if cat not in CATALOGUES:
        n = 0
        for i, _ in enumerate(fh):
            if limit and i >= limit:
                break
            n += 1
        return b'1' * n, {'checked': False, 'misses': 0}
    cache = load_cache(cat, fp)
    mask = bytearray(); seen = {}; pend = []; pend_idx = []; futs = []

    def flush():
        if pend:
            futs.append((list(pend_idx), pool.apply_async(_validate, ((cat, list(pend)),))))
            pend.clear(); pend_idx.clear()
    for i, raw in enumerate(fh):
        if limit and i >= limit:
            break
        k = hashlib.blake2b(raw, digest_size=KEY).digest()
        hit = cache.get(k)
        if hit is None:
            mask.append(0x31); pend.append(raw); pend_idx.append((i, k))
            if len(pend) >= 500:
                flush()
        else:
            mask.append(0x31 if hit else 0x30); seen[k] = hit
    flush()
    misses = 0
    for idx, fut in futs:
        for (i, k), ok in zip(idx, fut.get()):
            mask[i] = 0x31 if ok else 0x30; seen[k] = ok; misses += 1
    save_cache(cat, fp, seen)
    return bytes(mask), {'checked': True, 'misses': misses}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--spec', default=os.path.join(TAS, 'scripts', 'metrics_spec.json'))
    ap.add_argument('--no-history', action='store_true')
    ap.add_argument('--limit', type=int, default=0, help='rows per catalogue (testing)')
    ap.add_argument('--workers', type=int, default=4, help='schema-validation processes')
    a = ap.parse_args()
    spec_text = open(a.spec).read(); spec = json.loads(spec_text)
    # one alias table for both sides of coverage: the DSA estimate and our distinct-part count
    aliases_f = os.path.join(TAS, 'scripts', 'manufacturer_aliases.json')
    scorer = (tas_metrics.Scorer(spec_text, open(aliases_f).read()) if os.path.exists(aliases_f)
              else tas_metrics.Scorer(spec_text))
    W = spec['weights']
    alive_f = os.path.join(TAS, 'staging', 'metrics', 'url_alive.json')
    alive = json.load(open(alive_f)) if os.path.exists(alive_f) else {}
    cov_f = os.path.join(TAS, 'staging', 'coverage', 'coverage_estimate.json')
    cov = json.load(open(cov_f)) if os.path.exists(cov_f) else None
    fp = schema_fingerprint()

    res, handles = {}, []
    # the process pool forks before the thread pool starts any thread
    with multiprocessing.get_context('fork').Pool(a.workers) as pool, cf.ThreadPoolExecutor(4) as tp:
        futs = {}
        for name, cs in spec['catalogues'].items():
            cat = cs.get('file') or name
            path = os.path.join(TAS, 'data', cat)
            if not os.path.exists(path):
                continue
            # one open file for both passes: an os.replace() by a concurrent edit swaps the
            # inode under the path, and an append lands past the mask, so the C++ pass sees
            # exactly the lines the schema pass saw
            fh = open(path, 'rb'); handles.append(fh)
            mask, sinfo = schema_mask(fh, cat, a.limit, pool, fp)
            rate = (alive.get(cat) or {}).get('alive_rate', 0.0)
            futs[cat] = (tp.submit(scorer.score_file, f'/proc/self/fd/{fh.fileno()}', name, rate, mask), sinfo)
            print(f'{cat}: {len(mask):,} lines, schema re-validated {sinfo["misses"]:,}', flush=True)
        for cat, (fut, sinfo) in futs.items():
            t = fut.result()
            res[cat] = dict(t, schema=sinfo, url_measured=cat in alive,
                            checks={k: t['check_sum'][k] / t['check_n'][k] for k in W if t['check_n'].get(k)})
    for fh in handles:
        fh.close()

    qc = tas_metrics.quarantine_counts(os.path.join(TAS, 'data', 'quarantine.ndjson'))
    rows = []
    T = Q = 0.0
    for cat, r in sorted(res.items()):
        r['quarantine'] = qc.get(cat, 0)
        denom = r['live'] + r['malformed'] + r['quarantine']
        r['quality'] = r['sum'] / denom if denom else 0.0
        T += r['sum']; Q += denom
        uni = None
        if cov:
            uni = (cov.get('universe_in_scope', {}).get('by_catalogue', {}).get(cat.replace('.ndjson', '')) or {}).get('est')
        r['universe'] = uni
        r['coverage'] = (r['distinct'] / uni) if uni else None
        rows.append((cat, r))
    total_q = T / Q if Q else 0.0
    total_uni = cov and cov.get('universe_in_scope', {}).get('est')
    total_distinct = sum(r['distinct'] for _, r in rows)
    total_cov = total_distinct / total_uni if total_uni else None

    today = datetime.date.today().isoformat()
    out = os.path.join(TAS, 'docs', 'metrics'); os.makedirs(out, exist_ok=True)
    pct = lambda x: '—' if x is None else f'{100 * x:.1f} %'
    md = [f'# Database metrics ({today})', '',
          f'**Quality: {pct(total_q)}**  ·  **Coverage: {pct(total_cov)}**', '',
          'Quality = weighted checklist per part, quarantined rows count 0; coverage = distinct live parts / '
          'estimated in-scope Datasheet Archive parts. Method: `scripts/db_metrics.py` docstring and '
          '`validator/metrics/`; field and curve lists: `scripts/metrics_spec.json`.', '',
          '| Catalogue | Live | Quarantine | Quality | Existence | Traced | Fields | Curves | URL | Valid | Coverage |',
          '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for cat, r in rows:
        c = r['checks']
        md.append(f"| {cat.replace('.ndjson', '')} | {r['live']:,} | {r['quarantine']:,} | {pct(r['quality'])} | "
                  + ' | '.join(pct(c.get(k)) for k in ('existence', 'traced', 'fields', 'curves'))
                  + f" | {pct(c.get('url')) if r['url_measured'] else 'not measured'} | {pct(c.get('valid'))} | {pct(r['coverage'])} |")
    md.append(f"| **total** | {sum(r['live'] for _, r in rows):,} | {sum(r['quarantine'] for _, r in rows):,} | "
              f"**{pct(total_q)}** | | | | | | | **{pct(total_cov)}** |")
    md += ['', '## Defects behind the valid column', '',
           '| Catalogue | Schema-invalid | Not a part body | Blade Runner could not evaluate | Most frequent Blade Runner findings |',
           '|---|---:|---:|---:|---|']
    for cat, r in rows:
        top = sorted(r['blade_code_counts'].items(), key=lambda kv: -kv[1])[:4]
        sch = f"{r['schema_failed']:,}" if r['schema']['checked'] else 'not checked'
        md.append(f"| {cat.replace('.ndjson', '')} | {sch} | {r['malformed']:,} | {r['blade_errors']:,} | "
                  + ', '.join(f'{k} {v:,}' for k, v in top) + ' |')
    if qc.get(''):
        md += ['', f"{qc['']:,} quarantined rows carry no `_quarantineSource` and are not charged to any catalogue."]
    if a.limit:
        md.insert(1, f'\n> TEST RUN: first {a.limit} rows per catalogue only.\n')
    open(os.path.join(out, 'QUALITY.md' if not a.limit else 'QUALITY.test.md'), 'w').write('\n'.join(md) + '\n')
    if not a.no_history and not a.limit:
        with open(os.path.join(out, 'history.ndjson'), 'a') as h:
            h.write(json.dumps({'date': today, 'quality': round(total_q, 5), 'coverage': total_cov and round(total_cov, 5),
                                'spec_version': spec.get('version'),
                                'by_catalogue': {cat.replace('.ndjson', ''): {
                                    'live': r['live'], 'quarantine': r['quarantine'], 'malformed': r['malformed'],
                                    'schema_failed': r['schema_failed'],
                                    'quality': round(r['quality'], 5),
                                    'coverage': r['coverage'] and round(r['coverage'], 5),
                                    'checks': {k: round(v, 5) for k, v in r['checks'].items()}} for cat, r in rows}}) + '\n')
    print('\n'.join(md))


if __name__ == '__main__':
    main()
