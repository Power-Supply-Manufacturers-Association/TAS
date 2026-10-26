# Opening the pull request

Needs the GitHub CLI (`gh auth login` once).

```bash
cd ~/PSMA/TAS
git switch main && git pull --ff-only
gh repo fork --remote --remote-name fork          # once: your fork as remote "fork"
git switch -c contrib/<topic>

# your directory, validated (must print PASS)
python3 .claude/skills/tas-contribute/scripts/validate_submission.py incoming/<dir>

git add incoming/<dir>                             # ONLY your directory
git status --short                                 # nothing else staged: no data/, no schemas/
git diff --cached --stat
git commit -m "incoming: <N> <manufacturer> <family> parts (<catalogue>)"
git push fork contrib/<topic>
gh pr create --repo Power-Supply-Manufacturers-Association/TAS --base main \
  --title "incoming: <N> <manufacturer> <family> parts" --body-file pr_body.md
```

- `git check-attr filter -- incoming/<dir>/candidates.x.ndjson` must say `unset`: submission
  files are plain git text, not git-LFS (you cannot write to the LFS store).
- If `main` moved while your PR is open, rebase and re-run `validate_submission.py` (new
  live parts may now duplicate yours).
- Never push to `main`, never include changes outside `incoming/<dir>`.

## PR body template (`pr_body.md`)

```markdown
## What
<N> new <catalogue> records / <M> fixes for <manufacturer> <family>.

## Sources
<manufacturer pages / tables / datasheets; route>

## Counts
| catalogue | input | searched | accepted | not found | incomplete | out of scope |
|---|---|---|---|---|---|---|

## Checks
<paste incoming/<dir>/gate/SUMMARY.md here>

## Hand-check and controls
<10-row hand-check result; fabricated-code and real-obsolete controls>

## Doubts
<from REPORT.md, or "None">

- [ ] every value is printed by the manufacturer (or derived with a one-line formula)
- [ ] validate_submission.py prints PASS on this commit
- [ ] only `incoming/<dir>` is changed; no data/, schema or tool edits
- [ ] no tokens, cookies, e-mail addresses or local paths anywhere
```

## Schema changes: open an issue, never edit

Contributors never edit a schema (the schemas are a contract shared by many repos and
consumers). When a value has no place to go -- a missing field, a missing enum value, a
condition the schema cannot hold -- leave the value out of your records, mention it under
Doubts, and open an issue on the schema repo that owns the family (CAS for capacitors, SAS
for semiconductors, MAS for magnetics, RAS, CTAS, AAS, CONAS, TDAS, EMAS, TMAS, PEAS for
shared types):

```bash
gh issue create --repo Power-Supply-Manufacturers-Association/<SCHEMA_REPO> \
  --title "Proposal: <field/enum> for <what>" --body-file issue.md
```

```markdown
## Gap
<what the datasheets print that no field can hold; 2-3 example parts with document links>

## Proposal
<field name, type, unit, where in the schema; or the enum value>

## Why not an existing field
<the closest field and why it is a different quantity or condition>

## Impact
<how many parts you have that would use it>
```

Do not put the value in a different field, a name field or a note while you wait.

For a suspected bug in a gate or in Blade Runner (a rejection you believe is wrong), open an
issue on TAS with the record, the gate output and the vendor document; keep the row out of
your PR until it is resolved.
