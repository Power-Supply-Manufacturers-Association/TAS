# Submission format

One pull request = one directory:

```
incoming/<YYYY-MM-DD>-<github-handle>-<topic>/
  REPORT.md                       required
  candidates.<catalogue>.ndjson   new parts, one record per line (catalogue = capacitors, diodes, ...)
  fixes.<catalogue>.jsonl         changes to existing parts
  not_found.jsonl                 optional side files (shapes below)
  out_of_scope.jsonl
  incomplete.jsonl
  suspects.jsonl
  evidence.jsonl                  documents you registered as evidence (checks.md)
  gate/summary.json, gate/SUMMARY.md   written by validate_submission.py
```

- The date is the day you open the PR, `<github-handle>` your GitHub user name, `<topic>`
  lower-case words with hyphens (`kemet-mil-c0g`, `vishay-tvs-fixes`).
- At least one candidates or fixes file. Nothing else in the directory, no subdirectories
  besides `gate/`, each file under 50 MB (split a bigger unit into several PRs).
- Never a copy of, or an edit to, `data/*.ndjson`. Never schema files.

## candidates.<catalogue>.ndjson

Exactly the records you want added, every one accepted by the gate. Shape and rules:
[data-rules.md](data-rules.md).

## fixes.<catalogue>.jsonl

One JSON object per line. Rows are matched by normalised part number + exact manufacturer
name; where the live catalogue holds several spellings under one key (hyphen twins), the fix
applies only to the exact `pn` spelling.

```json
{"catalogue": "diodes.ndjson", "pn": "1SS403E", "mfr": "Toshiba", "op": "set",
 "path": "semiconductor.diode.manufacturerInfo.datasheetInfo.electrical.junctionCapacitance",
 "old": 3e-12, "new": 2.5e-12,
 "provenance": {"source": "manufacturerDatasheet", "sourceName": "Toshiba 1SS403E datasheet",
   "sourceUrl": "https://...", "retrievedDate": "2026-10-26",
   "fields": ["electrical.junctionCapacitance"], "verification": "valuesReadFromSource",
   "verificationDate": "2026-10-26"}}
{"pn": "...", "mfr": "...", "op": "unset", "path": "...", "old": 0.1, "reason": "value printed for a different package"}
{"pn": "...", "mfr": "...", "op": "quarantine", "reason": "datasheet prints 8 mOhm / 1155 pF; stored values match nothing printed"}
{"pn": "...", "mfr": "...", "op": "delete", "reason": "vendor lists no such part: product URL 404 while real-obsolete control resolves; vendor selector has no entry"}
```

| op | required keys | effect |
|---|---|---|
| `set` | `pn, mfr, path, old, new, provenance` | `path` is dotted from the record root (wrapper included, list indexes as numbers); `old` must equal the live value (`null` to add a field whose parent exists). The provenance entry is appended; older entries lose their claim on that field. |
| `unset` | `pn, mfr, path, old, reason` | removes the field |
| `quarantine` | `pn, mfr, reason` | real part, broken data: moves to the quarantine with the reason |
| `delete` | `pn, mfr, reason` | fabricated part or data: removed outright |

Allowed keys are exactly `catalogue, pn, mfr, op, path, old, new, provenance, reason`. A fix
whose result would gain a gate or Blade Runner finding the record did not already have is
refused. The `reason` states the evidence in one or two plain facts; no narrative about
tools or people.

## Side files

| file | keys per line |
|---|---|
| `not_found.jsonl` | `mpn, manufacturer, checked_url` |
| `out_of_scope.jsonl` | `mpn, manufacturer, found_category` |
| `incomplete.jsonl` | `mpn, manufacturer, reason` |
| `suspects.jsonl` | `mpn, manufacturer, reason` |
| `evidence.jsonl` | `url, documentSha256, parts` |

## REPORT.md

Use these headings (the validator requires each section; replace every `<fill ...>`):

```markdown
# <fill: manufacturer + family>

## Sources
<fill: the manufacturer pages / tables / datasheets used, and the route (export, product pages, PDFs)>

## Counts
<fill: per catalogue: input / already not found / searched / candidates / accepted / rejected (reasons) / not found / incomplete / out of scope; fixes per op>

## Field coverage
<fill: which fields are filled for how many rows; fields the documents print that the schema has no place for>

## Hand-check
<fill: 10 random accepted rows (and fixes) compared with the vendor document: which, what matched, what you fixed>

## Controls
<fill: fabricated-code control and real-obsolete control on your existence route; clone / letter tests if a family looked generated; doc-evidence --check on a fake part if you registered evidence>

## Doubts
<fill: anything uncertain, gate rejections you think are wrong, Blade Runner SUSPICIOUS findings explained, hyphen twins, proposed schema issues; "None" if none>
```

Counts and facts only: the report is read by the maintainer reviewing your PR.
