#!/usr/bin/env python3
"""
Validate Phase 2 MLCC entries against CAS schema.
"""

import json
import sys
from pathlib import Path
from typing import Tuple

def validate_mlcc_entry(entry: dict) -> Tuple[bool, str]:
    """Validate a single MLCC entry against CAS requirements."""
    
    try:
        # Extract capacitor data
        if "capacitor" in entry:
            cap_data = entry["capacitor"]
        else:
            cap_data = entry
        
        mfr_info = cap_data.get("manufacturerInfo", {})
        ds_info = mfr_info.get("datasheetInfo", {})
        
        # Check required sections
        if not ds_info:
            return False, "Missing datasheetInfo"
        
        part = ds_info.get("part", {})
        electrical = ds_info.get("electrical", {})
        mechanical = ds_info.get("mechanical", {})
        
        # Check required part fields
        required_part = ["partNumber", "series", "technology", "case"]
        for field in required_part:
            if field not in part:
                return False, f"Missing part.{field}"
        
        # Check required electrical fields
        required_electrical = ["capacitance", "ratedVoltage"]
        for field in required_electrical:
            if field not in electrical:
                return False, f"Missing electrical.{field}"
        
        # Validate capacitance object
        cap = electrical.get("capacitance", {})
        if not all(k in cap for k in ["nominal", "minimum", "maximum"]):
            return False, "Invalid capacitance object"
        
        # Check physical constraints
        if electrical["ratedVoltage"] <= 0:
            return False, "Invalid ratedVoltage"
        
        if electrical["capacitance"]["nominal"] <= 0:
            return False, "Invalid capacitance value"
        
        # Check mechanical
        if not mechanical or "dimensions" not in mechanical:
            return False, "Missing mechanical.dimensions"
        
        return True, "Valid"
        
    except Exception as e:
        return False, f"Exception: {str(e)}"

def main():
    """Validate all Phase 2 MLCC entries."""
    input_path = Path("/home/alf/OpenConverters/Proteus/TAS/phase2_mlcc_candidates.ndjson")
    
    if not input_path.exists():
        print(f"❌ File not found: {input_path}")
        sys.exit(1)
    
    print("🔍 Validating Phase 2 MLCC entries...")
    print("=" * 60)
    
    valid_count = 0
    invalid_count = 0
    errors = []
    
    with open(input_path, "r") as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue
            
            try:
                entry = json.loads(line)
                is_valid, msg = validate_mlcc_entry(entry)
                
                if is_valid:
                    valid_count += 1
                else:
                    invalid_count += 1
                    if len(errors) < 10:  # Save first 10 errors
                        errors.append(f"Line {line_num}: {msg}")
            
            except json.JSONDecodeError as e:
                invalid_count += 1
                if len(errors) < 10:
                    errors.append(f"Line {line_num}: JSON decode error")
    
    print(f"✓ Valid entries: {valid_count}")
    print(f"✗ Invalid entries: {invalid_count}")
    
    if errors:
        print(f"\n📋 First errors:")
        for err in errors:
            print(f"  {err}")
    
    if invalid_count == 0:
        print("\n✅ All entries are valid!")
        sys.exit(0)
    else:
        print(f"\n⚠️  {invalid_count} entries failed validation")
        sys.exit(1)

if __name__ == "__main__":
    main()
