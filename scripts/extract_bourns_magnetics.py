#!/usr/bin/env python3
"""Extract Bourns inductor rows from their parametric Excel export and emit
TAS-format magnetic records as NDJSON.

Usage:
    python3 scripts/extract_bourns_magnetics.py \
        /path/to/bourns-parametric.xlsx \
        data/magnetics_bourns_staged.ndjson

The output is a staging file ready for review/merge into data/magnetics.ndjson.
Each record is validated against the MAS JSON Schema (Draft 2020-12) before
being written; rows that fail schema validation are collected into a separate
.rejected.ndjson file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]        # TAS/
PROTEUS = REPO.parent                              # PSMA/


# ---------------------------------------------------------------------------
# Build a registry covering PEAS + MAS so $refs resolve.
# ---------------------------------------------------------------------------

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
    resources = [
        Resource(contents=s, specification=DRAFT202012)
        for s in by_id.values()
    ]
    return Registry().with_resources(
        [(s.contents["$id"], s) for s in resources]
    )


def _load_magnetic_schema(registry: Registry):
    schema = json.loads((PROTEUS / "MAS" / "schemas" / "magnetic.json").read_text())
    return Draft202012Validator(schema, registry=registry)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _na(v) -> bool:
    return v is None or str(v).strip().upper() in ("N/A", "", "-")


def _float(v) -> float | None:
    if _na(v):
        return None
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def _is_shielded(shielding: str | None) -> bool | None:
    if _na(shielding):
        return None
    s = shielding.strip().lower()
    if s in ("shielded", "semi-shielded"):
        return True
    if s in ("unshielded", "non-shielded"):
        return False
    return None


def _case_code(l_mm, w_mm, h_mm) -> str | None:
    parts = [l_mm, w_mm, h_mm]
    if any(_na(p) for p in parts):
        return None
    def fmt(x):
        v = _float(x)
        if v is None:
            return None
        return f"{v:g}"
    strs = [fmt(p) for p in parts]
    if any(s is None for s in strs):
        return None
    return f"{strs[0]}x{strs[1]}x{strs[2]}mm"


def _build_record(row: tuple) -> dict:
    (part_number, series, material_core, inductance_uh, tolerance_pct,
     current_rating_a, current_sat_a, shielding, dcr_ohm, q_val, q_freq_mhz,
     srf_mhz, ratings, temp_min_c, temp_max_c, test_freq_mhz,
     length_mm, width_mm, height_mm, _eng_files, _buy_now, datasheet_url) = row

    # --- electrical block ---
    L_H = _float(inductance_uh)
    if L_H is None:
        raise ValueError(f"Missing inductance for part {part_number!r}")
    L_H *= 1e-6

    tol_pct = _float(tolerance_pct)
    inductance: dict = {"nominal": L_H}
    if tol_pct is not None:
        inductance["minimum"] = L_H * (1 - tol_pct / 100)
        inductance["maximum"] = L_H * (1 + tol_pct / 100)

    electrical: dict = {"subtype": "inductor", "inductance": inductance}

    dcr = _float(dcr_ohm)
    if dcr is not None:
        electrical["dcResistance"] = {"maximum": dcr}

    i_rated = _float(current_rating_a)
    if i_rated is not None:
        electrical["ratedCurrents"] = [i_rated]

    i_sat = _float(current_sat_a)
    if i_sat is not None:
        electrical["saturationCurrentPeak"] = i_sat

    srf = _float(srf_mhz)
    if srf is not None:
        electrical["selfResonantFrequency"] = srf * 1e6

    # --- part block ---
    part_block: dict = {}
    shielded = _is_shielded(shielding)
    if shielded is not None:
        part_block["shielded"] = shielded
    if not _na(material_core):
        part_block["material"] = str(material_core).strip()
    if not _na(series):
        part_block["family"] = str(series).strip()
    cc = _case_code(length_mm, width_mm, height_mm)
    if cc is not None:
        part_block["caseCode"] = cc

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
    l_m = _float(length_mm)
    w_m = _float(width_mm)
    h_m = _float(height_mm)
    if l_m is not None or w_m is not None or h_m is not None:
        mechanical = {}
        if l_m is not None:
            mechanical["length"] = {"nominal": l_m / 1000}
        if w_m is not None:
            mechanical["width"] = {"nominal": w_m / 1000}
        if h_m is not None:
            mechanical["height"] = {"nominal": h_m / 1000}

    # --- datasheetInfo ---
    datasheet_info: dict = {"electrical": [electrical]}
    if part_block:
        datasheet_info["part"] = part_block
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(f"Usage: {argv[0]} <input.xlsx> <output.ndjson>", file=sys.stderr)
        return 1

    xlsx_path = Path(argv[1])
    out_path = Path(argv[2])
    rejected_path = out_path.with_suffix("").with_suffix("") \
        .with_name(out_path.stem + ".rejected.ndjson")

    print(f"Loading {xlsx_path} …")
    wb = openpyxl.load_workbook(str(xlsx_path), read_only=True)
    ws = wb["Parts"]
    rows = list(ws.iter_rows(values_only=True))
    data_rows = rows[1:]
    print(f"  {len(data_rows)} data rows")

    print("Building schema registry …")
    registry = _build_registry()
    validator = _load_magnetic_schema(registry)

    ok_count = 0
    rejected_count = 0
    skip_count = 0

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w") as fout, open(rejected_path, "w") as frej:
        for i, row in enumerate(data_rows, start=2):
            if len(row) < 22:
                skip_count += 1
                continue
            try:
                record = _build_record(row)
            except Exception as exc:
                print(f"  Row {i}: build error: {exc}", file=sys.stderr)
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
    print(f"  Written (valid):  {ok_count:>6}  → {out_path}")
    print(f"  Rejected (schema errors): {rejected_count:>6}  → {rejected_path}")
    print(f"  Skipped (build errors):   {skip_count:>6}")
    return 0 if rejected_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
