#!/usr/bin/env python3
"""Extract Bourns diode rows from their parametric Excel export and emit
TAS-format semiconductor/diode records as NDJSON.

Usage:
    python3 scripts/extract_bourns_diodes.py \
        /path/to/bourns-parametric-diodes.xlsx \
        data/diodes_bourns_staged.ndjson
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import openpyxl
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
PROTEUS = REPO.parent

_FUNCTION_MAP = {
    "schottky":               "schottky",
    "schottky bridge":        "schottky",
    "fast response rectifier": "fastRecovery",
    "standard rectifier":     "rectifier",
    "bridge":                 "rectifier",
}

_CASE_NORM = {
    "0603 (1608 metric)": "0603",
    "sma (do-214ac)":     "SMA",
    "smb (do-214aa)":     "SMB",
    "smc (do-214ab)":     "SMC",
}

_THR_HOLE_PACKAGES = {"to-269aa", "dfn3538", "dfs-4", "mbls"}


def _build_registry() -> Registry:
    by_id: dict[str, dict] = {}
    for repo_name in ("PEAS", "SAS"):
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
    schema = json.loads((PROTEUS / "SAS" / "schemas" / "diode.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def _na(v) -> bool:
    return v is None or str(v).strip().upper() in ("N/A", "", "-")


def _float(v) -> float | None:
    if _na(v):
        return None
    m = re.match(r"([\d.]+)", str(v).strip())
    return float(m.group(1)) if m else None


def _normalize_case(raw: str | None) -> str | None:
    if _na(raw):
        return None
    s = str(raw).strip()
    return _CASE_NORM.get(s.lower(), s)


def _build_record(row: tuple) -> dict:
    (part_number, series, if_a, vrrm_v, ifsm_a, vf_v, trr_ns,
     irrm_ma, cap_pf, rth_ja, tj_min, tj_max,
     function, package, length_mm, width_mm, _aec, _eng, _buy,
     datasheet_url) = row[:20]

    # electrical (all fields optional in diode schema)
    electrical: dict = {}
    f = _float(vrrm_v)
    if f is not None:
        electrical["reverseVoltage"] = f
    f = _float(if_a)
    if f is not None:
        electrical["forwardCurrent"] = f
    f = _float(ifsm_a)
    if f is not None:
        electrical["surgeCurrent"] = f
    f = _float(vf_v)
    if f is not None:
        electrical["forwardVoltage"] = f
    f = _float(trr_ns)
    if f is not None:
        electrical["reverseRecoveryTime"] = f * 1e-9
    f = _float(irrm_ma)
    if f is not None:
        electrical["reverseLeakageCurrent"] = f * 1e-3
    f = _float(cap_pf)
    if f is not None:
        electrical["junctionCapacitance"] = f * 1e-12

    # part
    func_str = str(function).strip().lower() if not _na(function) else ""
    sub_type = _FUNCTION_MAP.get(func_str)

    case_code = _normalize_case(package)
    part_block: dict = {
        "partNumber": str(part_number).strip(),
        "technology": "Si",
    }
    if sub_type:
        part_block["subType"] = sub_type
    if not _na(series):
        part_block["series"] = str(series).strip()
    if case_code:
        part_block["case"] = case_code

    # thermal
    thermal: dict = {}
    f = _float(rth_ja)
    if f is not None:
        thermal["thermalResistanceJunctionAmbient"] = f
    f = _float(tj_min)
    if f is not None:
        thermal["junctionTemperatureMin"] = f
    f = _float(tj_max)
    if f is not None:
        thermal["junctionTemperatureMax"] = f

    # mechanical
    pkg_lower = str(package).strip().lower() if not _na(package) else ""
    is_thr = any(p in pkg_lower for p in _THR_HOLE_PACKAGES)
    assy = "tht" if is_thr else "smt"
    mechanical: dict = {"assemblyType": assy}
    if case_code:
        mechanical["case"] = case_code
    l_m = _float(length_mm)
    if l_m is not None:
        mechanical["length"] = {"nominal": l_m / 1000}
    w_m = _float(width_mm)
    if w_m is not None:
        mechanical["width"] = {"nominal": w_m / 1000}

    datasheet_info: dict = {"part": part_block, "electrical": electrical}
    if thermal:
        datasheet_info["thermal"] = thermal
    if mechanical:
        datasheet_info["mechanical"] = mechanical

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

    return {"semiconductor": {"diode": {"manufacturerInfo": mfr_info}}}


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
    validator = _load_validator(registry)

    ok = rejected = skipped = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w") as fout, open(rejected_path, "w") as frej:
        for i, row in enumerate(data_rows, start=2):
            if len(row) < 20:
                skipped += 1
                continue
            try:
                record = _build_record(row)
            except Exception as exc:
                print(f"  Row {i}: build error: {exc}", file=sys.stderr)
                skipped += 1
                continue

            diode_doc = record["semiconductor"]["diode"]
            errors = sorted(validator.iter_errors(diode_doc), key=lambda e: str(e.path))
            if errors:
                entry = {
                    "record": record,
                    "errors": [{"path": list(e.absolute_path), "message": e.message}
                               for e in errors],
                }
                frej.write(json.dumps(entry, separators=(",", ":")) + "\n")
                rejected += 1
            else:
                fout.write(json.dumps(record, separators=(",", ":")) + "\n")
                ok += 1

    print(f"\nSchema validation:")
    print(f"  Written (valid):  {ok:>6}  → {out_path}")
    print(f"  Rejected:         {rejected:>6}  → {rejected_path}")
    print(f"  Skipped:          {skipped:>6}")

    if ok == 0:
        return 1

    try:
        sys.path.insert(0, str(REPO / "validator" / "build"))
        import tas_validator  # type: ignore
    except ImportError:
        print("\nC++ physics validator not available — skipping.")
        return 0 if rejected == 0 else 2

    print("\nRunning physics validator …")
    quar_path = out_path.with_name(out_path.stem + ".quarantine_physics.ndjson")
    clean_path = out_path.with_name(out_path.stem + ".tmp")
    phys_ok = phys_bad = 0
    with open(out_path) as fin, open(clean_path, "w") as fc, open(quar_path, "w") as fq:
        for line in fin:
            rec = json.loads(line)
            result = tas_validator.validate(rec)
            if result.valid:
                fc.write(line)
                phys_ok += 1
            else:
                findings = [{"code": str(f.code), "message": str(f)} for f in result.findings]
                fq.write(json.dumps({"record": rec, "findings": findings},
                                    separators=(",", ":")) + "\n")
                phys_bad += 1
    import os
    os.replace(clean_path, out_path)
    print(f"  Physics-clean:    {phys_ok:>6}  → {out_path}")
    print(f"  Physics-flagged:  {phys_bad:>6}  → {quar_path}")
    return 0 if (rejected == 0 and phys_bad == 0) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
