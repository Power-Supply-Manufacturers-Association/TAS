# Picking a unit of work

A **unit** is one manufacturer and one product family (or one catalogue), small enough to
finish and hand-check: a few hundred to a few thousand parts. Mixed units are fine if you
split the output per catalogue (`candidates.<catalogue>.ndjson`).

## What is worth doing

- **New parts** the manufacturer lists and TAS lacks (current or obsolete, as long as the
  manufacturer's own page or datasheet still prints them).
- **Fixes** to live parts: a value that disagrees with the manufacturer's document, a missing
  field the document prints, a citation that does not name the part. See the fixes format in
  [submission-format.md](submission-format.md).
- Check open GitHub issues on the TAS repo first; someone may already be on that vendor.

## Before searching anything

1. Make a work list (JSONL `{"mpn": ..., "manufacturer": ...}` or one part number per line)
   from the manufacturer's own catalogue listing.
2. Drop what TAS already has:
   `python3 scripts/filter_known.py worklist.jsonl myunit/` -> `myunit/input.jsonl`.
   This is a coarse lead filter (spaces, `-`, `_`, `/` ignored); the gate does the exact
   duplicate check later.
3. Look for earlier work on the same vendor: past `incoming/*-<vendor>-*` directories and
   their `not_found.jsonl`. **Do not search again for parts already reported not found**
   unless you have a new route (for example a parametric export the earlier unit did not use).
   Report the split in REPORT.md: new to search vs already not found, and your route.
4. Look at live records of the same manufacturer and family in `data/<catalogue>.ndjson`:
   their shape is what you will produce.

## Output per unit (see submission-format.md)

- `candidates.<catalogue>.ndjson` -- new records that pass every check.
- `fixes.<catalogue>.jsonl` -- changes to existing records.
- `not_found.jsonl` -- parts the manufacturer does not list (`{mpn, manufacturer, checked_url}`).
- `out_of_scope.jsonl` -- not this catalogue's component type (`{mpn, manufacturer, found_category}`).
- `incomplete.jsonl` -- real parts you could not record honestly (`{mpn, manufacturer, reason}`).
- `suspects.jsonl` -- live records whose stored values match nothing the document prints
  (`{mpn, manufacturer, reason}`); never fix those inline.
- `REPORT.md` -- written as you go, so a stop never loses finished, checked work.

Work in waves of a few thousand parts; update REPORT.md after each wave.
