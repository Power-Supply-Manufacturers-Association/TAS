#!/usr/bin/env python3
"""Ingest Würth Elektronik CMC impedance curves from REDEXPERT into
magnetics.ndjson (ABT #295).

Source: redexpert.we-online.com public chart API (tc/values/57/3 for CM Chokes
for Mains Power Lines, tc/values/16/23 for CM Chokes for Low Voltage and Data
Lines), pulled 2026-07-26; tuples are [f_MHz, |Z|cm_ohm, |Z|dm_ohm]. MAGNITUDE
ONLY — REDEXPERT publishes no phase, so points carry impedance.magnitude only
(schema-legal: phase is optional; same shape as the Abracon rows). Consumers
needing complex Z must reconstruct phase themselves and SAY SO (Hertz uses the
Bode gain-phase relation, validated against Murata's measured phase).

Line-patch, not bulk-rewrite: only WE common-mode-choke rows matched by
Order_Code are touched; every patched row is re-validated against the MAS
magnetic schema before the file is atomically replaced.

Usage: we_redexpert_curves_import.py <we-redexpert-cmc-curves.json> <magnetics.ndjson>
"""
import datetime
import json
import math
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

PROTEUS = Path.home() / "PSMA"
MAX_POINTS_PER_WINDING = 120


def build_validator():
    by_id = {}
    for repo_name in ("PEAS", "MAS"):
        schema_dir = PROTEUS / repo_name / "schemas"
        if not schema_dir.is_dir():
            continue
        for p in schema_dir.rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by_id[s["$id"]] = s
    resources = [Resource(contents=s, specification=DRAFT202012) for s in by_id.values()]
    registry = Registry().with_resources([(s.contents["$id"], s) for s in resources])
    schema = json.loads((PROTEUS / "MAS" / "schemas" / "magnetic.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def downsample(values):
    """Stride the measured grid to <= MAX_POINTS_PER_WINDING, keeping the last
    point — never resampled, never extrapolated."""
    stride = max(1, -(-len(values) // MAX_POINTS_PER_WINDING))
    picked = values[::stride]
    if picked[-1] != values[-1]:
        picked.append(values[-1])
    return picked


def to_points(values):
    points = []
    for row in downsample(values):
        f_mhz, zcm, zdm = row[0], row[1], row[2]
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (f_mhz, zcm, zdm)):
            continue
        if f_mhz <= 0 or zcm < 0 or zdm < 0:
            continue
        f_hz = f_mhz * 1e6
        points.append({"frequency": f_hz, "impedance": {"magnitude": float(zcm)}, "winding": "common"})
        points.append({"frequency": f_hz, "impedance": {"magnitude": float(zdm)}, "winding": "differential"})
    return points


def main(curves_path, ndjson_path):
    raw = json.loads(Path(curves_path).read_text())
    code_of = {}
    for key in ("list3", "list23"):
        rows = raw[key]["Data"] if isinstance(raw[key], dict) else raw[key]
        for p in rows:
            code_of[p["ID"]] = str(p["Order_Code"])
    curves = {}
    for key, module_url in (("z3", "https://redexpert.we-online.com/redexpert/#/module/3"),
                            ("z23", "https://redexpert.we-online.com/redexpert/#/module/23")):
        for entry in raw[key]:
            code = code_of.get(entry["ID"])
            if code and len(entry.get("Values") or []) >= 10:
                curves[code] = (to_points(entry["Values"]), module_url)
    print(f"curve sets keyed by order code: {len(curves)}")

    validator = build_validator()
    src = Path(ndjson_path)
    out_lines = []
    patched = skipped_invalid = 0
    for line in src.open():
        stripped = line.strip()
        if not stripped:
            continue
        if '"reference"' not in stripped or "rth" not in stripped.lower():
            out_lines.append(stripped)
            continue
        try:
            row = json.loads(stripped)
            info = row["magnetic"]["manufacturerInfo"]
            electrical = info["datasheetInfo"]["electrical"][0]
        except (KeyError, IndexError, json.JSONDecodeError):
            out_lines.append(stripped)
            continue
        name = str(info.get("name", ""))
        subtype = str(electrical.get("subtype", "")).lower().replace("_", "").replace("-", "")
        code = str(info.get("reference"))
        if ("rth" not in name.lower() and "ürth" not in name) or subtype != "commonmodechoke" \
                or code not in curves:
            out_lines.append(stripped)
            continue
        points, module_url = curves[code]
        electrical["impedancePoints"] = points
        prov = info["datasheetInfo"].setdefault("provenance", [])
        prov.append({
            "source": "manufacturerParametric",
            "sourceName": "Würth Elektronik REDEXPERT measured impedance (tc/values chart API, |Z| magnitude, CM+DM)",
            "sourceUrl": module_url,
            "retrievedDate": raw.get("fetchedAt", datetime.date.today().isoformat()),
        })
        errors = sorted(validator.iter_errors(row["magnetic"]), key=lambda e: e.path)
        if errors:
            print(f"REJECT {code}: {errors[0].message[:140]}", file=sys.stderr)
            skipped_invalid += 1
            out_lines.append(stripped)
            continue
        out_lines.append(json.dumps(row, ensure_ascii=False))
        patched += 1
    tmp = src.with_suffix(".tmp")
    tmp.write_text("\n".join(out_lines) + "\n")
    tmp.replace(src)
    print(f"patched {patched} WE CMC rows with REDEXPERT curves; {skipped_invalid} rejected by schema")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
