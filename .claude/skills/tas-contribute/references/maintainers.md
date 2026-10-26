# Maintainers: ingesting a submission, publishing Blade Runner binaries

## Review

1. The PR changes only `incoming/<dir>/`. Read REPORT.md, the hand-check and controls.
2. Spot-check a few rows yourself against the cited vendor documents (citation names the
   part, values match, conditions right).
3. If the submission has `evidence.jsonl` (documents the contributor registered so that a
   citation whose URL does not spell the part could count as `partNamed`), for each line:
   1. **fetch** the `url` yourself (polite UA, robots.txt) into a scratch dir;
   2. **hash check**: `sha256sum` of the fetched bytes must equal `documentSha256`; if not,
      the document changed or the line is wrong -- ask the contributor, do not register;
   3. **prints the part**: confirm the document text prints each part in `parts`;
   4. **register**: `python3 scripts/register_doc_evidence.py --url URL --file FILE
      --retrieved YYYY-MM-DD`, then `--check URL PART` on a listed part (must be named) and on
      a made-up part (must NOT be named);
   5. **re-gate**: only now run `ingest_submission.py` (it re-gates against your store).
4. Merge (or check out the PR branch locally).

## Re-gate and ingest

```bash
S=.claude/skills/tas-contribute/scripts
python3 $S/ingest_submission.py incoming/<dir>              # dry run: re-validates against
                                                            # TODAY's live data, re-gates in
                                                            # a fresh dir, dry-runs fixes
python3 $S/ingest_submission.py incoming/<dir> --apply [--allow-twins FILE] [OLD=NEW ...]
```

`--apply` runs `apply_fixes.py` (flock, verified backup in `.backups/`, streaming rewrite,
refuses a fix that would add a gate/Blade Runner finding) and then `ingest.py` (flock,
backup, duplicate re-check under the lock, prefix bytes and line count verified) for each
catalogue, stopping at the first failure. When every step succeeded it deletes
`incoming/<dir>`, so the directory leaves the repo in the same commit as the ingested data
(git history keeps it); it never commits. Spelling twins (same part number up to `-`, `_`,
`/`, spaces) refuse the whole ingest unless listed in `--allow-twins` after you checked the
vendor prints both spellings. `OLD=NEW` renames manufacturer names on the way in.

Then:
- `python3 scripts/changed_records_gate.py` (every record of every touched catalogue, schema
  + Blade Runner; exit 2 means it could not run, not a pass);
- `git diff --cached --stat` before committing: in a shared checkout stage only the
  catalogues you ingested;
- commit the catalogue changes together with the deletion of `incoming/<dir>`, batching several submissions per commit (every commit of a
  large LFS catalogue stores a full copy of it). Run ingests one at a time.

`TAS_ROOT` redirects the data root for testing the tools only; `ingest_submission.py`
refuses to run with it set.

## Publishing Blade Runner release binaries

After a change to `validator/` lands on `main`, on Linux x86_64 with CPython 3.12:

```bash
bash $S/build_blade_runner.sh --with-tests         # or an existing fresh validator/build
bash $S/package_release.sh --out /tmp/br-release   # refuses dirty sources or a stale build
# then run the printed: gh release create blade-runner-<sha> ...tar.gz ...tar.gz.sha256 --repo ...
```

The tag is `blade-runner-<first 12 hex of the last commit that changed validator/src,
validator/metrics, validator/tools or validator/CMakeLists.txt>`; `fetch_blade_runner.sh`
computes the same tag from a contributor's checkout, verifies the sha256 and then the
source freshness of what it installed.
