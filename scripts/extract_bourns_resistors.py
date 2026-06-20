#!/usr/bin/env python3
"""Extract Bourns resistor rows from their parametric Excel export and emit
TAS-format resistor records as NDJSON.

Usage:
    python3 scripts/extract_bourns_resistors.py \
        /path/to/bourns-parametric-resistors.xlsx \
        data/resistors_bourns_staged.ndjson

Valid records go to the output file; schema-rejected records go to
<output>.rejected.ndjson; physics-flagged records go to
<stem>.quarantine_physics.ndjson.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
PROTEUS = REPO.parent


# ---------------------------------------------------------------------------
# Registry + validator
# ---------------------------------------------------------------------------

def _build_registry() -> Registry:
    by_id: dict[str, dict] = {}
    for repo_name in ("PEAS", "RAS"):
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
    resources = [
        Resource(contents=s, specification=DRAFT202012)
        for s in by_id.values()
    ]
    return Registry().with_resources(
        [(s.contents["$id"], s) for s in resources]
    )


def _load_resistor_validator(registry: Registry) -> Draft202012Validator:
    schema = json.loads((PROTEUS / "RAS" / "schemas" / "resistor.json").read_text())
    return Draft202012Validator(schema, registry=registry)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CASE_NORM = {
    "201": "0201",
    "402": "0402",
    "603": "0603",
    "805": "0805",
}

_TECH_MAP = {
    "thick film": "thickFilm",
    "thin film": "thinFilm",
    "metal film": "metalFilm",
    "metal oxide": "metalOxide",
    "wirewound": "wirewound",
    "carbon composition": "carbonComposition",
    "carbon film": "carbonFilm",
    "metal foil": "metalFoil",
    "bulk metal foil": "bulkMetalFoil",
    "current sense shunt": "currentSenseShunt",
    "melf": "melf",
}


def _na(v) -> bool:
    return v is None or str(v).strip().lower() in ("n/a", "", "-")


def _float(v) -> float | None:
    if _na(v):
        return None
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def _normalize_case(raw: str | None) -> str | None:
    if _na(raw):
        return None
    s = str(raw).strip()
    return _CASE_NORM.get(s, s)


def _map_technology(raw: str | None) -> str:
    if _na(raw):
        raise ValueError(f"Missing resistive material: {raw!r}")
    key = str(raw).strip().lower()
    if key not in _TECH_MAP:
        raise ValueError(f"Unknown resistive material: {raw!r}")
    return _TECH_MAP[key]


def _build_record(row: tuple) -> dict:
    (part_number, series, material, resistance_ohm, power_w, tolerance_pct,
     size_inch, tcr_ppm, temp_min_c, temp_max_c, packaging, ratings,
     _eng_files, _buy_now, datasheet_url) = row[:15]

    # --- electrical ---
    R = _float(resistance_ohm)
    if R is None:
        raise ValueError(f"Missing resistance for {part_number!r}")
    tol_pct = _float(tolerance_pct)
    if tol_pct is None:
        raise ValueError(f"Missing tolerance for {part_number!r}")
    pwr = _float(power_w)
    if pwr is None:
        raise ValueError(f"Missing power rating for {part_number!r}")

    electrical: dict = {
        "resistance": {"nominal": R},
        "tolerance": tol_pct / 100.0,
        "powerRating": pwr,
    }
    tcr = _float(tcr_ppm)
    if tcr is not None:
        electrical["temperatureCoefficient"] = tcr

    # --- part ---
    technology = _map_technology(material)
    case_code = _normalize_case(size_inch)
    part_block: dict = {
        "partNumber": str(part_number).strip(),
        "technology": technology,
    }
    if not _na(series):
        part_block["series"] = str(series).strip()
    if case_code is not None:
        part_block["case"] = case_code

    # --- thermal ---
    thermal: dict | None = None
    t_min = _float(temp_min_c)
    t_max = _float(temp_max_c)
    if t_min is not None or t_max is not None:
        op_temp: dict = {}
        if t_min is not None:
            op_temp["minimum"] = t_min
        if t_max is not None:
            op_temp["maximum"] = t_max
        thermal = {"operatingTemperature": op_temp}

    # --- mechanical ---
    mechanical: dict | None = None
    if case_code is not None:
        mechanical = {"case": case_code, "assemblyType": "smt"}

    # --- datasheetInfo ---
    datasheet_info: dict = {"part": part_block, "electrical": electrical}
    if thermal:
        datasheet_info["thermal"] = thermal
    if mechanical:
        datasheet_info["mechanical"] = mechanical

    # --- manufacturerInfo ---
    mfr_info: dict = {
        "name": "Bourns",
        "reference": str(part_number).strip(),
        "status": "production",
        "datasheetInfo": datasheet_info,
    }
    if not _na(series):
        mfr_info["family"] = str(series).strip()
    if not _na(datasheet_url):
        mfr_info["datasheetUrl"] = str(datasheet_url).strip()

    return {"resistor": {"manufacturerInfo": mfr_info, "distributorsInfo": []}}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(f"Usage: {argv[0]} <input.xlsx> <output.ndjson>", file=sys.stderr)
        return 1

    xlsx_path = Path(argv[1])
    out_path = Path(argv[2])
    rejected_path = out_path.with_name(out_path.stem + ".rejected.ndjson")

    print(f"Loading {xlsx_path} …")
    wb = openpyxl.load_workbook(str(xlsx_path), read_only=True)
    ws = wb["Parts"]
    rows = list(ws.iter_rows(values_only=True))
    data_rows = rows[1:]
    print(f"  {len(data_rows)} data rows")

    print("Building schema registry …")
    registry = _build_registry()
    validator = _load_resistor_validator(registry)

    ok = rejected = skipped = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w") as fout, open(rejected_path, "w") as frej:
        for i, row in enumerate(data_rows, start=2):
            if len(row) < 15:
                skipped += 1
                continue
            try:
                record = _build_record(row)
            except Exception as exc:
                print(f"  Row {i}: build error: {exc}", file=sys.stderr)
                skipped += 1
                continue

            errors = sorted(validator.iter_errors(record["resistor"]),
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
                rejected += 1
            else:
                fout.write(json.dumps(record, separators=(",", ":")) + "\n")
                ok += 1

    print(f"\nSchema validation:")
    print(f"  Written (valid):   {ok:>6}  → {out_path}")
    print(f"  Rejected:          {rejected:>6}  → {rejected_path}")
    print(f"  Skipped (errors):  {skipped:>6}")

    if ok == 0:
        return 1

    # --- physics validation ---
    try:
        sys.path.insert(0, str(REPO / "validator" / "build"))
        import tas_validator  # type: ignore
    except ImportError:
        print("\nC++ physics validator not available — skipping physics pass.")
        return 0 if rejected == 0 else 2

    print("\nRunning physics validator …")
    quar_path = out_path.with_name(out_path.stem + ".quarantine_physics.ndjson")
    clean_path = out_path.with_name(out_path.stem + ".tmp")

    phys_ok = phys_bad = 0
    with open(out_path) as fin, open(clean_path, "w") as fclean, open(quar_path, "w") as fq:
        for line in fin:
            rec = json.loads(line)
            result = tas_validator.validate(rec)
            if result.valid:
                fclean.write(line)
                phys_ok += 1
            else:
                findings = [{"code": str(f.code), "message": str(f)}
                            for f in result.findings]
                entry = {"record": rec, "findings": findings}
                fq.write(json.dumps(entry, separators=(",", ":")) + "\n")
                phys_bad += 1

    import os
    os.replace(clean_path, out_path)

    print(f"  Physics-clean:     {phys_ok:>6}  → {out_path}")
    print(f"  Physics-flagged:   {phys_bad:>6}  → {quar_path}")

    return 0 if (rejected == 0 and phys_bad == 0) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
