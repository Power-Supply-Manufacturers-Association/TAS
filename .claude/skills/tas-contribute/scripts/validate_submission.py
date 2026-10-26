#!/usr/bin/env python3
"""Validate one contribution directory before it goes into a pull request.

    python3 validate_submission.py incoming/<YYYY-MM-DD>-<github-handle>-<topic> [--tas-root TAS]
                                   [--work-dir DIR]

Runs, and never skips:
  0. controls     -- each check below is first shown to FIRE on a planted bad input
                     (Blade Runner, schema, denylist, provenance shape, secrets scan). A check
                     that cannot catch its own control is not running, and the run stops.
  1. environment  -- sibling schema repos present, live catalogues are real data (not LFS
                     pointers), Blade Runner + metrics modules import and match the source.
  2. layout       -- directory name, allowed file names, REPORT.md sections, no git-LFS
                     filter on the submission's files.
  3. candidates.<catalogue>.ndjson
       wrapper + identity, JSON-Schema (Draft 2020-12, the gate's own validators),
       Blade Runner on EVERY row (IMPOSSIBLE fails; SUSPICIOUS is listed),
       provenance shape, then the full candidates gate (check_candidates.py: ingest gate,
       denylist, duplicates vs live + quarantine, batch rules) in a FRESH directory.
       Every row must be accepted: a submission carries no rejected rows.
  4. fixes.<catalogue>.jsonl
       manifest shape + provenance shape, then an apply_fixes.py DRY RUN against the live
       catalogue (on a symlinked scratch data root; data/ is never written). Requires
       unmatched 0, no refusals, no 'old value differs'.
  5. side files   -- not_found / out_of_scope / incomplete / suspects / evidence shapes.
  6. secrets and personal data -- tokens, keys, e-mail addresses, home-directory paths,
     in every file of the directory.

Writes <dir>/gate/summary.json and <dir>/gate/SUMMARY.md (paste SUMMARY.md into the PR).
Exit 0 = every check passed; 1 = a check failed; 2 = a check could not run (NOT a pass).
"""
import argparse
import copy
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _common import (SetupError, find_tas_root, pop_tas_root_arg, setup_imports,  # noqa: E402
                     missing_siblings, require_data_file, is_lfs_pointer)

# ---------------------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------------------
DIR_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})-([A-Za-z0-9][A-Za-z0-9-]{0,38})-([a-z0-9][a-z0-9-]{1,60})$')
CAND_RE = re.compile(r'^candidates\.([a-z_]+)\.ndjson$')
FIX_RE = re.compile(r'^fixes\.([a-z_]+)\.jsonl$')
SIDE_FILES = {'not_found.jsonl': ('mpn', 'manufacturer', 'checked_url'),
              'out_of_scope.jsonl': ('mpn', 'manufacturer', 'found_category'),
              'incomplete.jsonl': ('mpn', 'manufacturer', 'reason'),
              'suspects.jsonl': ('mpn', 'manufacturer', 'reason'),
              'evidence.jsonl': ('url', 'documentSha256', 'parts')}
NOT_PART_CATALOGUES = {'circuits.ndjson', 'converters.ndjson'}
REPORT_SECTIONS = ('sources', 'counts', 'field coverage', 'hand-check', 'controls', 'doubts')
MAX_FILE_BYTES = 50 * 1024 * 1024

PROV_KEYS = ('source', 'sourceName', 'sourceUrl', 'retrievedDate', 'fields',
             'verification', 'verificationDate')
# verificationMethod is allowed for facts read off the source that qualify the value
# ("Measured at 10 mA Max.", "1000 VRMS Min"), never for how the row was obtained.
PROV_OPTIONAL = ('verificationMethod',)
SCRAPE_NARRATIVE = re.compile(r'(https?://|\bhttp\b|\bGET\b|\bcurl\b|pdftotext|scrap|crawl|pars(e|ed|ing)\b|'
                              r'extract|regex|match(ed|ing)?\b|wayback|archive|playwright|browser|\.py\b|'
                              r'\bscript|\bagent\b|\bticket\b|#\d{2,}|staging|export|api\b|imported|pipeline)', re.I)
PROV_SOURCES = {'manufacturerParametric', 'manufacturerDatasheet', 'manufacturerDatabase', 'derived'}
PROV_VERIFICATIONS = {'partNamed', 'valuesReadFromSource', 'seriesConfirmed', 'existenceConfirmed'}
# Aggregators and archives are leads, never evidence for a value or a part.
AGGREGATOR_HOSTS = ('datasheetarchive.com', 'alldatasheet', 'datasheet4u', 'datasheetspdf',
                    'datasheetpdf', 'datasheetcatalog', 'datasheets360', 'octopart.com',
                    'findchips.com', 'digchip.com', 'icpdf', 'manualslib', 'kynix',
                    'utsource', 'datasheetq', 'pdf1.alldatasheet', 'datasheet.octopart',
                    'web.archive.org')
FORBIDDEN_URL = (re.compile(r'^https?://api\.te\.com/', re.I),
                 re.compile(r'^https?://(www\.)?te\.com/commerce', re.I))
# A source name is a noun phrase at any length; a finite verb, a script name or a ticket
# reference means it has become an explanation (judge by grammar, never by length).
NARRATIVE = re.compile(r'(\b(was|were|is|are|has been|have been|we)\b|\.py\b|\bticket\b|#\d{2,}|'
                       r'\bagent\b|\bscript\b)')

SECRET_PATTERNS = [
    ('github token', re.compile(r'\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})')),
    ('api key (sk-)', re.compile(r'\bsk-[A-Za-z0-9_-]{20,}')),
    ('aws key', re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('slack token', re.compile(r'\bxox[abprs]-[A-Za-z0-9-]{10,}')),
    ('google api key', re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b')),
    ('private key', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('jwt', re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}')),
    ('bearer credential', re.compile(r'\bBearer\s+[A-Za-z0-9._~+/=-]{16,}', re.I)),
    ('credential assignment', re.compile(
        r'(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd)["\']?\s*[:=]\s*'
        r'["\']?[A-Za-z0-9/_+.=-]{12,}', re.I)),
    ('cookie header', re.compile(r'\b(Set-)?Cookie:\s*\S+', re.I)),
    ('e-mail address', re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b')),
    ('home directory path', re.compile(r'(/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+|'
                                       r'[A-Za-z]:\\+Users\\+[^\\\s"]+|\\\\wsl[.$]|/mnt/[a-z]/Users/|'
                                       r'/root/)', re.I)),
]


class Result:
    def __init__(self):
        self.checks = []        # {name, status PASS|FAIL|ERROR, detail}
        # summary.json goes into a public PR: never let a local path leak through a detail
        self.scrub = [(tempfile.gettempdir(), '<tmp>'), (str(Path.home()), '~')]

    def add(self, name, status, detail=''):
        for a, b in self.scrub:
            if a and len(a) > 1:
                detail = detail.replace(a, b)
        self.checks.append({'check': name, 'status': status, 'detail': detail})
        mark = {'PASS': 'ok  ', 'FAIL': 'FAIL', 'ERROR': 'ERR '}[status]
        print(f'[{mark}] {name}' + (f' -- {detail}' if detail else ''), flush=True)

    def code(self):
        st = {c['status'] for c in self.checks}
        return 2 if 'ERROR' in st else 1 if 'FAIL' in st else 0


# ---------------------------------------------------------------------------------------
# checks that are pure functions (so the controls can exercise exactly the same code)
# ---------------------------------------------------------------------------------------
def iso_date(s):
    try:
        return dt.date.fromisoformat(s) if isinstance(s, str) and len(s) == 10 else None
    except ValueError:
        return None


def provenance_problems(entry, today=None):
    today = today or dt.date.today()
    out = []
    if not isinstance(entry, dict):
        return ['provenance entry is not an object']
    src = entry.get('source')
    allowed = set(PROV_KEYS) | set(PROV_OPTIONAL) | ({'derivation'} if src == 'derived' else set())
    extra = sorted(set(entry) - allowed)
    if extra:
        out.append(f'keys not allowed in a provenance entry: {extra} (allowed: {sorted(allowed)})')
    need = ('source', 'sourceName', 'fields', 'derivation') if src == 'derived' else PROV_KEYS
    miss = [k for k in need if k not in entry]
    if miss:
        out.append(f'missing provenance keys: {miss}')
    if src not in PROV_SOURCES:
        out.append(f'source {src!r} not allowed (use one of {sorted(PROV_SOURCES)}; '
                   'distributor and aggregator pages are leads, not evidence)')
    if src != 'derived' or 'verification' in entry:
        v = entry.get('verification')
        if v not in PROV_VERIFICATIONS:
            out.append(f'verification {v!r} not allowed (one of {sorted(PROV_VERIFICATIONS)}); '
                       'an unverified value does not belong in a contribution')
    for k in ('retrievedDate', 'verificationDate'):
        if k in entry:
            d = iso_date(entry[k])
            if d is None:
                out.append(f'{k} {entry[k]!r} is not YYYY-MM-DD')
            elif d > today or d.year < 2000:
                out.append(f'{k} {entry[k]} is not a real fetch date')
    url = entry.get('sourceUrl')
    if 'sourceUrl' in entry or src != 'derived':
        if not isinstance(url, str) or not re.match(r'^https?://[^\s/]+\.[^\s/]+/?\S*$', url or ''):
            out.append(f'sourceUrl {url!r} is not an http(s) URL')
        else:
            host = re.sub(r'^https?://', '', url).split('/')[0].lower()
            if any(a in host for a in AGGREGATOR_HOSTS):
                out.append(f'sourceUrl host {host} is an aggregator/archive: cite the manufacturer')
            if any(p.match(url) for p in FORBIDDEN_URL):
                out.append('sourceUrl is on a robots-disallowed TE endpoint (api.te.com, te.com/commerce)')
    name = entry.get('sourceName')
    if not isinstance(name, str) or not name.strip():
        out.append('sourceName is empty')
    elif NARRATIVE.search(name):
        out.append(f'sourceName reads like prose, not a source name: {name[:80]!r}')
    f = entry.get('fields')
    if not isinstance(f, list) or not f or not all(isinstance(x, str) and x.strip() for x in f):
        out.append('fields must be a non-empty list of field paths')
    elif any(' ' in x for x in f):
        out.append('fields holds something that is not a field path (a field NAME belongs there)')
    if 'verificationMethod' in entry:
        vm = entry['verificationMethod']
        if not isinstance(vm, str) or not vm.strip() or '\n' in vm or len(vm) > 200:
            out.append('verificationMethod must be one short line of measurement conditions/bounds')
        elif SCRAPE_NARRATIVE.search(vm):
            out.append(f'verificationMethod describes how data was obtained, not a measurement '
                       f'condition or bound: {vm[:80]!r}')
    if src == 'derived':
        d = entry.get('derivation')
        if not isinstance(d, str) or not d.strip() or '\n' in d or len(d) > 400:
            out.append('derivation must be a one-line formula (<= 400 chars)')
    return out


def provenance_lists(node, path=''):
    if isinstance(node, dict):
        for k, v in node.items():
            p = f'{path}.{k}' if path else k
            if k == 'provenance' and isinstance(v, list):
                yield p, v
            else:
                yield from provenance_lists(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from provenance_lists(v, f'{path}.{i}')


def record_provenance_problems(rec):
    out, n = [], 0
    for p, lst in provenance_lists(rec):
        for i, e in enumerate(lst):
            n += 1
            out += [f'{p}[{i}]: {x}' for x in provenance_problems(e)]
    if n == 0:
        out.append('record carries no provenance entry')
    return out


def scan_text(text):
    hits = []
    for name, rx in SECRET_PATTERNS:
        for m in rx.finditer(text):
            hits.append((name, m.group(0)[:12] + '...'))
            break
    return hits


# ---------------------------------------------------------------------------------------
def main():
    try:
        root = find_tas_root(pop_tas_root_arg())
    except SetupError as e:
        print(f'[ERR ] environment -- {e}'); sys.exit(2)
    os.environ['TAS_REPO'] = str(root)   # the gate subprocesses and imports resolve the same tree
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('dir')
    ap.add_argument('--work-dir', help='scratch space for the fresh gate dirs and the fixes dry run '
                    '(default: a new temp dir; a fixes dry run needs room for one catalogue copy)')
    a = ap.parse_args()
    sub = Path(a.dir).resolve()
    R = Result()
    R.scrub.insert(0, (str(root.parent), '<workspace>'))
    print(f'Validating submission {sub.name}\n')
    if not sub.is_dir():
        R.add('layout: directory exists', 'ERROR', f'{a.dir} is not a directory'); sys.exit(2)

    # ---- 1. environment --------------------------------------------------------------
    miss = missing_siblings(root)
    if miss:
        R.add('environment: sibling schema repos', 'ERROR',
              f'missing next to TAS: {miss} (clone them into {root.parent.name}/)')
        finish(R, sub, {}); return
    R.add('environment: sibling schema repos', 'PASS', 'all present')
    setup_imports(root)
    try:
        import tas_validator
        import tas_metrics
        import ingest_gate
    except ImportError as e:
        R.add('environment: Blade Runner / metrics / gate import', 'ERROR',
              f'{e}; build or fetch the modules (scripts/build_blade_runner.sh or fetch_blade_runner.sh)')
        finish(R, sub, {}); return
    fr = subprocess.run([sys.executable, str(root / 'validator' / 'tools' / 'check_freshness.py'),
                         '--module-dir', str(root / 'validator' / 'build'),
                         '--validator-dir', str(root / 'validator')], capture_output=True, text=True)
    if fr.returncode != 0:
        R.add('environment: Blade Runner matches validator source', 'ERROR',
              (fr.stdout + fr.stderr).strip().splitlines()[-1] if (fr.stdout + fr.stderr).strip()
              else 'check_freshness failed; rebuild with scripts/build_blade_runner.sh')
        finish(R, sub, {}); return
    R.add('environment: Blade Runner matches validator source', 'PASS', fr.stdout.strip()[:20] + '...')

    # ---- 0. controls (after the imports they exercise) -------------------------------
    controls_ok = run_controls(R, root, tas_validator, ingest_gate)
    if not controls_ok:
        finish(R, sub, {}); return

    # ---- 2. layout --------------------------------------------------------------------
    m = DIR_RE.match(sub.name)
    if not m or iso_date(m.group(1)) is None:
        R.add('layout: directory name', 'FAIL',
              f'{sub.name!r} is not <YYYY-MM-DD>-<github-handle>-<topic> (topic lower-case, hyphens)')
    elif sub.parent.name != 'incoming':
        R.add('layout: directory name', 'FAIL', 'the submission directory must sit in incoming/')
    else:
        R.add('layout: directory name', 'PASS', sub.name)
    cands, fixes, sides, unknown = {}, {}, [], []
    for p in sorted(sub.iterdir()):
        if p.name == 'gate' and p.is_dir():
            continue
        if p.is_dir():
            unknown.append(p.name + '/'); continue
        mc, mf = CAND_RE.match(p.name), FIX_RE.match(p.name)
        if mc:
            cands[mc.group(1) + '.ndjson'] = p
        elif mf:
            fixes[mf.group(1) + '.ndjson'] = p
        elif p.name in SIDE_FILES or p.name == 'REPORT.md':
            sides.append(p)
        else:
            unknown.append(p.name)
    if unknown:
        R.add('layout: allowed files only', 'FAIL', f'unexpected: {unknown}')
    elif not cands and not fixes:
        R.add('layout: allowed files only', 'FAIL', 'no candidates.<catalogue>.ndjson or fixes.<catalogue>.jsonl')
    else:
        R.add('layout: allowed files only', 'PASS',
              f'{len(cands)} candidates file(s), {len(fixes)} fixes file(s)')
    big = [p.name for p in sub.iterdir() if p.is_file() and p.stat().st_size > MAX_FILE_BYTES]
    if big:
        R.add('layout: file size', 'FAIL', f'over {MAX_FILE_BYTES >> 20} MB, split the unit: {big}')
    bad_cat = [c for c in list(cands) + list(fixes)
               if c not in ingest_gate.CATALOGUES or c in NOT_PART_CATALOGUES]
    if bad_cat:
        R.add('layout: catalogue names', 'FAIL',
              f'{bad_cat} not part catalogues (known: {sorted(set(ingest_gate.CATALOGUES) - NOT_PART_CATALOGUES)})')
        cands = {k: v for k, v in cands.items() if k not in bad_cat}
        fixes = {k: v for k, v in fixes.items() if k not in bad_cat}
    check_report(R, sub / 'REPORT.md')
    check_lfs_attr(R, root, sub, list(cands.values()) + list(fixes.values()))

    # data files the gate needs
    try:
        for c in sorted(set(cands) | set(fixes)):
            require_data_file(root, c)
        require_data_file(root, 'quarantine.ndjson')
        require_data_file(root, 'fabricated_denylist.ndjson')
        R.add('environment: live catalogues are real data', 'PASS',
              ', '.join(sorted(set(cands) | set(fixes) | {'quarantine.ndjson', 'fabricated_denylist.ndjson'})))
    except SetupError as e:
        R.add('environment: live catalogues are real data', 'ERROR', str(e))
        finish(R, sub, {}); return

    work = Path(a.work_dir).resolve() if a.work_dir else Path(tempfile.mkdtemp(prefix='tas-submission-'))
    work.mkdir(parents=True, exist_ok=True)
    stats = {'candidates': {}, 'fixes': {}}
    try:
        for cat, path in cands.items():
            stats['candidates'][cat] = check_candidates_file(R, root, cat, path, work,
                                                             tas_validator, tas_metrics, ingest_gate)
        for cat, path in fixes.items():
            stats['fixes'][cat] = check_fixes_file(R, root, cat, path, work)
    finally:
        if not a.work_dir:
            shutil.rmtree(work, ignore_errors=True)
    for p in sides:
        if p.name in SIDE_FILES:
            check_side_file(R, p)
    check_secrets(R, sub)
    finish(R, sub, stats)


def run_controls(R, root, tas_validator, ingest_gate):
    ok = True
    ctl = {'capacitor': {'manufacturerInfo': {'name': 'CONTROL', 'datasheetInfo': {
        'part': {'partNumber': 'CONTROL-0'},
        'electrical': {'capacitance': {'nominal': 1e-6, 'minimum': 2e-6, 'maximum': 5e-7}}}}}}
    try:
        f = [x.code for x in tas_validator.validate(ctl).findings if str(x.severity) == 'IMPOSSIBLE']
        good = 'CAP_TOLERANCE' in f
    except Exception as e:  # a control that cannot run is an error, not a pass
        good, f = False, [repr(e)]
    R.add('control: Blade Runner flags an impossible capacitor', 'PASS' if good else 'ERROR', str(f))
    ok &= good
    try:
        v = ingest_gate.validator_for(ingest_gate.CATALOGUES['capacitors.ndjson'])
        body = copy.deepcopy(ctl['capacitor']); body['manufacturerInfo']['notASchemaField'] = 1
        good = any(True for _ in v.iter_errors(body))
    except Exception as e:
        good = False
    R.add('control: schema rejects an unknown key', 'PASS' if good else 'ERROR')
    ok &= good
    try:
        spec = importlib.util.spec_from_file_location('cc_ctl', HERE / 'check_candidates.py')
        cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
        with open(root / 'data' / 'fabricated_denylist.ndjson', encoding='utf-8') as fh:
            first = json.loads(next(l for l in fh if l.strip()))
        verdict = cc.denylist().verdict(first['id'], first.get('manufacturer') or 'Any Maker')
        good = bool(verdict) and verdict[0] == 'duplicate'
    except Exception as e:
        good = False
    R.add('control: denylist catches a known fabricated id', 'PASS' if good else 'ERROR')
    ok &= good
    bad = {'source': 'manufacturerDatasheet', 'sourceName': 'X datasheet', 'sourceUrl': 'https://example.com/x.pdf',
           'retrievedDate': '2026-01-01', 'fields': ['electrical.capacitance'], 'verification': 'partNamed',
           'verificationDate': '2026-01-01', 'note': 'checked by hand'}
    clean = {k: v for k, v in bad.items() if k != 'note'}
    good = (bool(provenance_problems(bad)) and not provenance_problems(clean)
            and not provenance_problems(dict(clean, verificationMethod='Measured at 10 mA Max.'))
            and bool(provenance_problems(dict(clean, verificationMethod='HTTP GET + pdftotext'))))
    R.add('control: provenance check rejects an extra key and scrape narrative, accepts a clean entry', 'PASS' if good else 'ERROR')
    ok &= good
    planted = 'token=' + 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'
    good = bool(scan_text(planted)) and not scan_text('C056G151F2G5CS 1.5e-10 F, 200 V')
    R.add('control: secrets scan catches a planted token, passes a part row', 'PASS' if good else 'ERROR')
    ok &= good
    return ok


def check_report(R, p):
    if not p.is_file():
        R.add('layout: REPORT.md', 'FAIL', 'missing'); return
    text = p.read_text(encoding='utf-8', errors='replace')
    heads = [h.strip().lower() for h in re.findall(r'^#{1,3}\s+(.+)$', text, re.M)]
    miss = [s for s in REPORT_SECTIONS if not any(h.startswith(s) for h in heads)]
    if miss:
        R.add('layout: REPORT.md', 'FAIL', f'missing sections: {miss}')
    elif re.search(r'<fill[^>]*>', text):
        R.add('layout: REPORT.md', 'FAIL', 'template placeholders (<fill ...>) left in')
    else:
        R.add('layout: REPORT.md', 'PASS', 'all sections present')


def check_lfs_attr(R, root, sub, files):
    """Submission files are plain git text. If the repo's *.ndjson LFS rule applies to them,
    a contributor's push needs write access to the LFS store, which they do not have."""
    bad = []
    for p in files:
        rel = f'incoming/{sub.name}/{p.name}'
        r = subprocess.run(['git', '-C', str(root), 'check-attr', 'filter', '--', rel],
                           capture_output=True, text=True)
        if r.returncode != 0:
            R.add('layout: no git-LFS filter on submission files', 'ERROR', r.stderr.strip()); return
        if r.stdout.strip().endswith(': lfs'):
            bad.append(p.name)
    if bad:
        R.add('layout: no git-LFS filter on submission files', 'FAIL',
              f'{bad} would be stored in git-LFS; .gitattributes needs '
              '"incoming/** !filter !diff !merge text" (ask the maintainers)')
    else:
        R.add('layout: no git-LFS filter on submission files', 'PASS')


def check_candidates_file(R, root, cat, path, work, tas_validator, tas_metrics, ingest_gate):
    tag = f'candidates.{cat[:-7]}'
    C = ingest_gate.CATALOGUES[cat]
    rows, problems, schema_ok = [], [], []
    with open(path, encoding='utf-8') as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                problems.append(f'line {n}: blank line'); continue
            try:
                rec = json.loads(line)
            except ValueError as e:
                problems.append(f'line {n}: bad JSON: {e}'); continue
            rows.append((n, rec))
    if not rows:
        R.add(f'{tag}: rows', 'FAIL', 'file is empty'); return {}
    # wrapper + identity
    wrap_bad, bodies = [], {}
    for n, rec in rows:
        got = ingest_gate.unwrap(rec, C.disc, want_kind=True)
        body, kind = got if got else (None, None)
        if not isinstance(rec, dict) or list(rec) != [C.disc[0]] or body is None:
            wrap_bad.append(f'line {n}: not wrapped as {"/".join(C.disc)}'); continue
        if not ingest_gate.identity_of(body):
            wrap_bad.append(f'line {n}: no partNumber/reference'); continue
        if not (body.get('manufacturerInfo') or {}).get('name'):
            wrap_bad.append(f'line {n}: no manufacturerInfo.name'); continue
        bodies[n] = (body, kind)
    R.add(f'{tag}: wrapper, identity, manufacturer', 'FAIL' if (wrap_bad or problems) else 'PASS',
          '; '.join((problems + wrap_bad)[:5]) or f'{len(rows)} rows')
    # schema
    bad, vals = [], {}
    for n, rec in rows:
        if n not in bodies:
            schema_ok.append(False); continue
        body, kind = bodies[n]
        try:
            key = kind if C.schema_file == '*' else None
            if key not in vals:
                vals[key] = ingest_gate.validator_for(C, kind) if key else ingest_gate.validator_for(C)
            errs = list(vals[key].iter_errors(body))
        except Exception as e:
            R.add(f'{tag}: JSON-Schema', 'ERROR', f'line {n}: cannot load the schema: {e}'); return {}
        schema_ok.append(not errs)
        if errs:
            e = errs[0]
            bad.append(f'line {n}: {e.message[:120]} @ {list(e.absolute_path)}')
    R.add(f'{tag}: JSON-Schema ({C.repo}/{C.schema_file})', 'FAIL' if bad else 'PASS',
          '; '.join(bad[:5]) + (f' (+{len(bad) - 5} more)' if len(bad) > 5 else '') if bad
          else f'{len(rows)} rows valid')
    # Blade Runner on every row
    imp, sus = [], {}
    for n, rec in rows:
        try:
            v = tas_validator.validate(rec)
        except Exception as e:
            imp.append(f'line {n}: Blade Runner error {e}'); continue
        for f in v.findings:
            if str(f.severity) == 'IMPOSSIBLE':
                imp.append(f'line {n}: {f.code} {f.message[:100]}')
            elif str(f.severity) == 'SUSPICIOUS':
                sus[f.code] = sus.get(f.code, 0) + 1
    R.add(f'{tag}: Blade Runner (no IMPOSSIBLE)', 'FAIL' if imp else 'PASS',
          '; '.join(imp[:5]) if imp else (f'SUSPICIOUS (explain in REPORT.md): {sus}' if sus else 'no findings'))
    # provenance shape
    pb = []
    for n, rec in rows:
        pb += [f'line {n}: {x}' for x in record_provenance_problems(rec)]
    R.add(f'{tag}: provenance shape', 'FAIL' if pb else 'PASS',
          '; '.join(pb[:5]) + (f' (+{len(pb) - 5} more)' if len(pb) > 5 else '') if pb else 'every entry clean')
    # the candidates gate, in a fresh directory
    fresh = Path(tempfile.mkdtemp(prefix=f'gate-{cat[:-7]}-', dir=work))
    shutil.copyfile(path, fresh / 'candidates.ndjson')
    r = subprocess.run([sys.executable, str(HERE / 'check_candidates.py'), '--tas-root', str(root),
                        '--catalogue', cat, str(fresh / 'candidates.ndjson')],
                       capture_output=True, text=True)
    try:
        gs = json.loads(r.stdout[r.stdout.index('{'):])
    except ValueError:
        R.add(f'{tag}: candidates gate', 'ERROR',
              f'gate did not produce a summary (exit {r.returncode}): {(r.stderr or r.stdout).strip()[-300:]}')
        return {}
    rej_lines = []
    if (fresh / 'rejected.ndjson').is_file():
        for l in open(fresh / 'rejected.ndjson', encoding='utf-8'):
            d = json.loads(l)
            rej_lines.append(f"line {d.get('line')} {d.get('identity')}: {'; '.join(d['reasons'])[:200]}")
    passed = r.returncode == 0 and gs.get('rejected') == 0 and gs.get('batch_refusal_count') == 0
    R.add(f'{tag}: candidates gate (fresh dir)', 'PASS' if passed else 'FAIL',
          f"accepted {gs.get('accepted')}, rejected {gs.get('rejected')}, batch refusals "
          f"{gs.get('batch_refusal_count')}" + ('; ' + ' | '.join(rej_lines[:5]) if rej_lines else '')
          + ('; ' + ' | '.join(gs.get('batch_refusals', [])[:3]) if gs.get('batch_refusals') else ''))
    # quality score (informative): URL liveness is unmeasured for a submission, so its term is 0
    score = {}
    try:
        spec_f = root / 'scripts' / 'metrics_spec.json'
        spec_text = spec_f.read_text(); spec = json.loads(spec_text)
        name = next(k for k, v in spec['catalogues'].items() if (v.get('file') or k) == cat)
        al = root / 'scripts' / 'manufacturer_aliases.json'
        sc = tas_metrics.Scorer(spec_text, al.read_text()) if al.exists() else tas_metrics.Scorer(spec_text)
        t = sc.score_file(str(path), name, 0.0, b''.join(b'1' if ok else b'0' for ok in schema_ok))
        score = {'mean_quality_url_unmeasured': round(t['sum'] / max(1, t['live']), 4),
                 'checks': {k: round(t['check_sum'][k] / t['check_n'][k], 4)
                            for k in t['check_n'] if t['check_n'][k]}}
    except Exception as e:
        R.add(f'{tag}: quality score', 'ERROR', f'tas_metrics could not score the file: {e}')
    return {'rows': len(rows), 'gate': {k: gs.get(k) for k in ('accepted', 'rejected', 'batch_refusal_count',
                                                                 'reject_reasons')},
            'blade_runner_suspicious': sus, 'quality': score}


FIX_KEYS = {'catalogue', 'pn', 'mfr', 'op', 'path', 'old', 'new', 'provenance', 'reason'}


def check_fixes_file(R, root, cat, path, work):
    tag = f'fixes.{cat[:-7]}'
    bad, ops = [], {}
    with open(path, encoding='utf-8') as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                bad.append(f'line {n}: blank line'); continue
            try:
                f = json.loads(line)
            except ValueError as e:
                bad.append(f'line {n}: bad JSON {e}'); continue
            if not isinstance(f, dict):
                bad.append(f'line {n}: not an object'); continue
            extra = set(f) - FIX_KEYS
            if extra:
                bad.append(f'line {n}: unknown keys {sorted(extra)}')
            if f.get('catalogue', cat) != cat:
                bad.append(f"line {n}: catalogue {f.get('catalogue')} != file's {cat}")
            for k in ('pn', 'mfr', 'op'):
                if not isinstance(f.get(k), str) or not f[k].strip():
                    bad.append(f'line {n}: {k} missing')
            op = f.get('op'); ops[op] = ops.get(op, 0) + 1
            if op == 'set':
                for k in ('path', 'old', 'new', 'provenance'):
                    if k not in f:
                        bad.append(f'line {n}: set needs {k}')
                if isinstance(f.get('provenance'), dict):
                    bad += [f'line {n}: provenance: {x}' for x in provenance_problems(f['provenance'])]
                elif 'provenance' in f:
                    bad.append(f'line {n}: provenance must be one entry object')
            elif op == 'unset':
                for k in ('path', 'old', 'reason'):
                    if k not in f:
                        bad.append(f'line {n}: unset needs {k}')
            elif op in ('quarantine', 'delete'):
                if not isinstance(f.get('reason'), str) or len(f['reason'].strip()) < 10:
                    bad.append(f'line {n}: {op} needs a reason stating the evidence')
            else:
                bad.append(f'line {n}: op {op!r} not one of set/unset/quarantine/delete')
    R.add(f'{tag}: manifest shape', 'FAIL' if bad else 'PASS',
          '; '.join(bad[:5]) if bad else f'{sum(ops.values())} fixes {ops}')
    if bad:
        return {'ops': ops}
    # dry run against a symlinked scratch data root: data/ is never written
    dr = Path(tempfile.mkdtemp(prefix=f'fixes-{cat[:-7]}-', dir=work))
    (dr / 'data').mkdir()
    for f in (cat, 'quarantine.ndjson'):
        os.symlink(root / 'data' / f, dr / 'data' / f)
    shutil.copyfile(path, dr / 'fixes.jsonl')
    env = dict(os.environ, TAS_ROOT=str(dr))
    r = subprocess.run([sys.executable, str(HERE / 'apply_fixes.py'), '--tas-root', str(root),
                        cat, str(dr / 'fixes.jsonl'), '--dry-run'], capture_output=True, text=True, env=env)
    try:
        st = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        R.add(f'{tag}: apply_fixes dry run', 'ERROR', f'exit {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}')
        return {'ops': ops}
    refusals = {k: v for k, v in st.items() if k in ('unmatched_rows', 'refused_by_gate', 'refused_ambiguous_key',
                                                     'old value differs', 'path missing', 'bad op') and v}
    log = dr / f'fixes.jsonl.{cat}.applied.log'
    why = []
    if log.is_file():
        for l in open(log, encoding='utf-8'):
            d = json.loads(l)
            if d.get('result') not in ('quarantine', 'delete'):
                why.append(f"{d.get('pn')}: {d.get('result')} {d.get('why', '')}"[:160])
    R.add(f'{tag}: apply_fixes dry run', 'FAIL' if (refusals or r.returncode) else 'PASS',
          json.dumps(st) + ('; ' + ' | '.join(why[:5]) if refusals and why else ''))
    return {'ops': ops, 'dry_run': st}


def check_side_file(R, p):
    need = SIDE_FILES[p.name]
    bad, n = [], 0
    for i, line in enumerate(open(p, encoding='utf-8'), 1):
        if not line.strip():
            continue
        n += 1
        try:
            d = json.loads(line)
        except ValueError:
            bad.append(f'line {i}: bad JSON'); continue
        miss = [k for k in need if k not in d]
        if miss:
            bad.append(f'line {i}: missing {miss}')
    R.add(f'{p.name}: shape', 'FAIL' if bad else 'PASS', '; '.join(bad[:5]) if bad else f'{n} rows')


def check_secrets(R, sub):
    hits = []
    for p in sorted(sub.rglob('*')):
        if not p.is_file() or p.parent.name == 'gate' and p.parent.parent == sub:
            continue
        text = p.read_text(encoding='utf-8', errors='replace')
        for name, frag in scan_text(text):
            hits.append(f'{p.relative_to(sub)}: {name} ({frag})')
    R.add('secrets and personal data', 'FAIL' if hits else 'PASS',
          '; '.join(hits[:8]) if hits else 'no tokens, keys, e-mail addresses or home paths')


def finish(R, sub, stats):
    code = R.code()
    verdict = {0: 'PASS', 1: 'FAIL', 2: 'ERROR (a check could not run -- not a pass)'}[code]
    try:
        sha = subprocess.run(['git', '-C', str(HERE), 'rev-parse', '--short=12', 'HEAD'],
                             capture_output=True, text=True).stdout.strip()
    except OSError:
        sha = ''
    out = {'submission': sub.name, 'verdict': verdict, 'tas_head': sha,
           'date': dt.date.today().isoformat(), 'checks': R.checks, 'stats': stats}
    g = sub / 'gate'
    if g.exists():
        shutil.rmtree(g)
    g.mkdir()
    (g / 'summary.json').write_text(json.dumps(out, indent=1, ensure_ascii=False) + '\n')
    md = [f'### validate_submission: {verdict}', '',
          f'Submission `{sub.name}`, TAS `{sha}`, {out["date"]}', '',
          '| check | result | detail |', '|---|---|---|']
    for c in R.checks:
        md.append(f"| {c['check']} | {c['status']} | {c['detail'][:300].replace('|', '/')} |")
    for cat, s in stats.get('candidates', {}).items():
        if s:
            md += ['', f'**{cat}**: {s["rows"]} rows; gate {json.dumps(s["gate"]["accepted"])} accepted; '
                   f'Blade Runner SUSPICIOUS {s["blade_runner_suspicious"] or "none"}; '
                   f'quality (URL term unmeasured) {s["quality"].get("mean_quality_url_unmeasured")}']
    (g / 'SUMMARY.md').write_text('\n'.join(md) + '\n')
    print(f'\n{verdict}  ({sum(c["status"] == "PASS" for c in R.checks)}/{len(R.checks)} checks passed)')
    print(f'Wrote {sub.name}/gate/summary.json and gate/SUMMARY.md')
    sys.exit(code)


if __name__ == '__main__':
    main()
