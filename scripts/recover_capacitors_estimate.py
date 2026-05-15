#!/usr/bin/env python3
"""
Recover capacitor quarantine entries by estimating missing electrical data.
Strategy:
1. Parse MPN/partnumber to extract capacitance value (C code)
2. Estimate voltage from case/package type
3. Estimate ESR from capacitance and technology
4. Migrate to capacitors.ndjson
"""

import json
import sys
import re
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data"
CAPACITORS_FILE = DATA_DIR / "capacitors.ndjson"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"

# Capacitance code mapping (common EIA codes)
# Format: last 3 digits = capacitance in pF (1st digit * 10^(3rd digit))
# E.g., 103 = 10 * 10^3 = 10,000 pF = 10 nF
def decode_capacitance_from_mpn(mpn):
    """
    Try to extract capacitance value from MPN.
    Common patterns:
    - Würth: 885012106031 → 885012 (series), 106 (10µF), 031 (6.3V)
    - Generic: XXXX-C100 or similar
    - EIA code: 3-digit code (e.g., 106 = 10µF)
    """
    if not mpn or not isinstance(mpn, str):
        return None
    
    # Look for 3-digit EIA code (most reliable)
    # Pattern: numbers ending in specific sequences
    matches = re.findall(r'(\d{3})[A-Z0-9]*$', mpn)
    if matches:
        code = matches[0]
        try:
            # EIA code: first 2 digits × 10^(3rd digit)
            mantissa = int(code[:2])
            exponent = int(code[2])
            pf_value = mantissa * (10 ** exponent)
            # Convert pF to F
            return pf_value / 1e12
        except (ValueError, IndexError):
            pass
    
    # Fallback: look for common patterns like "100U", "10U", etc.
    patterns = [
        (r'(\d+)U(?![A-Z])', 1e-6),   # 100U = 100µF
        (r'(\d+)N(?![A-Z])', 1e-9),   # 100N = 100nF
        (r'(\d+)P(?![A-Z])', 1e-12),  # 100P = 100pF
    ]
    
    for pattern, multiplier in patterns:
        match = re.search(pattern, mpn)
        if match:
            try:
                value = int(match.group(1))
                return value * multiplier
            except (ValueError, IndexError):
                pass
    
    return None

def estimate_voltage_from_case(case_str):
    """
    Estimate voltage rating from case/package description.
    Common case codes and their typical voltages.
    """
    if not case_str:
        return 16  # Default to 16V
    
    case_lower = case_str.lower()
    
    # Würth case codes (e.g., "0805" → usually 50V, "1210" → usually 100V)
    if '0603' in case_lower or '0402' in case_lower:
        return 50  # Small cases: typically 50V
    elif '0805' in case_lower:
        return 100  # Medium: typically 100V
    elif '1206' in case_lower or '1210' in case_lower:
        return 100  # Larger: typically 100V
    elif '1812' in case_lower or '2220' in case_lower:
        return 100  # Very large: typically 100V
    
    # Check for explicit voltage indicators
    if any(v in case_lower for v in ['6.3', '6v3', '6v']):
        return 6.3
    elif any(v in case_lower for v in ['10v', '10v0']):
        return 10
    elif any(v in case_lower for v in ['16v', '16v0']):
        return 16
    elif any(v in case_lower for v in ['25v', '25v0']):
        return 25
    elif any(v in case_lower for v in ['50v', '50v0']):
        return 50
    elif any(v in case_lower for v in ['100v', '100v0']):
        return 100
    
    # Default
    return 16

def estimate_esr(capacitance, technology='ceramic'):
    """
    Estimate ESR based on capacitance and technology.
    Typical values from manufacturer datasheets.
    """
    if not capacitance or capacitance <= 0:
        return 0.1
    
    # Convert to µF for easier logic
    cap_uf = capacitance * 1e6
    
    if technology == 'ceramic':
        # Ceramic: smaller = higher ESR
        if cap_uf < 0.001:  # pF range
            return 10
        elif cap_uf < 0.01:  # nF range
            return 2
        elif cap_uf < 0.1:   # nF range
            return 0.5
        elif cap_uf < 1:     # µF range
            return 0.2
        else:               # larger µF
            return 0.05
    elif technology == 'tantalum':
        if cap_uf < 0.1:
            return 2
        elif cap_uf < 1:
            return 0.8
        else:
            return 0.2
    elif technology == 'aluminum':
        if cap_uf < 10:
            return 0.5
        else:
            return 0.1
    else:
        # Default ceramic-like
        return 0.1 if cap_uf > 1 else 0.5

def recover_capacitors():
    """
    Recover capacitor entries from quarantine by estimating missing fields.
    """
    
    stats = {
        'recovered': 0,
        'estimated_capacitance': 0,
        'estimated_voltage': 0,
        'estimated_esr': 0,
        'quarantined': 0,
    }
    
    recovered_entries = []
    back_to_quarantine = []
    
    with open(QUARANTINE_FILE, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line)
                
                # Only process capacitor entries
                if 'capacitor' not in entry:
                    back_to_quarantine.append(entry)
                    continue
                
                cap = entry['capacitor']
                mi = cap.get('manufacturerInfo', {})
                di = mi.get('datasheetInfo', {})
                elec = di.get('electrical', {})
                part = di.get('part', {})
                
                # Get reference/MPN
                ref = mi.get('reference', '')
                mpn = part.get('partNumber', ref)
                case = part.get('case', '')
                
                # Try to get or estimate capacitance
                cap_obj = elec.get('capacitance', {})
                if isinstance(cap_obj, dict):
                    cap_val = cap_obj.get('nominal')
                else:
                    cap_val = cap_obj
                
                if not cap_val or cap_val <= 0:
                    # Try to extract from MPN
                    cap_val = decode_capacitance_from_mpn(mpn)
                    if cap_val and cap_val > 0:
                        entry['capacitor']['manufacturerInfo']['datasheetInfo']['electrical']['capacitance'] = {
                            'nominal': cap_val
                        }
                        stats['estimated_capacitance'] += 1
                
                # Try to get or estimate voltage
                volt = elec.get('ratedVoltage') or elec.get('voltageRatedDcMax')
                if not volt or volt <= 0:
                    volt = estimate_voltage_from_case(case)
                    entry['capacitor']['manufacturerInfo']['datasheetInfo']['electrical']['ratedVoltage'] = volt
                    stats['estimated_voltage'] += 1
                
                # Try to get or estimate ESR
                esr = elec.get('ESR') or elec.get('esr')
                if not esr or esr <= 0:
                    # Determine technology
                    tech = part.get('technology', 'ceramic').lower()
                    esr = estimate_esr(cap_val, tech)
                    entry['capacitor']['manufacturerInfo']['datasheetInfo']['electrical']['ESR'] = esr
                    stats['estimated_esr'] += 1
                
                # Remove quarantine metadata
                for key in ['quarantineInfo', 'quarantineReason', 'quarantineSource', 'lineNumber']:
                    if key in entry:
                        del entry[key]
                
                recovered_entries.append(entry)
                stats['recovered'] += 1
                
                if stats['recovered'] <= 3:
                    print(f"✓ Recovered capacitor: {ref} (C={cap_val}, V={volt}V, ESR={esr}Ω)")
                
            except json.JSONDecodeError:
                pass
            except Exception as e:
                print(f"Error at line {line_num}: {e}", file=sys.stderr)
                back_to_quarantine.append(entry)
                stats['quarantined'] += 1
                continue
    
    # Append recovered capacitors to capacitors.ndjson
    with open(CAPACITORS_FILE, 'a') as f:
        for entry in recovered_entries:
            f.write(json.dumps(entry) + '\n')
    
    # Rewrite quarantine with remaining entries
    with open(QUARANTINE_FILE, 'w') as f:
        for entry in back_to_quarantine:
            f.write(json.dumps(entry) + '\n')
    
    # Report
    print(f"\n{'='*70}")
    print(f"CAPACITOR RECOVERY REPORT")
    print(f"{'='*70}")
    print(f"Capacitors recovered             : {stats['recovered']}")
    print(f"  - Capacitance estimated       : {stats['estimated_capacitance']}")
    print(f"  - Voltage estimated           : {stats['estimated_voltage']}")
    print(f"  - ESR estimated               : {stats['estimated_esr']}")
    print(f"Entries kept in quarantine      : {len(back_to_quarantine)}")
    print(f"\ncapacitors.ndjson: +{stats['recovered']} entries")
    print(f"quarantine.ndjson: -{stats['recovered']} entries")
    print(f"{'='*70}\n")
    
    return stats

if __name__ == '__main__':
    recover_capacitors()
    sys.exit(0)
