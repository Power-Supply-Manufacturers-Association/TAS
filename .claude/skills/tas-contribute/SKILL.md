---
name: tas-contribute
description: Contribute electronic-component parts (capacitors, resistors, diodes, MOSFETs, magnetics, connectors, controllers, analog ICs, ...) to the TAS component database, or fixes to parts already in it, sourced only from the manufacturer's own data, checked with the same gates the maintainers use, and submitted as a GitHub pull request. Use when someone wants to add parts to TAS, correct a TAS record, or prepare an incoming/ submission.
---

# Contributing parts to the TAS component database

TAS (`Power-Supply-Manufacturers-Association/TAS`) holds about a million real, orderable
power-electronics parts as NDJSON catalogues under `data/`, each record validated against the
PSMA JSON schemas (PEAS, CAS, SAS, RAS, MAS, ...). **A clean database matters more than a big
one**: a part you cannot source from its manufacturer is left out, never guessed.

Contributors never edit `data/*.ndjson`, never edit a schema, and never commit to `main`.
You put small submission files in `incoming/<YYYY-MM-DD>-<github-handle>-<topic>/` and open a
pull request; a maintainer re-runs the gates and ingests them.

## Workflow

1. **Set up once** -- [references/setup.md](references/setup.md). Clone TAS and its sibling
   schema repos side by side, pull the catalogues from git-LFS, get Blade Runner:
   `bash scripts/setup_workspace.sh ~/PSMA --catalogues capacitors` then
   `bash scripts/fetch_blade_runner.sh` (or `build_blade_runner.sh`).
2. **Pick a unit of work** -- [references/picking-work.md](references/picking-work.md). One
   manufacturer + one family per unit. Filter out parts TAS already has
   (`scripts/filter_known.py`) and do not re-search parts already reported not found.
3. **Source the data** -- [references/sourcing.md](references/sourcing.md). Only the
   manufacturer's own product page, parametric table or datasheet is evidence. Polite
   fetching: robots.txt, one fetcher per host, about one request per 2 s, the generic
   User-Agent `TAS-catalogue-research/1.0`, no personal data in any request.
4. **Build records** -- [references/data-rules.md](references/data-rules.md). Copy the shape
   of live records of the same family. SI units, the discriminator wrapper, provenance as a
   reference (`{source, sourceName, sourceUrl, retrievedDate, fields, verification,
   verificationDate}`), no invented values, no defaults, omit what you do not know.
   Read the traps section before mapping any vendor column.
5. **Check** -- [references/checks.md](references/checks.md). Pilot about 200 parts, gate
   them in a fresh directory, hand-check 10 against the vendor document, run the fabrication
   controls, fix the cause of every rejection (never weaken a check), then do the full run.
6. **Package and validate** -- [references/submission-format.md](references/submission-format.md).
   Write the incoming/ directory, then
   `python3 .claude/skills/tas-contribute/scripts/validate_submission.py incoming/<dir>`.
   It must print `PASS`.
7. **Open the pull request** -- [references/pull-request.md](references/pull-request.md).
   Fork, branch, add only your incoming/ directory, `gh pr create` with the body template.

Need a schema change (a field with no place to go, an enum value missing)? Do not change the
schema and do not squeeze the value into another field. Open a GitHub issue on the schema
repo proposing it (template in [references/pull-request.md](references/pull-request.md)) and
leave the value out of your records.

Maintainers: [references/maintainers.md](references/maintainers.md) (re-gate + ingest a
merged submission, publish Blade Runner release binaries).

## The rules that are never bent

- **No invented parts and no invented values.** Every part number is one the manufacturer
  lists; every number is one it prints. Unknown means omitted. A value computed from other
  values carries a `derived` provenance entry with its one-line formula.
- **Evidence is the manufacturer.** Distributor pages, aggregators and datasheet archives are
  leads for where to look, never the source of a value or proof a part exists.
- **No fallbacks.** No default, typical or family-wide value standing in for a missing one;
  no placeholder; no sentinel. A record that cannot be completed honestly stays out
  (`incomplete.jsonl`) and the reason goes in REPORT.md.
- **A failing check is a finding.** Fix the cause in your extraction code. Never edit a
  value, a citation claim or a check to get a row through. If you think a check is wrong,
  say so in REPORT.md and the PR; do not route around it.
- **Fabricated data is deleted, never kept.** Real parts with a data problem may be
  quarantined with a reason; invented ones are removed.
- **Nothing private in the submission**: no tokens, keys, cookies, e-mail addresses or
  local paths. `validate_submission.py` scans for them and fails.
- **Be a polite crawler.** robots.txt is binding, also on API hosts. One fetcher per host.
  Back off on 403/429. Keep caches small.
- If your process is killed for low memory, wait a random 10-60 minutes and retry that step.

## Scripts (in `scripts/`)

| script | purpose |
|---|---|
| `setup_workspace.sh` | clone TAS + sibling schema repos, git-LFS pull, venv, schema smoke test |
| `fetch_blade_runner.sh` | download prebuilt `tas_validator` + `tas_metrics` (Linux x86_64, CPython 3.12), verify sha256 + source freshness |
| `build_blade_runner.sh` | build them from `validator/` (CMake + Ninja, 3 jobs) |
| `filter_known.py` | drop part numbers TAS already has (live, quarantined, denylisted) from a work list |
| `check_candidates.py` | the candidates gate: ingest-gate rules, Blade Runner, denylist, duplicates, batch rules |
| `validate_submission.py` | every check a PR must pass; writes `gate/summary.json` + `gate/SUMMARY.md` |
| `apply_fixes.py`, `ingest.py`, `ingest_submission.py`, `package_release.sh` | maintainer tools |

All scripts find the TAS checkout from their own location, `--tas-root DIR`, or `TAS_REPO`,
and the sibling schema repos from its parent directory.
