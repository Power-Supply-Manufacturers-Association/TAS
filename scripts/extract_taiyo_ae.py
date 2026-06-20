#!/usr/bin/env python3
"""Extract Taiyo Yuden conductive polymer hybrid aluminum electrolytic capacitor rows
from TY-COMPAS CSV export and emit TAS-format capacitor records as NDJSON.

Usage:
    python3 scripts/extract_taiyo_ae.py \
        /path/to/TY_AE_ProductData.csv \
        data/capacitors_taiyo_ae.ndjson
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
PROTEUS = REPO.parent

VALID_STATUSES = {"Mass Production", "Mass Production (Preferred)"}


def _build_registry() -> Registry:
    by_id: dict[str, dict] = {}
    for repo_name in ("PEAS", "CAS"):
        schema_dir = PROTEUS / repo_name / "schemas"
        if not schema_dir.is_dir():
            continue
        for p in schema_dir.rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            sid = s.get("$id")
            if sid:
                by_id[sid] = s
    resources = [Resource(contents=s, specification=DRAFT202012) for s in by_id.values()]
    return Registry().with_resources([(s.contents["$id"], s) for s in resources])


def _load_validator(registry: Registry) -> Draft202012Validator:
    schema = json.loads((PROTEUS / "CAS" / "schemas" / "capacitor.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def _na(v) -> bool:
    return v is None or str(v).strip().upper() in ("N/A", "", "-")


def _float(v) -> float | None:
    if _na(v):
        return None
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def _parse_capacitance(s: str) -> float:
    """Parse '33 uF', '100 uF' etc. → value in Farads."""
    s = s.strip()
    m = re.match(r"^([\d.]+)\s*(pF|nF|uF|mF|F)$", s, re.IGNORECASE)
    if not m:
        raise ValueError(f"Cannot parse capacitance: {s!r}")
    val = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "pf":
        return val * 1e-12
    if unit == "nf":
        return val * 1e-9
    if unit == "uf":
        return val * 1e-6
    if unit == "mf":
        return val * 1e-3
    return val


def _parse_tolerance(s: str) -> float:
    """Parse '± 20 %' → 20.0"""
    m = re.search(r"([\d.]+)\s*%", s.strip())
    if not m:
        raise ValueError(f"Cannot parse tolerance: {s!r}")
    return float(m.group(1))


def _parse_ripple(s: str) -> tuple[float, float, int]:
    """Parse '900(105/100k)' → (ripple_A, temp_C, freq_Hz).

    Format: <mArms>(<temp>/<freq>[k|M]) where k=×1000, M=×1000000.
    """
    s = s.strip()
    m = re.match(r"^([\d.]+)\((\d+)/([\d.]+)([kKmM]?)\)$", s)
    if not m:
        raise ValueError(f"Cannot parse ripple: {s!r}")
    ripple_ma = float(m.group(1))
    temp = float(m.group(2))
    freq_base = float(m.group(3))
    suffix = m.group(4).lower()
    if suffix == "k":
        freq_hz = int(freq_base * 1000)
    elif suffix == "m":
        freq_hz = int(freq_base * 1000000)
    else:
        freq_hz = int(freq_base)
    return ripple_ma / 1000.0, temp, freq_hz


def _parse_size_phi_x_l(s: str) -> tuple[float, float]:
    """Parse 'φ5x5.8' or 'φ6.3x5.8' → (diameter_mm, height_mm)."""
    s = s.strip().lstrip("φΦ")
    parts = s.split("x")
    if len(parts) != 2:
        raise ValueError(f"Cannot parse φDxL size: {s!r}")
    return float(parts[0]), float(parts[1])


def _build_record(row: list[str]) -> dict:
    status = row[1].strip()
    part_number = row[3].strip()
    voltage_str = row[5].strip()
    cap_str = row[6].strip()
    tole_str = row[7].strip()
    esr_str = row[8].strip()
    ripple_str = row[10].strip()
    size_str = row[12].strip()
    temp_lower_str = row[13].strip()
    temp_upper_str = row[14].strip()

    if status not in VALID_STATUSES:
        raise ValueError(f"Status not in production: {status!r}")

    voltage = _float(voltage_str)
    if voltage is None:
        raise ValueError(f"Missing rated voltage for part {part_number!r}")

    C_F = _parse_capacitance(cap_str)
    tol_pct = _parse_tolerance(tole_str)

    t_min = _float(temp_lower_str)
    t_max = _float(temp_upper_str)
    if t_min is None or t_max is None:
        raise ValueError(f"Missing temperature range for part {part_number!r}")

    d_mm, h_mm = _parse_size_phi_x_l(size_str)

    capacitance: dict = {
        "nominal": C_F,
        "minimum": C_F * (1 - tol_pct / 100.0),
        "maximum": C_F * (1 + tol_pct / 100.0),
    }

    electrical: dict = {
        "capacitance": capacitance,
        "ratedVoltage": voltage,
        "polarized": True,
    }

    esr = _float(esr_str)
    if esr is not None:
        electrical["esr"] = esr

    if not _na(ripple_str):
        ripple_a, ripple_temp, ripple_freq = _parse_ripple(ripple_str)
        electrical["rippleCurrent"] = ripple_a
        electrical["rippleCurrentFrequency"] = ripple_freq
        electrical["rippleCurrentTemperature"] = ripple_temp

    dimensions: dict = {
        "diameter": {"nominal": d_mm / 1000.0},
        "height": {"nominal": h_mm / 1000.0},
    }

    mechanical: dict = {
        "dimensions": dimensions,
        "shape": {"assembly": "SMT", "shapeType": "SMD"},
    }

    thermal: dict = {"operatingTemperature": {"minimum": t_min, "maximum": t_max}}

    case_str = f"{d_mm:g}x{h_mm:g}"
    part_block: dict = {
        "partNumber": part_number,
        "technology": "aluminum-hybrid-polymer",
        "case": case_str,
    }

    datasheet_info: dict = {
        "part": part_block,
        "electrical": electrical,
        "thermal": thermal,
        "mechanical": mechanical,
    }

    mfr_info: dict = {
        "name": "Taiyo Yuden",
        "reference": part_number,
        "status": "production",
        "datasheetInfo": datasheet_info,
    }

    return {"capacitor": {"manufacturerInfo": mfr_info}}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(f"Usage: {argv[0]} <TY_AE_ProductData.csv> <output.ndjson>", file=sys.stderr)
        return 1

    csv_path = Path(argv[1])
    out_path = Path(argv[2])
    rejected_path = out_path.with_name(out_path.stem + ".rejected.ndjson")

    print(f"Loading {csv_path} …")
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    data_rows = rows[3:]
    print(f"  {len(data_rows)} data rows")

    print("Building schema registry …")
    registry = _build_registry()
    validator = _load_validator(registry)

    ok_count = 0
    rejected_count = 0
    skip_count = 0

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w") as fout, open(rejected_path, "w") as frej:
        for i, row in enumerate(data_rows, start=4):
            if len(row) < 15:
                skip_count += 1
                continue
            try:
                record = _build_record(row)
            except Exception as exc:
                print(f"  Row {i}: skip: {exc}", file=sys.stderr)
                skip_count += 1
                continue

            errors = sorted(validator.iter_errors(record["capacitor"]),
                            key=lambda e: str(e.path))
            if errors:
                entry = {
                    "record": record,
                    "errors": [
                        {"path": list(e.absolute_path), "message": e.message}
                        for e in errors
                    ],
                }
                frej.write(json.dumps(entry, separators=(",", ":")) + "\n")
                rejected_count += 1
            else:
                fout.write(json.dumps(record, separators=(",", ":")) + "\n")
                ok_count += 1

    print(f"\nResults:")
    print(f"  Written (valid):          {ok_count:>6}  → {out_path}")
    print(f"  Rejected (schema errors): {rejected_count:>6}  → {rejected_path}")
    print(f"  Skipped (build errors):   {skip_count:>6}")

    if rejected_count > 0:
        ec: Counter = Counter()
        with open(rejected_path) as f:
            for line in f:
                e = json.loads(line)
                for err in e["errors"]:
                    ec[err["message"][:80]] += 1
        print("\n  Top rejection reasons:")
        for msg, cnt in ec.most_common(5):
            print(f"    {cnt:>5}×  {msg}")

    return 0 if rejected_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
