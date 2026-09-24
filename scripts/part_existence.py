#!/usr/bin/env python3
"""Does a catalogue part number exist?  Tier-1 check from FREE public sources only.

Nothing here costs money: no API keys, no paid search, no paid services.  Two free
sources are consulted, in this order:

  1a. Vendor catalogue indexes (bulk).  One download (a vendor's product sitemap or
      catalogue listing) covers a whole vendor.  An index is used only after it has
      passed two controls, recorded by `index`: a real-but-OBSOLETE part of that
      vendor must be listed, and a nonsense string must not be.  An index that lists
      only the active catalogue fails the first control and is not used at all.
      Matching is exact (case and whitespace aside): an index of base products does
      not confirm "base + suffix", because invented suffixes on real stems are
      exactly what the fabricated rows looked like.

  1b. The Datasheet Archive (datasheetarchive.com), one polite request per part.
      Only two structured parts of the result page are read: the datasheet table
      (rows carrying data-mfr / data-mfrpartnumber) and the manufacturer + part
      headers of the price-and-stock block.  The free-text "context search" abstracts
      and descriptions are ignored: they echo the query and cross-reference other
      manufacturers' parts.  The stock block also lists fuzzy matches (LT8325 brings
      up a cable "LT-832+501"); the strict match below discards those.  A row counts when its part number is the exact
      reference, or a direct order-code extension of it (reference + "-LF",
      reference + "EDDB#PBF", ...; see extension_ok for the exact grammar: a grade letter
      or a further digit never confirms the stem, so "GBU6" is not confirmed by "GBU6J"
      nor "LT832" by "LT8325"), AND the row is attributed to the catalogue manufacturer
      (or a company it absorbed).
      Absence there is NOT proof: new parts can be missing.

A part is "real" only on one of those two conclusive results; everything else is
"unresolved", which is not the same as fake.  Tier 1 never concludes that a part is
fake.  Unresolved parts form the queue for a slower, manual second tier.

Rate limit for the archive: at most one request every --delay seconds (>= 2), a long
back-off on the first 403/429 or unexpected page, and a hard stop on the second.

Subcommands (every file lives under --work):
  extract  --data DIR                 stream the catalogues -> parts.jsonl (one row per record)
  sample   --n 500 --seed S           stratified random sample -> sample.jsonl
  cohort   [--limit N]                suspect rows (no datasheetUrl, sourceUnreachable,
                                      inferredNotVerified, aggregator provenance) -> cohort.jsonl
  index    --name N --manufacturer M (--url U ... | --file F) --regex R
           --obsolete P ... --nonsense P ...      build a vendor index and run its controls
  controls                            run the archive's own controls (obsolete present, nonsense absent)
  check    --in F [--out results.jsonl]           tier 1 on every line of F (resumable)
  rejudge  --results F                recompute archive verdicts from the page cache (no requests)
  stress   --results F                invented-suffix / truncation / wrong-maker mutations on cached pages
  score    --results F                confusion table for a labelled set ('truth' key)
  report                              report.md from calibration + sample results
"""
import argparse
import collections
import gzip
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse

UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/151.0.0.0 Safari/537.36')
DSA_URL = 'https://www.datasheetarchive.com/search?q=%s'
SKIP_CATALOGUES = {'circuits', 'converters', 'fabricated_denylist', 'quarantine'}
AGGREGATOR = re.compile(
    r'alldatasheet|datasheet4u|datasheetspdf|datasheetpdf|datasheetarchive|elcodis|chipfind|'
    r'octopart|findchips|icsource|netcomponents|oemstrade|datasheetq|kynix|utmel|jotrin|hqew|'
    r'szlcsc|lcsc\.com|win-source|chipdip|datasheet\.live|datasheets360|ic-components|'
    r'componentsearchengine|snapeda|ultralibrarian', re.I)

# Companies whose datasheets legitimately carry another name: predecessors and
# acquisitions.  Keyed by a word that identifies the catalogue manufacturer.
ALIASES = {
    'onsemi': ['onsemi', 'on semiconductor', 'fairchild', 'sanyo', 'motorola', 'catalyst semiconductor'],
    'on semiconductor': ['onsemi', 'on semiconductor', 'fairchild', 'sanyo', 'motorola'],
    'fairchild': ['onsemi', 'on semiconductor', 'fairchild'],
    'analog devices': ['analog devices', 'linear technology', 'maxim', 'hittite', 'dallas semiconductor'],
    'maxim': ['maxim', 'dallas semiconductor', 'analog devices'],
    'linear technology': ['linear technology', 'analog devices'],
    'infineon': ['infineon', 'international rectifier', 'siemens', 'eupec', 'cypress', 'gan systems'],
    'vishay': ['vishay', 'general semiconductor', 'siliconix', 'dale', 'international rectifier',
               'bc components', 'beyschlag', 'draloric', 'sprague', 'telefunken', 'sfernice', 'roederstein'],
    'nexperia': ['nexperia', 'nxp', 'philips'],
    'nxp': ['nxp', 'philips', 'freescale', 'motorola'],
    'texas instruments': ['texas instruments', 'national semiconductor', 'burr-brown', 'burr brown', 'unitrode'],
    'renesas': ['renesas', 'intersil', 'hitachi', 'nec', 'idt', 'integrated device technology', 'dialog'],
    'intersil': ['intersil', 'renesas', 'harris'],
    'microchip': ['microchip', 'microsemi', 'atmel', 'micrel', 'supertex', 'smsc', 'advanced power technology'],
    'microsemi': ['microsemi', 'microchip', 'advanced power technology'],
    'littelfuse': ['littelfuse', 'ixys', 'teccor'],
    'ixys': ['ixys', 'littelfuse'],
    'stmicroelectronics': ['stmicroelectronics', 'st microelectronics', 'sgs-thomson', 'sgs thomson'],
    'tdk': ['tdk', 'epcos', 'lambda'],
    'epcos': ['epcos', 'tdk'],
    'kemet': ['kemet', 'tokin', 'arcotronics', 'evox rifa', 'yageo'],
    'yageo': ['yageo', 'phycomp', 'vitrohm', 'kemet', 'pulse'],
    'murata': ['murata'],
    'panasonic': ['panasonic', 'matsushita'],
    'eaton': ['eaton', 'cooper', 'bussmann', 'coiltronics'],
    'te connectivity': ['te connectivity', 'tyco', 'amp', 'raychem', 'potter', 'cii technologies',
                        'alcoswitch', 'corcom', 'schrack', 'axicom', 'kilovac', 'deutsch',
                        'measurement specialties', 'agastat', 'buchanan', 'holsworthy', 'neohm'],
    'amphenol': ['amphenol', 'fci'],
    'monolithic power': ['monolithic power', 'mps'],
    'mps': ['monolithic power', 'mps'],
    'power integrations': ['power integrations'],
    'wurth': ['wurth', 'würth'],
    'wolfspeed': ['wolfspeed', 'cree'],
    'rohm': ['rohm', 'lapis', 'sicrystal'],
    'toshiba': ['toshiba'],
    'mitsubishi': ['mitsubishi', 'powerex'],
    'powerex': ['powerex', 'mitsubishi'],
    'sanken': ['sanken', 'allegro'],
    'kyocera': ['kyocera', 'avx'],
    'avx': ['avx', 'kyocera'],
    'skyworks': ['skyworks', 'silicon labs', 'silicon laboratories'],
    'silicon labs': ['silicon labs', 'silicon laboratories', 'skyworks'],
    'alpha and omega': ['alpha & omega', 'alpha and omega', 'aos'],
    'diodes inc': ['diodes incorporated', 'diodes inc', 'zetex'],
    'micro commercial': ['micro commercial'],
    'ween': ['ween', 'nxp'],
    'samsung': ['samsung'],
    'seiko epson': ['epson'],
    'epson': ['epson'],
    'sei stackpole': ['stackpole'],
    'stackpole': ['stackpole'],
    'koa': ['koa'],
    'bourns': ['bourns'],
    'pulse': ['pulse'],
    'hirose': ['hirose'],
    'weidm': ['weidm'],
    'txc': ['txc'],
}
# first words too generic to identify a company on their own
GENERIC_WORDS = {'micro', 'diodes', 'general', 'international', 'power', 'advanced', 'the',
                 'electronic', 'electronics', 'semiconductor', 'technology', 'vishay/dale'}


def fold(s):
    s = unicodedata.normalize('NFKD', s or '')
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


def aliases_for(name):
    n = fold(name)
    out = set()
    for k, v in ALIASES.items():
        if re.search(r'\b' + re.escape(fold(k)) + r'\b', n):
            out.update(fold(a) for a in v)
    words = re.findall(r'[a-z0-9&]+', n)
    if words and words[0] not in GENERIC_WORDS and len(words[0]) >= 2:
        out.add(words[0])
    elif words:
        out.add(' '.join(words[:3]))
    return out


def mfr_ok(catalogue_name, source_name):
    if not catalogue_name or not source_name:
        return False
    s = fold(source_name)
    return any(re.search(r'(?<![a-z0-9])' + re.escape(a) + r'(?![a-z0-9])', s)
               for a in aliases_for(catalogue_name))


def norm(pn):
    return re.sub(r'\s+', '', (pn or '').upper())


def extension_ok(q, found):
    """'exact' or 'extension' if `found` is `q` or a direct order-code extension of it.

    Calibrated by truncating real parts (GBU6J -> GBU6, P6KE20A -> P6KE20, RJP63K2 -> RJP63K):
    a grade letter or a further digit changes the part, so it must not confirm the stem.
      * separator start (-LF, #PBF, /TR, +T): accepted when the reference has >= 5 characters;
      * digit -> letter boundary: accepted for a >= 2-character extension on a >= 6-character
        reference (LT8330 + EDDB#PBF), or a single packaging letter on a >= 10-character
        reference (GRM1555C1H221GA01 + D);
      * letter -> letter, letter -> digit and digit -> digit continuations: rejected.  The
        stress test showed why letter -> letter cannot be allowed: TI package codes nest
        (TPS25750 + DR is not an order code, yet TPS25750DRJKR is), which is exactly the
        shape of an invented package suffix;
      * an evaluation board or kit (EVM, EVAL, EVK, KIT, DEMO in the extension) never
        confirms the chip: UCC28910 + FB matched UCC28910FBEVM-526;
      * a separator plus 1-3 digits (TE's -N) or 1-2 letters plus 1-2 digits (value
        continuations such as C3 + V3): rejected wherever they occur.
    """
    q, f = norm(q), norm(found)
    if not q or not f:
        return None
    if f == q:
        return 'exact'
    if not f.startswith(q) or not q[-1].isalnum():
        return None
    ext = f[len(q):]
    if len(ext) > 12 or re.search(r'EVM|EVAL|EVK|KIT|DEMO', ext):
        return None
    # a short digit group after a separator is part of the number, not an order suffix
    # (TE 6-1419128-6 vs 6-1419128-2); so is a value continuation like C3 + V3 (BZX84J-C3V3)
    # (a comma group is Nexperia/NXP 12NC packaging, BAS16,215, and stays allowed)
    if re.fullmatch(r'[-/._][0-9]{1,3}', ext) or re.fullmatch(r'[A-Z]{1,2}[0-9]{1,2}', ext):
        return None
    if ext[0] in '-/#+,(._':
        return 'extension' if len(q) >= 5 else None
    if q[-1].isdigit() and ext[0].isalpha():
        if len(ext) >= 2 and len(q) >= 6:
            return 'extension'
        if len(ext) == 1 and len(q) >= 10:
            return 'extension'
    return None


# --------------------------------------------------------------------------- extract

def _find_mi(o, need_name):
    q = [o]
    while q:
        x = q.pop(0)
        if isinstance(x, dict):
            m = x.get('manufacturerInfo')
            if isinstance(m, dict):
                if need_name and m.get('reference') and m.get('name'):
                    return m
                if not need_name:
                    pn = ((m.get('datasheetInfo') or {}).get('part') or {}).get('partNumber') \
                        or m.get('reference')
                    if pn:
                        m = dict(m)
                        m['reference'] = pn
                        m['name'] = m.get('name') or ''
                        return m
            q.extend(v for v in x.values() if isinstance(v, (dict, list)))
        elif isinstance(x, list):
            q.extend(v for v in x if isinstance(v, (dict, list)))
    return None


def _extract_one(path):
    cat = os.path.basename(path)[:-len('.ndjson')]
    out = []
    with open(path, 'rb') as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            o = json.loads(line)
            m = _find_mi(o, True) or _find_mi(o, False)
            if not m:
                continue
            flags = set()
            if not m.get('datasheetUrl'):
                flags.add('noDatasheetUrl')
            elif AGGREGATOR.search(str(m['datasheetUrl'])):
                flags.add('aggregator')
            prov = (m.get('datasheetInfo') or {}).get('provenance') or []
            for p in prov if isinstance(prov, list) else []:
                if not isinstance(p, dict):
                    continue
                v = p.get('verification')
                if v == 'sourceUnreachable':
                    flags.add('sourceUnreachable')
                if v == 'inferredNotVerified' and p.get('source') != 'derived':
                    flags.add('inferredNotVerified')
                if p.get('source') == 'scrape' or AGGREGATOR.search(
                        '%s %s' % (p.get('sourceUrl', ''), p.get('sourceName', ''))):
                    flags.add('aggregator')
            out.append({'catalogue': cat, 'line': i, 'manufacturer': m['name'],
                        'reference': m['reference'], 'flags': sorted(flags)})
    return path, out


def extract(data_dir, work):
    from concurrent.futures import ProcessPoolExecutor
    files = sorted(os.path.join(data_dir, f) for f in os.listdir(data_dir)
                   if f.endswith('.ndjson') and f[:-7] not in SKIP_CATALOGUES)
    with ProcessPoolExecutor(8) as ex, open(os.path.join(work, 'parts.jsonl'), 'w') as fh:
        for p, out in ex.map(_extract_one, files):
            for r in out:
                fh.write(json.dumps(r) + '\n')
            print('%s %d rows' % (os.path.basename(p), len(out)), flush=True)


def _by_catalogue(work):
    by = collections.defaultdict(list)
    for line in open(os.path.join(work, 'parts.jsonl')):
        r = json.loads(line)
        by[r['catalogue']].append(r)
    return by


def sample(work, n, seed):
    by = _by_catalogue(work)
    with open(os.path.join(work, 'sample.jsonl'), 'w') as fh:
        for c in sorted(by):
            rows = sorted(by[c], key=lambda r: r['line'])
            rng = random.Random('%s:%s' % (seed, c))
            pick = rows if len(rows) <= n else rng.sample(rows, n)
            for r in pick:
                fh.write(json.dumps({**r, 'stratum': 'sample', 'population': len(rows)}) + '\n')
            print('%s: %d of %d rows (seed %s)' % (c, len(pick), len(rows), seed))


def cohort(work, limit, seed):
    """Suspect rows not already in the sample, one per (catalogue, manufacturer, reference),
    smallest catalogues first, each catalogue shuffled with a fixed seed."""
    done = set()
    sp = os.path.join(work, 'sample.jsonl')
    if os.path.exists(sp):
        for line in open(sp):
            r = json.loads(line)
            done.add((r['catalogue'], r['manufacturer'], r['reference']))
    by = _by_catalogue(work)
    n = 0
    with open(os.path.join(work, 'cohort.jsonl'), 'w') as fh:
        for c in sorted(by, key=lambda c: len(by[c])):
            rows = [r for r in by[c] if r['flags']]
            random.Random('%s:%s' % (seed, c)).shuffle(rows)
            k = 0
            for r in rows:
                key = (r['catalogue'], r['manufacturer'], r['reference'])
                if key in done:
                    continue
                done.add(key)
                fh.write(json.dumps({**r, 'stratum': 'suspect'}) + '\n')
                k += 1
                n += 1
                if limit and k >= limit:
                    break
            print('%s: %d suspect rows queued' % (c, k))
    print('%d suspect rows in cohort.jsonl' % n)


# --------------------------------------------------------------------------- 1a: indexes

def curl(url, out=None, timeout=120):
    cmd = ['curl', '-s', '--compressed', '-m', str(timeout), '-A', UA,
           '-H', 'Accept-Language: en-US,en;q=0.9', '-w', '%{http_code}']
    if out:
        cmd += ['-o', out]
    r = subprocess.run(cmd + [url], capture_output=True)
    if out:
        return r.stdout.decode()[-3:], None
    body = r.stdout[:-3]
    return r.stdout[-3:].decode(), body


def index(work, name, manufacturer, urls, files, regex, obsolete, nonsense, source):
    parts = set()
    pat = re.compile(regex)
    texts = []
    for u in urls:
        code, body = curl(u)
        if code != '200':
            sys.exit('index %s: %s returned HTTP %s' % (name, u, code))
        if body[:2] == b'\x1f\x8b':
            body = gzip.decompress(body)
        texts.append(body.decode('utf-8', 'replace'))
        time.sleep(1)
    for f in files:
        texts.append(open(f, encoding='utf-8', errors='replace').read())
    for t in texts:
        for m in pat.finditer(t):
            parts.add(norm(urllib.parse.unquote(m.group(1))))
    parts.discard('')
    if not parts:
        sys.exit('index %s: the regex matched nothing' % name)
    ctl = {'obsolete_present': {p: norm(p) in parts for p in obsolete},
           'nonsense_absent': {p: norm(p) not in parts for p in nonsense}}
    passed = bool(obsolete) and bool(nonsense) and all(ctl['obsolete_present'].values()) \
        and all(ctl['nonsense_absent'].values())
    d = os.path.join(work, 'idx')
    os.makedirs(d, exist_ok=True)
    json.dump({'name': name, 'manufacturer': manufacturer, 'source': source or ' '.join(urls + files),
               'built': time.strftime('%Y-%m-%d'), 'size': len(parts), 'controls': ctl,
               'passed': passed, 'parts': sorted(parts)},
              open(os.path.join(d, name + '.json'), 'w'))
    print('index %s: %d parts; controls %s -> %s' % (name, len(parts), ctl,
                                                     'PASSED' if passed else 'FAILED (not used)'))


def load_indexes(work):
    d = os.path.join(work, 'idx')
    out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith('.json'):
                j = json.load(open(os.path.join(d, f)))
                if j['passed']:
                    j['parts'] = set(j['parts'])
                    out.append(j)
    return out


# --------------------------------------------------------------------------- 1b: archive

class Refused(Exception):
    pass


ROW = re.compile(r'<tr\b[^>]*\bdata-mfr="([^"]*)"[^>]*>')
PN = re.compile(r'\bdata-mfrpartnumber="([^"]*)"')
COUNT = re.compile(r'Datasheets\s*\((\d[\d,]*)\)')
STOCK = re.compile(r'<tr class="part-info">\s*<td[^>]*>\s*<div class="info-container">\s*<h3>\s*([^<]*?)\s*'
                   r'<span class="part">([^<]*)</span>')


def parse_dsa(html):
    """Datasheet-table rows and price-and-stock part headers, each [manufacturer, part number].
    Free text (context-search abstracts, descriptions) is never read."""
    import html as h
    rows = []
    for m in ROW.finditer(html):
        p = PN.search(m.group(0))
        if p:
            rows.append([h.unescape(m.group(1)), h.unescape(p.group(1))])
    stock = [[h.unescape(a), h.unescape(b)] for a, b in STOCK.findall(html)]
    c = COUNT.search(html)
    return rows, stock, int(c.group(1).replace(',', '')) if c else len(rows)


class Archive:
    def __init__(self, work, delay, html_cache=None):
        self.path = os.path.join(work, 'dsa_cache.jsonl')
        self.delay = max(2.0, delay)
        self.cache = {}
        self.html_cache = html_cache
        self.last = 0.0
        self.strikes = 0
        self.fetched = 0
        if os.path.exists(self.path):
            for line in open(self.path):
                j = json.loads(line)
                self.cache[j['q']] = j

    def _store(self, j):
        self.cache[j['q']] = j
        with open(self.path, 'a') as fh:
            fh.write(json.dumps(j) + '\n')

    def lookup(self, q):
        if q in self.cache:
            return self.cache[q]
        if self.html_cache:            # pages another run already fetched politely
            f = os.path.join(self.html_cache, q + '.html')
            if '/' not in q and os.path.exists(f):
                t = open(f, errors='ignore').read()
                if 'Search Results' in t and 'Access denied' not in t:
                    rows, stock, n = parse_dsa(t)
                    j = {'q': q, 'http': 'cached', 'rows': rows, 'stock': stock, 'count': n,
                         'date': time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(f)))}
                    self._store(j)
                    return j
        wait = self.last + self.delay - time.time()
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        code, body = curl(DSA_URL % urllib.parse.quote(q, safe=''), timeout=40)
        self.fetched += 1
        t = (body or b'').decode('utf-8', 'replace')
        ok = (code == '200' and 'Search Results' in t) or \
             (code == '404' and 'no results found' in t.lower())
        if not ok:
            self.strikes += 1
            if self.strikes >= 2:
                raise Refused('archive refused twice (last HTTP %s on %r)' % (code, q))
            print('PUSHBACK archive: HTTP %s on %r at %s; no requests for 15 min' % (
                code, q, time.strftime('%H:%M:%S')), flush=True)
            time.sleep(900)
            self.last = time.time()
            return self.lookup(q)
        self.strikes = 0
        rows, stock, n = parse_dsa(t) if code == '200' else ([], [], 0)
        j = {'q': q, 'http': code, 'rows': rows, 'stock': stock, 'count': n,
             'date': time.strftime('%Y-%m-%d')}
        self._store(j)
        return j


def judge_archive(rec, j):
    hits = []
    for table, rows in (('datasheet', j['rows']), ('stock', j.get('stock', []))):
        for mfr, pn in rows:
            kind = extension_ok(rec['reference'], pn)
            if kind and mfr_ok(rec['manufacturer'], mfr):
                hits.append((table != 'datasheet', kind != 'exact', table, kind, pn, mfr))
    if hits:
        hits.sort()
        _, _, table, kind, pn, mfr = hits[0]
        return 'real', 'datasheetarchive: %s %s row %r attributed to %r' % (table, kind, pn, mfr)
    both = j['rows'] + j.get('stock', [])
    if not both:
        return 'unresolved', 'datasheetarchive: no datasheet or stock rows'
    near = [pn for mfr, pn in both if norm(pn).startswith(norm(rec['reference']))][:3]
    other = sorted({mfr for mfr, pn in both if extension_ok(rec['reference'], pn)})[:3]
    ev = 'datasheetarchive: %d datasheet + %d stock rows, none exact/extension from the manufacturer' % (
        len(j['rows']), len(j.get('stock', [])))
    if other:
        ev += '; exact number under %s' % other
    elif near:
        ev += '; near-misses %s' % near
    return 'unresolved', ev


def key(r):
    return '%s\t%s\t%s' % (r.get('catalogue', ''), r['manufacturer'], r['reference'])


def check(work, inp, outp, delay, html_cache):
    outp = outp or os.path.join(work, 'results.jsonl')
    idx = load_indexes(work)
    arc = Archive(work, delay, html_cache)
    done = set()
    if os.path.exists(outp):
        for line in open(outp):
            done.add(key(json.loads(line)))
    todo = [json.loads(l) for l in open(inp) if l.strip()]
    todo = [r for r in todo if key(r) not in done]
    print('%d to check, %d already done; indexes in use: %s' % (
        len(todo), len(done), [i['name'] for i in idx]), flush=True)
    t0 = time.time()
    counts = collections.Counter()
    with open(outp, 'a') as fh:
        for i, r in enumerate(todo, 1):
            verdict, source, ev = 'unresolved', None, None
            for ix in idx:
                if mfr_ok(r['manufacturer'], ix['manufacturer']) and norm(r['reference']) in ix['parts']:
                    verdict, source = 'real', 'index:' + ix['name']
                    ev = '%s lists %s (%s)' % (ix['name'], r['reference'], ix['source'])
                    break
            if verdict != 'real':
                q = r['reference'].strip()
                if len(re.sub(r'[^A-Za-z0-9]', '', q)) < 3:
                    source, ev = 'none', 'reference too short to search'
                else:
                    try:
                        j = arc.lookup(q)
                    except Refused as e:
                        print('STOP: %s' % e, flush=True)
                        break
                    verdict, ev = judge_archive(r, j)
                    source = 'datasheetarchive'
            out = {k: r[k] for k in ('catalogue', 'manufacturer', 'reference') if k in r}
            for k in ('truth', 'stratum', 'flags', 'line', 'population'):
                if k in r:
                    out[k] = r[k]
            out.update(verdict=verdict, source=source, evidence=ev, checked=time.strftime('%Y-%m-%d'))
            fh.write(json.dumps(out) + '\n')
            fh.flush()
            counts[verdict] += 1
            if i % 50 == 0 or i == len(todo):
                el = time.time() - t0
                print('%d/%d  %s  fetched %d  %.0fs' % (i, len(todo), dict(counts), arc.fetched, el),
                      flush=True)
    write_unresolved(work, outp)


def rejudge(work, outp):
    """Recompute every archive verdict in `outp` from the cached pages (no requests)."""
    arc = Archive(work, 2.0)
    rs = [json.loads(l) for l in open(outp)]
    changed = collections.Counter()
    for r in rs:
        if r.get('source') != 'datasheetarchive':
            continue
        j = arc.cache.get(r['reference'].strip())
        if j is None:
            sys.exit('no cached page for %r' % r['reference'])
        v, ev = judge_archive(r, j)
        if v != r['verdict']:
            changed[(r['verdict'], v)] += 1
        r['verdict'], r['evidence'] = v, ev
    with open(outp + '.tmp', 'w') as fh:
        for r in rs:
            fh.write(json.dumps(r) + '\n')
    os.replace(outp + '.tmp', outp)
    print('rejudged %d rows; changes %s' % (len(rs), dict(changed)))
    if 'calibration' not in os.path.basename(outp):
        write_unresolved(work, outp)


def stress(work, outp):
    """Offline check of the matcher on pages already fetched (no requests).  For every part
    the archive confirmed, invented suffixes (the shape of the fabricated rows) and one- or
    two-character truncations are judged against the SAME page; a mutated string that the
    page lists verbatim is genuinely real and is skipped."""
    arc = Archive(work, 2.0)
    n = collections.Counter()
    bad = collections.Counter()
    examples = []
    for line in open(outp):
        r = json.loads(line)
        if r.get('source') != 'datasheetarchive' or r['verdict'] != 'real':
            continue
        j = arc.cache[r['reference'].strip()]
        listed = {norm(p) for _, p in j['rows'] + j.get('stock', [])}
        ref = r['reference']
        muts = [('suffix', ref + x) for x in ('ASLE', 'DFN', 'FB', 'QZ', 'BGA', 'MSO', 'LS', 'DT', 'DR', 'XQ')]
        muts += [('truncation', ref[:-1]), ('truncation', ref[:-2])]
        muts += [('manufacturer', None)]
        for kind, m in muts:
            if kind == 'manufacturer':
                rec = {'manufacturer': 'Nichicon' if 'nichicon' not in fold(r['manufacturer']) else 'Toshiba',
                       'reference': ref}
            else:
                if len(norm(m)) < 3 or norm(m) in listed:
                    continue
                rec = {'manufacturer': r['manufacturer'], 'reference': m}
            n[kind] += 1
            v, ev = judge_archive(rec, j)
            if v == 'real':
                bad[kind] += 1
                if sum(1 for e in examples if e[0] == kind) < 200:
                    examples.append((kind, ref, rec['reference'], rec['manufacturer'], ev))
    out = {'tested': dict(n), 'called_real': dict(bad), 'examples': examples}
    json.dump(out, open(os.path.join(work, 'stress.json'), 'w'), indent=1)
    print('mutations tested %s; called real %s' % (dict(n), dict(bad)))


def write_unresolved(work, outp):
    seen = {}
    for line in open(outp):
        r = json.loads(line)
        if r['verdict'] != 'unresolved' or 'truth' in r:
            continue
        k = (r['manufacturer'], r['reference'])
        if k not in seen:
            seen[k] = {'manufacturer': r['manufacturer'], 'reference': r['reference'],
                       'catalogues': [], 'stratum': r.get('stratum'), 'tier1': r['evidence']}
        if r.get('catalogue') and r['catalogue'] not in seen[k]['catalogues']:
            seen[k]['catalogues'].append(r['catalogue'])
    with open(os.path.join(work, 'unresolved.jsonl'), 'w') as fh:
        for v in seen.values():
            fh.write(json.dumps(v) + '\n')


def controls(work, delay, html_cache):
    arc = Archive(work, delay, html_cache)
    cases = [('obsolete', 'Infineon', 'ICE2A165'), ('obsolete', 'Monolithic Power Systems', 'MP1410ES'),
             ('obsolete', 'Monolithic Power Systems', 'MP2303DN'), ('obsolete', 'onsemi', 'HGTG30N60A4D'),
             ('obsolete', 'Infineon', 'IKW25T120'), ('obsolete', 'Infineon', 'SGW25N120'),
             ('nonsense', 'Analog Devices', 'XQZ7PM4413GK'), ('nonsense', 'Infineon', 'ICE2A165QZ'),
             ('nonsense', 'Monolithic Power Systems', 'MP1410QZ'), ('nonsense', 'onsemi', 'NCPZ9931XQ')]
    res = []
    for truth, m, p in cases:
        v, ev = judge_archive({'manufacturer': m, 'reference': p}, arc.lookup(p))
        res.append({'truth': truth, 'manufacturer': m, 'reference': p, 'verdict': v, 'evidence': ev})
        print(truth, p, v, ev)
    ok = all((c['verdict'] == 'real') == (c['truth'] == 'obsolete') for c in res)
    json.dump({'cases': res, 'passed': ok}, open(os.path.join(work, 'dsa_controls.json'), 'w'), indent=1)
    print('archive controls', 'PASSED' if ok else 'FAILED')


# --------------------------------------------------------------------------- scoring / report

def wilson(k, n, z=1.96):
    if n == 0:
        return (float('nan'), float('nan'))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def confusion(results):
    tab = collections.Counter()
    bad = []
    for r in results:
        tab[(r['truth'], r['verdict'])] += 1
        if r['truth'] in ('fake', 'nonsense') and r['verdict'] == 'real':
            bad.append(r)
    return tab, bad


def score(path):
    rs = [json.loads(l) for l in open(path)]
    tab, bad = confusion(rs)
    print('%-10s %6s %11s' % ('truth', 'real', 'unresolved'))
    for t in sorted({t for t, _ in tab}):
        print('%-10s %6d %11d' % (t, tab[(t, 'real')], tab[(t, 'unresolved')]))
    print('\nfakes/nonsense called real: %d' % len(bad))
    for b in bad:
        print('  ', b['manufacturer'], b['reference'], b['evidence'])


def report(work):
    L = ['# Part existence, tier 1 (free sources)', '',
         'Generated %s by `scripts/part_existence.py report`.' % time.strftime('%Y-%m-%d %H:%M'), '',
         'Verdicts are `real` (conclusive: a controlled vendor index lists the exact part, or '
         'the Datasheet Archive shows the exact part or a direct order-code extension of it, '
         'attributed to the manufacturer) or `unresolved`. Tier 1 never concludes a part is fake.', '']
    cp = os.path.join(work, 'calibration.jsonl')
    if os.path.exists(cp):
        rs = [json.loads(l) for l in open(cp)]
        tab, bad = confusion(rs)
        L += ['## Calibration (%d labelled parts)' % len(rs), '',
              '| truth | n | real | unresolved |', '|---|---:|---:|---:|']
        for t in ('fake', 'nonsense', 'real', 'obsolete'):
            n = tab[(t, 'real')] + tab[(t, 'unresolved')]
            L.append('| %s | %d | %d | %d |' % (t, n, tab[(t, 'real')], tab[(t, 'unresolved')]))
        rr = tab[('real', 'real')] + tab[('obsolete', 'real')]
        rn = sum(tab[(t, v)] for t in ('real', 'obsolete') for v in ('real', 'unresolved'))
        lo, hi = wilson(rr, rn)
        L += ['', '**Fakes or nonsense called real: %d.** Recall on real parts (incl. obsolete): '
              '%d/%d = %.0f%% (95%% CI %.0f-%.0f%%).' % (len(bad), rr, rn, 100 * rr / max(rn, 1),
                                                         100 * lo, 100 * hi), '']
        miss = [r for r in rs if r['truth'] in ('real', 'obsolete') and r['verdict'] != 'real']
        if miss:
            L += ['Real parts left unresolved:', '']
            L += ['- %s %s (%s): %s' % (r['manufacturer'], r['reference'], r['truth'], r['evidence'])
                  for r in miss]
            L.append('')
    L += ['## Source controls', '']
    dc = os.path.join(work, 'dsa_controls.json')
    if os.path.exists(dc):
        j = json.load(open(dc))
        L += ['Datasheet Archive: %s' % ('PASSED' if j['passed'] else 'FAILED'), '',
              '| control | part | verdict | evidence |', '|---|---|---|---|']
        L += ['| %s | %s %s | %s | %s |' % (c['truth'], c['manufacturer'], c['reference'], c['verdict'],
                                            c['evidence'].replace('|', '/')) for c in j['cases']]
        L.append('')
    d = os.path.join(work, 'idx')
    if os.path.isdir(d):
        L += ['| vendor index | parts | obsolete present | nonsense absent | used |', '|---|---:|---|---|---|']
        for f in sorted(os.listdir(d)):
            j = json.load(open(os.path.join(d, f)))
            L.append('| %s (%s) | %d | %s | %s | %s |' % (
                j['name'], j['source'], j['size'],
                ', '.join('%s %s' % (k, 'yes' if v else 'NO') for k, v in j['controls']['obsolete_present'].items()),
                ', '.join('%s %s' % (k, 'yes' if v else 'NO') for k, v in j['controls']['nonsense_absent'].items()),
                'yes' if j['passed'] else 'no'))
        L.append('')
    extra = os.path.join(work, 'report_notes.md')
    if os.path.exists(extra):
        L += [open(extra).read(), '']
    rp = os.path.join(work, 'results.jsonl')
    if os.path.exists(rp):
        rs = [json.loads(l) for l in open(rp)]
        for stratum, title in (('sample', 'Stratified sample'), ('suspect', 'Suspect cohorts')):
            sel = [r for r in rs if r.get('stratum') == stratum]
            if not sel:
                continue
            by = collections.defaultdict(collections.Counter)
            pop = {}
            for r in sel:
                by[r['catalogue']][r['verdict']] += 1
                by[r['catalogue']]['src:' + (r['source'] or 'none')] += 1
                pop[r['catalogue']] = r.get('population')
            L += ['## %s' % title, '',
                  '| catalogue | rows in catalogue | checked | real | unresolved | real share | 95% CI | via index | via archive |',
                  '|---|---:|---:|---:|---:|---:|---|---:|---:|']
            tk = tn = 0
            for c in sorted(by):
                k, u = by[c]['real'], by[c]['unresolved']
                n = k + u
                tk += k
                tn += n
                lo, hi = wilson(k, n)
                via_idx = sum(v for s, v in by[c].items() if s.startswith('src:index'))
                L.append('| %s | %s | %d | %d | %d | %.1f%% | %.1f-%.1f%% | %d | %d |' % (
                    c, pop[c] if pop[c] is not None else '', n, k, u, 100 * k / max(n, 1), 100 * lo, 100 * hi,
                    via_idx, n - via_idx - by[c]['src:none']))
            lo, hi = wilson(tk, tn)
            L += ['| **all (unweighted)** | | %d | %d | %d | %.1f%% | %.1f-%.1f%% | | |' % (
                tn, tk, tn - tk, 100 * tk / max(tn, 1), 100 * lo, 100 * hi), '']
            if stratum == 'sample' and all(pop[c] for c in by):
                npop = sum(pop[c] for c in by)
                est = sum(by[c]['real'] / (by[c]['real'] + by[c]['unresolved']) * pop[c] for c in by) / npop
                var = sum((pop[c] / npop) ** 2 * (lambda p, n: p * (1 - p) / n)(
                    by[c]['real'] / (by[c]['real'] + by[c]['unresolved']), by[c]['real'] + by[c]['unresolved'])
                    for c in by)
                L += ['Weighted by catalogue size (%d rows), the share of rows confirmed real is '
                      '%.1f%% +/- %.1f (95%%, stratified normal approximation).' % (
                          npop, 100 * est, 196 * math.sqrt(var)), '']
    if os.path.exists(rp):
        def why(r):
            e = r['evidence'] or ''
            if r['verdict'] == 'real':
                return 'real'
            if 'exact number under' in e:
                return 'number listed under another maker'
            if 'near-misses' in e:
                return 'only near-miss numbers'
            if 'no datasheet or stock rows' in e:
                return 'archive has nothing'
            if 'too short' in e:
                return 'reference unsearchable'
            return 'rows, none matching'
        cls = ['archive has nothing', 'only near-miss numbers', 'number listed under another maker',
               'rows, none matching', 'reference unsearchable']
        for stratum, title in (('sample', 'sample'), ('suspect', 'suspect cohorts')):
            sel = [r for r in rs if r.get('stratum') == stratum]
            if not sel:
                continue
            L += ['## Unresolved %s, by what the archive showed' % title, '',
                  '| catalogue | unresolved | ' + ' | '.join(cls) + ' |',
                  '|---|---:|' + '---:|' * len(cls)]
            by = collections.defaultdict(collections.Counter)
            for r in sel:
                by[r['catalogue']][why(r)] += 1
            for c in sorted(by):
                u = sum(v for k, v in by[c].items() if k != 'real')
                L.append('| %s | %d | ' % (c, u) + ' | '.join(str(by[c][k]) for k in cls) + ' |')
            L.append('')
            L += ['## Per manufacturer, %s' % title, '',
                  '| catalogue | manufacturer | checked | real | unresolved | real share |',
                  '|---|---|---:|---:|---:|---:|']
            bm = collections.defaultdict(collections.Counter)
            for r in sel:
                bm[(r['catalogue'], r['manufacturer'])][r['verdict']] += 1
            for (c, m), v in sorted(bm.items()):
                n = v['real'] + v['unresolved']
                L.append('| %s | %s | %d | %d | %d | %.0f%% |' % (c, m, n, v['real'], v['unresolved'],
                                                                 100 * v['real'] / n))
            L.append('')
    open(os.path.join(work, 'report.md'), 'w').write('\n'.join(L) + '\n')
    print('wrote', os.path.join(work, 'report.md'))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--work', required=True, help='directory for every input/output file')
    sub = ap.add_subparsers(dest='cmd', required=True)
    e = sub.add_parser('extract'); e.add_argument('--data', required=True)
    s = sub.add_parser('sample'); s.add_argument('--n', type=int, default=500)
    s.add_argument('--seed', required=True)
    c = sub.add_parser('cohort'); c.add_argument('--limit', type=int, default=0)
    c.add_argument('--seed', required=True)
    x = sub.add_parser('index'); x.add_argument('--name', required=True)
    x.add_argument('--manufacturer', required=True); x.add_argument('--url', nargs='*', default=[])
    x.add_argument('--file', nargs='*', default=[]); x.add_argument('--regex', required=True)
    x.add_argument('--obsolete', nargs='*', default=[]); x.add_argument('--nonsense', nargs='*', default=[])
    x.add_argument('--source', default='')
    for name in ('check', 'controls'):
        k = sub.add_parser(name)
        k.add_argument('--delay', type=float, default=2.5)
        k.add_argument('--html-cache', help='directory of archive pages already fetched, named <query>.html')
        if name == 'check':
            k.add_argument('--in', dest='inp', required=True); k.add_argument('--out')
    sc = sub.add_parser('score'); sc.add_argument('--results', required=True)
    rj = sub.add_parser('rejudge'); rj.add_argument('--results', required=True)
    st = sub.add_parser('stress'); st.add_argument('--results', required=True)
    sub.add_parser('report')
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    if a.cmd == 'extract':
        extract(a.data, a.work)
    elif a.cmd == 'sample':
        sample(a.work, a.n, a.seed)
    elif a.cmd == 'cohort':
        cohort(a.work, a.limit, a.seed)
    elif a.cmd == 'index':
        index(a.work, a.name, a.manufacturer, a.url, a.file, a.regex, a.obsolete, a.nonsense, a.source)
    elif a.cmd == 'controls':
        controls(a.work, a.delay, a.html_cache)
    elif a.cmd == 'check':
        check(a.work, a.inp, a.out, a.delay, a.html_cache)
    elif a.cmd == 'stress':
        stress(a.work, a.results)
    elif a.cmd == 'rejudge':
        rejudge(a.work, a.results)
    elif a.cmd == 'score':
        score(a.results)
    else:
        report(a.work)
