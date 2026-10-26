#!/usr/bin/env python3
"""MAINTAINERS ONLY: re-gate a merged/checked-out contribution and ingest it into data/.

    python3 ingest_submission.py incoming/<dir> [--tas-root TAS] [--work-dir DIR]
                                 [--apply] [--allow-twins FILE] [OLD=NEW ...]

Without --apply (the default) nothing in data/ is touched: it re-runs validate_submission.py
against the CURRENT live catalogues (they may have moved since the contributor ran it),
re-gates every candidates file in a fresh directory and dry-runs every fixes manifest, then
prints what --apply would do.

With --apply, after the same re-gate it:
  * applies each fixes.<catalogue>.jsonl with apply_fixes.py (flock, verified backup in
    <TAS>/.backups/, streaming rewrite; a fix that would add a gate or Blade Runner
    finding is refused and logged),
  * appends each re-gated accepted.ndjson with ingest.py (flock, backup, duplicate re-check
    under the lock, prefix-bytes and line-count verification). Spelling twins refuse the
    whole ingest unless listed in --allow-twins (only after checking the vendor prints both
    spellings). OLD=NEW renames manufacturer names on the way in.
It stops at the first failure. After every step succeeded it deletes incoming/<dir> (the
directory goes away in the same commit as the ingested data; git history keeps it). It never
commits: review `git diff --stat`, then commit the
catalogue changes (batch several submissions per commit -- every commit of a large LFS
catalogue stores a full copy).

Run one ingest at a time: each step streams a whole catalogue.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _common import SetupError, find_tas_root, pop_tas_root_arg  # noqa: E402


def run(cmd, env=None):
    print('$ ' + ' '.join(str(c) for c in cmd), flush=True)
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env)
    sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
    return r


def main():
    try:
        root = find_tas_root(pop_tas_root_arg())
    except SetupError as e:
        sys.exit(f'ERROR: {e}')
    os.environ['TAS_REPO'] = str(root)
    args = sys.argv[1:]
    apply = '--apply' in args
    args = [x for x in args if x != '--apply']
    work = allow = None
    for flag in ('--work-dir', '--allow-twins'):
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                sys.exit(f'{flag} needs a value')
            val = args[i + 1]; del args[i:i + 2]
            if flag == '--work-dir':
                work = val
            else:
                allow = val
    if not args:
        sys.exit(__doc__)
    sub = Path(args[0]).resolve()
    renames = args[1:]
    if any('=' not in r for r in renames):
        sys.exit(f'unexpected arguments {renames}: renames are OLD=NEW')
    if os.environ.get('TAS_ROOT'):
        sys.exit('TAS_ROOT is set: it redirects the data root (tooling tests only). Unset it to ingest.')

    # 1. full validation against the current live data
    r = run([sys.executable, HERE / 'validate_submission.py', '--tas-root', root, sub])
    if r.returncode != 0:
        sys.exit(f'validate_submission exit {r.returncode}: not ingesting')

    w = Path(work).resolve() if work else Path(tempfile.mkdtemp(prefix='tas-ingest-'))
    w.mkdir(parents=True, exist_ok=True)
    plan = []
    for p in sorted(sub.glob('fixes.*.jsonl')):
        cat = p.name.split('.')[1] + '.ndjson'
        dst = w / f'fixes.{cat[:-7]}.jsonl'
        shutil.copyfile(p, dst)
        plan.append(('fixes', cat, dst))
    for p in sorted(sub.glob('candidates.*.ndjson')):
        cat = p.name.split('.')[1] + '.ndjson'
        fresh = Path(tempfile.mkdtemp(prefix=f'regate-{cat[:-7]}-', dir=w))
        shutil.copyfile(p, fresh / 'candidates.ndjson')
        r = run([sys.executable, HERE / 'check_candidates.py', '--tas-root', root,
                 '--catalogue', cat, fresh / 'candidates.ndjson'])
        try:
            gs = json.loads(r.stdout[r.stdout.index('{'):])
        except ValueError:
            sys.exit(f'{cat}: the gate produced no summary: not ingesting')
        if r.returncode != 0 or gs.get('rejected') or gs.get('batch_refusal_count'):
            sys.exit(f'{cat}: re-gate rejected rows (see {fresh}/rejected.ndjson): not ingesting')
        n_in = sum(1 for l in open(p, encoding='utf-8') if l.strip())
        if gs.get('accepted') != n_in:
            sys.exit(f'{cat}: accepted {gs.get("accepted")} of {n_in} rows: not ingesting')
        plan.append(('ingest', cat, fresh / 'accepted.ndjson'))

    if not apply:
        print('\nDRY RUN (no --apply). Would run, in order:')
        for kind, cat, f in plan:
            print(f'  {kind:7s} {cat}  <- {f}')
        print(f'Work files kept in {w}')
        return
    for kind, cat, f in plan:
        if kind == 'fixes':
            r = run([sys.executable, HERE / 'apply_fixes.py', '--tas-root', root, cat, f])
            if r.returncode != 0:
                sys.exit(f'apply_fixes {cat} failed: stop')
            st = json.loads(r.stdout.strip().splitlines()[-1])
            bad = {k: v for k, v in st.items() if k in ('unmatched_rows', 'refused_by_gate',
                                                         'refused_ambiguous_key', 'old value differs',
                                                         'path missing') and v}
            if bad:
                sys.exit(f'apply_fixes {cat} applied with refusals {bad}: inspect {f}.{cat}.applied.log '
                         'and the backup in .backups/ before going on')
        else:
            cmd = [sys.executable, HERE / 'ingest.py', '--tas-root', root, cat, f, *renames]
            if allow:
                cmd += ['--allow-twins', allow]
            r = run(cmd)
            if r.returncode != 0:
                sys.exit(f'ingest {cat} failed: stop')
    shutil.rmtree(sub)
    print(f'\nIngested; removed {sub.name}. Next: run changed_records_gate.py on the touched '
          'catalogues, review `git status` / `git diff --stat` (the catalogues plus the deleted '
          'incoming dir), then commit them together.')


if __name__ == '__main__':
    main()
