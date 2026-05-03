#!/usr/bin/env python3
"""
CRITICAL REPAIR: Fix quarantine.ndjson wrapper corruption
Date: 2026-04-09

ISSUE: 111 entries missing component type wrappers, 2 completely malformed.
ROOT CAUSE: Librarian added quarantine entries without proper nesting of component data inside
            component-type wrappers (resistor/magnetic/capacitor/diode/semiconductor/converter).

SOLUTION: Reconstruct wrapper based on quarantineInfo.sourceFile or infer from deviceType.
"""

import json
import sys

def repair_quarantine():
    with open('data/quarantine.ndjson') as f:
        lines = f.readlines()

    repaired = []
    repaired_count = 0
    dropped_count = 0

    for i, line in enumerate(lines, 1):
        entry = json.loads(line)
        keys = set(entry.keys()) - {'quarantineInfo'}
        component_keys = {'resistor', 'magnetic', 'capacitor', 'diode', 'semiconductor', 'converter'}
        valid_comp_keys = keys & component_keys

        # Already valid
        if len(valid_comp_keys) == 1:
            repaired.append(entry)
            continue

        # Completely malformed (entries with 'source', 'entry', 'reason' at top level)
        if 'source' in keys and 'entry' in keys:
            qinfo = entry.get('quarantineInfo', {})
            reason = qinfo.get('reason', '')

            # These are COMPLETELY broken - try to extract the nested entry
            inner_entry = entry.get('entry', {})
            if inner_entry:
                # Reconstruct with quarantineInfo
                fixed = {'quarantineInfo': qinfo}
                fixed.update(inner_entry)
                repaired.append(fixed)
                repaired_count += 1
                continue
            else:
                # No salvageable data - drop it
                print(f"DROP Line {i}: Completely malformed, no salvageable entry data")
                dropped_count += 1
                continue

        # Missing wrapper (has manufacturerInfo but no component type)
        if 'manufacturerInfo' in keys and len(valid_comp_keys) == 0:
            qinfo = entry.get('quarantineInfo', {})
            source = qinfo.get('sourceFile', '')

            # Try to infer component type from sourceFile
            inferred_type = None
            if source == 'resistors':
                inferred_type = 'resistor'
            elif source == 'magnetics':
                inferred_type = 'magnetic'
            elif source == 'capacitors':
                inferred_type = 'capacitor'
            elif source == 'diodes':
                inferred_type = 'diode'
            elif source in ['mosfets', 'igbts', 'semiconductors']:
                inferred_type = 'semiconductor'
            elif source == 'converters':
                inferred_type = 'converter'

            # Fallback: try deviceType from datasheetInfo
            if not inferred_type:
                device_type = entry.get('manufacturerInfo', {}).get('datasheetInfo', {}).get('part', {}).get('deviceType', '').lower()
                if 'diode' in device_type:
                    inferred_type = 'diode'
                elif 'resistor' in device_type:
                    inferred_type = 'resistor'
                elif 'capacitor' in device_type:
                    inferred_type = 'capacitor'

            if inferred_type:
                # Wrap the data
                fixed = {'quarantineInfo': qinfo, inferred_type: {}}

                # Copy component-specific data
                if inferred_type == 'diode':
                    fixed['diode']['manufacturerInfo'] = entry.get('manufacturerInfo')
                    if 'distributorsInfo' in entry:
                        fixed['diode']['distributorsInfo'] = entry.get('distributorsInfo')
                elif inferred_type == 'magnetic':
                    fixed['magnetic']['manufacturerInfo'] = entry.get('manufacturerInfo')
                    if 'distributorsInfo' in entry:
                        fixed['magnetic']['distributorsInfo'] = entry.get('distributorsInfo')
                elif inferred_type == 'capacitor':
                    fixed['capacitor']['manufacturerInfo'] = entry.get('manufacturerInfo')
                    if 'distributorsInfo' in entry:
                        fixed['capacitor']['distributorsInfo'] = entry.get('distributorsInfo')
                elif inferred_type == 'resistor':
                    fixed['resistor']['manufacturerInfo'] = entry.get('manufacturerInfo')
                elif inferred_type == 'semiconductor':
                    fixed['semiconductor']['manufacturerInfo'] = entry.get('manufacturerInfo')

                repaired.append(fixed)
                repaired_count += 1
                continue

        # Unknown structure - log and drop
        print(f"DROP Line {i}: Unknown structure, keys={keys}")
        dropped_count += 1

    # Write repaired file
    with open('data/quarantine.ndjson', 'w') as f:
        for entry in repaired:
            f.write(json.dumps(entry) + '\n')

    print(f"\n=== REPAIR SUMMARY ===")
    print(f"Total entries processed: {len(lines)}")
    print(f"  Kept valid: {len(repaired) - repaired_count}")
    print(f"  Repaired: {repaired_count}")
    print(f"  Dropped: {dropped_count}")
    print(f"Final quarantine entries: {len(repaired)}")

    return dropped_count == 0  # All data salvaged

if __name__ == '__main__':
    success = repair_quarantine()
    sys.exit(0 if success else 1)
