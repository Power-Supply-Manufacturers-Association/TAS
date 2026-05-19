#!/usr/bin/env python3
"""
Resolve NDJSON merge conflicts by keeping all unique records from both sides.
Handles both inline conflicts and whole-file conflicts.
"""

import sys
import json
import re
from collections import OrderedDict

def get_record_key(record):
    """Extract a unique key from a record."""
    if not isinstance(record, dict):
        return json.dumps(record, sort_keys=True)
    
    # Try different possible key paths for different component types
    for path in [
        ['capacitor', 'manufacturerInfo', 'datasheetInfo', 'part', 'partNumber'],
        ['capacitor', 'manufacturerInfo', 'datasheetInfo', 'part', 'reference'],
        ['magnetic', 'manufacturerInfo', 'reference'],
        ['magnetic', 'manufacturerInfo', 'datasheetInfo', 'part', 'partNumber'],
        ['diode', 'manufacturerInfo', 'reference'],
        ['diode', 'manufacturerInfo', 'datasheetInfo', 'part', 'partNumber'],
        ['mosfet', 'manufacturerInfo', 'reference'],
        ['mosfet', 'manufacturerInfo', 'datasheetInfo', 'part', 'partNumber'],
        ['igbt', 'manufacturerInfo', 'reference'],
        ['igbt', 'manufacturerInfo', 'datasheetInfo', 'part', 'partNumber'],
        ['resistor', 'manufacturerInfo', 'datasheetInfo', 'part', 'partNumber'],
        ['semiconductor', 'diode', 'manufacturerInfo', 'reference'],
        ['semiconductor', 'diode', 'manufacturerInfo', 'datasheetInfo', 'part', 'partNumber'],
    ]:
        val = record
        for key in path:
            if isinstance(val, dict) and key in val:
                val = val[key]
            else:
                val = None
                break
        if val:
            return str(val)
    
    # Fallback: use JSON string as key
    return json.dumps(record, sort_keys=True)

def parse_ndjson(text):
    """Parse NDJSON text into list of records."""
    records = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('<<<<<<<') or line.startswith('>>>>>>>') or line.startswith('======='):
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # Skip invalid lines
    return records

def extract_versions(content):
    """Extract HEAD and incoming versions from conflicted content."""
    # Check if entire file is a conflict
    if content.startswith('<<<<<<< HEAD'):
        parts = content.split('\n=======\n')
        if len(parts) == 2:
            head = parts[0].replace('<<<<<<< HEAD\n', '')
            incoming_parts = parts[1].split('\n>>>>>>> ')
            incoming = incoming_parts[0]
            return head.strip(), incoming.strip()
    
    # Handle inline conflicts
    pattern = r'<<<<<<< HEAD\n(.*?)\n=======\n(.*?)\n>>>>>>> [^\n]+'
    matches = list(re.finditer(pattern, content, re.DOTALL))
    
    if not matches:
        return content, None
    
    head_parts = []
    incoming_parts = []
    last_end = 0
    
    for match in matches:
        start = match.start()
        head_parts.append(content[last_end:start])
        head_parts.append('\n' + match.group(1) + '\n')
        incoming_parts.append(content[last_end:start])
        incoming_parts.append('\n' + match.group(2) + '\n')
        last_end = match.end()
    
    head_parts.append(content[last_end:])
    incoming_parts.append(content[last_end:])
    
    head = ''.join(head_parts).strip()
    incoming = ''.join(incoming_parts).strip()
    
    return head, incoming

def resolve_conflicts(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    
    print(f"\nProcessing {filepath}...")
    
    head_text, incoming_text = extract_versions(content)
    
    if incoming_text is None:
        print(f"  No conflicts found")
        return
    
    print(f"  Parsing versions...")
    head_records = parse_ndjson(head_text)
    incoming_records = parse_ndjson(incoming_text)
    print(f"    HEAD: {len(head_records)} records")
    print(f"    Incoming: {len(incoming_records)} records")
    
    # Merge by keeping all unique records
    merged = OrderedDict()
    
    for record in head_records:
        key = get_record_key(record)
        merged[key] = record
    
    kept_from_head = len(merged)
    
    for record in incoming_records:
        key = get_record_key(record)
        if key not in merged:
            merged[key] = record
        else:
            # Prefer record with more data (larger JSON)
            existing = merged[key]
            if len(json.dumps(record)) > len(json.dumps(existing)):
                merged[key] = record
    
    print(f"  Merged: {len(merged)} unique records")
    print(f"    Kept all {kept_from_head} from HEAD")
    print(f"    Added {len(merged) - kept_from_head} from incoming")
    
    # Write merged file
    with open(filepath, 'w', encoding='utf-8') as f:
        for record in merged.values():
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    
    print(f"  Written successfully")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 resolve_ndjson_conflicts.py <file1> [<file2> ...]")
        sys.exit(1)
    
    for filepath in sys.argv[1:]:
        resolve_conflicts(filepath)
    
    print("\n" + "="*60)
    print("All conflicts resolved!")
    print("Run: git add -A && git commit -m 'Resolved NDJSON merge conflicts'")
