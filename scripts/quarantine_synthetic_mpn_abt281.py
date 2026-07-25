#!/usr/bin/env python3
"""ABT #281: quarantine common-mode-choke rows that carry no
manufacturerInfo.reference and only a synthetic import id as partNumber
(e.g. 'TDK001m08051261_50', 'Bou069m08051569_50') — distributor-scrape
artifacts with category-text descriptions. They are real part *categories* but
not orderable parts: recommending them is impossible and matching them to real
MPNs by spec would be fabrication. Moved (never deleted) to
magnetics.quarantine_synthetic_mpn.ndjson pending re-sourcing of real MPNs.

Usage: quarantine_synthetic_mpn_abt281.py [--data DIR] [--dry-run]
"""

import argparse
import collections
import datetime
import json
import os
import re
import sys
import tempfile

SYNTHETIC_PN = re.compile(r"^[A-Za-z]{3}\d{3}m[A-Za-z0-9]+_\d+$")


def is_target(magnetic):
    info = magnetic.get("manufacturerInfo", {})
    if info.get("reference"):
        return False
    datasheet = info.get("datasheetInfo", {})
    electrical = datasheet.get("electrical")
    if not isinstance(electrical, list) or not electrical:
        return False
    if electrical[0].get("subtype") != "commonModeChoke":
        return False
    part_number = datasheet.get("part", {}).get("partNumber") or ""
    return bool(SYNTHETIC_PN.match(part_number))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=os.path.join(os.path.dirname(__file__), "..", "data"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_path = os.path.join(args.data, "magnetics.ndjson")
    quarantine_path = os.path.join(args.data, "magnetics.quarantine_synthetic_mpn.ndjson")
    reason = ("synthetic import id as partNumber, no manufacturerInfo.reference — not an "
              "orderable part; needs re-sourcing of real MPNs (ABT #281, "
              f"{datetime.date.today().isoformat()})")

    moved = collections.Counter()
    kept = 0
    out = tempfile.NamedTemporaryFile("w", dir=args.data, delete=False, suffix=".tmp")
    quarantined_lines = []
    with open(source_path) as source:
        for line in source:
            try:
                row = json.loads(line)
                magnetic = row["magnetic"]
            except (json.JSONDecodeError, KeyError):
                out.write(line)
                kept += 1
                continue
            if is_target(magnetic):
                moved[magnetic["manufacturerInfo"].get("name", "?")] += 1
                quarantined_lines.append(json.dumps(
                    {"magnetic": magnetic, "quarantineReason": reason},
                    separators=(",", ":"), ensure_ascii=False) + "\n")
            else:
                out.write(line)
                kept += 1
    out.close()

    print(f"kept {kept}; quarantining {sum(moved.values())}: {dict(moved.most_common())}")
    if args.dry_run:
        os.unlink(out.name)
        print("dry run — nothing written")
        return 0
    with open(quarantine_path, "a") as quarantine:
        quarantine.writelines(quarantined_lines)
    os.replace(out.name, source_path)
    print(f"wrote {source_path} and appended to {quarantine_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
