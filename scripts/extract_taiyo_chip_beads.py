#!/usr/bin/env python3
"""Extract Taiyo Yuden chip bead rows from TY-COMPAS CSV export and emit
TAS-format magnetic/chipBead records as NDJSON.

Usage:
    python3 scripts/extract_taiyo_chip_beads.py \
        /path/to/TY_B_ProductData.csv \
        data/magnetics_taiyo_beads.ndjson
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
    for repo_name in ("PEAS", "MAS"):
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
    schema = json.loads((PROTEUS / "MAS" / "schemas" / "magnetic.json").read_text())
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


def _parse_tolerance(s: str) -> float:
    """Parse '± 25 %' → 25.0"""
    m = re.search(r"([\d.]+)\s*%", s.strip())
    if not m:
        raise ValueError(f"Cannot parse tolerance: {s!r}")
    return float(m.group(1))


def _parse_size_lxw(s: str) -> tuple[float, float]:
    """Parse '1.6x0.8' → (1.6, 0.8) in mm."""
    parts = s.strip().split("x")
    if len(parts) != 2:
        raise ValueError(f"Cannot parse size: {s!r}")
    return float(parts[0]), float(parts[1])


def _parse_temp_range(s: str) -> tuple[float, float]:
    """Parse '-40 to +125' → (-40.0, 125.0)"""
    m = re.match(r"^([+-]?\d+)\s*to\s*([+-]?\d+)$", s.strip())
    if not m:
        raise ValueError(f"Cannot parse temp range: {s!r}")
    return float(m.group(1)), float(m.group(2))


def _parse_eia_code(s: str) -> str | None:
    """Extract EIA code from '0603/1608'; return None if unparseable."""
    if _na(s):
        return None
    first = s.strip().split("/")[0].strip()
    if not first or first == "-":
        return None
    if re.match(r"^\d{4}$", first) or re.match(r"^\d{6}$", first):
        return first
    return None


def _build_record(row: list[str]) -> dict:
    status = row[1].strip()
    part_number = row[3].strip()
    z_100mhz_str = row[5].strip()
    z_1ghz_str = row[6].strip()
    size_str = row[7].strip()
    t_max_str = row[8].strip()
    i_max_str = row[9].strip()
    rdc_max_str = row[10].strip()
    z_tole_str = row[11].strip()
    temp_str = row[12].strip()
    case_size_str = row[13].strip()

    if status not in VALID_STATUSES:
        raise ValueError(f"Status not in production: {status!r}")

    tol_pct = _parse_tolerance(z_tole_str)

    electrical: dict = {"subtype": "chipBead", "impedanceTolerance": tol_pct}

    rdc_max = _float(rdc_max_str)
    if rdc_max is not None:
        electrical["dcResistance"] = {"maximum": rdc_max}

    i_max = _float(i_max_str)
    if i_max is not None:
        electrical["ratedCurrents"] = [i_max]

    impedance_points = []
    z_100 = _float(z_100mhz_str)
    if z_100 is not None:
        impedance_points.append({"frequency": 100000000.0, "impedance": {"magnitude": z_100}})
    z_1g = _float(z_1ghz_str)
    if z_1g is not None:
        impedance_points.append({"frequency": 1000000000.0, "impedance": {"magnitude": z_1g}})
    if impedance_points:
        electrical["impedancePoints"] = impedance_points

    l_mm, w_mm = _parse_size_lxw(size_str)
    t_max_mm = _float(t_max_str)

    mechanical: dict = {
        "length": {"nominal": l_mm / 1000.0},
        "width": {"nominal": w_mm / 1000.0},
    }
    if t_max_mm is not None:
        mechanical["height"] = {"nominal": t_max_mm / 1000.0}

    t_min, t_max = _parse_temp_range(temp_str)
    thermal: dict = {"operatingTemperature": {"minimum": t_min, "maximum": t_max}}

    eia_code = _parse_eia_code(case_size_str)
    part_block: dict = {"shielded": False, "material": "Ferrite"}
    if eia_code is not None:
        part_block["caseCode"] = eia_code

    datasheet_info: dict = {
        "part": part_block,
        "electrical": [electrical],
        "mechanical": mechanical,
        "thermal": thermal,
    }

    mfr_info: dict = {
        "name": "Taiyo Yuden",
        "reference": part_number,
        "status": "production",
        "datasheetInfo": datasheet_info,
    }

    return {
        "magnetic": {
            "manufacturerInfo": mfr_info,
            "core": {
                "functionalDescription": {
                    "type": "twoPieceSet",
                    "material": "Dummy",
                    "shape": "Dummy",
                    "gapping": [],
                }
            },
            "coil": {
                "bobbin": "Dummy",
                "functionalDescription": [
                    {
                        "name": "Dummy",
                        "numberTurns": 1,
                        "numberParallels": 1,
                        "isolationSide": "primary",
                        "wire": "Dummy",
                    }
                ],
            },
        }
    }


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(f"Usage: {argv[0]} <TY_B_ProductData.csv> <output.ndjson>", file=sys.stderr)
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
            if len(row) < 14:
                skip_count += 1
                continue
            try:
                record = _build_record(row)
            except Exception as exc:
                print(f"  Row {i}: skip: {exc}", file=sys.stderr)
                skip_count += 1
                continue

            errors = sorted(validator.iter_errors(record["magnetic"]),
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

    if ok_count == 0:
        return 1

    try:
        sys.path.insert(0, str(REPO / "validator" / "build"))
        import tas_validator  # type: ignore
    except ImportError:
        print("\nC++ physics validator not available — skipping.")
        return 0 if rejected_count == 0 else 2

    print("\nRunning physics validator …")
    quar_path = out_path.with_name(out_path.stem + ".quarantine_physics.ndjson")
    clean_path = out_path.with_name(out_path.stem + ".tmp")
    phys_ok = phys_bad = 0

    with open(out_path) as fin, open(clean_path, "w") as fc, open(quar_path, "w") as fq:
        for line in fin:
            rec = json.loads(line)
            try:
                result = tas_validator.validate(rec)
            except ValueError:
                fc.write(line)
                phys_ok += 1
                continue
            if result.valid:
                fc.write(line)
                phys_ok += 1
            else:
                findings = [{"code": str(f.code), "message": str(f)} for f in result.findings]
                fq.write(json.dumps({"record": rec, "findings": findings},
                                    separators=(",", ":")) + "\n")
                phys_bad += 1

    os.replace(clean_path, out_path)
    print(f"  Physics-clean:    {phys_ok:>6}  → {out_path}")
    print(f"  Physics-flagged:  {phys_bad:>6}  → {quar_path}")

    return 0 if (rejected_count == 0 and phys_bad == 0) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
