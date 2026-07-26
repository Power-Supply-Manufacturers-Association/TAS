#!/usr/bin/env python3
"""Ingest Würth Elektronik CMC voltage ratings from REDEXPERT into
magnetics.ndjson (ABT #292 follow-up: 3-phase filter slots need real ratings).

Source: the same redexpert.we-online.com parametric pull as the #295 curves
import (product/list module 3, pulled 2026-07-26). Fields used per Order_Code:
  Rated_Voltage      -> datasheetInfo.electrical[0].ratedVoltageAC
  Voltage_Insulation -> datasheetInfo.electrical[0].insulationTestVoltageAC
Module 3 (CM Chokes for Mains Power Lines) states these in V AC — verified
against the WE-CMB H (250 VAC) and WE-TPB HV (760 VAC, 3 kV test) datasheets.
Module 23 (data lines) is NOT imported: its Rated_Voltage basis (AC vs DC) is
not stated by the API and guessing would poison the field.

Line-patch, not bulk-rewrite: only WE common-mode-choke rows matched by
Order_Code are touched; existing values are never overwritten (conflicts are
reported instead); every patched row is re-validated against the MAS magnetic
schema before the file is atomically replaced. The WE `Lines` field (2/3) is
cross-checked against the TAS coil winding count and mismatches are reported,
never written.

Usage: we_redexpert_ratings_import.py <we-redexpert-cmc-curves.json> <magnetics.ndjson>
"""
import datetime
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

PROTEUS = Path.home() / "PSMA"


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


def main(pull_path, ndjson_path):
    raw = json.loads(Path(pull_path).read_text())
    ratings = {}
    for p in raw["list3"]["Data"]:
        code = str(p["Order_Code"])
        entry = {}
        if isinstance(p.get("Rated_Voltage"), (int, float)) and p["Rated_Voltage"] > 0:
            entry["ratedVoltageAC"] = float(p["Rated_Voltage"])
        if isinstance(p.get("Voltage_Insulation"), (int, float)) and p["Voltage_Insulation"] > 0:
            entry["insulationTestVoltageAC"] = float(p["Voltage_Insulation"])
        if entry:
            entry["lines"] = p.get("Lines")
            ratings[code] = entry
    print(f"rating sets keyed by order code: {len(ratings)}")

    validator = build_validator()
    src = Path(ndjson_path)
    out_lines = []
    patched = skipped_invalid = conflicts = line_mismatches = 0
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
                or code not in ratings:
            out_lines.append(stripped)
            continue
        entry = ratings[code]
        wrote = False
        for field in ("ratedVoltageAC", "insulationTestVoltageAC"):
            if field not in entry:
                continue
            if electrical.get(field) is not None:
                if electrical[field] != entry[field]:
                    print(f"CONFLICT {code}: {field} TAS={electrical[field]} REDEXPERT={entry[field]} — kept TAS",
                          file=sys.stderr)
                    conflicts += 1
                continue
            electrical[field] = entry[field]
            wrote = True
        we_lines = entry.get("lines")
        coil = (row["magnetic"].get("coil") or {}).get("functionalDescription") or []
        if we_lines is not None and len(coil) >= 2 and int(we_lines) != len(coil):
            print(f"LINES MISMATCH {code}: WE says {we_lines}, TAS coil has {len(coil)} windings",
                  file=sys.stderr)
            line_mismatches += 1
        if not wrote:
            out_lines.append(stripped)
            continue
        prov = info["datasheetInfo"].setdefault("provenance", [])
        prov.append({
            "source": "manufacturerParametric",
            "sourceName": "Würth Elektronik REDEXPERT parametric ratings (Rated_Voltage VAC, Voltage_Insulation)",
            "sourceUrl": "https://redexpert.we-online.com/redexpert/#/module/3",
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
    print(f"patched {patched} WE CMC rows with REDEXPERT ratings; "
          f"{conflicts} conflicts kept TAS; {line_mismatches} line-count mismatches; "
          f"{skipped_invalid} rejected by schema")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
