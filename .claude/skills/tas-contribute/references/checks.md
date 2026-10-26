# Checks

Every check below must pass; none may be weakened. A rejection you believe is wrong is a
finding: write it in REPORT.md and the PR, and leave the row out.

## 1. The candidates gate, in a fresh directory, every time

The gate overwrites `DIR/accepted.ndjson` and `DIR/rejected.ndjson`, so give it a new
directory per run:

```bash
S=TAS/.claude/skills/tas-contribute/scripts
mkdir -p myunit/rg_capacitors_1
cp myunit/candidates.capacitors.ndjson myunit/rg_capacitors_1/candidates.ndjson
python3 $S/check_candidates.py --catalogue capacitors.ndjson myunit/rg_capacitors_1/candidates.ndjson
```

Per row it runs: identity, manufacturer present, denylist (exact and spelling twins),
already live or quarantined, ordering-code variants, duplicates inside the batch, inverted
ranges, unsourced status, then the ingest gate's rules -- identity is real, the citation
names the part (or the claim is downgraded), SI units and physically possible magnitudes,
schema (an empty required object counts as missing), seed-expansion signatures -- and
Blade Runner IMPOSSIBLE findings. Then batch rules over the accepted rows: minted constants
(a byte-identical value across the batch that the parts' own descriptions refute) and
arithmetic ladders (several unrelated fields affine in a part index). It prints a JSON
summary; `batch_refusal_count` must be 0 and every remaining reject explained.

Fix the CAUSE in your extraction code (wrong column, wrong condition, wrong citation claim)
and regenerate; never patch values or claims by hand to pass.

### Citations whose URL does not spell the part

Rule 2 accepts a series PDF or document-id URL as `partNamed` only when a cached copy of
that exact document, whose text prints the exact part number, is registered in the local
document-evidence store:

```bash
python3 TAS/scripts/register_doc_evidence.py --url URL --file doc.pdf --retrieved YYYY-MM-DD
python3 TAS/scripts/register_doc_evidence.py --check URL REAL_PART     # must say named
python3 TAS/scripts/register_doc_evidence.py --check URL FAKE_PART     # control: must NOT
```

The store is local (not committed). List every document you registered in the submission's
`evidence.jsonl` (`{url, documentSha256, parts}`) so the maintainer can fetch and register
the same document before re-gating; otherwise stamp such entries `seriesConfirmed`.

## 2. Pilot first

Before a full run: pilot about 200 parts, gate them, and **hand-check 10 random accepted
records** against the vendor page or PDF (every stored field, units, conditions). Report
what you found, including mismatches you then fixed.

## 3. Fabrication controls (Blade Runner cannot see fabrication)

Blade Runner checks physics. Invented numbers that are physically plausible pass it, and
fake parts with plausible values pass every pattern rule. So also run, and report:

- **Fabricated-code control:** make up a part code in the vendor's own grammar and run it
  through your existence route (lookup, product URL, listing). It must come back not found.
  Also run a real but obsolete part: it must be found (or you know the route only covers the
  active catalogue and cannot condemn anything).
- **Clone test:** serialise each record with its part number masked and hash it. A family
  whose members collapse to one hash is a copy, not a product line. (Over-fires across a
  whole file where one die ships in many packages; decisive within a suspect cohort.)
- **I/O letter test:** a contiguous single-letter suffix run (A, B, C, ...) that includes
  I or O is suspect: vendors avoid both as confusable with 1 and 0. Contiguity alone is NOT
  evidence (real diode voltage-code alphabets skip letters; many real ladders are
  contiguous).
- **Naming grammar:** the stem and suffix set must exist at that vendor.
- **Value re-read:** any step that moves or rewrites a stored value compares it with the
  printed one; a stored value that matches nothing printed goes to `suspects.jsonl`, never
  silently fixed.

## 4. validate_submission.py (the PR gate)

```bash
python3 TAS/.claude/skills/tas-contribute/scripts/validate_submission.py incoming/<dir>
```

It first proves its own checks fire on planted bad inputs (Blade Runner on an impossible
capacitor, schema on an unknown key, the denylist on a known fabricated id, provenance on an
extra key and on scrape narrative in `verificationMethod`, the secrets scan on a planted token), then runs: sibling repos present,
catalogues are real data, Blade Runner fresh; directory name and files; REPORT.md
sections; no git-LFS filter on the files; per candidates file: wrapper/identity, schema,
Blade Runner on every row (IMPOSSIBLE fails, SUSPICIOUS listed -- explain them in REPORT.md),
provenance shape, and the candidates gate in a fresh directory (every row must be
accepted); per fixes file: manifest shape and an `apply_fixes.py --dry-run` against the live
catalogue (unmatched 0, no refusals); side-file shapes; secrets and personal data. It also
reports the rows' quality score from `tas_metrics` (informative). Exit 0 = PASS, 1 = a
check failed, 2 = a check could not run, which is NOT a pass.

It writes `gate/summary.json` and `gate/SUMMARY.md` into the submission; commit them with
it and paste SUMMARY.md into the PR.

## Never

- Never weaken, skip or edit a check, a threshold or a test to get green.
- Never change a value, a citation claim or a part's design to make it fit.
- Never offer a check that did not run as a pass.
