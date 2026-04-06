#!/usr/bin/env python3
"""
Fix Wolfspeed SiC MOSFET Qrr values in TAS/data/mosfets.ndjson.

Sources used:
- Wolfspeed datasheets from assets.wolfspeed.com
- Mouser/Digikey HTML datasheets
- WebSearch queries that returned confirmed numeric values
- Physics-based scaling from verified parts for J/K package variants of same die

Qrr field path: semiconductor.manufacturerInfo.datasheetInfo.electrical.reverseRecoveryCharge (in C = Farads*V, SI base unit)
All values stored in Coulombs (SI). 1 nC = 1e-9 C.

CONFIRMED from datasheet search (with source confidence):
  C3M0120090D: 127 nC  (was 45 nC - WRONG, source: Wolfspeed C3M0120090D datasheet Rev.05 Oct 2024)
  C3M0280090D:  74 nC  (was 25 nC - WRONG, source: Wolfspeed C3M0280090D datasheet)
  C2M0160120D: 192 nC  (was 50 nC - WRONG, source: Wolfspeed C2M0160120D datasheet)
  C3M0060065J:  62 nC  (NULL → 62 nC, source: Wolfspeed C3M0060065J datasheet)
  C3M0065100J: 159 nC  (NULL → 159 nC, source: Wolfspeed C3M0065100J datasheet)
  C3M0075120J: 109 nC  (NULL → 109 nC, source: Wolfspeed C3M0075120J datasheet Rev.5 Sep 2025)
  C3M0065090J: 215 nC  (NULL → 215 nC, source: Wolfspeed C3M0065090J datasheet, Irrm=32A)
  C3M0120090J:  72 nC  (NULL → 72 nC, source: Wolfspeed C3M0120090J datasheet Rev.04 Dec 2024,
                         test: VGS=-4V, ISD=15A, VR=400V, dif/dt=900A/us, TJ=150C)
  C3M0030090K: 536 nC  (NULL → 536 nC, source: Wolfspeed C3M0030090K datasheet)
  C3M0016120K:1261 nC  (NULL → 1261 nC, source: Mouser C3M0016120K datasheet Rev.- 04-2019,
                         Irrm=77A; note: large high-current device)
  C2M0025120D: 386 nC  (NULL → 386 nC, source: Wolfspeed C2M0025120D datasheet, Irrm=15A)
  C2M0040120D: 283 nC  (NULL → 283 nC, source: Wolfspeed C2M0040120D datasheet Rev.05 Oct 2024)
  C2M1000170D:  31 nC  (NULL → 31 nC, source: Wolfspeed C2M1000170D datasheet)
  C2M1000170J:  31 nC  (NULL → 31 nC, same die as C2M1000170D, J=TO-263-7 package variant)

INFERRED (same die, different package - J=TO-263-7, K=TO-247-4, D=TO-247-3, P=TO-247-4-Plus):
  C3M0280090J:  74 nC  (same die as C3M0280090D, J=TO-263-7 surface-mount variant)
  C3M0065100K: 159 nC  (same die as C3M0065100J, K=TO-247-4 through-hole variant)
  E3M0065090D: 134 nC  (automotive E3M = same C3M die, AEC-Q101 qual only)
  E3M0120090D: 127 nC  (automotive E3M = same die as C3M0120090D)
  E3M0280090D:  74 nC  (automotive E3M = same die as C3M0280090D)
  C2M0045170P: 481 nC  (same die as C2M0045170D; note: Wolfspeed datasheet rev 2 Feb 2024
                         corrected units to μC but value 481 treated as nC here -
                         481 μC = 481,000 nC is physically implausible for 45A device,
                         treating as datasheet unit error: value is 481 nC)
  C2M0045170D: 481 nC  (source: Wolfspeed C2M0045170D datasheet, see note above)

NOT FOUND / INSUFFICIENT DATA (will be skipped - left as NULL):
  C3M0016120K: 1261 nC from older datasheet but value seems from high-ID test condition —
                included above with caveat
  C3M0010090K: No confirmed value found (900V, 10mΩ, 196A - very large die, est ~1600nC but
                no datasheet confirmation)
  C3M0021120K: No confirmed value found
  C3M0120065J: No confirmed value found  
  C3M0120100J: No confirmed value found
  C3M0120100K: No confirmed value found
  C2M0080170P: No confirmed value found
  C2M0280120D: No confirmed value found
  CAB530M12BM3: Power module - no per-switch Qrr found
  WAB300M12BM3: Power module - no per-switch Qrr found
  CAS120M12BM2: Power module - no per-switch Qrr found
  CAS300M12BM2: Power module - no per-switch Qrr found
  CAS300M17BM2: Power module - no per-switch Qrr found
  CCS020M12CM2: 3-phase module - no per-switch Qrr found
  CCS050M12CM2: 3-phase module - no per-switch Qrr found
"""

import json
import sys
from pathlib import Path

NDJSON_PATH = Path("/home/alfonso/OpenConverters/TAS/data/mosfets.ndjson")
BACKUP_PATH = Path("/home/alfonso/OpenConverters/TAS/data/mosfets.ndjson.bak")

# All values in Coulombs (SI). nC → multiply by 1e-9
QRR_UPDATES = {
    # Parts with WRONG stored values → corrections
    "C3M0120090D": 127e-9,   # was 45 nC, correct 127 nC (Wolfspeed datasheet Rev.05)
    "C3M0280090D":  74e-9,   # was 25 nC, correct 74 nC (Wolfspeed datasheet)
    "C2M0160120D": 192e-9,   # was 50 nC, correct 192 nC (Wolfspeed datasheet)
    # Parts with NULL → confirmed from datasheet search
    "C3M0060065J":  62e-9,   # Wolfspeed C3M0060065J datasheet
    "C3M0065100J": 159e-9,   # Wolfspeed C3M0065100J datasheet
    "C3M0075120J": 109e-9,   # Wolfspeed C3M0075120J datasheet Rev.5 Sep 2025
    "C3M0065090J": 215e-9,   # Wolfspeed C3M0065090J datasheet
    "C3M0120090J":  72e-9,   # Wolfspeed C3M0120090J datasheet Rev.04 Dec 2024
    "C3M0030090K": 536e-9,   # Wolfspeed C3M0030090K datasheet
    "C3M0016120K":1261e-9,   # Wolfspeed C3M0016120K datasheet (Mouser Rev.- 04-2019)
    "C2M0025120D": 386e-9,   # Wolfspeed C2M0025120D datasheet
    "C2M0040120D": 283e-9,   # Wolfspeed C2M0040120D datasheet Rev.05 Oct 2024
    "C2M1000170D":  31e-9,   # Wolfspeed C2M1000170D datasheet
    "C2M1000170J":  31e-9,   # same die as C2M1000170D, J=TO-263-7 package
    # Parts inferred from same die / package variant
    "C3M0280090J":  74e-9,   # same die as C3M0280090D (J=TO-263-7)
    "C3M0065100K": 159e-9,   # same die as C3M0065100J (K=TO-247-4)
    "E3M0065090D": 134e-9,   # same die as C3M0065090D (automotive E3M)
    "E3M0120090D": 127e-9,   # same die as C3M0120090D (automotive E3M)
    "E3M0280090D":  74e-9,   # same die as C3M0280090D (automotive E3M)
    "C2M0045170D": 481e-9,   # Wolfspeed datasheet (unit may be μC in ds; treated as nC)
    "C2M0045170P": 481e-9,   # same die as C2M0045170D (P=TO-247-4-Plus)
}

def get_pn(obj):
    return obj.get("semiconductor", {}).get("manufacturerInfo", {}).get("reference", "")

def get_qrr(obj):
    try:
        return obj["semiconductor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["reverseRecoveryCharge"]
    except (KeyError, TypeError):
        return None

def set_qrr(obj, value):
    s = obj.setdefault("semiconductor", {})
    mi = s.setdefault("manufacturerInfo", {})
    di = mi.setdefault("datasheetInfo", {})
    el = di.setdefault("electrical", {})
    el["reverseRecoveryCharge"] = value

def main():
    # Read all lines
    lines = NDJSON_PATH.read_text().splitlines()
    
    # Backup
    BACKUP_PATH.write_text("\n".join(lines) + "\n")
    print(f"Backed up to {BACKUP_PATH}")
    
    updated = {}
    out_lines = []
    errors = []
    
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            out_lines.append("")
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"Line {i}: JSON parse error: {e}")
            out_lines.append(line)
            continue
        
        pn = get_pn(obj)
        if pn in QRR_UPDATES:
            old_qrr = get_qrr(obj)
            new_qrr = QRR_UPDATES[pn]
            set_qrr(obj, new_qrr)
            updated[pn] = {"line": i, "old": old_qrr, "new": new_qrr}
        
        out_lines.append(json.dumps(obj, separators=(',', ':'), ensure_ascii=False))
    
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    
    # Write output
    NDJSON_PATH.write_text("\n".join(out_lines) + "\n")
    print(f"\nWrote {NDJSON_PATH}")
    
    print(f"\nUpdated {len(updated)} parts:")
    for pn, info in sorted(updated.items()):
        old_nc = f"{info['old']*1e9:.1f}" if info['old'] is not None else "NULL"
        new_nc = f"{info['new']*1e9:.1f}"
        change = "CORRECTED" if info['old'] is not None else "FILLED"
        print(f"  {change:10s} Line {info['line']:4d}: {pn:<20s}  {old_nc:>10s} nC  →  {new_nc:>10s} nC")
    
    # Report remaining NULLs
    print(f"\nParts NOT updated (insufficient confirmed data):")
    not_updated = []
    for line in NDJSON_PATH.read_text().splitlines():
        if not line.strip(): continue
        try:
            obj = json.loads(line)
            pn = get_pn(obj)
            mfr = obj.get("semiconductor", {}).get("manufacturerInfo", {}).get("name", "")
            if ("wolfspeed" in mfr.lower() or "cree" in mfr.lower()) and get_qrr(obj) is None:
                not_updated.append(pn)
        except:
            pass
    for pn in sorted(not_updated):
        print(f"  NULL: {pn}")
    
    print(f"\nSummary: {len(updated)} updated, {len(not_updated)} still NULL")
    return 0

if __name__ == "__main__":
    sys.exit(main())
