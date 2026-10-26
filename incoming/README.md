# incoming/ -- contributed parts waiting for ingest

Outside contributors do not edit `data/*.ndjson`. They add one directory here per pull request:

```
incoming/<YYYY-MM-DD>-<github-handle>-<topic>/
  REPORT.md                       sources, counts, field coverage, hand-check, controls, doubts
  candidates.<catalogue>.ndjson   new parts, one schema-valid record per line
  fixes.<catalogue>.jsonl         changes to existing parts (set / unset / quarantine / delete)
  not_found.jsonl, out_of_scope.jsonl, incomplete.jsonl, suspects.jsonl, evidence.jsonl   (optional)
  gate/summary.json, gate/SUMMARY.md   written by validate_submission.py
```

Every value comes from the manufacturer's own page, parametric table or datasheet, with a
provenance entry pointing at it. Before opening the PR the contributor runs

```bash
python3 .claude/skills/tas-contribute/scripts/validate_submission.py incoming/<dir>
```

which must print PASS: schema validation, Blade Runner (the physics validator), the
candidates gate (duplicates against the live catalogue, the quarantine and the
fabricated-parts denylist, plus the ingest-gate rules), the provenance shape, a dry run of any
fixes, and a scan for secrets and personal data.

A maintainer reviews the PR, re-gates the directory against the live catalogues of that day
in a fresh directory, and ingests it with
`.claude/skills/tas-contribute/scripts/ingest_submission.py`, which deletes the directory
after a successful ingest: the ingest commit adds the parts to `data/` and removes
`incoming/<dir>` together (git history keeps the submission). Files here are plain git
text (not git-LFS).

How to contribute: `.claude/skills/tas-contribute/SKILL.md` (a Claude Code skill; the
`references/` files beside it are plain Markdown and read fine without Claude).
