#!/usr/bin/env python3
"""Rolling sample of citation URLs: does each cited document load, and does it name the part?

    python3 scripts/url_alive_sample.py [--per-catalogue 500] [--seed N]

Feeds the `url` check of scripts/db_metrics.py. For each live catalogue it draws a random
sample of parts that cite a URL (the sourceUrl of the strongest provenance entry, i.e. where the
values are claimed to come from; datasheetUrl only when no entry cites one), fetches
each URL once (plain User-Agent, at most 1 request/s per host, robots.txt honoured, PDFs read
with pdftotext) and records:

  ok       HTTP 200 and the document text contains the exact part number (normalised:
           spaces, hyphens, underscores and slashes removed, dots kept)
  series   HTTP 200, part number not in the document, and either the document names the
           part's series or the row itself claims only a series datasheet (seriesConfirmed)
           — counts 0.6, as in the quality score
  wrong    HTTP 200 but the part number is not in the document
  dead     404/410, DNS failure, or a soft-404 page
  blocked  403/429/bot challenge or robots.txt disallow — the link's state is UNKNOWN and is
           left out of the rate
  skip     the citation is not a document: a known API endpoint (see is_api_url), a POST-only
           endpoint (405) or a JSON reply that never names the part. It is not scored; the next
           citation of the row is tried (next provenance entry, then datasheetUrl)

Echo control: a page whose URL embeds the part number may print that number whatever it is (an SPA
shell that renders "Can't find data for <PN>" still contains <PN>). For every host/path pattern with
the part number in the URL, a nonsense part number of the same shape is fetched once; if the page
"names" it too, the pattern proves nothing statically and each citation of that pattern is scored by
a rendered check in headless Chromium (a "can't find" page is wrong). Without Chromium, it scores 0.

alive_rate = (ok + 0.6 series) / (ok + series + wrong + dead). Results accumulate in staging/metrics/url_alive.json
(the rate uses the last 8 weeks of samples) and every check is logged in url_checks.ndjson.
"""
import argparse, collections, datetime, hashlib, io, json, os, random, re, subprocess, sys, tempfile, time
import urllib.parse, urllib.request, urllib.robotparser
TAS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(TAS, 'staging', 'metrics'); os.makedirs(OUT, exist_ok=True)
UA = 'TAS-catalogue-research/1.0'
norm = lambda s: re.sub(r'[\s\-_/]', '', s or '').upper()
RANK = {'valuesReadFromSource': 3, 'partNamed': 3, 'seriesConfirmed': 2, 'existenceConfirmed': 1}
SKIP = {'circuits.ndjson', 'converters.ndjson', 'quarantine.ndjson', 'fabricated_denylist.ndjson'}
SOFT404 = re.compile(r'page (?:you requested )?(?:was )?not (?:be )?found|no longer available|404 error|'
                     r'sorry, the page|documentNotFound', re.I)
CHALLENGE = re.compile(r'just a moment|cf-chl|captcha|access denied|are you a robot', re.I)


def body_of(rec):
    b = next(iter(rec.values()))
    while isinstance(b, dict) and 'manufacturerInfo' not in b and len(b) == 1:
        b = next(iter(b.values()))
    return b if isinstance(b, dict) else None


def is_api_url(url):
    """True for URLs that are data endpoints, not documents. Evidence (staging/metrics/url_checks.ndjson):
    ksim3.kemet.com/api/csv/ (POST-only, 405), ds.yuden.co.jp/TYCOMPAS/eu/graphRest (500), wago.com/wagoapi/...
    (JSON, never names the part), pimapi.murata.com (category stub / POST-only), product.tdk.com/pdc_api/,
    api.harwin.com, redexpert.we-online.com/api/v2. General rule: the host's first label is `api` or ends in
    `api`, or a directory segment is `api` / ends in `api` (wagoapi, pdc_api), or the last segment is graphRest."""
    p = urllib.parse.urlsplit(url)
    label = p.netloc.split('.')[0].lower()
    if label == 'api' or re.fullmatch(r'[a-z]*api', label):
        return True
    segs = [x.lower() for x in p.path.split('/') if x]
    if segs and segs[-1] == 'graphrest':
        return True
    return any(re.fullmatch(r'(?:[a-z]*_)?[a-z]*api', x) for x in segs[:-1])


def candidates(prov, datasheet_url):
    """ordered citations to try for one row: provenance sourceUrls strongest evidence first (ties keep file
    order), then datasheetUrl; API endpoints are not documents and are left out; duplicates dropped"""
    prov = sorted((e for e in prov or [] if isinstance(e, dict) and e.get('sourceUrl')),
                  key=lambda e: -RANK.get(e.get('verification'), 0))
    out, seen = [], set()
    for url, ver in [(e['sourceUrl'], e.get('verification')) for e in prov] + [(datasheet_url, None)]:
        if url and url not in seen and not is_api_url(url):
            seen.add(url); out.append((url, ver))
    return out


def sample(cat, k, rng):
    """reservoir sample of (pn, url) over rows that cite a URL"""
    res, seen = [], 0
    with open(os.path.join(TAS, 'data', cat)) as fh:
        for line in fh:
            if '"http' not in line:
                continue
            try:
                b = body_of(json.loads(line))
            except ValueError:
                continue
            if not b:
                continue
            mi = b.get('manufacturerInfo') or {}
            di = mi.get('datasheetInfo') or {}
            part = di.get('part') or {}
            pn = part.get('partNumber') or mi.get('reference')
            # citations to test: where the values are claimed to come from (strongest evidence first), then
            # datasheetUrl -- often, and correctly, a series datasheet that never prints the full ordering code.
            # API endpoints are not documents and are never sampled (see candidates)
            cands = candidates(di.get('provenance'), mi.get('datasheetUrl'))
            if not pn or not cands:
                continue
            item = (pn, cands, part.get('series') or '')
            seen += 1
            if len(res) < k:
                res.append(item)
            else:
                j = rng.randrange(seen)
                if j < k:
                    res[j] = item
    return res, seen


ROBOTS, LAST = {}, {}


def allowed(url):
    p = urllib.parse.urlsplit(url); host = p.netloc
    if host not in ROBOTS:
        # fetched with our own User-Agent: RobotFileParser.read() sends urllib's default UA,
        # which some sites refuse with 403, and a 403 makes the parser disallow everything
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(f'{p.scheme}://{host}/robots.txt', headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                rp.parse(r.read(500_000).decode('utf-8', 'replace').splitlines())
        except urllib.error.HTTPError as e:
            rp = 'deny' if e.code in (401, 403) else None
        except Exception:
            rp = None
        ROBOTS[host] = rp
    rp = ROBOTS[host]
    if rp == 'deny':
        return False, 1.0
    delay = 1.0
    if rp:
        d = rp.crawl_delay(UA)
        if d:
            delay = max(delay, float(d))
    return (rp is None or rp.can_fetch(UA, url)), delay


def fetch(url):
    host = urllib.parse.urlsplit(url).netloc
    ok, delay = allowed(url)
    if not ok:
        return 'blocked', 'robots', b'', ''
    wait = LAST.get(host, 0) + delay - time.time()
    if wait > 0:
        time.sleep(wait)
    LAST[host] = time.time()
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = r.read(8_000_000)
            return 'http', r.status, data, r.headers.get('Content-Type', '')
    except urllib.error.HTTPError as e:
        return 'http', e.code, b'', ''
    except Exception as e:
        return 'error', type(e).__name__, b'', ''


def text_of(data, ctype):
    if data[:5] == b'%PDF-' or 'pdf' in ctype:
        with tempfile.NamedTemporaryFile(suffix='.pdf') as t:
            t.write(data); t.flush()
            try:
                return subprocess.run(['pdftotext', '-q', t.name, '-'], capture_output=True, timeout=60).stdout.decode('utf-8', 'replace')
            except Exception:
                return ''
    return data.decode('utf-8', 'replace')


def classify_static(pn, url, series=''):
    kind, status, data, ctype = fetch(url)
    if kind == 'blocked':
        return 'blocked', status
    if kind == 'error':
        return ('dead', status) if status in ('URLError', 'gaierror') else ('blocked', status)
    if status in (404, 410):
        return 'dead', status
    if status == 405:
        return 'skip', 'POST-only (405)'  # an endpoint that refuses GET is not a document
    if status in (401, 403, 429, 503):
        return 'blocked', status
    if status != 200:
        return 'dead', status
    txt = text_of(data, ctype)
    if CHALLENGE.search(txt[:5000]) and len(txt) < 20000:
        return 'blocked', 'challenge'
    if norm(pn) in norm(txt):
        return 'ok', 200
    if 'json' in ctype.lower():
        return 'skip', 'JSON reply never names the part'  # a data API, not a document
    if SOFT404.search(txt[:20000]):
        return 'dead', 'soft404'
    if len(norm(series)) >= 3 and norm(series) in norm(txt):
        return 'series', 'series named'  # a series datasheet: names the series, not every code
    return 'wrong', 200


def fake_pn(pn):
    """a nonsense part number of the same shape (digits stay digits, letters stay letters, separators stay),
    deterministic, never equal to pn"""
    h = hashlib.sha256(pn.encode()).digest()
    out = []
    for i, ch in enumerate(pn):
        b = h[i % len(h)]
        out.append(str(b % 10) if ch.isdigit() else (chr(65 + b % 26) if ch.isupper() else chr(97 + b % 26)) if ch.isalpha() else ch)
    f = ''.join(out)
    return f if norm(f) != norm(pn) else 'ZZ' + f[2:]


def pn_pattern(pn, url):
    """host/path pattern of a URL that embeds the part number, with the number replaced; None when the URL
    does not contain it (a URL without the number cannot echo it)"""
    for form in {pn, urllib.parse.quote(pn, safe=''), urllib.parse.quote(pn)}:
        if form and form.lower() in url.lower():
            return re.sub(re.escape(form), '{PN}', url, flags=re.I), form
    return None, None


def fake_url(pat, form, pn, fake):
    """the pattern with the nonsense number written the way the real one was written in the URL"""
    enc = fake if form == pn else urllib.parse.quote(fake, safe='' if form == urllib.parse.quote(pn, safe='') else '/')
    return pat.replace('{PN}', enc)


CANT_FIND = re.compile(r"can'?t find|cannot find|no (?:results|data|parts?) (?:found|available)|not found|page doesn'?t exist", re.I)
CHROMIUM = [os.environ.get('TAS_CHROMIUM', '')] + sorted(
    __import__('glob').glob(os.path.expanduser('~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome')), reverse=True)
_BR = {}


def rendered_names(pn, url):
    """True/False: the page rendered in headless Chromium names pn and does not say it cannot find it;
    None when no browser is available or the page cannot be rendered"""
    if 'pg' not in _BR:
        _BR['pg'] = None
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            exe = next((c for c in CHROMIUM if c and os.path.exists(c)), None)
            br = pw.chromium.launch(headless=True, executable_path=exe, args=['--no-sandbox'])
            _BR['pw'], _BR['pg'] = pw, br.new_page(user_agent=UA)
        except Exception as e:
            print('chromium unavailable:', type(e).__name__, str(e)[:100], file=sys.stderr)
    pg = _BR['pg']
    if pg is None:
        return None
    ok, delay = allowed(url)
    if not ok:
        return None
    host = urllib.parse.urlsplit(url).netloc
    wait = LAST.get(host, 0) + delay - time.time()
    if wait > 0:
        time.sleep(wait)
    LAST[host] = time.time()
    try:
        pg.goto(url, wait_until='networkidle', timeout=60000); pg.wait_for_timeout(1500)
        txt = pg.inner_text('body')
    except Exception:
        return None
    return norm(pn) in norm(txt) and not CANT_FIND.search(txt)


ECHO = {}  # pattern -> True when a nonsense part number in the same pattern is "named" as well


def echoes(pn, url):
    pat, form = pn_pattern(pn, url)
    if pat is None:
        return False
    if pat not in ECHO:
        fake = fake_pn(pn)
        ECHO[pat] = classify_static(fake, fake_url(pat, form, pn, fake))[0] == 'ok'
    return ECHO[pat]


RENDER_CONTROL = {}


def classify_cited(pn, url, series=''):
    """classify() plus the echo control"""
    verdict, why = classify_static(pn, url, series)
    if verdict != 'ok' or not echoes(pn, url):
        return verdict, why
    # the static page names any part number in this pattern: only a rendered page can tell
    pat, form = pn_pattern(pn, url)
    if pat not in RENDER_CONTROL:
        fake = fake_pn(pn)
        RENDER_CONTROL[pat] = rendered_names(fake, fake_url(pat, form, pn, fake))
    if RENDER_CONTROL[pat] is not False:  # fake also "found" (True) or no browser (None): nothing can be proven
        return 'wrong', 'echo pattern, unverifiable' if RENDER_CONTROL[pat] else 'echo pattern, no rendered check'
    r = rendered_names(pn, url)
    return ('ok', 'rendered') if r else ('wrong', 'echo pattern, rendered page does not name the part')


classify = classify_cited


def resolve(pn, cands, series=''):
    """score one row: the first citation that is a document; (verdict, why, url, skipped urls)"""
    skipped = []
    for url, ver in cands:
        verdict, why = classify_cited(pn, url, series)
        if verdict == 'skip':  # not a document (POST-only, JSON): try the row's next citation
            skipped.append(url); continue
        if verdict == 'wrong' and ver == 'seriesConfirmed' and not str(why).startswith('echo'):
            verdict = 'series'  # a series sheet need not print every orderable code
        return verdict, why, url, skipped
    return 'skip', 'no citation is a document', cands[-1][0], skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--per-catalogue', type=int, default=500)
    ap.add_argument('--seed', type=int, default=int(time.time()))
    ap.add_argument('--catalogues', nargs='*')
    ap.add_argument('--out-dir', default=OUT, help='where url_checks.ndjson and url_alive.json go (default staging/metrics; '
                    'a scratch directory measures a fresh sample without touching the metric inputs)')
    a = ap.parse_args()
    out_dir = os.path.abspath(a.out_dir); os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(a.seed); today = datetime.date.today().isoformat()
    cats = a.catalogues or sorted(f for f in os.listdir(os.path.join(TAS, 'data'))
                                  if f.endswith('.ndjson') and f not in SKIP)
    log = open(os.path.join(out_dir, 'url_checks.ndjson'), 'a')
    work = []
    for cat in cats:
        s, seen = sample(cat, a.per_catalogue, rng)
        work += [(cat, pn, cands, series) for pn, cands, series in s]
        print(cat, 'citing rows', seen, 'sampled', len(s), flush=True)
    rng.shuffle(work)  # interleave hosts so per-host delays overlap
    for cat, pn, cands, series in work:
        verdict, why, url, skipped = resolve(pn, cands, series)
        rec = {'date': today, 'catalogue': cat, 'pn': pn, 'url': url, 'verdict': verdict, 'why': str(why), 'seed': a.seed}
        if skipped:
            rec['skippedUrls'] = skipped
        log.write(json.dumps(rec) + '\n'); log.flush()
    log.close()
    # aggregate the last 8 weeks of checks
    cutoff = (datetime.date.today() - datetime.timedelta(weeks=8)).isoformat()
    agg = collections.defaultdict(collections.Counter)
    for line in open(os.path.join(out_dir, 'url_checks.ndjson')):
        d = json.loads(line)
        if d['date'] >= cutoff:
            agg[d['catalogue']][d['verdict']] += 1
    out = {}
    for cat, c in agg.items():
        known = c['ok'] + c['series'] + c['wrong'] + c['dead']
        out[cat] = {'alive_rate': (c['ok'] + 0.6 * c['series']) / known if known else 0.0, 'counts': dict(c), 'since': cutoff}
    json.dump(out, open(os.path.join(out_dir, 'url_alive.json'), 'w'), indent=1)
    print(json.dumps(out, indent=1))
    if _BR.get('pw'):
        _BR['pw'].stop()


if __name__ == '__main__':
    main()
