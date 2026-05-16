#!/usr/bin/env python3
"""
Validate Phase 3 resistor and MOSFET entries against schemas.
"""

import json
import sys
from pathlib import Path
from typing import Tuple

def validate_resistor(entry: dict) -> Tuple[bool, str]:
    """Validate a resistor entry against RAS requirements."""
    try:
        if "resistor" in entry:
            res_data = entry["resistor"]
        else:
            res_data = entry
        
        mfr_info = res_data.get("manufacturerInfo", {})
        ds_info = mfr_info.get("datasheetInfo", {})
        
        if not ds_info:
            return False, "Missing datasheetInfo"
        
        part = ds_info.get("part", {})
        electrical = ds_info.get("electrical", {})
        
        # Check required part fields
        required_part = ["partNumber", "technology", "case"]
        for field in required_part:
            if field not in part:
                return False, f"Missing part.{field}"
        
        # Check required electrical fields
        if "resistance" not in electrical:
            return False, "Missing electrical.resistance"
        
        if "powerRating" not in electrical:
            return False, "Missing electrical.powerRating"
        
        # Validate resistance object
        res = electrical.get("resistance", {})
        if not all(k in res for k in ["nominal", "minimum", "maximum"]):
            return False, "Invalid resistance object"
        
        if electrical["resistance"]["nominal"] <= 0:
            return False, "Invalid resistance value"
        
        if electrical["powerRating"] <= 0:
            return False, "Invalid powerRating"
        
        return True, "Valid"
    except Exception as e:
        return False, f"Exception: {str(e)}"

def validate_mosfet(entry: dict) -> Tuple[bool, str]:
    """Validate a MOSFET entry against SAS requirements."""
    try:
        if "mosfet" in entry:
            mos_data = entry["mosfet"]
        else:
            mos_data = entry
        
        mfr_info = mos_data.get("manufacturerInfo", {})
        ds_info = mfr_info.get("datasheetInfo", {})
        
        if not ds_info:
            return False, "Missing datasheetInfo"
        
        part = ds_info.get("part", {})
        electrical = ds_info.get("electrical", {})
        
        if not part or not electrical:
            return False, "Missing part or electrical"
        
        # Check required electrical fields
        required_elec = [
            "drainSourceVoltage",
            "continuousDrainCurrent",
            "onResistance",
        ]
        for field in required_elec:
            if field not in electrical:
                return False, f"Missing electrical.{field}"
        
        if electrical["drainSourceVoltage"] <= 0:
            return False, "Invalid drainSourceVoltage"
        
        if electrical["continuousDrainCurrent"] <= 0:
            return False, "Invalid continuousDrainCurrent"
        
        if electrical["onResistance"] <= 0:
            return False, "Invalid onResistance"
        
        return True, "Valid"
    except Exception as e:
        return False, f"Exception: {str(e)}"

def validate_file(filepath: str, validator_fn, component_type: str) -> Tuple[int, int, list]:
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
    """Validate Phase 3 entries."""
    print("🔍 Validating Phase 3 entries...")
    print("=" * 60)
    
    # Validate resistors
    res_file = Path("/home/alf/OpenConverters/Proteus/TAS/phase3_resistors_candidates.ndjson")
    if res_file.exists():
        valid, invalid, errors = validate_file(str(res_file), validate_resistor, "Resistor")
        print(f"\n📍 Resistors:")
        print(f"   ✓ Valid: {valid:,d}")
        print(f"   ✗ Invalid: {invalid:,d}")
        if errors:
            print(f"   First errors:")
            for err in errors[:3]:
                print(f"      {err}")
    
    # Validate MOSFETs
    mos_file = Path("/home/alf/OpenConverters/Proteus/TAS/phase3_mosfets_candidates.ndjson")
    if mos_file.exists():
        valid, invalid, errors = validate_file(str(mos_file), validate_mosfet, "MOSFET")
        print(f"\n📍 MOSFETs:")
        print(f"   ✓ Valid: {valid:,d}")
        print(f"   ✗ Invalid: {invalid:,d}")
        if errors:
            print(f"   First errors:")
            for err in errors[:3]:
                print(f"      {err}")

if __name__ == "__main__":
    main()
