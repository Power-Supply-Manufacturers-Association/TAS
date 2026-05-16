#!/usr/bin/env python3
"""
Validate Phase 5 magnetics and resistor entries.
"""

import json
import sys
from pathlib import Path
from typing import Tuple

def validate_magnetic(entry: dict) -> Tuple[bool, str]:
    """Validate a magnetic entry."""
    try:
        if "magnetic" in entry:
            mag_data = entry["magnetic"]
        else:
            mag_data = entry
        
        if "core" not in mag_data or "coil" not in mag_data:
            return False, "Missing core or coil"
        
        return True, "Valid"
    except Exception as e:
        return False, f"Exception: {str(e)}"

def validate_resistor(entry: dict) -> Tuple[bool, str]:
    """Validate a resistor entry."""
    try:
        if "resistor" in entry:
            res_data = entry["resistor"]
        else:
            res_data = entry
        
        mfr_info = res_data.get("manufacturerInfo", {})
        ds_info = mfr_info.get("datasheetInfo", {})
        
        if not ds_info:
            return False, "Missing datasheetInfo"
        
        electrical = ds_info.get("electrical", {})
        if "powerRating" not in electrical or "resistance" not in electrical:
            return False, "Missing electrical fields"
        
        return True, "Valid"
    except Exception as e:
        return False, f"Exception: {str(e)}"

def validate_file(filepath: str, validator_fn) -> Tuple[int, int, list]:
    """Validate all entries in a file."""
    valid_count = 0
    invalid_count = 0
    errors = []
    
    with open(filepath, "r") as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue
            
            try:
                entry = json.loads(line)
                is_valid, msg = validator_fn(entry)
                
                if is_valid:
                    valid_count += 1
                else:
                    invalid_count += 1
                    if len(errors) < 5:
                        errors.append(f"Line {line_num}: {msg}")
            except json.JSONDecodeError as e:
                invalid_count += 1
                if len(errors) < 5:
                    errors.append(f"Line {line_num}: JSON decode error")
    
    return valid_count, invalid_count, errors

def main():
    """Validate Phase 5 entries."""
    print("🔍 Validating Phase 5 entries...")
    print("=" * 60)
    
    # Validate magnetics
    mag_file = Path("/home/alf/OpenConverters/Proteus/TAS/phase5_inductors_candidates.ndjson")
    if mag_file.exists():
        valid, invalid, errors = validate_file(str(mag_file), validate_magnetic)
        print(f"\n📍 Inductors:")
        print(f"   ✓ Valid: {valid:,d}")
        print(f"   ✗ Invalid: {invalid:,d}")
        if errors:
            for err in errors[:3]:
                print(f"      {err}")
    
    # Validate resistors
    res_file = Path("/home/alf/OpenConverters/Proteus/TAS/phase5_resistors_candidates.ndjson")
    if res_file.exists():
        valid, invalid, errors = validate_file(str(res_file), validate_resistor)
        print(f"\n📍 Resistor Variants:")
        print(f"   ✓ Valid: {valid:,d}")
        print(f"   ✗ Invalid: {invalid:,d}")
        if errors:
            for err in errors[:3]:
                print(f"      {err}")

if __name__ == "__main__":
    main()
