#!/usr/bin/env python3
"""Does a part number exist? One web search, judged by Sonnet at low effort.

For each (manufacturer, part number) the model searches the web and answers
from the results only. The verdict is deliberately strict, because the parts
this tool exists to catch came FROM the places a loose judge would accept:

  * "exists" needs the EXACT part-number string on the manufacturer's own site
    (or a datasheet it hosts) or on an authorised distributor's product page.
  * Datasheet-archive and aggregator sites, broker/stock-listing sites that
    generate a page for any searched string, forums and search-result pages are
    not evidence -- fabricated rows in this database were scraped from exactly
    those.
  * A page about a neighbouring part (same base, another suffix) is a near-miss
    and does not confirm the part. "MAX17501" existing says nothing about
    "MAX17501DFN".

It is an ORACLE, not a verdict on the database. Calibrate it first: score the tool against parts already proven fake and proven real (including
real-but-obsolete ones), and nothing should be deleted on its word until that
calibration is known. See `score` below.

Usage:
  part_existence.py check  --in parts.jsonl --out results.jsonl [--workers 8]
  part_existence.py sample --catalogue data/x.ndjson --n 500 --seed 1 --out parts.jsonl
  part_existence.py score  --out results.jsonl   (confusion table; input lines need a 'truth' key)

Calibrate by running `check` on a labelled set (truth: fake / real / obsolete / nonsense)
and then `score` on its results.

Input lines: {"manufacturer": "...", "reference": "..."}; extra keys pass through.
Results are appended and the run resumes where it stopped.

Needs ANTHROPIC_API_KEY, or the key in ~/.config/anthropic/api_key (chmod 600).
Web search is billed per search; the run prints cost.
"""
import argparse
import concurrent.futures as cf
import json
import os
import random
import sys
import threading
import time

MODEL = 'claude-sonnet-5'
MAX_SEARCHES = 2
PRICE_IN, PRICE_OUT = 2.0 / 1e6, 10.0 / 1e6      # USD per token, Sonnet 5
PRICE_SEARCH = 10.0 / 1000                         # USD per web search

SYSTEM = """You check whether an electronic component part number exists as a real product
(orderable now, or obsolete / end-of-life / NRND -- all of those count as existing).

Search the web, then answer ONLY from what the search results show. Do not use memory.

Answer "exists" only if a result shows the EXACT part-number string you were given
(case-insensitive, nothing added or removed) on one of:
  - the manufacturer's own website, or a datasheet the manufacturer hosts;
  - an authorised distributor's product page: Digi-Key, Mouser, Arrow, Avnet,
    Newark / Farnell / element14, RS Components, TTI, Future Electronics, Rutronik.

These are NOT evidence, whatever they show:
  - datasheet archives and aggregators (datasheetpdf, alldatasheet, datasheet4u,
    datasheetspdf, datasheetarchive, elcodis, chipfind and similar);
  - broker and stock-listing sites, which generate a page for any string searched;
  - forums, Q&A sites, search-result pages.

A result about a DIFFERENT part is a near-miss and does NOT confirm the part: the same
base number with another suffix, the base number alone, or a neighbouring number.

Answer "not_found" when no qualifying source shows the exact string.
Answer "ambiguous" only when a qualifying source shows something that may be the part
but you cannot tell (for example a truncated listing). Never guess."""

SCHEMA = {
    'type': 'object',
    'properties': {
        'verdict': {'type': 'string', 'enum': ['exists', 'not_found', 'ambiguous']},
        'evidence_url': {'type': ['string', 'null']},
        'evidence_source': {'type': 'string',
                            'enum': ['manufacturer', 'authorized_distributor', 'none']},
        'near_miss': {'type': ['string', 'null']},
        'note': {'type': 'string'},
    },
    'required': ['verdict', 'evidence_url', 'evidence_source', 'near_miss', 'note'],
    'additionalProperties': False,
}


def key(rec):
    return (rec['manufacturer'].strip().lower(), rec['reference'].strip().upper())


def check_one(client, rec):
    """Return the verdict dict for one part. Raises on anything unexpected."""
    t0 = time.time()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=SYSTEM,
        tools=[{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': MAX_SEARCHES}],
        output_config={'effort': 'low', 'format': {'type': 'json_schema', 'schema': SCHEMA}},
        messages=[{'role': 'user', 'content': 'Manufacturer: %s\nPart number: %s'
                   % (rec['manufacturer'], rec['reference'])}],
    )
    if msg.stop_reason == 'refusal':
        raise RuntimeError('refusal for %s' % rec['reference'])
    if msg.stop_reason == 'max_tokens':
        raise RuntimeError('hit max_tokens for %s' % rec['reference'])
    texts = [b.text for b in msg.content if b.type == 'text']
    if not texts:
        raise RuntimeError('no text block for %s (stop_reason %s)' % (rec['reference'], msg.stop_reason))
    out = json.loads(texts[-1])
    searches = sum(1 for b in msg.content if b.type == 'web_search_tool_result')
    cost = (msg.usage.input_tokens * PRICE_IN + msg.usage.output_tokens * PRICE_OUT
            + searches * PRICE_SEARCH)
    out.update(searches=searches, input_tokens=msg.usage.input_tokens,
               output_tokens=msg.usage.output_tokens, cost_usd=round(cost, 5),
               seconds=round(time.time() - t0, 1), model=MODEL,
               checked=time.strftime('%Y-%m-%d'))
    # an "exists" with no URL is not evidence of anything
    if out['verdict'] == 'exists' and not out['evidence_url']:
        out['verdict'] = 'ambiguous'
        out['note'] = ('downgraded: exists without an evidence URL. ' + out['note'])[:300]
    return out


def run_check(inp, outp, workers):
    import anthropic
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    kf = os.path.expanduser('~/.config/anthropic/api_key')
    if not api_key and os.path.exists(kf):
        if os.stat(kf).st_mode & 0o077:
            sys.exit('%s is readable by others; chmod 600 it first' % kf)
        api_key = open(kf).read().strip()
    if not api_key:
        sys.exit('no API key: set ANTHROPIC_API_KEY or put it in %s (chmod 600)' % kf)
    client = anthropic.Anthropic(api_key=api_key, max_retries=6)
    todo = [json.loads(l) for l in open(inp, encoding='utf-8') if l.strip()]
    done = set()
    if os.path.exists(outp):
        for l in open(outp, encoding='utf-8'):
            if l.strip():
                done.add(key(json.loads(l)))
    todo = [r for r in todo if key(r) not in done]
    print('%d to check, %d already done' % (len(todo), len(done)), flush=True)
    lock = threading.Lock()
    spent = [0.0]
    failures = 0
    with open(outp, 'a', encoding='utf-8') as fh, cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(check_one, client, r): r for r in todo}
        for i, f in enumerate(cf.as_completed(futs), 1):
            r = futs[f]
            try:
                v = f.result()
            except Exception as e:                     # recorded, never swallowed
                failures += 1
                v = {'verdict': 'error', 'error': str(e)[:300]}
            with lock:
                fh.write(json.dumps({**r, **v}) + '\n'); fh.flush()
                spent[0] += v.get('cost_usd', 0.0)
            if i % 25 == 0 or i == len(todo):
                print('%d/%d  spent $%.2f  errors %d' % (i, len(todo), spent[0], failures), flush=True)
    if failures:
        print('WARNING: %d checks failed; they are recorded with verdict "error"' % failures)


def sample(catalogue, n, seed, outp):
    rng = random.Random(seed)
    pool = []
    for l in open(catalogue, encoding='utf-8'):
        l = l.strip()
        if not l:
            continue
        st = [json.loads(l)]
        while st:
            o = st.pop()
            if isinstance(o, dict):
                mi = o.get('manufacturerInfo')
                if isinstance(mi, dict) and mi.get('reference') and mi.get('name'):
                    pool.append({'manufacturer': mi['name'], 'reference': mi['reference'],
                                 'catalogue': os.path.basename(catalogue)})
                    break
                st.extend(v for v in o.values() if isinstance(v, (dict, list)))
            elif isinstance(o, list):
                st.extend(v for v in o if isinstance(v, (dict, list)))
    picks = rng.sample(pool, min(n, len(pool)))
    with open(outp, 'w', encoding='utf-8') as fh:
        for p in picks:
            fh.write(json.dumps(p) + '\n')
    print('sampled %d of %d rows (seed %d) -> %s' % (len(picks), len(pool), seed, outp))


def score(outp):
    """Confusion table for a results file whose input lines carried a 'truth' key."""
    import collections
    tab = collections.Counter()
    cost = 0.0
    wrong = []
    for l in open(outp, encoding='utf-8'):
        r = json.loads(l)
        tab[(r.get('truth'), r['verdict'])] += 1
        cost += r.get('cost_usd', 0.0)
        if (r.get('truth') == 'fake' and r['verdict'] == 'exists') or \
           (r.get('truth') in ('real', 'obsolete') and r['verdict'] == 'not_found'):
            wrong.append((r['truth'], r['manufacturer'], r['reference'], r['verdict'], r.get('evidence_url')))
    truths = sorted({t for t, _ in tab})
    print('%-10s %8s %10s %10s %7s' % ('truth', 'exists', 'not_found', 'ambiguous', 'error'))
    for t in truths:
        print('%-10s %8d %10d %10d %7d' % (t, tab[(t, 'exists')], tab[(t, 'not_found')],
                                           tab[(t, 'ambiguous')], tab[(t, 'error')]))
    n = sum(tab.values())
    print('\n%d checks, $%.2f total, $%.4f per part' % (n, cost, cost / max(n, 1)))
    print('\nDISAGREEMENTS (fake called exists, or real called not_found):')
    for w in wrong:
        print('  ', w)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check'); c.add_argument('--in', dest='inp', required=True)
    c.add_argument('--out', required=True); c.add_argument('--workers', type=int, default=8)
    s = sub.add_parser('sample'); s.add_argument('--catalogue', required=True)
    s.add_argument('--n', type=int, required=True); s.add_argument('--seed', type=int, required=True)
    s.add_argument('--out', required=True)
    k = sub.add_parser('score'); k.add_argument('--out', required=True)
    a = ap.parse_args()
    if a.cmd == 'check':
        run_check(a.inp, a.out, a.workers)
    elif a.cmd == 'sample':
        sample(a.catalogue, a.n, a.seed, a.out)
    else:
        score(a.out)
