#!/usr/bin/env python3
"""
Validate Phase 4 IGBT and Diode entries against SAS schemas.
"""

import json
import sys
from pathlib import Path
from typing import Tuple

def validate_igbt(entry: dict) -> Tuple[bool, str]:
    """Validate an IGBT entry against SAS requirements."""
    try:
        if "igbt" in entry:
            igbt_data = entry["igbt"]
        else:
            igbt_data = entry
        
        mfr_info = igbt_data.get("manufacturerInfo", {})
        ds_info = mfr_info.get("datasheetInfo", {})
        
        if not ds_info:
            return False, "Missing datasheetInfo"
        
        part = ds_info.get("part", {})
        electrical = ds_info.get("electrical", {})
        
        if not part or not electrical:
            return False, "Missing part or electrical"
        
        # Check required electrical fields
        required_elec = [
            "collectorEmitterVoltage",
            "continuousCollectorCurrent",
            "collectorEmitterSaturation",
        ]
        for field in required_elec:
            if field not in electrical:
                return False, f"Missing electrical.{field}"
        
        if electrical["collectorEmitterVoltage"] <= 0:
            return False, "Invalid collectorEmitterVoltage"
        
        if electrical["continuousCollectorCurrent"] <= 0:
            return False, "Invalid continuousCollectorCurrent"
        
        if electrical["collectorEmitterSaturation"] <= 0:
            return False, "Invalid collectorEmitterSaturation"
        
        return True, "Valid"
    except Exception as e:
        return False, f"Exception: {str(e)}"

def validate_diode(entry: dict) -> Tuple[bool, str]:
    """Validate a Diode entry against SAS requirements."""
    try:
        if "diode" in entry:
            diode_data = entry["diode"]
        else:
            diode_data = entry
        
        mfr_info = diode_data.get("manufacturerInfo", {})
        ds_info = mfr_info.get("datasheetInfo", {})
        
        if not ds_info:
            return False, "Missing datasheetInfo"
        
        part = ds_info.get("part", {})
        electrical = ds_info.get("electrical", {})
        
        if not part or not electrical:
            return False, "Missing part or electrical"
        
        # Check required electrical fields
        required_elec = [
            "reverseVoltage",
            "forwardCurrent",
            "forwardVoltage",
        ]
        for field in required_elec:
            if field not in electrical:
                return False, f"Missing electrical.{field}"
        
        if electrical["reverseVoltage"] <= 0:
            return False, "Invalid reverseVoltage"
        
        if electrical["forwardCurrent"] <= 0:
            return False, "Invalid forwardCurrent"
        
        if electrical["forwardVoltage"] <= 0:
            return False, "Invalid forwardVoltage"
        
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
    """Validate Phase 4 entries."""
    print("🔍 Validating Phase 4 entries...")
    print("=" * 60)
    
    # Validate IGBTs
    igbt_file = Path("/home/alf/OpenConverters/Proteus/TAS/phase4_igbts_candidates.ndjson")
    if igbt_file.exists():
        valid, invalid, errors = validate_file(str(igbt_file), validate_igbt, "IGBT")
        print(f"\n📍 IGBTs:")
        print(f"   ✓ Valid: {valid:,d}")
        print(f"   ✗ Invalid: {invalid:,d}")
        if errors:
            print(f"   First errors:")
            for err in errors[:3]:
                print(f"      {err}")
    
    # Validate Diodes
    diode_file = Path("/home/alf/OpenConverters/Proteus/TAS/phase4_diodes_candidates.ndjson")
    if diode_file.exists():
        valid, invalid, errors = validate_file(str(diode_file), validate_diode, "Diode")
        print(f"\n📍 Diodes:")
        print(f"   ✓ Valid: {valid:,d}")
        print(f"   ✗ Invalid: {invalid:,d}")
        if errors:
            print(f"   First errors:")
            for err in errors[:3]:
                print(f"      {err}")

if __name__ == "__main__":
    main()
