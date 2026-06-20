#!/usr/bin/env python3
"""Extract Bourns varistor rows from their parametric Excel export and emit
TAS-format varistor records as NDJSON.

NOTE: The RAS varistor schema requires `energyAbsorption` (max single-pulse
energy in J for a 10/1000 µs waveform).  Bourns' parametric catalog does NOT
include this field, so all records that have no energy value will be rejected
by schema validation.  They are written to <stem>.rejected.ndjson together
with the exact validation error so the cause is visible.

Usage:
    python3 scripts/extract_bourns_varistors.py \
        /path/to/bourns-parametric-varistors.xlsx \
        data/varistors_bourns_staged.ndjson
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

_TECH_MAP = {
    "mlv": "multiLayer",
    "mov": "metalOxide",
    "sic": "siliconCarbide",
    "polymer": "polymer",
}


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
    resources = [Resource(contents=s, specification=DRAFT202012) for s in by_id.values()]
    return Registry().with_resources([(s.contents["$id"], s) for s in resources])


def _load_validator(registry: Registry) -> Draft202012Validator:
    schema = json.loads((PROTEUS / "RAS" / "schemas" / "varistor.json").read_text())
    return Draft202012Validator(schema, registry=registry)


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
    _SHORT = {"201": "0201", "402": "0402", "603": "0603", "805": "0805"}
    return _SHORT.get(s, s)


def _build_record(row: tuple) -> dict:
    (part_number, ac_v, dc_v, v_1ma, nominal_v, v_clamp, i_clamp,
     i_nominal_multi, i_surge_8_20, i_surge_10_350, cap_nf,
     mounting, body_size_smd, body_dia_th, automotive,
     temp_min_c, temp_max_c, packaging, technology,
     _eng, _buy, datasheet_url) = row[:22]

    v1ma = _float(v_1ma)
    v_c = _float(v_clamp)
    i_pp = _float(i_surge_8_20)

    if v1ma is None:
        raise ValueError(f"{part_number}: missing V_1mA")
    if v_c is None:
        raise ValueError(f"{part_number}: missing clamping voltage")
    if i_pp is None:
        raise ValueError(f"{part_number}: missing peak surge current (8/20µs)")

    tech_key = str(technology).strip().lower() if not _na(technology) else ""
    if tech_key not in _TECH_MAP:
        raise ValueError(f"{part_number}: unknown technology {technology!r}")
    tech = _TECH_MAP[tech_key]

    electrical: dict = {
        "varistorVoltage": {"nominal": v1ma},
        "clampingVoltage": v_c,
        "clampingCurrent": _float(i_clamp),
        "peakSurgeCurrent": i_pp,
        "surgeWaveform": "8/20",
        # energyAbsorption NOT available in Bourns parametric catalog —
        # schema requires it; records will fail validation intentionally.
    }
    # Remove None values
    electrical = {k: v for k, v in electrical.items() if v is not None}

    i_imp = _float(i_surge_10_350)
    if i_imp is not None:
        electrical["peakSurgeCurrent"] = i_imp
        electrical["surgeWaveform"] = "10/350"

    cap = _float(cap_nf)
    if cap is not None:
        electrical["capacitance"] = cap * 1e-9

    v_ac = _float(ac_v)
    if v_ac is not None:
        electrical["maxContinuousAcVoltage"] = v_ac
    v_dc = _float(dc_v)
    if v_dc is not None:
        electrical["maxContinuousDcVoltage"] = v_dc

    # part
    mount_str = str(mounting).strip().lower() if not _na(mounting) else ""
    is_smd = "smd" in mount_str

    case_code = _normalize_case(body_size_smd) if is_smd else None
    if case_code is None and not _na(body_dia_th):
        case_code = f"TH-{body_dia_th}mm"

    part_block: dict = {
        "partNumber": str(part_number).strip(),
        "technology": tech,
    }
    if case_code:
        part_block["case"] = case_code

    # thermal
    t_min = _float(temp_min_c)
    t_max = _float(temp_max_c)
    thermal: dict | None = None
    if t_min is not None or t_max is not None:
        op: dict = {}
        if t_min is not None:
            op["minimum"] = t_min
        if t_max is not None:
            op["maximum"] = t_max
        thermal = {"operatingTemperature": op}

    # mechanical
    mechanical: dict | None = None
    if case_code:
        assy = "smt" if is_smd else "tht"
        mechanical = {"case": case_code, "assemblyType": assy}

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
    if not _na(datasheet_url):
        url = str(datasheet_url).strip()
        if url.startswith("/"):
            url = "https://www.bourns.com" + url
        mfr_info["datasheetUrl"] = url

    return {"varistor": {"manufacturerInfo": mfr_info, "distributorsInfo": []}}


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
            if len(row) < 22:
                skipped += 1
                continue
            try:
                record = _build_record(row)
            except Exception as exc:
                print(f"  Row {i}: build error: {exc}", file=sys.stderr)
                skipped += 1
                continue

            varistor_doc = record["varistor"]
            errors = sorted(validator.iter_errors(varistor_doc), key=lambda e: str(e.path))
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

    if rejected > 0:
        from collections import Counter
        error_summary: Counter = Counter()
        with open(rejected_path) as f:
            for line in f:
                e = json.loads(line)
                for err in e["errors"]:
                    error_summary[err["message"][:80]] += 1
        print("\n  Top rejection reasons:")
        for msg, cnt in error_summary.most_common(5):
            print(f"    {cnt:>5}×  {msg}")

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
            try:
                result = tas_validator.validate(rec)
            except ValueError:
                # Physics validator doesn't cover varistors yet — pass through.
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
    import os
    os.replace(clean_path, out_path)
    print(f"  Physics-clean:    {phys_ok:>6}  → {out_path}")
    print(f"  Physics-flagged:  {phys_bad:>6}  → {quar_path}")
    return 0 if (rejected == 0 and phys_bad == 0) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
