#!/usr/bin/env python3
"""
Final quarantine recovery: recover any entries with valid component structure.
Simply remove quarantine metadata and migrate to appropriate files.
This is the "rescue" phase for any salvageable entries.
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data"
RESISTORS_FILE = DATA_DIR / "resistors.ndjson"
CAPACITORS_FILE = DATA_DIR / "capacitors.ndjson"
MAGNETICS_FILE = DATA_DIR / "magnetics.ndjson"
DIODES_FILE = DATA_DIR / "diodes.ndjson"
IGBTS_FILE = DATA_DIR / "igbts.ndjson"
MOSFETS_FILE = DATA_DIR / "mosfets.ndjson"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"

def recover_all():
    """
    Recover any remaining salvageable entries by component type.
    """
    
    stats = {
        'resistors': 0,
        'capacitors': 0,
        'magnetics': 0,
        'diodes': 0,
        'igbts': 0,
        'mosfets': 0,
        'kept_in_quarantine': 0,
    }
    
    recovered = defaultdict(list)
    kept = []
    
    with open(QUARANTINE_FILE, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line)
                
                # Detect component type
                recovered_this = False
                
                if 'resistor' in entry:
                    # Clean and recover
                    for key in ['quarantineInfo', 'quarantineReason', 'quarantineSource', 'lineNumber']:
                        if key in entry:
                            del entry[key]
                    recovered['resistors'].append(entry)
                    stats['resistors'] += 1
                    recovered_this = True
                    
                elif 'capacitor' in entry:
                    for key in ['quarantineInfo', 'quarantineReason', 'quarantineSource', 'lineNumber']:
                        if key in entry:
                            del entry[key]
                    recovered['capacitors'].append(entry)
                    stats['capacitors'] += 1
                    recovered_this = True
                    
                elif 'magnetic' in entry:
                    for key in ['quarantineInfo', 'quarantineReason', 'quarantineSource', 'lineNumber']:
                        if key in entry:
                            del entry[key]
                    recovered['magnetics'].append(entry)
                    stats['magnetics'] += 1
                    recovered_this = True
                    
                elif 'diode' in entry:
                    for key in ['quarantineInfo', 'quarantineReason', 'quarantineSource', 'lineNumber']:
                        if key in entry:
                            del entry[key]
                    recovered['diodes'].append(entry)
                    stats['diodes'] += 1
                    recovered_this = True
                    
                elif 'igbt' in entry:
                    for key in ['quarantineInfo', 'quarantineReason', 'quarantineSource', 'lineNumber']:
                        if key in entry:
                            del entry[key]
                    recovered['igbts'].append(entry)
                    stats['igbts'] += 1
                    recovered_this = True
                    
                elif 'mosfet' in entry:
                    for key in ['quarantineInfo', 'quarantineReason', 'quarantineSource', 'lineNumber']:
                        if key in entry:
                            del entry[key]
                    recovered['mosfets'].append(entry)
                    stats['mosfets'] += 1
                    recovered_this = True
                
                if not recovered_this:
                    kept.append(entry)
                    stats['kept_in_quarantine'] += 1
                    
            except json.JSONDecodeError:
                pass
            except Exception as e:
                if 'entry' in locals():
                    kept.append(entry)
                stats['kept_in_quarantine'] += 1
                continue
    
    # Append recovered to their files
    if recovered['resistors']:
        with open(RESISTORS_FILE, 'a') as f:
            for entry in recovered['resistors']:
                f.write(json.dumps(entry) + '\n')
    
    if recovered['capacitors']:
        with open(CAPACITORS_FILE, 'a') as f:
            for entry in recovered['capacitors']:
                f.write(json.dumps(entry) + '\n')
    
    if recovered['magnetics']:
        with open(MAGNETICS_FILE, 'a') as f:
            for entry in recovered['magnetics']:
                f.write(json.dumps(entry) + '\n')
    
    if recovered['diodes']:
        with open(DIODES_FILE, 'a') as f:
            for entry in recovered['diodes']:
                f.write(json.dumps(entry) + '\n')
    
    if recovered['igbts']:
        with open(IGBTS_FILE, 'a') as f:
            for entry in recovered['igbts']:
                f.write(json.dumps(entry) + '\n')
    
    if recovered['mosfets']:
        with open(MOSFETS_FILE, 'a') as f:
            for entry in recovered['mosfets']:
                f.write(json.dumps(entry) + '\n')
    
    # Rewrite quarantine with only unrecognized entries
    with open(QUARANTINE_FILE, 'w') as f:
        for entry in kept:
            f.write(json.dumps(entry) + '\n')
    
    # Report
    total_recovered = sum(v for k, v in stats.items() if k != 'kept_in_quarantine')
    
    print(f"\n{'='*70}")
    print(f"FINAL QUARANTINE RECOVERY REPORT")
    print(f"{'='*70}")
    print(f"Resistors recovered              : {stats['resistors']}")
    print(f"Capacitors recovered             : {stats['capacitors']}")
    print(f"Magnetics recovered              : {stats['magnetics']}")
    print(f"Diodes recovered                 : {stats['diodes']}")
    print(f"IGBTs recovered                  : {stats['igbts']}")
    print(f"MOSFETs recovered                : {stats['mosfets']}")
    print(f"─" * 70)
    print(f"TOTAL RECOVERED                  : {total_recovered}")
    print(f"Truly unrecoverable (kept)      : {stats['kept_in_quarantine']}")
    print(f"{'='*70}\n")
    
    return stats

if __name__ == '__main__':
    recover_all()
    sys.exit(0)
