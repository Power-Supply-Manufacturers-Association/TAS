#!/usr/bin/env python3
"""Apply the staged #474 IC pinout (analog AAS pinout + controller CTAS pins).

analog_ics: datasheetInfo.pinout (AAS pinout) + datasheetInfo.part.case (the
0%-covered package, from the datasheet). controllers: datasheetInfo.pins (CTAS).
Only when absent. source='manufacturerDatasheet' + the datasheet URL.

Safe-write per file (refuse on active writer, byte-identical untouched lines,
per-record schema validation before atomic swap).

Usage: apply_ic_pinout.py [--dry-run]
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO.parent
TODAY = "2026-08-02"
STAGE = Path("/tmp/claude-1000/-home-alf/e0566217-bb16-4d03-9e3f-f35b93581bf0/scratchpad/ic_pinout")

PINOUT = json.load(open(STAGE / "pinout_by_part.json"))
CASE = json.load(open(STAGE / "case_by_part.json"))


def build_registry():
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    res = []
    for repo in ("PEAS", "CIAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS", "TAS", "TDAS", "COAS"):
        sdir = WORKSPACE / repo / "schemas"
        if sdir.is_dir():
            for p in sdir.rglob("*.json"):
                d = json.loads(p.read_text())
                if "$id" in d:
                    res.append((d["$id"], Resource.from_contents(d, default_specification=DRAFT202012)))
    return Registry().with_resources(res)


def part_of(pn):
    return PINOUT.get(pn)


def apply_family(family, registry, dry_run):
    from jsonschema import Draft202012Validator
    if family == "analog":
        fname, disc, schema_id = "analog_ics.ndjson", "analog", "https://psma.com/aas/AAS.json"
    else:
        fname, disc, schema_id = "controllers.ndjson", "controller", "https://psma.com/ctas/controller.json"
    path = REPO / "data" / fname
    validator = Draft202012Validator(registry.get_or_retrieve(schema_id).value.contents, registry=registry)
    lsof = subprocess.run(["lsof", "-F", "a", str(path)], capture_output=True, text=True)
    if [l for l in lsof.stdout.splitlines() if l.startswith("a") and ("w" in l or "u" in l)]:
        sys.exit(f"REFUSING: {fname} is open for writing by another process")

    stats = Counter()
    tmp = path.with_suffix(".ndjson.ic_tmp")
    size0 = path.stat().st_size
    with open(path, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            stats["lines"] += 1
            try:
                doc = json.loads(raw)
                body = doc[disc]
                # analog: body = {subfamily: {...}}; controller: body = {manufacturerInfo,...}
                inner = next(iter(body.values())) if family == "analog" else body
                di = inner["manufacturerInfo"]["datasheetInfo"]
                pn = di.get("part", {}).get("partNumber") or inner["manufacturerInfo"].get("reference")
            except Exception:
                out.write(raw)
                continue
            e = part_of(pn)
            modified = False
            if e and e.get("family") == ("analog" if family == "analog" else "controller"):
                field = "pinout" if family == "analog" else "pins"
                if field not in di:
                    di[field] = e["pinout"]
                    if family == "analog" and CASE.get(pn) and not di.get("part", {}).get("case"):
                        di.setdefault("part", {})["case"] = CASE[pn]
                    di.setdefault("provenance", []).append({
                        "source": "manufacturerDatasheet",
                        "sourceName": f"IC pin-assignment table extraction 2026-08 (#474)",
                        "sourceUrl": e.get("sourceUrl"),
                        "retrievedDate": TODAY,
                        "fields": [f"datasheetInfo.{field}"] + (["part.case"] if family == "analog" and CASE.get(pn) else []),
                    })
                    stats["pinout"] += 1
                    modified = True
            if not modified:
                out.write(raw)
                continue
            errs = list(validator.iter_errors(body))
            if errs:
                tmp.unlink(missing_ok=True)
                sys.exit(f"ABORT {family}: schema fail at line {stats['lines']} ({pn}): {errs[0].message[:300]}")
            out.write((json.dumps(doc, ensure_ascii=False) + "\n").encode())
            stats["modified"] += 1
        src.seek(0, os.SEEK_END)
        if src.tell() > size0:
            src.seek(size0)
            for raw in src:
                out.write(raw)
                stats["tail"] += 1
    n_out = sum(1 for _ in open(tmp, "rb"))
    if n_out != stats["lines"] + stats["tail"]:
        tmp.unlink()
        sys.exit(f"ABORT {family}: line count {n_out} != {stats['lines'] + stats['tail']}")
    print(f"{family}: lines={stats['lines']} pinout={stats['pinout']} modified={stats['modified']} tail={stats['tail']}")
    if dry_run:
        tmp.unlink()
    else:
        os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    registry = build_registry()
    for f in ("analog", "controller"):
        apply_family(f, registry, args.dry_run)
    if args.dry_run:
        print("dry-run: no swap")


if __name__ == "__main__":
    main()
