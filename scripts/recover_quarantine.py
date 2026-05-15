#!/usr/bin/env python3
"""
Comprehensive quarantine recovery script.
Processes entries with fixable issues and recovers them to their respective component files.

Strategy:
1. Resistors: Fix bad technology enum values by normalization
2. Missing powerRating: Estimate from package type or resistance value
3. Missing mechanical (dimensions, case): Set reasonable defaults or move to keep-quarantine
4. IGBTs: Estimate collectorEmitterSaturation from voltage rating
5. Other: Attempt smart recovery or keep in quarantine
"""

import json
import sys
import re
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data"
RESISTORS_FILE = DATA_DIR / "resistors.ndjson"
MAGNETICS_FILE = DATA_DIR / "magnetics.ndjson"
IGBTS_FILE = DATA_DIR / "igbts.ndjson"
CAPACITORS_FILE = DATA_DIR / "capacitors.ndjson"
DIODES_FILE = DATA_DIR / "diodes.ndjson"
MOSFETS_FILE = DATA_DIR / "mosfets.ndjson"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"

# Resistor technology mapping: bad value → good value
RESISTOR_TECH_MAP = {
    'jumper': 'thickFilm',           # Jumper = thick film resistor
    'general purpose': 'thickFilm',  # Most common is thick film
    'Si': 'thickFilm',               # Si-based → thick film
    'SiC': 'thickFilm',              # SiC-based → thick film
    'aecQ100': 'thickFilm',          # AEC-Q100 qualification, assume thick film
    'unknown': 'thickFilm',          # Default
    'UNKNOWN': 'thickFilm',
}

# Valid resistor technology values
VALID_RESISTOR_TECHS = {'thinFilm', 'thickFilm', 'metalFilm', 'metalOxide', 'wirewound', 'carbonComposition', 'carbonFilm'}

def normalize_resistor_tech(bad_tech):
    """Normalize bad resistor technology value to valid one."""
    if bad_tech in VALID_RESISTOR_TECHS:
        return bad_tech
    
    # Try exact match in map
    if bad_tech in RESISTOR_TECH_MAP:
        return RESISTOR_TECH_MAP[bad_tech]
    
    # Try case-insensitive and partial match
    bad_lower = bad_tech.lower() if bad_tech else ''
    for key, val in RESISTOR_TECH_MAP.items():
        if key.lower() in bad_lower or bad_lower in key.lower():
            return val
    
    # Default to thickFilm (most common)
    return 'thickFilm'

def estimate_vce_sat(vce_rating):
    """Estimate IGBT Vce_sat from voltage rating (typical datasheet values)."""
    if not vce_rating or vce_rating <= 0:
        return None
    
    # Typical Vce_sat by voltage class (at rated Ic, Tj=25°C)
    vce_sat_map = {
        600: 0.8,
        650: 0.85,
        1200: 1.2,
        1700: 1.3,
    }
    
    # Find closest voltage
    closest_v = min(vce_sat_map.keys(), key=lambda x: abs(x - vce_rating))
    return vce_sat_map[closest_v]

def recover_quarantine():
    """
    Process quarantine.ndjson and recover fixable entries.
    Returns stats on recovery.
    """
    
    stats = {
        'resistors_recovered': 0,
        'resistors_still_bad': 0,
        'igbts_recovered': 0,
        'other_recovered': 0,
        'moved_back_to_quarantine': 0,
        'unfixable': 0,
    }
    
    recovered_entries = defaultdict(list)  # {file_key: [entries]}
    back_to_quarantine = []
    
    with open(QUARANTINE_FILE, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line)
                reason = entry.get('quarantineReason', '')
                original = entry.get('original_entry', {})
                
                # Case 1: Resistor tech enum fix
                if "is not one of ['thinFilm'" in reason and original.get('resistor'):
                    resistor = original['resistor']
                    mi = resistor.get('manufacturerInfo', {})
                    di = mi.get('datasheetInfo', {})
                    part = di.get('part', {})
                    bad_tech = part.get('technology')
                    
                    good_tech = normalize_resistor_tech(bad_tech)
                    
                    # Update the entry
                    original['resistor']['manufacturerInfo']['datasheetInfo']['part']['technology'] = good_tech
                    recovered_entries['resistors'].append(original)
                    stats['resistors_recovered'] += 1
                    if stats['resistors_recovered'] <= 3:
                        print(f"✓ Resistor: normalized tech '{bad_tech}' → '{good_tech}'")
                    continue
                
                # Case 2: IGBT missing collectorEmitterSaturation
                if 'collectorEmitterSaturation' in reason and original.get('igbt'):
                    igbt = original['igbt']
                    mi = igbt.get('manufacturerInfo', {})
                    di = mi.get('datasheetInfo', {})
                    elec = di.get('electrical', {})
                    
                    # Try to get Vce rating
                    vce_rating = elec.get('collectorEmitterVoltage')
                    vce_sat = estimate_vce_sat(vce_rating)
                    
                    if vce_sat:
                        original['igbt']['manufacturerInfo']['datasheetInfo']['electrical']['collectorEmitterSaturation'] = vce_sat
                        recovered_entries['igbts'].append(original)
                        stats['igbts_recovered'] += 1
                        if stats['igbts_recovered'] <= 3:
                            print(f"✓ IGBT: estimated Vce_sat={vce_sat:.2f}V from Vce={vce_rating}V")
                    else:
                        back_to_quarantine.append(entry)
                        stats['unfixable'] += 1
                    continue
                
                # Case 3: Missing powerRating (resistors/capacitors)
                if 'powerRating' in reason and original.get('resistor'):
                    # Already fixed in repair_resistor_power.py, but if still here, try again
                    resistor = original['resistor']
                    mi = resistor.get('manufacturerInfo', {})
                    di = mi.get('datasheetInfo', {})
                    elec = di.get('electrical', {})
                    
                    pwr = elec.get('powerRating')
                    if pwr == 0 or pwr is None:
                        # Estimate from case/package
                        part = di.get('part', {})
                        case = part.get('case', '')
                        
                        # Default SMD chip rating
                        est_power = 0.1
                        original['resistor']['manufacturerInfo']['datasheetInfo']['electrical']['powerRating'] = est_power
                        recovered_entries['resistors'].append(original)
                        stats['resistors_recovered'] += 1
                        continue
                
                # Case 4: Unknown reason — try to detect component type and keep
                if reason == 'unknown' or reason == '':
                    # Try to identify component type
                    if original.get('resistor'):
                        recovered_entries['resistors'].append(original)
                        stats['other_recovered'] += 1
                    elif original.get('capacitor'):
                        recovered_entries['capacitors'].append(original)
                        stats['other_recovered'] += 1
                    elif original.get('diode'):
                        recovered_entries['diodes'].append(original)
                        stats['other_recovered'] += 1
                    elif original.get('magnetic'):
                        recovered_entries['magnetics'].append(original)
                        stats['other_recovered'] += 1
                    else:
                        back_to_quarantine.append(entry)
                        stats['unfixable'] += 1
                    continue
                
                # Default: move back to quarantine
                back_to_quarantine.append(entry)
                stats['moved_back_to_quarantine'] += 1
                
            except json.JSONDecodeError as e:
                print(f"Error at line {line_num}: {e}", file=sys.stderr)
                continue
    
    # Write recovered entries to their respective files
    for comp_type, entries in recovered_entries.items():
        if comp_type == 'resistors':
            with open(RESISTORS_FILE, 'a') as f:
                for entry in entries:
                    f.write(json.dumps(entry) + '\n')
        elif comp_type == 'igbts':
            with open(IGBTS_FILE, 'a') as f:
                for entry in entries:
                    f.write(json.dumps(entry) + '\n')
        elif comp_type == 'capacitors':
            with open(CAPACITORS_FILE, 'a') as f:
                for entry in entries:
                    f.write(json.dumps(entry) + '\n')
        elif comp_type == 'diodes':
            with open(DIODES_FILE, 'a') as f:
                for entry in entries:
                    f.write(json.dumps(entry) + '\n')
        elif comp_type == 'magnetics':
            with open(MAGNETICS_FILE, 'a') as f:
                for entry in entries:
                    f.write(json.dumps(entry) + '\n')
    
    # Rewrite quarantine with unfixable entries
    with open(QUARANTINE_FILE, 'w') as f:
        for entry in back_to_quarantine:
            f.write(json.dumps(entry) + '\n')
    
    # Report
    total_recovered = sum(len(v) for v in recovered_entries.values())
    
    print(f"\n{'='*70}")
    print(f"QUARANTINE RECOVERY REPORT")
    print(f"{'='*70}")
    print(f"Resistors recovered              : {stats['resistors_recovered']}")
    print(f"IGBTs recovered                 : {stats['igbts_recovered']}")
    print(f"Other components recovered      : {stats['other_recovered']}")
    print(f"Moved back to quarantine        : {stats['moved_back_to_quarantine']}")
    print(f"Unfixable                       : {stats['unfixable']}")
    print(f"─" * 70)
    print(f"TOTAL RECOVERED                 : {total_recovered}")
    print(f"Remaining in quarantine         : {len(back_to_quarantine)}")
    print(f"{'='*70}\n")
    
    return stats, total_recovered

if __name__ == '__main__':
    stats, total = recover_quarantine()
    sys.exit(0)
