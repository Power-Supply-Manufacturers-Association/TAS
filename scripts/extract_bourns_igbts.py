#!/usr/bin/env python3
"""Convert the five Bourns BID-series IGBT rows (pasted from web catalog) to
TAS-format semiconductor/igbt NDJSON.

Usage:
    python3 scripts/extract_bourns_igbts.py \
        data/igbts_bourns_staged.ndjson
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
PROTEUS = REPO.parent

# ---------------------------------------------------------------------------
# Raw data (pasted from Bourns parametric catalog, BID series)
# Columns: Part Number, Series, Package, Copacked Diode, Switching Freq,
#   Vcemax(V), VGEmax(V), Ic@25C(A), Icp(A), Ic@100C(A),
#   VGE(th)min(V), VGE(th)typ(V), VCE(sat)typ(V), VFtyp(V), VFmax(V),
#   IF@25C(A), IF@100C(A), Tsc(us), Tj_max(C),
#   Eon(mJ), Eoff(mJ), Ptot(W), RoHS, EngFiles, BuyNow
# ---------------------------------------------------------------------------
_RAW = [
    ("BIDD05N60T",   "BID", "TO-252",  "Yes", "Medium", 600, "±30", 10.3, 15,  5,
     "3.5 @Ic=250uA Vce=Vge", 5.5,  "1.5 @ Vge=15V Ic=5A",  "1.3 @ If=5A",  1.8,  10, "N/A",
     "10 @ Vge=15V Vcc=300V Tc=150°C", 150, 0.2,  "0.07 @ Tc=25°C Vcc 400V Ic=5A Vge=0/15V",  82),
    ("BIDNW30N60H3", "BID", "TO-247N", "Yes", "High",   600, "±20", 5.6,  120, 30,
     "4.0 @Ic=250uA Vce=Vge", 5,    "1.65 @ Vge=15V Ic=30A", "1.8 @ If=12A", "N/A", 12, 12,
     "N/A", 150, 1.85, "0.45 @ Tc=25°C Vcc=400V Ic=30A Vge=0/15V Rg=10 ohm", 230),
    ("BIDW20N60T",   "BID", "TO-247",  "Yes", "Medium", 600, "±20", 8.5,  60,  20,
     "4.0 @ Ic=250uA Vce=Vge", 5,   "1.7 @ Vge=15V Ic=20A",  "1.8 @ If=20A", "N/A", 40, 20,
     "10 @ Vge=15V Vcc=300V",  150, 1,    "0.3 @ Tc=25°C Vcc=400V Ic=20A Vge=0/15V", 192),
    ("BIDW30N60T",   "BID", "TO-247",  "Yes", "Medium", 600, "±20", 4.6,  90,  30,
     "4.0 @Ic=250uA Vce=Vge", 5,    "1.65 @ Vge=15V Ic=30A", "1.8 @ If=30A", "N/A", 60, 30,
     "10 @ Vge=15V Vcc=300V Tc=150°C", 150, 1.85, "0.45 @ Tc=25°C Vcc=400V Ic=30A Vge=0/15V", 230),
    ("BIDW50N65T",   "BID", "TO-247",  "Yes", "Medium", 650, "±20", 4.0,  300, 50,
     "4.0 @Ic=250uA Vce=Vge", 5,    "1.65 @ Vge=15V Ic=50A", "1.7 @ If=50A", 2.5,  50, 50,
     "10 @ Vge=15V Vcc=300V Tc=150°C", 150, 3,    "1.1 @ Tc=25°C Vcc 400V Ic=50A Vge=0/15V", 416),
]


# ---------------------------------------------------------------------------
# Registry + validator
# ---------------------------------------------------------------------------

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
    schema = json.loads((PROTEUS / "SAS" / "schemas" / "igbt.json").read_text())
    return Draft202012Validator(schema, registry=registry)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _na(v) -> bool:
    return v is None or str(v).strip().upper() in ("N/A", "", "-")


def _leading_number(v) -> float | None:
    """Extract the first float from a potentially annotated string like '3.5 @Ic=...'."""
    if _na(v):
        return None
    m = re.match(r"[±]?\s*([\d.]+)", str(v).strip())
    return float(m.group(1)) if m else None


def _parse_vge_max(v) -> float | None:
    """'±30' → 30"""
    if _na(v):
        return None
    m = re.search(r"[\d.]+", str(v))
    return float(m.group()) if m else None


def _build_record(row: tuple) -> dict:
    (part_number, series, package, _copacked, _sw_freq,
     vce_max, vge_max_str, ic_25, _icp, _ic_100,
     vge_th_min_str, vge_th_typ_str, vce_sat_str, _vf_typ, _vf_max,
     _if_25, _if_100, tsc_str, tj_max,
     eon_mj, eoff_str, ptot_w) = row

    # --- electrical ---
    electrical: dict = {
        "collectorEmitterVoltage": float(vce_max),
        "continuousCollectorCurrent": float(ic_25),
        "collectorEmitterSaturation": _leading_number(vce_sat_str),
    }

    vge_max = _parse_vge_max(vge_max_str)
    if vge_max is not None:
        electrical["gateEmitterVoltageMax"] = vge_max

    vge_th_min = _leading_number(vge_th_min_str)
    vge_th_typ = _leading_number(vge_th_typ_str)
    if vge_th_min is not None or vge_th_typ is not None:
        gate_thresh: dict = {}
        if vge_th_min is not None:
            gate_thresh["minimum"] = vge_th_min
        if vge_th_typ is not None:
            gate_thresh["nominal"] = vge_th_typ
        electrical["gateThresholdVoltage"] = gate_thresh

    eon = _leading_number(eon_mj)
    if eon is not None:
        electrical["turnOnEnergy"] = eon * 1e-3

    eoff = _leading_number(eoff_str)
    if eoff is not None:
        electrical["turnOffEnergy"] = eoff * 1e-3

    if not _na(ptot_w):
        electrical["powerDissipation"] = float(ptot_w)

    tsc = _leading_number(tsc_str)
    if tsc is not None:
        electrical["shortCircuitTime"] = tsc * 1e-6

    # --- part ---
    part_block: dict = {
        "partNumber": str(part_number).strip(),
        "technology": "Si",
        "subType": "nChannel",
        "case": str(package).strip(),
    }
    if not _na(series):
        part_block["series"] = str(series).strip()

    # --- thermal ---
    thermal: dict | None = None
    if not _na(tj_max):
        thermal = {"junctionTemperatureMax": float(tj_max)}

    # --- mechanical ---
    mechanical = {"case": str(package).strip(), "assemblyType": "tht"}

    # --- datasheetInfo ---
    datasheet_info: dict = {"part": part_block, "electrical": electrical}
    if thermal:
        datasheet_info["thermal"] = thermal
    datasheet_info["mechanical"] = mechanical

    mfr_info: dict = {
        "name": "Bourns",
        "reference": str(part_number).strip(),
        "status": "production",
        "family": str(series).strip(),
        "datasheetInfo": datasheet_info,
    }

    return {"semiconductor": {"igbt": {"manufacturerInfo": mfr_info}}}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    out_path = Path(argv[1] if len(argv) > 1 else "data/igbts_bourns_staged.ndjson")
    rejected_path = out_path.with_name(out_path.stem + ".rejected.ndjson")

    print("Building schema registry …")
    registry = _build_registry()
    validator = _load_validator(registry)

    ok = rejected = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w") as fout, open(rejected_path, "w") as frej:
        for row in _RAW:
            try:
                record = _build_record(row)
            except Exception as exc:
                print(f"  Build error for {row[0]}: {exc}", file=sys.stderr)
                continue

            igbt_doc = record["semiconductor"]["igbt"]
            errors = sorted(validator.iter_errors(igbt_doc), key=lambda e: str(e.path))
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

    if ok == 0:
        return 1

    # Physics validation
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
