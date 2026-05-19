#!/usr/bin/env python3
"""Wrap SAS device rows inside a ``semiconductor`` envelope.

Finding B from the Heaviside URI-migration probe:

The PEAS discriminator branch for semiconductors is shaped as

    {"semiconductor": {"mosfet": {...} | "diode": {...} | ... }}

(matching SAS.json which is itself a oneOf over mosfet / diode / igbt /
bjt). Historical ``TAS/data/*.ndjson`` rows were written one level
flatter:

    {"mosfet": {...}}
    {"diode":  {...}}
    {"igbt":   {...}}

This script rewraps every row in place to the PEAS-expected shape:

    {"semiconductor": {"mosfet": {...}}}
    {"semiconductor": {"diode":  {...}}}
    {"semiconductor": {"igbt":   {...}}}

Idempotent: rows that already carry a top-level ``semiconductor`` key
are left untouched. Atomic: writes go to ``<file>.new`` and are
renamed only after success. The original NDJSON is preserved as
``<file>.pre_semiconductor_wrap.bak`` on first run.

This is a one-shot structural migration. Once it has run successfully
on every checkout and the librarian writer has been updated to emit
the wrapped shape on import, this script is obsolete.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _REPO_ROOT / "TAS" / "data"

# (filename, expected inner discriminator key)
_TARGETS: tuple[tuple[str, str], ...] = (
    ("mosfets.ndjson", "mosfet"),
    ("diodes.ndjson",  "diode"),
    ("igbts.ndjson",   "igbt"),
)


def _wrap_file(path: Path, inner_key: str, *, dry_run: bool) -> tuple[int, int, int]:
    """Return (wrapped, already_wrapped, skipped_other)."""
    wrapped = 0
    already = 0
    skipped = 0

    out_lines: list[str] = []
    with path.open() as fh:
        for lineno, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                out_lines.append(line)
                continue
            try:
                doc = json.loads(stripped)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"{path.name}:{lineno} invalid JSON: {e}") from e
            if not isinstance(doc, dict):
                raise RuntimeError(f"{path.name}:{lineno} top-level is not an object")

            if "semiconductor" in doc:
                already += 1
                out_lines.append(line if line.endswith("\n") else line + "\n")
                continue
            if inner_key in doc:
                # Wrap. Preserve any other top-level keys verbatim — there
                # shouldn't be any in well-formed rows, but if there are
                # (annotations, legacy metadata) we keep them at the outer
                # level so they're not silently lost.
                inner = doc.pop(inner_key)
                new_doc: dict = {"semiconductor": {inner_key: inner}}
                # Re-attach any leftover top-level keys.
                for k, v in doc.items():
                    new_doc[k] = v
                out_lines.append(json.dumps(new_doc, ensure_ascii=False) + "\n")
                wrapped += 1
            else:
                skipped += 1
                out_lines.append(line if line.endswith("\n") else line + "\n")

    if dry_run:
        return wrapped, already, skipped

    # Backup once.
    backup = path.with_suffix(path.suffix + ".pre_semiconductor_wrap.bak")
    if not backup.exists():
        shutil.copy2(path, backup)

    tmp = path.with_suffix(path.suffix + ".new")
    with tmp.open("w") as fh:
        fh.writelines(out_lines)
    tmp.replace(path)
    return wrapped, already, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Count changes without writing.")
    args = ap.parse_args()

    grand_wrapped = 0
    grand_already = 0
    grand_skipped = 0
    print(f"{'file':<22} {'wrapped':>10} {'already':>10} {'skipped':>10}")
    print("-" * 56)
    for fname, inner in _TARGETS:
        path = _DATA_DIR / fname
        if not path.exists():
            print(f"{fname:<22} MISSING")
            continue
        w, a, s = _wrap_file(path, inner, dry_run=args.dry_run)
        grand_wrapped += w
        grand_already += a
        grand_skipped += s
        print(f"{fname:<22} {w:>10} {a:>10} {s:>10}")
    print("-" * 56)
    print(f"{'TOTAL':<22} {grand_wrapped:>10} {grand_already:>10} {grand_skipped:>10}")
    if args.dry_run:
        print("\n(dry-run — no files modified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
