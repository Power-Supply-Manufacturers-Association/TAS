#!/usr/bin/env python3
"""Extract Taiyo Yuden MLCC rows from TY-COMPAS CSV export and emit
TAS-format capacitor records as NDJSON.

Usage:
    python3 scripts/extract_taiyo_mlcc.py \
        /path/to/TY_C_ProductData.csv \
        data/capacitors_taiyo_mlcc.ndjson
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

_CLASS1_PREFIXES = {"C0G", "C0H", "C0J", "C0K", "U2J", "U2K"}
_CLASS2_PREFIXES = {"X5R", "X6S", "X6T", "X7R", "X7S", "X7T", "X8L"}
_CLASS3_PREFIXES = {"Y5V", "Z5U"}


def _dielectric_to_technology(raw: str) -> str:
    """Map TY dielectric code to CAS technology string, or raise."""
    raw = raw.strip()
    if raw == "(undefined)":
        raise ValueError(f"Undefined dielectric code — cannot determine class")
    base = raw.split("/")[0].strip()
    if base in _CLASS1_PREFIXES:
        return "ceramic-class-1"
    if base in _CLASS2_PREFIXES:
        return "ceramic-class-2"
    if base in _CLASS3_PREFIXES:
        return "ceramic-class-3"
    raise ValueError(f"Unknown dielectric code: {raw!r}")


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
    """Parse '4.7 uF', '100 pF', '10 nF' etc. → value in Farads."""
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


def _parse_tolerance(s: str) -> tuple[float, bool]:
    """Parse tolerance string.

    Returns (value, is_absolute_pF):
      '± 20 %'   → (20.0, False)   — percentage
      '± 0.1pF'  → (1e-13, True)   — absolute Farads
    """
    s = s.strip()
    m_pct = re.search(r"([\d.]+)\s*%", s)
    if m_pct:
        return float(m_pct.group(1)), False
    m_pf = re.match(r"[±\s]*([\d.]+)\s*pF$", s, re.IGNORECASE)
    if m_pf:
        return float(m_pf.group(1)) * 1e-12, True
    raise ValueError(f"Cannot parse tolerance: {s!r}")


def _parse_size_lxw(s: str) -> tuple[float, float]:
    """Parse '0.6x0.3' → (0.6, 0.3) in mm."""
    parts = s.strip().split("x")
    if len(parts) != 2:
        raise ValueError(f"Cannot parse size: {s!r}")
    return float(parts[0]), float(parts[1])


def _parse_temp_range(s: str) -> tuple[float, float]:
    """Parse '-55 to +85' → (-55.0, 85.0)"""
    m = re.match(r"^([+-]?\d+)\s*to\s*([+-]?\d+)$", s.strip())
    if not m:
        raise ValueError(f"Cannot parse temp range: {s!r}")
    return float(m.group(1)), float(m.group(2))


def _parse_eia_code(s: str) -> str | None:
    """Extract EIA code from '0201/0603'; return None if unparseable."""
    if _na(s):
        return None
    first = s.strip().split("/")[0].strip()
    if not first or first == "-":
        return None
    if re.match(r"^\d{4}$", first) or re.match(r"^\d{6}$", first):
        return first
    return None


def _build_record(row: list[str]) -> dict:
    series_raw = row[0].strip()
    status = row[1].strip()
    part_number = row[3].strip()
    cap_str = row[5].strip()
    size_str = row[6].strip()
    t_max_str = row[7].strip()
    voltage_str = row[8].strip()
    dielectric_str = row[9].strip()
    tole_str = row[10].strip()
    temp_str = row[11].strip()
    case_size_str = row[12].strip()

    if status not in VALID_STATUSES:
        raise ValueError(f"Status not in production: {status!r}")

    technology = _dielectric_to_technology(dielectric_str)

    C_F = _parse_capacitance(cap_str)
    tol_val, tol_is_abs = _parse_tolerance(tole_str)

    voltage = _float(voltage_str)
    if voltage is None:
        raise ValueError(f"Missing rated voltage for part {part_number!r}")

    l_mm, w_mm = _parse_size_lxw(size_str)
    t_max_mm = _float(t_max_str)

    t_min, t_max = _parse_temp_range(temp_str)

    eia_code = _parse_eia_code(case_size_str)

    if tol_is_abs:
        c_min = C_F - tol_val
        c_max = C_F + tol_val
    else:
        c_min = C_F * (1 - tol_val / 100.0)
        c_max = C_F * (1 + tol_val / 100.0)

    capacitance: dict = {
        "nominal": C_F,
        "minimum": c_min,
        "maximum": c_max,
    }

    electrical: dict = {
        "capacitance": capacitance,
        "ratedVoltage": voltage,
    }

    dimensions: dict = {
        "length": {"nominal": l_mm / 1000.0},
        "width": {"nominal": w_mm / 1000.0},
    }
    if t_max_mm is not None:
        dimensions["height"] = {"nominal": t_max_mm / 1000.0}

    mechanical: dict = {
        "dimensions": dimensions,
        "shape": {"assembly": "SMT", "shapeType": "SMD"},
    }

    thermal: dict = {"operatingTemperature": {"minimum": t_min, "maximum": t_max}}

    part_block: dict = {
        "partNumber": part_number,
        "technology": technology,
        "dielectricCode": dielectric_str,
    }
    if eia_code is not None:
        part_block["case"] = eia_code

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

    series_short = series_raw.split()[0] if series_raw else None
    if series_short:
        mfr_info["family"] = series_short

    return {"capacitor": {"manufacturerInfo": mfr_info}}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(f"Usage: {argv[0]} <TY_C_ProductData.csv> <output.ndjson>", file=sys.stderr)
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
            if len(row) < 13:
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
