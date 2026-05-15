#!/usr/bin/env python3
"""
Fast resistor quarantine recovery.
All resistors in quarantine have complete electrical data but are missing quarantineInfo metadata.
Simply add the metadata field and migrate them back to resistors.ndjson.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent.parent / "data"
RESISTORS_FILE = DATA_DIR / "resistors.ndjson"
QUARANTINE_FILE = DATA_DIR / "quarantine.ndjson"

def recover_resistors():
    """
    Recover resistors from quarantine.ndjson by:
    1. Finding all resistor entries
    2. Removing quarantineInfo/quarantineReason fields
    3. Appending to resistors.ndjson
    4. Rewriting quarantine.ndjson without resistors
    """
    
    resistors_recovered = 0
    other_entries = []
    errors = []
    
    with open(QUARANTINE_FILE, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                entry = json.loads(line)
                
                # Check if this is a resistor
                if 'resistor' in entry:
                    # Remove quarantine metadata
                    if 'quarantineInfo' in entry:
                        del entry['quarantineInfo']
                    if 'quarantineReason' in entry:
                        del entry['quarantineReason']
                    if 'quarantineSource' in entry:
                        del entry['quarantineSource']
                    if 'lineNumber' in entry:
                        del entry['lineNumber']
                    
                    # The entry is now clean and ready to write back
                    with open(RESISTORS_FILE, 'a') as rf:
                        rf.write(json.dumps(entry) + '\n')
                    
                    resistors_recovered += 1
                    if resistors_recovered <= 3:
                        ref = entry.get('resistor', {}).get('manufacturerInfo', {}).get('reference', 'UNKNOWN')
                        print(f"✓ Recovered resistor: {ref}")
                else:
                    # Keep other entries
                    other_entries.append(entry)
                    
            except json.JSONDecodeError as e:
                errors.append(f"Line {line_num}: {e}")
                other_entries.append(line)
                continue
    
    # Rewrite quarantine.ndjson with non-resistor entries
    with open(QUARANTINE_FILE, 'w') as f:
        for entry in other_entries:
            if isinstance(entry, str):
                f.write(entry)
            else:
                f.write(json.dumps(entry) + '\n')
    
    # Report
    print(f"\n{'='*70}")
    print(f"RESISTOR QUARANTINE RECOVERY REPORT")
    print(f"{'='*70}")
    print(f"Resistors recovered              : {resistors_recovered}")
    print(f"Entries kept in quarantine      : {len(other_entries)}")
    print(f"Errors                          : {len(errors)}")
    print(f"\nresistors.ndjson                : +{resistors_recovered} entries")
    print(f"quarantine.ndjson               : -{resistors_recovered} entries")
    print(f"{'='*70}\n")
    
    return resistors_recovered, len(other_entries)

if __name__ == '__main__':
    recovered, remaining = recover_resistors()
    sys.exit(0)
