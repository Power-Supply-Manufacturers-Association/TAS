#!/usr/bin/env python3
"""Apply the staged IPC-7351 landPattern library (#471) to the passive catalogs.

For each capacitor / resistor / varistor record: normalize its case code (the
staged normalizer, technology-gated for tantalum), look up the land pattern, and
write mechanical.landPattern (source='derived' + derivation citing the IPC-7351B
density level and the case). Only when landPattern is absent and the case maps.

Safe-write per file (the connector-fill contract): refuse if a writer holds the
file; stream, copying untouched lines byte-identical; validate every MODIFIED
record against its family schema before the atomic os.replace; verify the line
count. One invalid modification aborts.

Usage: apply_passive_landpattern.py [--family capacitor|resistor|varistor|all] [--dry-run]
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
STAGE = Path("/tmp/claude-1000/-home-alf/e0566217-bb16-4d03-9e3f-f35b93581bf0/scratchpad/passive_landpattern")
TODAY = "2026-08-02"

sys.path.insert(0, str(STAGE))
from normalizer import normalize_case  # noqa: E402

LIB = json.load(open(STAGE / "landpattern_by_case.json"))

# family -> (ndjson filename, discriminator key, schema $id)
FAMILIES = {
    "capacitor": ("capacitors.ndjson", "capacitor", "https://psma.com/cas/capacitor.json"),
    "resistor": ("resistors.ndjson", "resistor", "https://psma.com/ras/resistor.json"),
    "varistor": ("varistors.ndjson", "varistor", "https://psma.com/ras/varistor.json"),
}


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


def apply_family(family, registry, dry_run):
    from jsonschema import Draft202012Validator
    fname, disc, schema_id = FAMILIES[family]
    path = REPO / "data" / fname
    validator = Draft202012Validator(registry.get_or_retrieve(schema_id).value.contents, registry=registry)

    lsof = subprocess.run(["lsof", "-F", "a", str(path)], capture_output=True, text=True)
    if [l for l in lsof.stdout.splitlines() if l.startswith("a") and ("w" in l or "u" in l)]:
        sys.exit(f"REFUSING: {fname} is open for writing by another process")

    stats = Counter()
    codes = Counter()
    tmp = path.with_suffix(".ndjson.lp_tmp")
    size0 = path.stat().st_size
    with open(path, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            stats["lines"] += 1
            try:
                doc = json.loads(raw)
                comp = doc[disc]
                di = comp["manufacturerInfo"]["datasheetInfo"]
            except Exception:
                out.write(raw)
                continue
            mech = di.get("mechanical")
            part = di.get("part", {}) or {}
            if not isinstance(mech, dict) or "landPattern" in mech:
                out.write(raw)
                continue
            code = normalize_case(part.get("case"), part.get("technology"), family)
            if not code or code not in LIB:
                out.write(raw)
                continue
            mech["landPattern"] = LIB[code]["landPattern"]
            di.setdefault("provenance", []).append({
                "source": "derived",
                "sourceName": "OpenConverters passive landPattern fill 2026-08 (#471, IPC-7351B nominal)",
                "retrievedDate": TODAY,
                "fields": ["mechanical.landPattern"],
                "derivation": (f"recommended land pattern generated from the standardized case "
                               f"code '{part.get('case')}' -> canonical {code} at IPC-7351B density "
                               f"level M (nominal); {LIB[code].get('source','IPC-7351B')}. Two-pad "
                               f"chip geometry, no per-part measurement."),
            })
            # the family schema validates the UNWRAPPED component (test_data.py
            # unwraps the single discriminator key before validating).
            errs = list(validator.iter_errors(comp))
            if errs:
                tmp.unlink(missing_ok=True)
                sys.exit(f"ABORT {family}: modified record fails schema at line {stats['lines']}: "
                         f"{errs[0].message[:300]}")
            out.write((json.dumps(doc, ensure_ascii=False) + "\n").encode())
            stats["modified"] += 1
            codes[code] += 1
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
    print(f"{family}: lines={stats['lines']} modified={stats['modified']} tail={stats['tail']}  "
          f"top codes={codes.most_common(6)}")
    if dry_run:
        tmp.unlink()
    else:
        os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="all")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    registry = build_registry()
    fams = list(FAMILIES) if args.family == "all" else [args.family]
    for f in fams:
        apply_family(f, registry, args.dry_run)
    if args.dry_run:
        print("dry-run: no swap")


if __name__ == "__main__":
    main()
