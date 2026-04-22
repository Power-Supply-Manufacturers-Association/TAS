#!/usr/bin/env python3
"""
Resolve NDJSON merge conflicts by keeping all entries from both sides.
Usage: python3 resolve_ndjson_conflicts.py <file>
"""
import sys
import json
import re

def resolve_conflicts(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Pattern to match git conflict blocks
    # <<<<<<< HEAD
    # ...lines...
    # =======
    # ...lines...
    # >>>>>>> branch-name
    pattern = r'<<<<<<< HEAD\n(.*?)=======(.*?)>>>>>>> [^\n]+\n'
    
    def replace_conflict(match):
        head_lines = match.group(1).strip('\n')
        their_lines = match.group(2).strip('\n')
        
        # Combine both sides, removing empty lines
        all_lines = []
        for line in (head_lines + '\n' + their_lines).split('\n'):
            line = line.strip()
            if line:
                all_lines.append(line)
        
        # Remove exact duplicates while preserving order
        seen = set()
        unique_lines = []
        for line in all_lines:
            if line not in seen:
                seen.add(line)
                unique_lines.append(line)
        
        return '\n'.join(unique_lines) + '\n'
    
    resolved = re.sub(pattern, replace_conflict, content, flags=re.DOTALL)
    
    # Verify no conflict markers remain
    if '<<<<<<<' in resolved:
        print(f"ERROR: Still has conflict markers after resolution: {filepath}")
        sys.exit(1)
    
    with open(filepath, 'w') as f:
        f.write(resolved)
    
    print(f"Resolved conflicts in {filepath}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 resolve_ndjson_conflicts.py <file>")
        sys.exit(1)
    
    for filepath in sys.argv[1:]:
        resolve_conflicts(filepath)
