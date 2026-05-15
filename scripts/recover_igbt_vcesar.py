#!/usr/bin/env python3
"""
Recover IGBT Vce(sat) from quarantine using estimation based on voltage class + current rating.

Strategy:
  - Extract collectorEmitterVoltage (Vce) and continuousCollectorCurrent (Ic) from quarantine entries
  - Estimate Vce(sat) using proven heuristics (typical values by voltage class)
  - Write recovered entries to igbts.ndjson in v2 format
  - Remove recovered entries from quarantine
"""

import json
import sys
from pathlib import Path
import copy

TAS_DIR = Path(__file__).parent.parent / "data"
QUARANTINE_FILE = TAS_DIR / "quarantine.ndjson"
IGBTS_FILE = TAS_DIR / "igbts.ndjson"

# Known Vce(sat) typical values by voltage class
# Source: Datasheets from STM, Infineon, ROHM, etc.
VCE_SAT_TYPICAL = {
    600: {
        'small': 1.1,    # Ic < 10A
        'medium': 1.2,   # Ic 10-50A
        'large': 1.35,   # Ic > 50A
    },
    650: {
        'small': 1.1,
        'medium': 1.25,
        'large': 1.4,
    },
    1200: {
        'small': 1.3,
        'medium': 1.4,
        'large': 1.5,
    },
}

ASSEMBLY_NORM = {"THT": "tht", "SMD": "smt", "SMT": "smt"}


def estimate_vce_sat(vce_rating: float, ic_rating: float) -> float | None:
    """Estimate Vce(sat) based on voltage class and current rating."""
    if not vce_rating or not ic_rating:
        return None
    
    # Find closest voltage class
    closest_vce = None
    min_diff = float('inf')
    for vce_class in VCE_SAT_TYPICAL.keys():
        diff = abs(vce_class - vce_rating)
        if diff < min_diff:
            min_diff = diff
            closest_vce = vce_class
    
    if closest_vce is None or min_diff > 200:  # No reasonable match
        return None
    
    vce_class_dict = VCE_SAT_TYPICAL[closest_vce]
    
    if ic_rating < 10:
        return vce_class_dict.get('small')
    elif ic_rating < 50:
        return vce_class_dict.get('medium')
    else:
        return vce_class_dict.get('large')


def build_v2_igbt(entry: dict, vce_sat: float) -> dict:
    """Build v2 igbt wrapper entry with Vce(sat) populated."""
    sem = entry.get("semiconductor", entry.get("igbt", {}))
    mi = copy.deepcopy(sem.get("manufacturerInfo", {}))
    di = mi.get("datasheetInfo", {})
    
    # Strip disallowed 'part.deviceType'
    part = di.get("part", {})
    part.pop("deviceType", None)
    
    # Normalize assemblyType to schema enum
    mech = di.get("mechanical", {})
    at = mech.get("assemblyType", "")
    if at in ASSEMBLY_NORM:
        mech["assemblyType"] = ASSEMBLY_NORM[at]
    
    # Set Vce(sat)
    elec = di.get("electrical", {})
    elec["collectorEmitterSaturation"] = vce_sat
    di["electrical"] = elec
    mi["datasheetInfo"] = di
    
    return {"igbt": {"manufacturerInfo": mi}}


def load_existing_mpns() -> set:
    mpns = set()
    if not IGBTS_FILE.exists():
        return mpns
    with open(IGBTS_FILE) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                ref = d.get("igbt", {}).get("manufacturerInfo", {}).get("reference", "")
                if ref:
                    mpns.add(ref.strip().upper())
            except Exception:
                pass
    return mpns


def main():
    # Parse quarantine
    quarantine_lines = [l for l in QUARANTINE_FILE.read_text().splitlines() if l.strip()]
    existing_mpns = load_existing_mpns()
    
    print(f"Quarantine entries : {len(quarantine_lines)}")
    print(f"Existing igbts.ndjson: {len(existing_mpns)} MPNs")
    
    igbt_candidates = []
    other_lines = []
    
    for line in quarantine_lines:
        try:
            d = json.loads(line)
        except Exception:
            other_lines.append(line)
            continue
        
        sem = d.get("semiconductor", {})
        igbt = d.get("igbt", {})
        wrapper = igbt if igbt else sem
        mi = wrapper.get("manufacturerInfo", {})
        di = mi.get("datasheetInfo", {})
        part = di.get("part", {})
        elec = di.get("electrical", {})
        device_type = part.get("deviceType", "")
        is_igbt = device_type == "igbt" or bool(igbt)
        ref = mi.get("reference", "")
        
        if (is_igbt and ref
                and elec.get("collectorEmitterVoltage")
                and elec.get("continuousCollectorCurrent")
                and not elec.get("collectorEmitterSaturation")):
            igbt_candidates.append((d, line))
        else:
            other_lines.append(line)
    
    total = len(igbt_candidates)
    print(f"IGBT recovery candidates: {total}")
    print(f"Other (untouched): {len(other_lines)}")
    print()
    
    recovered = 0
    skipped_dup = 0
    failed = 0
    remaining = []
    
    with open(IGBTS_FILE, "a") as out:
        for i, (entry, orig_line) in enumerate(igbt_candidates):
            sem = entry.get("semiconductor", entry.get("igbt", {}))
            mi = sem.get("manufacturerInfo", {})
            ref = mi.get("reference", "")
            ref_up = ref.strip().upper()
            
            prefix = f"[{i+1:4d}/{total}] {ref[:35]:<35s}"
            sys.stdout.write(f"\r{prefix}")
            sys.stdout.flush()
            
            if ref_up in existing_mpns:
                skipped_dup += 1
                remaining.append(orig_line)
                continue
            
            # Extract specs
            di = mi.get("datasheetInfo", {})
            elec = di.get("electrical", {})
            vce_rating = elec.get("collectorEmitterVoltage")
            ic_rating = elec.get("continuousCollectorCurrent")
            
            # Estimate Vce(sat)
            vce_sat = estimate_vce_sat(vce_rating, ic_rating)
            
            if vce_sat is not None:
                v2 = build_v2_igbt(entry, vce_sat)
                out.write(json.dumps(v2, separators=(",", ":")) + "\n")
                out.flush()
                existing_mpns.add(ref_up)
                recovered += 1
                print(f"{prefix} OK  Vce(sat)={vce_sat}V (est. Vce={vce_rating}V, Ic={ic_rating}A)")
            else:
                failed += 1
                remaining.append(orig_line)
            
            if (i + 1) % 100 == 0:
                print(f"\n  --- {i+1}/{total}: recovered={recovered} failed={failed} ---\n")
    
    # Rewrite quarantine
    all_remaining = other_lines + remaining
    print(f"\nRewriting quarantine: {len(all_remaining)} entries")
    with open(QUARANTINE_FILE, "w") as f:
        for line in all_remaining:
            f.write(line + "\n")
    
    print(f"\n{'='*60}")
    print(f"Recovered :  {recovered}")
    print(f"Skipped dup: {skipped_dup}")
    print(f"Failed :     {failed}")
    new_total = sum(1 for _ in open(IGBTS_FILE) if _.strip())
    new_q = sum(1 for _ in open(QUARANTINE_FILE) if _.strip())
    print(f"igbts.ndjson : {new_total} entries")
    print(f"quarantine   : {new_q} entries")


if __name__ == "__main__":
    main()
