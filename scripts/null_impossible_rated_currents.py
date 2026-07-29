#!/usr/bin/env python3
"""Null the provably-impossible ratedCurrents (and inductances) that ABT #351
class A identifies in data/magnetics.ndjson.

    python3 scripts/null_impossible_rated_currents.py --dry-run
    python3 scripts/null_impossible_rated_currents.py

CLASS A: the rated current is the part number's digits.

    SM453230-121N7Y  ratedCurrents [453230.0] A
    PT61017XPEL      ratedCurrents [61017.0] A
    SM91071AL        ratedCurrents [91071.0] A

173 rows carry a ratedCurrent above 1000 A, but ONLY 38 of them are touched here:
the ones where the number appears VERBATIM IN THE PART NUMBER, which is proof it
is an identifier rather than a measurement. A magnitude threshold on its own is
not evidence, and using one would have done real damage to the other 135:

  * 5 iNRCORE PL32xx rows are CURRENT-SENSE TRANSFORMERS rated 2000 A. For a CT
    that is the PRIMARY current and it is entirely legitimate. A ">1000 A is
    impossible" rule would have destroyed correct data.
  * 118 rows sit at 1000-5000 A and look like MILLIAMPS STORED AS AMPS —
    CVH201610A at "1200 A" is a 2.2 uH 2016-size inductor, i.e. almost certainly
    1200 mA. That is a unit error, a different defect with a different fix, and
    it is not provable from the part number. Dividing by 1000 would be a guess.
  * 12 more at 5k-50k A (AC2-13K at "6000 A") are unexplained; likewise left.

Those three groups are reported on ABT #351 as separate sub-classes rather than
swept into this repair.

Rows in the provable set that additionally carry an inductance above 1 H
(SM51625EL: 10 H nominal, 1000 H maximum) lose that too — a 1000 H inductor is
not a catalogue part either.

WHAT IS DONE: the impossible fields are REMOVED. The row, its real part number
and everything else on it are kept, so a later vendor-direct re-source can refill
them — the same treatment as the Laird repair. Nothing is guessed: the MPN digits
tell you what the number IS (an identifier), not what the rated current SHOULD be.

DELIBERATELY NOT DONE: the wider inductance-tolerance problem visible on some of
these rows (CVH201610A: nominal 2.2 uH, maximum 0.47 H — a 200,000x spread) is a
DIFFERENT signature and is left alone. Fixing it here would mean widening a
targeted repair into an unreviewed audit; it is noted on #351 instead.

The write is an IN-PLACE BYTE PATCH: removing fields only ever shortens a line,
so each edited line is written back at its exact original byte length, padded with
spaces before the newline. No temp file, no rename, no truncation — safe against
concurrent appends. Every edited record is re-validated against the MAS schema
before the write, and the run aborts rather than write anything that would not
validate.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_tdk_cmc import _build_registry, _load_magnetic_schema  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "impossible_rated_currents_audit.json"

MAX_PLAUSIBLE_A = 1000.0     # no catalogue magnetic is rated in kiloamps
MAX_PLAUSIBLE_H = 1.0        # nor is a 1000 H inductor a catalogue part


def digits_in_mpn(value: float, reference: str) -> bool:
    """True when the number appears verbatim in the part number — i.e. it is
    demonstrably an identifier that landed in a measurement field."""
    return str(int(value)) in (reference or "").replace("-", "").replace(" ", "")


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    validator = _load_magnetic_schema(_build_registry())
    size_before = DATA.stat().st_size

    targets, off = [], 0
    with open(DATA, "rb") as f:
        for raw in f:
            n = len(raw)
            try:
                rec = json.loads(raw)
                info = rec["magnetic"]["manufacturerInfo"]
                el = info["datasheetInfo"]["electrical"][0]
            except Exception:
                off += n
                continue
            rated = (el.get("ratedCurrents") or [None])[0]
            # PROVABLE ONLY: the number appears verbatim in the part number, so it
            # is demonstrably an identifier. A magnitude threshold alone is not
            # evidence and would do real damage here — see the module docstring.
            if (rated is not None and rated > MAX_PLAUSIBLE_A
                    and digits_in_mpn(rated, str(info.get("reference")))):
                targets.append((off, n, rec))
            off += n

    print(f"{len(targets)} rows carry a ratedCurrent above {MAX_PLAUSIBLE_A:.0f} A "
          f"that appears VERBATIM in the part number")

    edits, audit, in_mpn = [], [], 0
    for off, n, rec in targets:
        info = rec["magnetic"]["manufacturerInfo"]
        el = info["datasheetInfo"]["electrical"][0]
        ref = str(info.get("reference"))
        removed = {"ratedCurrents": el.pop("ratedCurrents")}
        if digits_in_mpn(removed["ratedCurrents"][0], ref):
            in_mpn += 1
        ind = el.get("inductance") or {}
        if any((ind.get(k) or 0) > MAX_PLAUSIBLE_H for k in ("nominal", "maximum")):
            removed["inductance"] = el.pop("inductance")
        errors = list(validator.iter_errors(rec["magnetic"]))
        if errors:
            print(f"ABORT: {ref} would not validate after the edit: {errors[0].message[:130]}")
            return 1
        new = json.dumps(rec, separators=(",", ":")).encode()
        if len(new) + 1 > n:
            print(f"ABORT: {ref} grew — in-place patch impossible")
            return 1
        edits.append((off, n, new))
        audit.append({"reference": ref, "manufacturer": info.get("name"),
                      "removed": removed,
                      "ratedCurrentAppearsInPartNumber": digits_in_mpn(removed["ratedCurrents"][0], ref)})

    print(f"  of which the current appears verbatim in the part number: {in_mpn}")
    print(f"  rows also losing an inductance above {MAX_PLAUSIBLE_H:.0f} H: "
          f"{sum(1 for a in audit if 'inductance' in a['removed'])}")

    if dry:
        print("\n--dry-run: nothing written")
        return 0

    with open(DATA, "r+b") as f:
        for off, n, new in edits:
            payload = new + b" " * (n - len(new) - 1) + b"\n"
            assert len(payload) == n
            f.seek(off)
            f.write(payload)
        f.flush()
        os.fsync(f.fileno())

    size_after = DATA.stat().st_size
    print(f"\npatched {len(edits)} rows in place")
    print(f"file size before={size_before} after={size_after} delta={size_after - size_before} (must be 0)")
    AUDIT.write_text(json.dumps({"ticket": "ABT #351 class A", "file": str(DATA), "rows": audit}, indent=1))
    print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
