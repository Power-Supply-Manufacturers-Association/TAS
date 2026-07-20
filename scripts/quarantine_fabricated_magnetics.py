#!/usr/bin/env python3
"""One-off: quarantine the fabricated magnetics left behind by the April-2026 bulk
sourcing campaign (aggressive_mag_sourcing.py / parametric_sourcing.py).

Those scripts SYNTHESIZED parts: they looped over a hardcoded E-series inductance
list x a package list, invented a value-encoded MPN, and computed DCR/Isat from a
made-up scaling formula (e.g. `base_dcr = 0.008 / (L/1uH)`). The stored DCR values
reproduce that formula digit-for-digit, which is the proof — these numbers never
came from a datasheet, and the MPNs are not orderable part numbers.

The sibling fabrications (WCAP-* capacitors, 7443MAPI-* magnetics) were already
quarantined in June 2026; this pass catches the magnetics that sweep missed.
Selection is by EXACT generator template, so real parts that merely share a prefix
(IHLP-2020CZ-01-1R0M01, SRR-A1R-G1A-HHHHH) are untouched.

Idempotent: re-running with a clean catalogue moves nothing.
"""
import datetime
import json
import re
import shutil
import sys
from pathlib import Path

DATA = Path("/home/alf/PSMA/TAS/data")
LIVE = DATA / "magnetics.ndjson"
QUAR = DATA / "magnetics.quarantine_fabricated.ndjson"

# EXACT generator templates. A value-code MPN ("<number><unit>" as the part
# identifier) is the signature: no vendor sells "TDK-SPM-100nH" -- real TDK is
# SPM6530T-100M. Anchored so real prefixed MPNs never match.
FABRICATED = [
    (re.compile(r"^7443HCF-\d{4}-\d{4}$"), "aggressive_mag_sourcing.generate_wuerth_hcf_comprehensive"),
    (re.compile(r"^7443MAPI-\d{4}-\d{4}$"), "aggressive_mag_sourcing.generate_wuerth_mapi"),
    (re.compile(r"^WE-HCF-\d+(nH|uH|mH)-(STD|HC|XC)$"), "parametric_sourcing (WE-HCF variants)"),
    (re.compile(r"^WE-HCI-\d{4}-\d+$"), "bulk/parametric sourcing (WE-HCI stubs)"),
    (re.compile(r"^CC-[A-Z0-9]+-\d+(nH|uH|mH)$"), "aggressive_mag_sourcing (Coilcraft synth)"),
    (re.compile(r"^TDK-SPM-\d+(nH|uH|mH)$"), "aggressive_mag_sourcing (TDK synth)"),
    (re.compile(r"^SRR-\d+(nH|uH|mH)$"), "aggressive_mag_sourcing (Bourns synth)"),
    (re.compile(r"^IHLP-\d+(nH|uH|mH)$"), "aggressive_mag_sourcing (Vishay synth)"),
]


def classify(reference):
    for pattern, generator in FABRICATED:
        if pattern.match(reference):
            return generator
    return None


def main():
    if not LIVE.exists():
        sys.exit(f"missing {LIVE}")
    today = datetime.date.today().isoformat()

    kept, removed = [], []
    for line in LIVE.open(encoding="utf-8"):
        if not line.strip():
            continue
        record = json.loads(line)
        info = (record.get("magnetic") or {}).get("manufacturerInfo") or {}
        generator = classify(str(info.get("reference", "")))
        if generator is None:
            kept.append(line.rstrip("\n"))
            continue
        record["_validatorQuarantine"] = {
            "date": today,
            "reason": "fabricated part - synthesized by a bulk sourcing script, not sourced from a datasheet",
            "codes": ["MAG_FABRICATED_MPN"],
            "messages": [
                f"MPN matches the value-code template emitted by {generator}; "
                "electrical values reproduce that script's scaling formula exactly "
                "and no datasheet exists for this part number",
            ],
        }
        removed.append(json.dumps(record, ensure_ascii=False))

    if not removed:
        print("no fabricated records found - catalogue is clean")
        return

    shutil.copy2(LIVE, LIVE.with_suffix(f".ndjson.bak-{today}"))
    with LIVE.open("w", encoding="utf-8") as fh:
        for line in kept:
            fh.write(line + "\n")
    with QUAR.open("a", encoding="utf-8") as fh:
        for line in removed:
            fh.write(line + "\n")

    print(f"quarantined {len(removed)} fabricated magnetics -> {QUAR.name}")
    print(f"magnetics.ndjson: {len(kept) + len(removed)} -> {len(kept)}")


if __name__ == "__main__":
    main()
