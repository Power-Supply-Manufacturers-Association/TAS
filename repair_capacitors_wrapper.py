#!/usr/bin/env python3
"""
CRITICAL REPAIR: Fix capacitors.ndjson wrapper corruption
Date: 2026-04-09

ISSUE: All 11,323 capacitor entries missing top-level capacitor wrapper.
ROOT CAUSE: Würth import wrote capacitors with structure:
            {"manufacturerInfo": {...}}
            Should be:
            {"capacitor": {"manufacturerInfo": {...}}}

SOLUTION: Wrap all entries in {"capacitor": {...}}
"""

import json

def repair_capacitors():
    with open('data/capacitors.ndjson') as f:
        lines = f.readlines()

    repaired = []
    repaired_count = 0

    for i, line in enumerate(lines, 1):
        entry = json.loads(line)

        # Check if already has wrapper
        if 'capacitor' in entry:
            # Already correct
            repaired.append(entry)
            continue

        # If it has manufacturerInfo at root level, wrap it
        if 'manufacturerInfo' in entry and 'capacitor' not in entry:
            fixed = {'capacitor': entry}
            repaired.append(fixed)
            repaired_count += 1
        else:
            # Unexpected structure
            print(f"WARNING Line {i}: Unexpected structure, keys={list(entry.keys())}")
            repaired.append(entry)

    # Write repaired file
    with open('data/capacitors.ndjson', 'w') as f:
        for entry in repaired:
            f.write(json.dumps(entry) + '\n')

    print(f"=== CAPACITOR WRAPPER REPAIR ===")
    print(f"Total entries: {len(lines)}")
    print(f"  Already correct: {len(lines) - repaired_count}")
    print(f"  Repaired: {repaired_count}")
    print(f"Final capacitor entries: {len(repaired)}")

if __name__ == '__main__':
    repair_capacitors()
