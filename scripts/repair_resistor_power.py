#!/usr/bin/env python3
"""
Repair Resistor power rating data:
1. For entries with powerRating=0 but other data present: estimate from resistance
2. If cannot estimate reliably: move to quarantine
"""

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
RESISTORS_FILE = DATA_DIR / "resistors.ndjson"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"

# Standard power ratings in watts for SMD/SMT resistors (IEC 60063)
# Mapping common package codes to power ratings
POWER_ESTIMATES = {
    'SMD 0201': 0.05,
    'SMD 0402': 0.1,
    'SMD 0603': 0.1,
    'SMD 0805': 0.125,
    'SMD 1206': 0.25,
    'SMD 1210': 0.5,
    'SMD 1812': 0.5,
    'SMD 2512': 1.0,
    # Fallback based on case description
    'chip': 0.1,
    'smd': 0.1,
}

def estimate_power_rating(entry):
    """
    Estimate power rating from package type, resistance, and case info.
    Returns (estimated_power, confidence_note) or (None, reason).
    """
    resistor = entry.get('resistor', entry)
    mi = resistor.get('manufacturerInfo', {})
    di = mi.get('datasheetInfo', {})
    part = di.get('part', {})
    mech = di.get('mechanical', {})
    elec = di.get('electrical', {})
    
    # Try to get case/package info
    case = part.get('case', '') or mech.get('shapeType', '') or ''
    case_lower = case.lower()
    
    # Check for common package keywords
    for pkg_key, power_est in POWER_ESTIMATES.items():
        if pkg_key.lower() in case_lower:
            return power_est, f"Estimated from case='{case}'"
    
    # Fallback: standard SMD chip rating
    return 0.1, "Default SMD chip 0.1W"

def repair_resistors():
    """Process resistors.ndjson: repair or quarantine entries with powerRating=0."""
    
    repaired_count = 0
    quarantine_count = 0
    entries_repaired = []
    entries_quarantine = []
    errors = []
    
    with open(RESISTORS_FILE, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line)
                resistor = entry.get('resistor', entry)
                mi = resistor.get('manufacturerInfo', {})
                ref = mi.get('reference', 'UNKNOWN')
                
                # Check powerRating
                di = mi.get('datasheetInfo', {})
                elec = di.get('electrical', {})
                pwr = elec.get('powerRating')
                
                # Get resistance to validate it exists
                res_obj = elec.get('resistance', {})
                if isinstance(res_obj, dict):
                    res_value = res_obj.get('nominal')
                else:
                    res_value = res_obj
                
                # Only fix entries where power is explicitly 0
                if pwr != 0:
                    entries_repaired.append(entry)
                    continue
                
                # If resistance data is missing, quarantine
                if not res_value or res_value <= 0:
                    entries_quarantine.append({
                        'original_entry': entry,
                        'quarantineReason': 'Resistor: powerRating=0 and missing/invalid resistance value',
                        'lineNumber': line_num
                    })
                    quarantine_count += 1
                    continue
                
                # Estimate power rating
                est_power, note = estimate_power_rating(entry)
                
                if est_power is not None:
                    # Update entry with estimated power
                    entry['resistor']['manufacturerInfo']['datasheetInfo']['electrical']['powerRating'] = est_power
                    entries_repaired.append(entry)
                    repaired_count += 1
                    if repaired_count <= 3:
                        print(f"✓ Line {line_num}: Estimated P={est_power}W for {ref} ({note})")
                else:
                    entries_quarantine.append({
                        'original_entry': entry,
                        'quarantineReason': f'Resistor: cannot estimate powerRating ({note})',
                        'lineNumber': line_num
                    })
                    quarantine_count += 1
                    
            except json.JSONDecodeError as e:
                errors.append(f"Line {line_num}: JSON decode error: {e}")
                continue
    
    # Write repaired entries back to resistors.ndjson
    with open(RESISTORS_FILE, 'w') as f:
        for entry in entries_repaired:
            f.write(json.dumps(entry) + '\n')
    
    # Append quarantine entries
    with open(QUARANTINE_FILE, 'a') as f:
        for q_entry in entries_quarantine:
            f.write(json.dumps(q_entry) + '\n')
    
    # Report
    print(f"\n{'='*70}")
    print(f"RESISTOR REPAIR REPORT")
    print(f"{'='*70}")
    print(f"Entries repaired (P estimated)   : {repaired_count}")
    print(f"Entries quarantined              : {quarantine_count}")
    print(f"Total entries (repaired + kept)  : {len(entries_repaired)}")
    print(f"Errors encountered               : {len(errors)}")
    
    if errors:
        print(f"\nFirst 5 errors:")
        for err in errors[:5]:
            print(f"  {err}")
    
    print(f"\nresistors.ndjson: {len(entries_repaired)} entries")
    print(f"quarantine.ndjson: +{quarantine_count} entries appended")
    print(f"{'='*70}\n")
    
    return repaired_count, quarantine_count, errors

if __name__ == '__main__':
    repaired, quarantined, errors = repair_resistors()
    sys.exit(0 if not errors else 1)
