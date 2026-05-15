"""Quarantine recovery pass for TAS v2.

Attempts to recover records from data/quarantine.ndjson that failed the initial
port_part_libraries.py run due to fixable coercion gaps, then re-validates them
against the per-type schemas.

Recovery strategies (no external API calls required):

RESISTORS — invalid technology enum
    'general purpose' -> 'thickFilm'   (most common SMD resistor technology)
    'metal_plate'     -> 'currentSenseShunt'  (metal-plate current-sense resistors)
    'jumper'          -> skip  (0-ohm jumpers are not real resistors; stay quarantined)
    'Si' / 'SiC'      -> skip  (mislabeled semiconductors; stay quarantined)
    Re-applies the full _coerce pass (strips mechanical dimensions/shape which
    are not in the RAS schema, strips top-level flat aliases, etc.).

Records that still fail after recovery or for which no recovery strategy applies
are written back to quarantine unchanged.

Writes:
  - Recovered records appended to their respective data/<type>.ndjson files
  - Remaining failures remain in data/quarantine.ndjson (file is rebuilt in-place)
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
QUARANTINE = DATA / "quarantine.ndjson"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from port_part_libraries import (  # noqa: E402
    build_registry, get_validator, _coerce, _drop_nulls,
)

# ---------------------------------------------------------------------------
# Technology remapping for resistors
# ---------------------------------------------------------------------------

BAD_TECH_MAP = {
    "general purpose": "thickFilm",
    "metal_plate": "currentSenseShunt",
}
SKIP_TECHS = {"jumper", "Si", "SiC"}


def _try_recover_resistor(body: dict, validator) -> dict | None:
    """Return a fixed resistor body that passes validation, or None."""
    body = copy.deepcopy(body)
    # Re-apply _coerce to strip mechanical flats, top-level aliases, etc.
    body = _coerce(body, "resistor")
    body = _drop_nulls(body) or {}

    di = body.get("manufacturerInfo", {}).get("datasheetInfo", {})
    part = di.get("part", {})
    tech = part.get("technology")

    if tech in SKIP_TECHS:
        return None  # intentionally excluded
    if tech in BAD_TECH_MAP:
        part["technology"] = BAD_TECH_MAP[tech]

    errs = list(validator.iter_errors(body))
    return body if not errs else None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be recovered without writing files")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not QUARANTINE.exists():
        print("quarantine.ndjson not found", file=sys.stderr)
        return 1

    registry = build_registry()
    validators = {
        "resistors.ndjson": ("resistor", get_validator(registry, "RAS", "resistor.json")),
    }

    kept: list[str] = []        # quarantine lines to keep
    recovered: dict[str, list[str]] = {}   # file -> [json lines]

    n_in = n_recovered = n_kept = 0

    with QUARANTINE.open() as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if not line.strip():
                continue
            n_in += 1

            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                n_kept += 1
                continue

            source = rec.get("quarantineSource", "")
            if source not in validators:
                kept.append(line)
                n_kept += 1
                continue

            disc, validator = validators[source]
            orig_body = rec.get(disc)
            if not isinstance(orig_body, dict):
                kept.append(line)
                n_kept += 1
                continue

            reason = rec.get("quarantineReason", "")

            # Determine if recovery applies
            recovered_body = None
            if source == "resistors.ndjson":
                bad_techs_present = any(
                    f"'{t}' is not one of" in reason for t in (set(BAD_TECH_MAP) | SKIP_TECHS)
                )
                if bad_techs_present:
                    recovered_body = _try_recover_resistor(orig_body, validator)

            if recovered_body is not None:
                n_recovered += 1
                file_path = DATA / source
                entry = json.dumps({disc: recovered_body})
                recovered.setdefault(source, []).append(entry)
                if args.verbose:
                    pn = recovered_body.get("manufacturerInfo", {}).get("reference", "?")
                    print(f"  RECOVERED {disc} {pn}")
            else:
                kept.append(line)
                n_kept += 1

    print(f"\n[recover_quarantine] in={n_in} recovered={n_recovered} kept={n_kept}")
    for src, lines in recovered.items():
        print(f"  {len(lines):5d}  -> {src}")

    if args.dry_run:
        print("\n(dry run — no files written)")
        return 0

    # Write recovered records to their respective files
    for source, lines in recovered.items():
        target = DATA / source
        if not target.exists():
            print(f"WARNING: {target} does not exist; skipping", file=sys.stderr)
            continue
        with target.open("a") as fh:
            for line in lines:
                fh.write(line + "\n")
        print(f"Appended {len(lines)} records to {source}")

    # Rebuild quarantine with only the kept records
    tmp = QUARANTINE.with_suffix(".tmp")
    with tmp.open("w") as fh:
        for line in kept:
            fh.write(line + "\n")
    tmp.replace(QUARANTINE)
    print(f"Quarantine rebuilt: {n_kept} records remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
