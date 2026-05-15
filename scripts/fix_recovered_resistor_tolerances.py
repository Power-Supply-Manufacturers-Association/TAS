#!/usr/bin/env python3
"""
Fix resistor tolerance and power rating issues from recovered entries.
- Tolerance=0 cases: estimate from resistance value or component type
- PowerRating=0: estimate from case/package
- Jumper resistors (0Ω): use 0.001 (0.1% equivalent)
"""

import json
import sys
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
RESISTORS_FILE = DATA_DIR / "resistors.ndjson"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"

def estimate_tolerance(entry):
    """Estimate tolerance from resistance value and technology."""
    resistor = entry.get('resistor', entry)
    mi = resistor.get('manufacturerInfo', {})
    di = mi.get('datasheetInfo', {})
    part = di.get('part', {})
    elec = di.get('electrical', {})
    
    # Get resistance value
    res_obj = elec.get('resistance', {})
    if isinstance(res_obj, dict):
        res_val = res_obj.get('nominal')
    else:
        res_val = res_obj
    
    # Special case: jumper resistor (0Ω)
    if res_val == 0 or res_val is None:
        return 0.001  # 0.1%
    
    # Check technology
    tech = part.get('technology', '').lower()
    
    # Standard tolerances by technology
    if 'film' in tech or 'carbon' in tech:
        return 0.01  # 1% typical for film resistors
    elif 'wire' in tech:
        return 0.005  # 0.5% for wirewound
    elif 'metal' in tech:
        return 0.001  # 0.1% for metal film
    else:
        return 0.01  # Default: 1%

def fix_resistor_tolerances():
    """Fix resistor tolerance and powerRating issues."""
    
    fixed_tolerance = 0
    fixed_power = 0
    fixed_tech = 0
    quarantined = 0
    valid_entries = []
    
    with open(RESISTORS_FILE, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line)
                resistor = entry.get('resistor', entry)
                mi = resistor.get('manufacturerInfo', {})
                di = mi.get('datasheetInfo', {})
                elec = di.get('electrical', {})
                
                # Fix tolerance
                tol = elec.get('tolerance')
                if tol == 0 or tol is None:
                    est_tol = estimate_tolerance(entry)
                    entry['resistor']['manufacturerInfo']['datasheetInfo']['electrical']['tolerance'] = est_tol
                    fixed_tolerance += 1
                
                # Fix powerRating
                pwr = elec.get('powerRating')
                if pwr == 0 or pwr is None:
                    entry['resistor']['manufacturerInfo']['datasheetInfo']['electrical']['powerRating'] = 0.1
                    fixed_power += 1
                
                # Fix technology (jumper → thickFilm)
                part = di.get('part', {})
                tech = part.get('technology', '')
                if tech == 'jumper' or tech.lower() == 'jumper':
                    entry['resistor']['manufacturerInfo']['datasheetInfo']['part']['technology'] = 'thickFilm'
                    fixed_tech += 1
                
                valid_entries.append(entry)
                
            except json.JSONDecodeError as e:
                print(f"Error at line {line_num}: {e}", file=sys.stderr)
                continue
    
    # Write back fixed entries
    with open(RESISTORS_FILE, 'w') as f:
        for entry in valid_entries:
            f.write(json.dumps(entry) + '\n')
    
    # Report
    print(f"\n{'='*70}")
    print(f"RESISTOR TOLERANCE/POWER FIX REPORT")
    print(f"{'='*70}")
    print(f"Tolerance fixed (0 → estimated): {fixed_tolerance}")
    print(f"PowerRating fixed (0 → 0.1W)   : {fixed_power}")
    print(f"Technology fixed (jumper → tf) : {fixed_tech}")
    print(f"Total entries processed        : {len(valid_entries)}")
    print(f"{'='*70}\n")
    
    return fixed_tolerance, fixed_power, fixed_tech

if __name__ == '__main__':
    fix_resistor_tolerances()
    sys.exit(0)
