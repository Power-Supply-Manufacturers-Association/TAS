#!/usr/bin/env python3
"""
Final Comprehensive Audit on TAS Database (2026-04-09)
Tests: Quarantine validity, production file integrity, BOM validation
"""

import json
import os
from collections import defaultdict, Counter
from pathlib import Path

DATA_DIR = Path('/home/alf/OpenConverters/Proteus/TAS/data')

def load_ndjson(filepath):
    """Load NDJSON file and return list of entries."""
    entries = []
    with open(filepath) as f:
        for i, line in enumerate(f, 1):
            try:
                entries.append((i, json.loads(line)))
            except json.JSONDecodeError as e:
                print(f"  JSON ERROR {filepath}:{i}: {e}")
                entries.append((i, None))
    return entries

def validate_quarantine():
    """Validate quarantine.ndjson structure and content."""
    print("\n=== QUARANTINE FILE VALIDATION ===")
    entries = load_ndjson(DATA_DIR / 'quarantine.ndjson')

    issues = []
    quarantine_map = defaultdict(list)  # Track what component types are quarantined

    for line_num, entry in entries:
        if entry is None:
            issues.append(f"Line {line_num}: JSON parsing failed")
            continue

        qinfo = entry.get('quarantineInfo', {})

        # Identify component type
        component_type = None
        for ct in ('resistor', 'magnetic', 'capacitor', 'diode', 'mosfet', 'igbt',
                   'semiconductor', 'converter', 'original_entry', 'manufacturerInfo',
                   'inputs'):
            if ct in entry:
                component_type = ct
                break

        if component_type is None:
            component_type = 'other'

        source = qinfo.get('sourceFile', qinfo.get('quarantineSource', 'unknown'))
        quarantine_map[component_type].append(source)

    # Report summary
    total_quarantine = len([e for _, e in entries if e is not None])
    print(f"Total quarantine entries: {total_quarantine}")
    print(f"Structural issues: {len(issues)}")

    if issues:
        print("\nQuarantine Issues:")
        for issue in issues[:10]:  # Show first 10
            print(f"  {issue}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")

    print(f"\nQuarantine by source file:")
    for source, refs in sorted(quarantine_map.items()):
        print(f"  {source}: {len(refs)} entries")

    # Check for converters (should not be in quarantine)
    if 'converter' in quarantine_map:
        issues.append(f"ERROR: {len(quarantine_map['converter'])} converter designs in quarantine (should be in converters.ndjson)")

    return len(issues) == 0, len(issues), total_quarantine

def validate_magnetics():
    """Validate magnetics.ndjson for ETD transformers with null inductance, ferrite beads."""
    print("\n=== MAGNETICS FILE VALIDATION ===")
    entries = load_ndjson(DATA_DIR / 'magnetics.ndjson')

    issues = []
    ferrite_beads = 0
    etd_with_null_inductance = 0
    missing_inductance_non_bead = 0

    for line_num, entry in entries:
        if entry is None:
            issues.append(f"Line {line_num}: JSON parsing failed")
            continue

        # Get electrical specs safely
        try:
            mag = entry.get('magnetic', {})
            mfr = mag.get('manufacturerInfo', {})
            dsinfo = mfr.get('datasheetInfo', {})
            elec = dsinfo.get('electrical', {})
            part = dsinfo.get('part', {})
        except:
            issues.append(f"Line {line_num}: Failed to extract electrical specs")
            continue

        subtype = part.get('componentSubType', '')
        inductance = None
        if elec:
            ind_block = elec.get('inductance', {})
            if ind_block and isinstance(ind_block, dict):
                inductance = ind_block.get('nominal')

        # Check ferrite beads
        if subtype == 'ferrite_bead':
            ferrite_beads += 1
            # Ferrite beads use impedance, not inductance
            if inductance is not None and inductance != 0:
                # Some ferrite beads may have both - that's OK
                pass
        else:
            # Non-ferrite magnetics should have inductance
            if inductance is None or inductance == 0:
                missing_inductance_non_bead += 1
                ref = mfr.get('reference', '?')
                if subtype.startswith('etd') or subtype.startswith('ETD'):
                    etd_with_null_inductance += 1
                    issues.append(f"Line {line_num}: ETD transformer {ref} has null inductance")

    total_magnetics = len([e for _, e in entries if e is not None])
    print(f"Total magnetics entries: {total_magnetics}")
    print(f"  Ferrite beads: {ferrite_beads}")
    print(f"  Missing inductance (non-ferrite): {missing_inductance_non_bead}")
    print(f"  ETD transformers with null inductance: {etd_with_null_inductance}")
    print(f"Structural issues: {len(issues)}")

    if issues:
        print("\nMagnetics Issues (first 10):")
        for issue in issues[:10]:
            print(f"  {issue}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")

    return etd_with_null_inductance == 0 and len(issues) == 0, len(issues), total_magnetics

def validate_capacitors():
    """Validate capacitors.ndjson for skeleton-marked entries and capacitance validity."""
    print("\n=== CAPACITORS FILE VALIDATION ===")
    entries = load_ndjson(DATA_DIR / 'capacitors.ndjson')

    issues = []
    skeleton_marked = 0
    missing_capacitance = 0
    invalid_capacitance = 0

    for line_num, entry in entries:
        if entry is None:
            issues.append(f"Line {line_num}: JSON parsing failed")
            continue

        try:
            cap = entry.get('capacitor', {})
            mfr = cap.get('manufacturerInfo', {})
            dsinfo = mfr.get('datasheetInfo', {})
            elec = dsinfo.get('electrical', {})
        except:
            issues.append(f"Line {line_num}: Failed to extract electrical specs")
            continue

        # Check if marked as skeleton
        if dsinfo.get('_isSkeletonData'):
            skeleton_marked += 1
            ref = mfr.get('reference', '?')
            issues.append(f"Line {line_num}: Skeleton-marked capacitor {ref}")

        # Check capacitance (accept both dict with nominal key and bare scalars)
        capacitance = None
        if isinstance(elec, dict):
            cap_val = elec.get('capacitance')
            if isinstance(cap_val, dict):
                capacitance = cap_val.get('nominal')
            elif isinstance(cap_val, (int, float)):
                capacitance = cap_val
        if capacitance is None:
            missing_capacitance += 1
            ref = mfr.get('reference', '?')
            issues.append(f"Line {line_num}: Missing capacitance for {ref}")
        elif capacitance and capacitance <= 0:
            invalid_capacitance += 1
            ref = mfr.get('reference', '?')
            issues.append(f"Line {line_num}: Invalid capacitance {capacitance} for {ref}")

    total_capacitors = len([e for _, e in entries if e is not None])
    print(f"Total capacitor entries: {total_capacitors}")
    print(f"  Skeleton-marked: {skeleton_marked}")
    print(f"  Missing capacitance: {missing_capacitance}")
    print(f"  Invalid capacitance (<=0): {invalid_capacitance}")
    print(f"Structural issues: {len(issues)}")

    if issues:
        print("\nCapacitor Issues (first 10):")
        for issue in issues[:10]:
            print(f"  {issue}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")

    return skeleton_marked == 0 and missing_capacitance == 0 and len(issues) == 0, len(issues), total_capacitors

def validate_resistors():
    """Validate resistors.ndjson for zero resistance values."""
    print("\n=== RESISTORS FILE VALIDATION ===")
    entries = load_ndjson(DATA_DIR / 'resistors.ndjson')

    issues = []
    zero_resistance = 0
    missing_resistance = 0
    resistor_kits = 0

    for line_num, entry in entries:
        if entry is None:
            issues.append(f"Line {line_num}: JSON parsing failed")
            continue

        try:
            res = entry.get('resistor', {})
            mfr = res.get('manufacturerInfo', {})
            dsinfo = mfr.get('datasheetInfo', {})
            part = dsinfo.get('part', {})
            elec = dsinfo.get('electrical', {})
        except:
            issues.append(f"Line {line_num}: Failed to extract specs")
            continue

        partnum = part.get('partNumber', '?')
        resistance = elec.get('resistance', {}).get('nominal')

        # Check for kit exception
        if '-KIT-FILE' in partnum or '-KIT' in partnum:
            resistor_kits += 1
            # Kits legitimately have no single resistance value
            continue

        # Check for zero or missing (zero-ohm links are valid; only flag negative)
        if resistance is None:
            missing_resistance += 1
            issues.append(f"Line {line_num}: Missing resistance for {partnum}")
        elif resistance < 0:
            zero_resistance += 1
            issues.append(f"Line {line_num}: Negative resistance for {partnum}")

    total_resistors = len([e for _, e in entries if e is not None])
    print(f"Total resistor entries: {total_resistors}")
    print(f"  Resistor kits (excluded): {resistor_kits}")
    print(f"  Zero resistance: {zero_resistance}")
    print(f"  Missing resistance: {missing_resistance}")
    print(f"Structural issues: {len(issues)}")

    if issues:
        print("\nResistor Issues (first 10):")
        for issue in issues[:10]:
            print(f"  {issue}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")

    return zero_resistance == 0 and missing_resistance == 0 and len(issues) == 0, len(issues), total_resistors

def validate_diodes():
    """Validate diodes.ndjson for schema nesting and electrical completeness."""
    print("\n=== DIODES FILE VALIDATION ===")
    entries = load_ndjson(DATA_DIR / 'diodes.ndjson')

    issues = []
    structural_errors = 0
    incomplete_specs = 0
    invalid_surgecurrent = 0

    for line_num, entry in entries:
        if entry is None:
            issues.append(f"Line {line_num}: JSON parsing failed")
            continue

        try:
            diode = entry.get('diode', {})
            mfr = diode.get('manufacturerInfo', {})
            dsinfo = mfr.get('datasheetInfo', {})
            elec = dsinfo.get('electrical', {})
            part = dsinfo.get('part', {})
        except:
            issues.append(f"Line {line_num}: Failed to extract specs")
            continue

        # Check for top-level datasheetInfo (structural error)
        if 'datasheetInfo' in diode and 'manufacturerInfo' in diode:
            # This is wrong nesting
            structural_errors += 1
            partnum = part.get('partNumber', '?')
            issues.append(f"Line {line_num}: Diode {partnum} has datasheetInfo at wrong nesting level")

        # Check electrical completeness
        partnum = part.get('partNumber', '?')
        subtype = part.get('subType', part.get('componentSubType', ''))
        fc = elec.get('forwardCurrent')
        forward_current = fc.get('nominal') if isinstance(fc, dict) else fc
        surge_current = elec.get('surgeCurrent')
        vrrm_raw = elec.get('reverseVoltageMax', elec.get('reverseVoltage'))
        vrrm = vrrm_raw.get('nominal') if isinstance(vrrm_raw, dict) else vrrm_raw

        # surgeCurrent validation: must be > forwardCurrent (or >> 1A for small-signal)
        if forward_current and surge_current:
            if surge_current < 1 and forward_current > 1:
                # Probably leakage current, not surge current
                invalid_surgecurrent += 1
                if subtype not in ['small_signal', 'schottky']:  # Small signal OK
                    issues.append(f"Line {line_num}: {partnum} surgeCurrent={surge_current}A < forwardCurrent={forward_current}A (leakage error?)")

    total_diodes = len([e for _, e in entries if e is not None])
    print(f"Total diode entries: {total_diodes}")
    print(f"  Structural errors (wrong nesting): {structural_errors}")
    print(f"  Invalid surgeCurrent (likely leakage): {invalid_surgecurrent}")
    print(f"Structural issues: {len(issues)}")

    if issues:
        print("\nDiode Issues (first 10):")
        for issue in issues[:10]:
            print(f"  {issue}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")

    return structural_errors == 0 and len(issues) == 0, len(issues), total_diodes

def validate_mosfets():
    """Validate MOSFETs for correct schema nesting."""
    print("\n=== MOSFETs FILE VALIDATION ===")
    entries = load_ndjson(DATA_DIR / 'mosfets.ndjson')

    issues = []
    structural_errors = 0

    for line_num, entry in entries:
        if entry is None:
            issues.append(f"Line {line_num}: JSON parsing failed")
            continue

        try:
            mos = entry.get('mosfet', entry.get('semiconductor', {}))
            mfr = mos.get('manufacturerInfo', {})
            dsinfo = mfr.get('datasheetInfo', {})
            elec = dsinfo.get('electrical', {})
        except:
            issues.append(f"Line {line_num}: Failed to extract specs")
            continue

        # Check that datasheetInfo is NOT at root level
        if 'datasheetInfo' in mos and 'manufacturerInfo' not in mos:
            structural_errors += 1
            partnum = mfr.get('reference', '?') if mfr else '?'
            issues.append(f"Line {line_num}: MOSFET {partnum} has datasheetInfo at wrong nesting (should be inside manufacturerInfo)")

        # Verify electrical specs exist
        if not elec or len(elec) == 0:
            partnum = mfr.get('reference', '?') if mfr else '?'
            issues.append(f"Line {line_num}: MOSFET {partnum} has empty electrical block")

    total_mosfets = len([e for _, e in entries if e is not None])
    print(f"Total MOSFET entries: {total_mosfets}")
    print(f"  Structural errors (wrong nesting): {structural_errors}")
    print(f"Structural issues: {len(issues)}")

    if issues:
        print("\nMOSFET Issues (first 10):")
        for issue in issues[:10]:
            print(f"  {issue}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")

    return structural_errors == 0 and len(issues) == 0, len(issues), total_mosfets

def validate_igbts():
    """Validate IGBTs for schema nesting and electrical specs."""
    print("\n=== IGBTs FILE VALIDATION ===")
    entries = load_ndjson(DATA_DIR / 'igbts.ndjson')

    issues = []
    structural_errors = 0
    missing_vce = 0

    for line_num, entry in entries:
        if entry is None:
            issues.append(f"Line {line_num}: JSON parsing failed")
            continue

        try:
            # Handle both direct {"igbt": {...}} and wrapped {"semiconductor": {"igbt": {...}}} formats
            igbt = entry.get('igbt', {})
            if not igbt and 'semiconductor' in entry:
                igbt = entry['semiconductor'].get('igbt', {})
            mfr = igbt.get('manufacturerInfo', {})
            dsinfo = mfr.get('datasheetInfo', {})
            elec = dsinfo.get('electrical', {})
        except:
            issues.append(f"Line {line_num}: Failed to extract specs")
            continue

        # Check that datasheetInfo is inside manufacturerInfo (not at igbt root)
        if 'datasheetInfo' in igbt and 'manufacturerInfo' not in igbt:
            structural_errors += 1
            partnum = sem.get('datasheetInfo', {}).get('part', {}).get('partNumber', '?')
            issues.append(f"Line {line_num}: IGBT {partnum} has datasheetInfo at wrong nesting level")

        # Check Vce (collectorEmitterVoltage - accept both dict and bare scalar)
        partnum = mfr.get('reference', '?') if mfr else '?'
        vce = None
        if isinstance(elec, dict):
            vce_val = elec.get('collectorEmitterVoltage')
            if isinstance(vce_val, dict):
                vce = vce_val.get('nominal')
            elif isinstance(vce_val, (int, float)):
                vce = vce_val
        if vce is None:
            missing_vce += 1
            issues.append(f"Line {line_num}: IGBT {partnum} missing collectorEmitterVoltage")

    total_igbts = len([e for _, e in entries if e is not None])
    print(f"Total IGBT entries: {total_igbts}")
    print(f"  Structural errors (wrong nesting): {structural_errors}")
    print(f"  Missing Vce: {missing_vce}")
    print(f"Structural issues: {len(issues)}")

    if issues:
        print("\nIGBT Issues (first 10):")
        for issue in issues[:10]:
            print(f"  {issue}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")

    return structural_errors == 0 and len(issues) == 0, len(issues), total_igbts

def count_unique_mpns():
    """Count unique MPNs across all production files."""
    print("\n=== MPN COVERAGE ===")
    mpn_set = set()
    mfr_to_mpn = defaultdict(set)

    for filename in ['resistors.ndjson', 'magnetics.ndjson', 'capacitors.ndjson',
                     'diodes.ndjson', 'mosfets.ndjson', 'igbts.ndjson']:
        entries = load_ndjson(DATA_DIR / filename)

        for _, entry in entries:
            if entry is None:
                continue

            # Extract MPN based on component type
            mpn = None
            manufacturer = None

            if 'resistor' in entry:
                part = entry.get('resistor', {}).get('manufacturerInfo', {}).get('datasheetInfo', {}).get('part', {})
                mpn = part.get('partNumber')
                manufacturer = entry.get('resistor', {}).get('manufacturerInfo', {}).get('manufacturer')
            elif 'magnetic' in entry:
                mpn = entry.get('magnetic', {}).get('manufacturerInfo', {}).get('reference')
                manufacturer = entry.get('magnetic', {}).get('manufacturerInfo', {}).get('name')
            elif 'capacitor' in entry:
                part = entry.get('capacitor', {}).get('manufacturerInfo', {}).get('datasheetInfo', {}).get('part', {})
                mpn = part.get('partNumber')
                manufacturer = entry.get('capacitor', {}).get('manufacturerInfo', {}).get('name')
            elif 'diode' in entry:
                part = entry.get('diode', {}).get('manufacturerInfo', {}).get('datasheetInfo', {}).get('part', {})
                mpn = part.get('partNumber')
                manufacturer = entry.get('diode', {}).get('manufacturerInfo', {}).get('name')
            elif 'semiconductor' in entry:
                part = entry.get('semiconductor', {}).get('manufacturerInfo', {}).get('datasheetInfo', {}).get('part', {})
                mpn = part.get('partNumber')
                manufacturer = entry.get('semiconductor', {}).get('manufacturerInfo', {}).get('name')

            if mpn:
                mpn_set.add(mpn)
                if manufacturer:
                    mfr_to_mpn[manufacturer].add(mpn)

    print(f"Total unique MPNs across production files: {len(mpn_set)}")

    # Show manufacturer concentration
    print(f"\nManufacturer MPN concentration (top 10):")
    for mfr, mpns in sorted(mfr_to_mpn.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
        pct = len(mpns) / len(mpn_set) * 100
        print(f"  {mfr}: {len(mpns)} ({pct:.1f}%)")

    # Check for Würth specifically
    werth_count = len(mfr_to_mpn.get('Würth Elektronik', set()))
    if werth_count > 0:
        werth_pct = werth_count / len(mpn_set) * 100
        print(f"\n*** Würth Elektronik: {werth_count} components ({werth_pct:.1f}% of database)")

    return mpn_set, mfr_to_mpn

def validate_converters():
    """Spot-check converter designs for BOM validity."""
    print("\n=== CONVERTER DESIGNS (SPOT CHECK) ===")
    entries = load_ndjson(DATA_DIR / 'converters.ndjson')

    issues = []
    total_converters = 0
    converters_with_bom = 0
    unresolved_mpns = 0

    # Load production MPNs for validation
    prod_mpns = set()
    for filename in ['resistors.ndjson', 'magnetics.ndjson', 'capacitors.ndjson',
                     'diodes.ndjson', 'mosfets.ndjson', 'igbts.ndjson']:
        prod_entries = load_ndjson(DATA_DIR / filename)
        for _, entry in prod_entries:
            if entry is None:
                continue
            # Extract MPN
            mpn = None
            if 'resistor' in entry:
                mpn = entry.get('resistor', {}).get('manufacturerInfo', {}).get('datasheetInfo', {}).get('part', {}).get('partNumber')
            elif 'magnetic' in entry:
                mpn = entry.get('magnetic', {}).get('manufacturerInfo', {}).get('reference')
            elif 'capacitor' in entry:
                mpn = entry.get('capacitor', {}).get('manufacturerInfo', {}).get('datasheetInfo', {}).get('part', {}).get('partNumber')
            elif 'diode' in entry:
                mpn = entry.get('diode', {}).get('manufacturerInfo', {}).get('datasheetInfo', {}).get('part', {}).get('partNumber')
            elif 'semiconductor' in entry:
                mpn = entry.get('semiconductor', {}).get('manufacturerInfo', {}).get('datasheetInfo', {}).get('part', {}).get('partNumber')

            if mpn:
                prod_mpns.add(mpn)

    # Check converters
    for line_num, entry in entries:
        if entry is None:
            issues.append(f"Line {line_num}: JSON parsing failed")
            continue

        total_converters += 1

        try:
            conv = entry.get('converter', {})
            bom = conv.get('bom', [])

            if bom:
                converters_with_bom += 1
                # Sample check: verify first 3 MPNs
                for i, component in enumerate(bom[:3]):
                    mpn = component.get('partNumber')
                    if mpn and mpn not in prod_mpns:
                        unresolved_mpns += 1
                        conv_name = conv.get('metadata', {}).get('designName', '?')
                        issues.append(f"Line {line_num}: Converter {conv_name} BOM component {mpn} not in production database")
        except Exception as e:
            issues.append(f"Line {line_num}: Failed to parse BOM: {e}")

    print(f"Total converter designs: {total_converters}")
    print(f"  Converters with BOM: {converters_with_bom}")
    print(f"  Unresolved MPN references: {unresolved_mpns}")
    print(f"  Production MPNs in database: {len(prod_mpns)}")

    if issues:
        print("\nConverter Issues (first 10):")
        for issue in issues[:10]:
            print(f"  {issue}")
        if len(issues) > 10:
            print(f"  ... and {len(issues) - 10} more")

    return unresolved_mpns == 0 and len(issues) == 0, len(issues), total_converters

def main():
    print("=" * 70)
    print("FINAL COMPREHENSIVE AUDIT - TAS DATABASE (2026-04-09)")
    print("=" * 70)

    all_passed = True
    total_issues = 0

    # Run all validations
    q_pass, q_issues, q_count = validate_quarantine()
    all_passed = all_passed and q_pass
    total_issues += q_issues

    m_pass, m_issues, m_count = validate_magnetics()
    all_passed = all_passed and m_pass
    total_issues += m_issues

    c_pass, c_issues, c_count = validate_capacitors()
    all_passed = all_passed and c_pass
    total_issues += c_issues

    r_pass, r_issues, r_count = validate_resistors()
    all_passed = all_passed and r_pass
    total_issues += r_issues

    d_pass, d_issues, d_count = validate_diodes()
    all_passed = all_passed and d_pass
    total_issues += d_issues

    mos_pass, mos_issues, mos_count = validate_mosfets()
    all_passed = all_passed and mos_pass
    total_issues += mos_issues

    igb_pass, igb_issues, igb_count = validate_igbts()
    all_passed = all_passed and igb_pass
    total_issues += igb_issues

    mpn_set, mfr_to_mpn = count_unique_mpns()

    conv_pass, conv_issues, conv_count = validate_converters()
    all_passed = all_passed and conv_pass
    total_issues += conv_issues

    # Final summary
    print("\n" + "=" * 70)
    print("AUDIT SUMMARY")
    print("=" * 70)
    print(f"\nProduction Files:")
    print(f"  Resistors:    {r_count}")
    print(f"  Magnetics:    {m_count}")
    print(f"  Capacitors:   {c_count}")
    print(f"  Diodes:       {d_count}")
    print(f"  MOSFETs:      {mos_count}")
    print(f"  IGBTs:        {igb_count}")
    print(f"  Total prod:   {r_count + m_count + c_count + d_count + mos_count + igb_count}")
    print(f"\nQuarantine:     {q_count}")
    print(f"Converter DFMs: {conv_count}")
    print(f"\nUnique MPNs:    {len(mpn_set)}")

    print(f"\n{'='*70}")
    if all_passed:
        print("✓ ALL AUDITS PASSED - Database ready for production")
    else:
        print(f"✗ AUDIT FAILURES: {total_issues} issues detected")
    print(f"{'='*70}\n")

    return 0 if all_passed else 1

if __name__ == '__main__':
    exit(main())
