#!/usr/bin/env python3
"""Quarantine the last three series-stub rows left in data/magnetics.ndjson
(ABT #351 class A tail).

    python3 scripts/quarantine_series_stubs.py --dry-run
    python3 scripts/quarantine_series_stubs.py

After the Vanguard rescales, eight magnetics rows still held a rated current above
1000 A. Five are legitimate — iNRCORE PL32xx CURRENT-SENSE TRANSFORMERS, where the
rated current is the PRIMARY current. The other three are not unit errors at all;
they are SERIES STUBS: one record standing for a whole family, with the family's
range smashed into a single part's fields.

    CVH201610A       L nominal 2.2 uH, minimum 2.2 uH, MAXIMUM 0.47   I "1200"   no DCR
    CVH201610A-2R2M  L nominal 2.2 uH, minimum 1.76,  maximum 2.64    I 1.2 A    DCR 0.11

The stub's "maximum 0.47" is the 0.47 uH VARIANT's inductance, not a tolerance —
the series spans 0.47 to 2.2 uH and got flattened into one row. Its "1200" is
1200 mA, which is exactly the 1.2 A its real sibling carries. All three proper
per-part rows (CVH201610A-1R0M / -2R2M / -R47M, at 1.4 / 1.2 / 1.6 A with real
DCRs) are already in the corpus, so the stub is a duplicate that adds nothing and
misleads anything that reads it.

CVH160808H and 152901 are the same shape with no surviving siblings; 152901 also
carries a 0.35 H inductance, which no Bourns chip part has.

These are quarantined rather than rescaled: fixing the current would leave an
unorderable part number in the catalog claiming to be a part. Same treatment as
the B82796* wildcards dropped from the TDK pull and the 2016 synthetic-MPN rows.
Quarantine, don't delete — traceability is preserved with a quarantineReason.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
QUARANTINE = REPO / "data" / "magnetics.quarantine_stubs.ndjson"

STUBS = {
    "CVH201610A": "Series stub, not an orderable part: inductance minimum 2.2 uH with maximum 0.47 "
                  "is the 0.47 uH variant's value, i.e. the whole CVH201610A family (0.47-2.2 uH) "
                  "flattened into one row, and ratedCurrents 1200 is 1200 mA. The real per-part rows "
                  "CVH201610A-1R0M / -2R2M / -R47M are already in the corpus at 1.4 / 1.2 / 1.6 A "
                  "with real DC resistances. ABT #351.",
    "CVH160808H": "Series stub, not an orderable part: inductance minimum 2.2 uH with maximum 0.24 "
                  "flattens a family into one row, and ratedCurrents 1300 is 1300 mA. No per-part "
                  "sibling rows survive for this family. ABT #351.",
    "152901": "Series stub / unusable row: inductance nominal = minimum = maximum = 0.35 H, which no "
              "Bourns chip inductor carries, and ratedCurrents 1130 is 1130 mA. No per-part sibling "
              "rows. ABT #351.",
}


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    moved, kept = [], 0

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            ref = None
            if b"Bourns" in raw:
                try:
                    ref = json.loads(raw)["magnetic"]["manufacturerInfo"].get("reference")
                except Exception:
                    ref = None
            if ref in STUBS:
                moved.append((ref, json.loads(raw)))
                continue
            out.write(raw)
            kept += 1
        out.flush()
        os.fsync(out.fileno())

    print(f"kept {kept} rows, moving {len(moved)} series stubs to quarantine")
    for ref, _ in moved:
        print(f"  {ref}")
    if len(moved) != len(STUBS):
        found = {r for r, _ in moved}
        print(f"ABORT: expected {sorted(STUBS)}, found {sorted(found)}")
        tmp.unlink(missing_ok=True)
        return 1

    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
        return 0

    with open(QUARANTINE, "a") as q:      # append: never rewrite a quarantine file
        for ref, rec in moved:
            q.write(json.dumps({**rec, "quarantineReason": STUBS[ref]}, separators=(",", ":")) + "\n")
    os.replace(tmp, DATA)
    print(f"\nquarantined -> {QUARANTINE}\nrewrote {DATA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
