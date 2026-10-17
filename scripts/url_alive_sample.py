#!/usr/bin/env python3
"""Rolling sample of citation URLs: does each cited document load, and does it name the part?

    python3 scripts/url_alive_sample.py [--per-catalogue 500] [--seed N]

Feeds the `url` check of scripts/db_metrics.py. For each live catalogue it draws a random
sample of parts that cite a URL (datasheetUrl, else the first provenance sourceUrl), fetches
each URL once (plain User-Agent, at most 1 request/s per host, robots.txt honoured, PDFs read
with pdftotext) and records:

  ok       HTTP 200 and the document text contains the exact part number (normalised:
           spaces, hyphens, underscores and slashes removed, dots kept)
  series   HTTP 200, part number not in the document, and the row itself claims only a
           series datasheet (seriesConfirmed) — counts 0.6, as in the quality score
  wrong    HTTP 200 but the part number is not in the document
  dead     404/410, DNS failure, or a soft-404 page
  blocked  403/429/bot challenge or robots.txt disallow — the link's state is UNKNOWN and is
           left out of the rate

alive_rate = (ok + 0.6 series) / (ok + series + wrong + dead). Results accumulate in staging/metrics/url_alive.json
(the rate uses the last 8 weeks of samples) and every check is logged in url_checks.ndjson.
"""
import argparse, collections, datetime, io, json, os, random, re, subprocess, sys, tempfile, time
import urllib.parse, urllib.request, urllib.robotparser
TAS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(TAS, 'staging', 'metrics'); os.makedirs(OUT, exist_ok=True)
UA = 'TAS-catalogue-research/1.0'
norm = lambda s: re.sub(r'[\s\-_/]', '', s or '').upper()
SKIP = {'circuits.ndjson', 'converters.ndjson', 'quarantine.ndjson', 'fabricated_denylist.ndjson'}
SOFT404 = re.compile(r'page (?:you requested )?(?:was )?not (?:be )?found|no longer available|404 error|'
                     r'sorry, the page|documentNotFound', re.I)
CHALLENGE = re.compile(r'just a moment|cf-chl|captcha|access denied|are you a robot', re.I)


def body_of(rec):
    b = next(iter(rec.values()))
    while isinstance(b, dict) and 'manufacturerInfo' not in b and len(b) == 1:
        b = next(iter(b.values()))
    return b if isinstance(b, dict) else None


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
            pn = (di.get('part') or {}).get('partNumber') or mi.get('reference')
            prov = [e for e in di.get('provenance') or [] if isinstance(e, dict)]
            url = mi.get('datasheetUrl') or next((e.get('sourceUrl') for e in prov if e.get('sourceUrl')), None)
            if not pn or not url:
                continue
            ver = next((e.get('verification') for e in prov if e.get('sourceUrl') == url), None)
            seen += 1
            if len(res) < k:
                res.append((pn, url, ver))
            else:
                j = rng.randrange(seen)
                if j < k:
                    res[j] = (pn, url, ver)
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


def classify(pn, url):
    kind, status, data, ctype = fetch(url)
    if kind == 'blocked':
        return 'blocked', status
    if kind == 'error':
        return ('dead', status) if status in ('URLError', 'gaierror') else ('blocked', status)
    if status in (404, 410):
        return 'dead', status
    if status in (401, 403, 429, 503):
        return 'blocked', status
    if status != 200:
        return 'dead', status
    txt = text_of(data, ctype)
    if CHALLENGE.search(txt[:5000]) and len(txt) < 20000:
        return 'blocked', 'challenge'
    if norm(pn) in norm(txt):
        return 'ok', 200
    if SOFT404.search(txt[:20000]):
        return 'dead', 'soft404'
    return 'wrong', 200


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--per-catalogue', type=int, default=500)
    ap.add_argument('--seed', type=int, default=int(time.time()))
    ap.add_argument('--catalogues', nargs='*')
    a = ap.parse_args()
    rng = random.Random(a.seed); today = datetime.date.today().isoformat()
    cats = a.catalogues or sorted(f for f in os.listdir(os.path.join(TAS, 'data'))
                                  if f.endswith('.ndjson') and f not in SKIP)
    log = open(os.path.join(OUT, 'url_checks.ndjson'), 'a')
    work = []
    for cat in cats:
        s, seen = sample(cat, a.per_catalogue, rng)
        work += [(cat, pn, url, ver) for pn, url, ver in s]
        print(cat, 'citing rows', seen, 'sampled', len(s), flush=True)
    rng.shuffle(work)  # interleave hosts so per-host delays overlap
    for cat, pn, url, ver in work:
        verdict, why = classify(pn, url)
        if verdict == 'wrong' and ver == 'seriesConfirmed':
            verdict = 'series'  # a series sheet need not print every orderable code
        log.write(json.dumps({'date': today, 'catalogue': cat, 'pn': pn, 'url': url,
                              'verdict': verdict, 'why': str(why), 'seed': a.seed}) + '\n'); log.flush()
    log.close()
    # aggregate the last 8 weeks of checks
    cutoff = (datetime.date.today() - datetime.timedelta(weeks=8)).isoformat()
    agg = collections.defaultdict(collections.Counter)
    for line in open(os.path.join(OUT, 'url_checks.ndjson')):
        d = json.loads(line)
        if d['date'] >= cutoff:
            agg[d['catalogue']][d['verdict']] += 1
    out = {}
    for cat, c in agg.items():
        known = c['ok'] + c['series'] + c['wrong'] + c['dead']
        out[cat] = {'alive_rate': (c['ok'] + 0.6 * c['series']) / known if known else 0.0, 'counts': dict(c), 'since': cutoff}
    json.dump(out, open(os.path.join(OUT, 'url_alive.json'), 'w'), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
