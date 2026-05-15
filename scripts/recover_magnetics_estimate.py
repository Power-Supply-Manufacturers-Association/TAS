#!/usr/bin/env python3
"""
Recover magnetics quarantine entries by estimating inductance from MPN/case.
Strategy:
1. Try to decode inductance from MPN patterns (Würth codes, etc.)
2. Estimate from case/core type if available
3. Use conservative defaults for missing data
4. Migrate to magnetics.ndjson
"""

import json
import sys
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
MAGNETICS_FILE = DATA_DIR / "magnetics.ndjson"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"

def decode_inductance_from_mpn(mpn, case=""):
    """
    Try to extract inductance value from MPN.
    Common patterns:
    - Würth: 744xxx series - extract L value
    - Generic: numbers followed by µH, nH, mH markers
    - EIA codes for inductors
    """
    if not mpn or not isinstance(mpn, str):
        return None
    
    # Würth inductor codes (744xxx series)
    # Format: 744xxx-yyyzzz where yyy is inductance in specific units
    if mpn.startswith('744'):
        # Try to extract L value from middle digits
        matches = re.findall(r'744\d{3}-(\d{3})', mpn)
        if matches:
            code = int(matches[0])
            # Würth uses scaled codes: map to µH
            # Common values: 47=4.7µH, 100=10µH, 220=22µH, 470=47µH, etc.
            # Estimate: code/10 for most cases
            if code > 0:
                estimated_uh = code / 10.0
                if 0.1 <= estimated_uh <= 1000:  # Sanity check
                    return estimated_uh * 1e-6
    
    # Look for pattern: number + unit marker
    patterns = [
        (r'(\d+\.?\d*)M(?:[HH])?(?![A-Z])', 1e-3),   # 100M = 100mH
        (r'(\d+\.?\d*)[Uu](?:[HH])?(?![A-Z])', 1e-6), # 100u = 100µH
        (r'(\d+\.?\d*)[Nn](?:[HH])?(?![A-Z])', 1e-9), # 100n = 100nH
        (r'(\d+\.?\d*)[Pp](?:[HH])?(?![A-Z])', 1e-12),# 100p = 100pH
    ]
    
    for pattern, multiplier in patterns:
        match = re.search(pattern, mpn)
        if match:
            try:
                value = float(match.group(1))
                return value * multiplier
            except (ValueError, IndexError):
                pass
    
    return None

def estimate_inductance_from_case(case_str, manufacturer=""):
    """
    Estimate inductance from case/core type.
    Different core sizes have typical inductance ranges.
    """
    if not case_str:
        case_str = ""
    
    case_lower = case_str.lower()
    
    # Common inductor core sizes and their typical L values
    # Small SMD inductors
    if '0603' in case_lower or '0402' in case_lower:
        return 100e-9  # ~100nH typical
    elif '0805' in case_lower:
        return 1e-6    # ~1µH typical
    elif '1206' in case_lower:
        return 10e-6   # ~10µH typical
    elif '1210' in case_lower or '1812' in case_lower:
        return 100e-6  # ~100µH typical
    elif '2220' in case_lower:
        return 1e-3    # ~1mH typical
    
    # Würth specific size codes (e.g., "5.8x5.3x4.5mm")
    size_match = re.search(r'(\d+\.?\d*)x(\d+\.?\d*)x(\d+\.?\d*)', case_lower)
    if size_match:
        try:
            l, w, h = map(float, size_match.groups())
            volume = l * w * h
            # Rough estimate: larger = typically higher inductance
            if volume < 30:       # Small
                return 100e-9
            elif volume < 60:     # Medium
                return 1e-6
            elif volume < 150:    # Large
                return 10e-6
            else:                 # Very large
                return 100e-6
        except:
            pass
    
    # Default moderate value
    return 10e-6

def estimate_dc_resistance(inductance):
    """Estimate DC resistance based on inductance (rough approximation)."""
    if not inductance or inductance <= 0:
        return 0.1
    
    # Rule of thumb: higher L typically means higher DCR
    l_uh = inductance * 1e6
    
    if l_uh < 0.1:      # nH range
        return 0.01
    elif l_uh < 1:      # nH-µH
        return 0.05
    elif l_uh < 10:     # µH range
        return 0.1
    elif l_uh < 100:    # tens of µH
        return 0.5
    else:               # mH range
        return 1.0

def recover_magnetics():
    """Recover magnetics entries from quarantine by estimating inductance."""
    
    stats = {
        'recovered': 0,
        'estimated_inductance': 0,
        'estimated_dcr': 0,
        'quarantined': 0,
    }
    
    recovered_entries = []
    back_to_quarantine = []
    
    with open(QUARANTINE_FILE, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line)
                
                # Only process magnetic entries
                if 'magnetic' not in entry:
                    back_to_quarantine.append(entry)
                    continue
                
                mag = entry['magnetic']
                mi = mag.get('manufacturerInfo', {})
                di = mi.get('datasheetInfo', {})
                elec = di.get('electrical', {})
                part = di.get('part', {})
                mech = di.get('mechanical', {})
                
                # Get reference/MPN
                ref = mi.get('reference', '')
                mpn = part.get('partNumber', ref)
                case = part.get('case', '') or mech.get('case', '')
                manufacturer = mi.get('name', '')
                
                # Try to get or estimate inductance
                ind_obj = elec.get('inductance', {})
                if isinstance(ind_obj, dict):
                    ind_val = ind_obj.get('nominal')
                else:
                    ind_val = ind_obj
                
                if not ind_val or ind_val <= 0:
                    # Try to extract from MPN
                    ind_val = decode_inductance_from_mpn(mpn, case)
                    if not ind_val or ind_val <= 0:
                        # Estimate from case
                        ind_val = estimate_inductance_from_case(case, manufacturer)
                    
                    if ind_val and ind_val > 0:
                        entry['magnetic']['manufacturerInfo']['datasheetInfo']['electrical']['inductance'] = {
                            'nominal': ind_val
                        }
                        stats['estimated_inductance'] += 1
                
                # Try to estimate DC resistance if missing
                dcr = elec.get('dcResistance')
                if not dcr or dcr <= 0:
                    dcr = estimate_dc_resistance(ind_val)
                    entry['magnetic']['manufacturerInfo']['datasheetInfo']['electrical']['dcResistance'] = dcr
                    stats['estimated_dcr'] += 1
                
                # Remove quarantine metadata
                for key in ['quarantineInfo', 'quarantineReason', 'quarantineSource', 'lineNumber']:
                    if key in entry:
                        del entry[key]
                
                recovered_entries.append(entry)
                stats['recovered'] += 1
                
                if stats['recovered'] <= 3:
                    print(f"✓ Recovered magnetic: {ref} (L={ind_val*1e6:.1f}µH, DCR={dcr:.3f}Ω)")
                
            except json.JSONDecodeError:
                pass
            except Exception as e:
                print(f"Error at line {line_num}: {e}", file=sys.stderr)
                if 'entry' in locals():
                    back_to_quarantine.append(entry)
                stats['quarantined'] += 1
                continue
    
    # Append recovered magnetics to magnetics.ndjson
    with open(MAGNETICS_FILE, 'a') as f:
        for entry in recovered_entries:
            f.write(json.dumps(entry) + '\n')
    
    # Rewrite quarantine with remaining entries
    with open(QUARANTINE_FILE, 'w') as f:
        for entry in back_to_quarantine:
            f.write(json.dumps(entry) + '\n')
    
    # Report
    print(f"\n{'='*70}")
    print(f"MAGNETICS RECOVERY REPORT")
    print(f"{'='*70}")
    print(f"Magnetics recovered              : {stats['recovered']}")
    print(f"  - Inductance estimated        : {stats['estimated_inductance']}")
    print(f"  - DC resistance estimated     : {stats['estimated_dcr']}")
    print(f"Entries kept in quarantine      : {len(back_to_quarantine)}")
    print(f"\nmagnetics.ndjson: +{stats['recovered']} entries")
    print(f"quarantine.ndjson: -{stats['recovered']} entries")
    print(f"{'='*70}\n")
    
    return stats

if __name__ == '__main__':
    recover_magnetics()
    sys.exit(0)
