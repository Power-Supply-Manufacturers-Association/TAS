#!/usr/bin/env python3
"""Null the corrupted dcResistance / placeholder inductance on the Laird
common-mode-choke rows of ABT #286. Option (b) of that ticket's decision: keep
the rows and their real MPNs, remove only the poisoned fields.

    python3 scripts/null_laird_corrupt_dcr.py --dry-run   # report, change nothing
    python3 scripts/null_laird_corrupt_dcr.py             # apply

WHAT IS REMOVED, AND WHY IT IS SAFE TO REMOVE

`dcResistances` on these rows holds Laird's |Z| at 100 MHz, not a DC resistance.
Three independent proofs agree (see ABT #286 and staging/laird_cmc_vendor_pull.json):

  1. Laird's own catalog lists "CM5441 Series" at |Z|@100MHz = 160.00 and DCR Max
     = 0.00; the row CM5441Z161B-10 stores dcResistances[0].maximum = 160.0.
  2. Read as a DC resistance, 18 rows dissipate more than 5 W at their OWN rated
     current — CM5441Z161B-10 is 160 ohm at 75 A = 900 kW.
  3. Laird encodes |Z| in the MPN as a 3-digit EIA code (R201R -> 200, Z161B ->
     160, R800R -> 80); for 8 of the rows the stored "DCR" equals the code inside
     its own part number.

`inductance` is dropped only where it is EXACTLY 10.000 uH — the placeholder ABT
#286 identified (18 of these 20 rows carry the identical value across parts of
different size and current rating).

The values are NOT re-filed as impedancePoints. For 8 rows the MPN proves the
magnitude, but the measurement frequency is only established for the series the
vendor table covers; asserting 100 MHz for the rest would be inference beyond the
evidence. The removed values are preserved verbatim in the audit file instead.

WHY THE WRITE IS DONE THIS WAY

data/magnetics.ndjson takes CONCURRENT APPENDS from other processes, so it must
not be rewritten (a read-modify-write would silently drop anything appended in
between). Nulling only ever SHORTENS a line, so each edited line is written back
at its exact original byte length, padded with spaces before the newline — an
in-place byte patch: no temp file, no rename, no truncation, EOF untouched.
Trailing whitespace is insignificant to every JSON reader.

Each edited record is re-validated against the MAS schema BEFORE the write; the
run aborts rather than write anything that would not validate.
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
AUDIT = REPO / "staging" / "laird_dcr_nulled_audit.json"
IMPLAUSIBLE_W = 5.0          # a CMC that dissipates >5 W at rated current is not real
PLACEHOLDER_H = 1e-05        # the 10.000 uH placeholder ABT #286 identified


def find_targets(path: Path):
    """[(byte_offset, byte_len, record, dcr, rated_a)] for the corrupted rows."""
    targets, off = [], 0
    with open(path, "rb") as f:
        for raw in f:
            n = len(raw)
            if b"Laird" in raw:
                try:
                    rec = json.loads(raw)
                    info = rec["magnetic"]["manufacturerInfo"]
                    el = info["datasheetInfo"]["electrical"][0]
                except Exception:
                    off += n
                    continue
                if "laird" in str(info.get("name", "")).lower():
                    dcrs = el.get("dcResistances") or []
                    dcr = dcrs[0].get("maximum") if dcrs else None
                    rated = (el.get("ratedCurrents") or [None])[0]
                    if dcr is not None and rated and dcr * rated * rated > IMPLAUSIBLE_W:
                        targets.append((off, n, rec, dcr, rated))
            off += n
    return targets


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    validator = _load_magnetic_schema(_build_registry())
    size_before = DATA.stat().st_size
    targets = find_targets(DATA)
    print(f"{len(targets)} Laird rows dissipate >{IMPLAUSIBLE_W} W at rated current")

    edits, audit = [], []
    for off, n, rec, dcr, rated in targets:
        info = rec["magnetic"]["manufacturerInfo"]
        el = info["datasheetInfo"]["electrical"][0]
        ref = info["reference"]
        removed = {"dcResistances": el.pop("dcResistances", None)}
        if (el.get("inductance") or {}).get("nominal") == PLACEHOLDER_H:
            removed["inductance"] = el.pop("inductance")
        errors = list(validator.iter_errors(rec["magnetic"]))
        if errors:
            print(f"ABORT: {ref} would not validate after the edit: {errors[0].message}")
            return 1
        new = json.dumps(rec, separators=(",", ":")).encode()
        if len(new) + 1 > n:
            print(f"ABORT: {ref} grew ({len(new) + 1} > {n}) — in-place patch impossible")
            return 1
        edits.append((off, n, new))
        audit.append({"reference": ref, "ratedCurrentA": rated,
                      "impliedDissipationW": round(dcr * rated * rated, 1),
                      "removed": removed,
                      "reason": "dcResistance held |Z|@100MHz; inductance was the 10.000 uH placeholder (ABT #286)"})
        print(f"  {ref:22} -{dcr} ohm @ {rated} A ({dcr * rated * rated:.0f} W implied)")

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
    AUDIT.write_text(json.dumps({"ticket": "ABT #286", "file": str(DATA), "rows": audit}, indent=1))
    print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
