#!/usr/bin/env python3
"""Quarantine diodes whose stored ratings are physically impossible.

Found 2026-07-20 while stress-testing the Kelvin cross-reference tool: asking for
a substitute for a 100 V / 2 A Schottky returned "BAS40-02V, 1000 V, 120 A" as a
recommended drop-in upgrade. A BAS40 is a 40 V, 200 mA small-signal Schottky.

Every affected row traces to one source — "Vishay parametric (__NEXT_DATA__
webtable)" — and the damage is a column mis-mapping: the parts are also
mis-typed (BAS170WS, BAS281/282/285/286 are switching diodes, not Schottky;
UFS560/UFS580 are ultrafast rectifiers), so both the type and the ratings are
wrong. Nothing here is recoverable by re-deriving a field; the rows have to come
out until the source is re-scraped correctly.

The rules below are deliberately conservative — each rejects a combination that
cannot exist in silicon, not one that is merely unusual:

  * a silicon Schottky above 300 V. Schottky barrier height caps the practical
    reverse rating; SiC Schottkys reach 1700 V, so parts marked SiC are exempt.
  * a Schottky with a forward drop above 1.2 V. The low drop IS the device: a
    part above that is a PN or ultrafast rectifier that has been mis-typed.

Idempotent: re-running on a clean catalogue moves nothing.
"""
import datetime
import json
import shutil
import sys
from pathlib import Path

DATA = Path("/home/alf/PSMA/TAS/data")
LIVE = DATA / "diodes.ndjson"
QUAR = DATA / "diodes.quarantine_invalid_physics.ndjson"

SCHOTTKY_MAX_VRRM = 300.0   # V, silicon
SCHOTTKY_MAX_VF = 1.2       # V


def num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def violations(info):
    """Physics rules this record breaks; empty when it is plausible."""
    datasheet = info.get("datasheetInfo") or {}
    electrical = datasheet.get("electrical") or {}
    part = datasheet.get("part") or {}
    subtype = str(part.get("subType") or "").lower()
    technology = str(part.get("technology") or "").lower()
    if subtype != "schottky" or "sic" in technology:
        return []

    out = []
    vrrm = num(electrical.get("reverseVoltage"))
    vf = num(electrical.get("forwardVoltage"))
    if vrrm is not None and vrrm > SCHOTTKY_MAX_VRRM:
        out.append(
            f"silicon Schottky rated {vrrm:g} V reverse; the barrier height caps this "
            f"around {SCHOTTKY_MAX_VRRM:g} V (SiC parts are exempt and are not marked SiC here)"
        )
    if vf is not None and vf > SCHOTTKY_MAX_VF:
        out.append(
            f"Schottky with a {vf:g} V forward drop; the low drop is the defining property, "
            "so this is a mis-typed PN or ultrafast rectifier"
        )
    return out


def each_diode(record):
    node = ((record.get("semiconductor") or {}).get("diode")) or {}
    info = node.get("manufacturerInfo")
    return info if isinstance(info, dict) else None


def main():
    if not LIVE.exists():
        sys.exit(f"missing {LIVE}")
    today = datetime.date.today().isoformat()

    kept, removed = [], []
    for line in LIVE.open(encoding="utf-8"):
        if not line.strip():
            continue
        record = json.loads(line)
        info = each_diode(record)
        broken = violations(info) if info else []
        if not broken:
            kept.append(line.rstrip("\n"))
            continue
        record["_validatorQuarantine"] = {
            "date": today,
            "reason": "physically impossible ratings (source column mis-mapping)",
            "codes": ["DIODE_IMPOSSIBLE_RATINGS"],
            "messages": broken,
        }
        removed.append(json.dumps(record, ensure_ascii=False))

    if not removed:
        print("no physically impossible diodes found - catalogue is clean")
        return

    shutil.copy2(LIVE, LIVE.with_suffix(f".ndjson.bak-{today}"))
    with LIVE.open("w", encoding="utf-8") as fh:
        for line in kept:
            fh.write(line + "\n")
    with QUAR.open("a", encoding="utf-8") as fh:
        for line in removed:
            fh.write(line + "\n")
    print(f"quarantined {len(removed)} impossible diodes -> {QUAR.name}")
    print(f"diodes.ndjson: {len(kept) + len(removed)} -> {len(kept)}")


if __name__ == "__main__":
    main()
