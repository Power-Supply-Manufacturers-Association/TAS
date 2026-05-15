#!/usr/bin/env python3
"""
Repair Magnetics field data:
1. For entries with skeleton references: quarantine
2. For inductors with missing inductance: quarantine
"""

import json
import sys
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
MAGNETICS_FILE = DATA_DIR / "magnetics.ndjson"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"

SKELETON_PATTERNS = re.compile(r'^[?]+$|^TBD$|^N/A$', re.IGNORECASE)

def is_skeleton(val):
    if not isinstance(val, str):
        return False
    return bool(SKELETON_PATTERNS.match(val.strip()))

# Families treated as "not inductors" for the inductance check
NON_INDUCTOR_FAMILIES = {'CMC', 'WE-CMB', 'WE-FB'}
NON_INDUCTOR_SUBTYPES = {'ferrite_bead', 'common_mode_choke', 'cmc', 'transformer'}
NON_INDUCTOR_KEYWORDS = {'cmc', 'ferrite bead', 'ferrite-bead', 'choke', 'transformer'}

def is_inductor_magnetics(d):
    """Return True if entry should be checked for inductance > 0."""
    m = d.get('magnetic', d)
    mi = m.get('manufacturerInfo', {})
    family = (mi.get('family') or '').strip()
    if family in NON_INDUCTOR_FAMILIES:
        return False
    di = mi.get('datasheetInfo', {})
    part = di.get('part', {})
    sub = (part.get('subType') or part.get('componentSubType') or part.get('type') or '').lower()
    if sub in NON_INDUCTOR_SUBTYPES:
        return False
    desc = (part.get('description') or '').lower()
    for kw in NON_INDUCTOR_KEYWORDS:
        if kw in desc:
            return False
    return True

def repair_magnetics():
    """Process magnetics.ndjson: quarantine incomplete/skeleton entries."""
    
    quarantine_count = 0
    entries_kept = []
    entries_quarantine = []
    errors = []
    
    with open(MAGNETICS_FILE, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line)
                mag = entry.get('magnetic', entry)
                mi = mag.get('manufacturerInfo', {})
                ref = mi.get('reference', '')
                
                # Check for skeleton reference
                if is_skeleton(ref):
                    entries_quarantine.append({
                        'original_entry': entry,
                        'quarantineReason': f"Magnetic: skeleton reference '{ref}'",
                        'lineNumber': line_num
                    })
                    quarantine_count += 1
                    continue
                
                # Check for inductors with missing inductance
                if is_inductor_magnetics(entry):
                    di = mi.get('datasheetInfo', {})
                    elec = di.get('electrical', {})
                    ind_obj = elec.get('inductance', {})
                    if isinstance(ind_obj, dict):
                        ind = ind_obj.get('nominal')
                    else:
                        ind = ind_obj
                    
                    if not ind or ind <= 0:
                        entries_quarantine.append({
                            'original_entry': entry,
                            'quarantineReason': f"Magnetic: inductor missing or zero inductance (val={ind})",
                            'lineNumber': line_num
                        })
                        quarantine_count += 1
                        continue
                
                # Entry is valid, keep it
                entries_kept.append(entry)
                    
            except json.JSONDecodeError as e:
                errors.append(f"Line {line_num}: JSON decode error: {e}")
                continue
    
    # Write kept entries back to magnetics.ndjson
    with open(MAGNETICS_FILE, 'w') as f:
        for entry in entries_kept:
            f.write(json.dumps(entry) + '\n')
    
    # Append quarantine entries
    with open(QUARANTINE_FILE, 'a') as f:
        for q_entry in entries_quarantine:
            f.write(json.dumps(q_entry) + '\n')
    
    # Report
    print(f"\n{'='*70}")
    print(f"MAGNETICS FIELD REPAIR REPORT")
    print(f"{'='*70}")
    print(f"Entries quarantined              : {quarantine_count}")
    print(f"Entries kept (field-valid)      : {len(entries_kept)}")
    print(f"Errors encountered              : {len(errors)}")
    
    if errors:
        print(f"\nFirst 5 errors:")
        for err in errors[:5]:
            print(f"  {err}")
    
    print(f"\nmagnetics.ndjson: {len(entries_kept)} entries")
    print(f"quarantine.ndjson: +{quarantine_count} entries appended")
    print(f"{'='*70}\n")
    
    return len(entries_kept), quarantine_count, errors

if __name__ == '__main__':
    kept, quarantined, errors = repair_magnetics()
    sys.exit(0 if not errors else 1)
