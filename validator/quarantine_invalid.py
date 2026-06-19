#!/usr/bin/env python3
"""Split physics-INVALID records (>=1 IMPOSSIBLE finding) out of each production
data/*.ndjson into data/<file>.quarantine_invalid_physics.ndjson.

Each quarantined record gets a "_validatorQuarantine" annotation recording the
date and the firing check codes/messages, so it can be repaired and reinstated.
Production files are rewritten in place (atomic replace); originals are tracked
by git-LFS so the operation is fully reversible.

    cd TAS/validator && python3 quarantine_invalid.py            # dry run (counts only)
    cd TAS/validator && python3 quarantine_invalid.py --apply    # perform the split
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "build"))
import tas_validator  # noqa: E402

DATA = HERE.parent / "data"
FILES = ["magnetics", "capacitors", "resistors", "diodes", "mosfets", "igbts"]
DATE = "2026-06-19"
APPLY = "--apply" in sys.argv

# Surgical scope: quarantine ONLY records whose impossibility is unambiguous data
# corruption. Deliberately EXCLUDED (left in production for later repair):
#   - MOS_VGS_VS_VTH / MOS_VTH_WINDOW : real SiC/P-channel parts whose
#     gateThresholdVoltage field was populated with gate-DRIVE voltage; the part
#     is valid, only that one field needs re-sourcing.
#   - MAG_SRF_L : borderline (values sit right at the threshold; likely a
#     mis-sourced SRF, not a corrupt part).
CORRUPTION_CODES = {
    "CAP_ENERGY_DENSITY",   # impossible C/V/size combination
    "CAP_LEAKAGE_CV",       # leakage physically impossible for the dielectric
    "MAG_ISAT_POWER",       # corrupt saturationCurrentPeak (Isat^2*DCR in kW range)
    "MAG_ENERGY_DENSITY",   # same corrupt Isat -> impossible stored-energy density
    "MAG_L_TOLERANCE",      # inductance min>nominal / max<nominal ordering
    "RES_POWER_SIZE",       # power rating impossible for the stated body size
    "MOS_CHARGE_HIERARCHY", # Qgs+Qgd > total Qg
}


def process(name):
    src = DATA / f"{name}.ndjson"
    if not src.exists():
        return
    qpath = DATA / f"{name}.quarantine_invalid_physics.ndjson"
    tmp = DATA / f"{name}.ndjson.tmp"
    kept = quarantined = 0
    code_tally = {}

    # Write mode (not append): the quarantine file is regenerated from scratch
    # each run so repeated invocations never duplicate or accumulate records.
    with open(src) as fin, open(tmp, "w") as fkeep, open(qpath, "w") as fq:
        for line in fin:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            rec = json.loads(line)
            try:
                v = tas_validator.validate(rec)
            except Exception:
                # Malformed → keep in production untouched (not our call to drop);
                # the sweep reported zero of these.
                fkeep.write(line + "\n")
                kept += 1
                continue
            imp = [f for f in v.findings if f.severity == "IMPOSSIBLE"]
            corruption = [f for f in imp if f.code in CORRUPTION_CODES]
            if not corruption:
                # Valid, or invalid only for excluded (repairable) reasons -> keep.
                fkeep.write(line + "\n")
                kept += 1
            else:
                imp = corruption
                for f in imp:
                    code_tally[f.code] = code_tally.get(f.code, 0) + 1
                rec["_validatorQuarantine"] = {
                    "date": DATE,
                    "reason": "physics-invalid (>=1 IMPOSSIBLE check)",
                    "codes": sorted({f.code for f in imp}),
                    "messages": [f.message for f in imp],
                }
                fq.write(json.dumps(rec) + "\n")
                quarantined += 1

    if APPLY:
        os.replace(tmp, src)
        if quarantined == 0:
            qpath.unlink()  # don't leave an empty quarantine file
    else:
        os.remove(tmp)
        qpath.unlink()  # dry run leaves no quarantine artifact

    tally = ", ".join(f"{k}={c}" for k, c in sorted(code_tally.items(), key=lambda x: -x[1]))
    print(f"  {name:11s} kept={kept:<7d} quarantined={quarantined:<5d} [{tally}]")


def main():
    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — physics-invalid quarantine ({DATE})")
    for name in FILES:
        process(name)
    if not APPLY:
        print("\n(dry run — re-run with --apply to write changes)")


if __name__ == "__main__":
    main()
