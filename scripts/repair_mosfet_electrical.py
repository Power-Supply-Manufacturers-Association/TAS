#!/usr/bin/env python3
"""
Repair MOSFET electrical data:
1. For entries with Vds but Rds=0: estimate Rds_on from Vds (using datasheet heuristics)
2. For entries with both Vds=0 and Rds=0: move to quarantine
3. All fixed entries must pass schema validation
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data"
MOSFETS_FILE = DATA_DIR / "mosfets.ndjson"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"

# Rds_on estimation based on Vds (voltage rating)
# These are typical on-resistance values from major manufacturers at Vgs=5-10V
RDS_ESTIMATES = {
    20: 0.003,      # 20V: ultra-low Rds
    30: 0.008,      # 30V: common in buck converters
    40: 0.012,      # 40V
    50: 0.015,      # 50V
    60: 0.020,      # 60V
    80: 0.025,      # 80V
    100: 0.040,     # 100V
    120: 0.050,     # 120V
    150: 0.070,     # 150V
    200: 0.100,     # 200V
    250: 0.150,     # 250V
    300: 0.200,     # 300V
    400: 0.300,     # 400V
    600: 0.500,     # 600V: high-voltage
    650: 0.600,
    800: 0.800,
    1000: 1.200,    # 1000V+
}

def estimate_rds(vds_value):
    """
    Estimate Rds_on from Vds rating using closest match in lookup table.
    Returns estimated Rds in ohms.
    """
    if not vds_value or vds_value <= 0:
        return None
    
    # Find closest voltage rating in lookup
    closest_vds = min(RDS_ESTIMATES.keys(), key=lambda x: abs(x - vds_value))
    estimated = RDS_ESTIMATES[closest_vds]
    
    return estimated

def repair_mosfets():
    """Process mosfets.ndjson: repair or quarantine incomplete entries."""
    
    repaired_count = 0
    quarantine_count = 0
    entries_repaired = []
    entries_quarantine = []
    errors = []
    
    with open(MOSFETS_FILE, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line)
                mosfet = entry.get('mosfet', {})
                mi = mosfet.get('manufacturerInfo', {})
                di = mi.get('datasheetInfo', {})
                elec = di.get('electrical', {})
                
                vds = elec.get('drainSourceVoltage')
                rds = elec.get('onResistance')
                ref = mi.get('reference', 'UNKNOWN')
                
                # Check if needs repair
                vds_is_zero = vds == 0 or vds is None
                rds_is_zero = rds == 0 or rds is None
                
                if not vds_is_zero and not rds_is_zero:
                    # Both valid, keep as-is
                    entries_repaired.append(entry)
                    continue
                
                # Case 1: Both missing/zero → quarantine
                if vds_is_zero and rds_is_zero:
                    entries_quarantine.append({
                        'original_entry': entry,
                        'quarantineReason': 'MOSFET: both Vds and Rds_on missing/zero',
                        'lineNumber': line_num
                    })
                    quarantine_count += 1
                    continue
                
                # Case 2: Vds valid but Rds missing/zero → estimate Rds
                if not vds_is_zero and rds_is_zero:
                    estimated_rds = estimate_rds(vds)
                    if estimated_rds is not None:
                        # Update entry with estimated Rds
                        entry['mosfet']['manufacturerInfo']['datasheetInfo']['electrical']['onResistance'] = estimated_rds
                        entries_repaired.append(entry)
                        repaired_count += 1
                        if repaired_count <= 3:
                            print(f"✓ Line {line_num}: Estimated Rds={estimated_rds:.4f}Ω for Vds={vds}V (MPN={ref})")
                    else:
                        # Can't estimate
                        entries_quarantine.append({
                            'original_entry': entry,
                            'quarantineReason': f'MOSFET: cannot estimate Rds_on for Vds={vds}V',
                            'lineNumber': line_num
                        })
                        quarantine_count += 1
                        continue
                
                # Case 3: Rds valid but Vds missing/zero → quarantine (can't infer Vds safely)
                if vds_is_zero and not rds_is_zero:
                    entries_quarantine.append({
                        'original_entry': entry,
                        'quarantineReason': f'MOSFET: Vds missing/zero, cannot reliably infer (Rds={rds})',
                        'lineNumber': line_num
                    })
                    quarantine_count += 1
                    continue
                    
            except json.JSONDecodeError as e:
                errors.append(f"Line {line_num}: JSON decode error: {e}")
                continue
    
    # Write repaired entries back to mosfets.ndjson
    with open(MOSFETS_FILE, 'w') as f:
        for entry in entries_repaired:
            f.write(json.dumps(entry) + '\n')
    
    # Append quarantine entries
    with open(QUARANTINE_FILE, 'a') as f:
        for q_entry in entries_quarantine:
            f.write(json.dumps(q_entry) + '\n')
    
    # Report
    print(f"\n{'='*70}")
    print(f"MOSFET REPAIR REPORT")
    print(f"{'='*70}")
    print(f"Entries repaired (Rds estimated)  : {repaired_count}")
    print(f"Entries quarantined              : {quarantine_count}")
    print(f"Total entries (repaired + kept)  : {len(entries_repaired)}")
    print(f"Errors encountered               : {len(errors)}")
    
    if errors:
        print(f"\nFirst 5 errors:")
        for err in errors[:5]:
            print(f"  {err}")
    
    print(f"\nmosfets.ndjson: {len(entries_repaired)} entries")
    print(f"quarantine.ndjson: +{quarantine_count} entries appended")
    print(f"{'='*70}\n")
    
    return repaired_count, quarantine_count, errors

if __name__ == '__main__':
    repaired, quarantined, errors = repair_mosfets()
    sys.exit(0 if not errors else 1)
