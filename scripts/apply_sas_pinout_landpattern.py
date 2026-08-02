#!/usr/bin/env python3
"""Apply the staged #473 discrete-semiconductor landPattern + pinout.

Per record (semiconductor / {diode,igbt,bjt,mosfet}): normalize the package,
set mechanical.landPattern (JEDEC/IPC library) and mechanical.pinout (the
package-standard entry, present only for diode/mosfet/igbt; BJT and dual-diode
and non-standard-lead entries are intentionally absent from the staged pinout).
Only when the field is absent. source: landPattern derived-from-package; pinout
'manual' (package-standard convention).

MOSFETS ARE SKIPPED BY DEFAULT: another session holds uncommitted WIP in
mosfets.ndjson, and committing it would sweep up their work. Pass
--family mosfet only once that file is clean.

Safe-write per file (refuse on active writer, byte-identical untouched lines,
per-record schema validation before the atomic swap).

Usage: apply_sas_pinout_landpattern.py [--family diode|igbt|bjt|mosfet|clean] [--dry-run]
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
STAGE = Path("/tmp/claude-1000/-home-alf/e0566217-bb16-4d03-9e3f-f35b93581bf0/scratchpad/sas_fill")

sys.path.insert(0, str(STAGE))
from normalizer import normalize_package  # noqa: E402

LP = json.load(open(STAGE / "landpattern_by_package.json"))
PO = json.load(open(STAGE / "pinout_by_package.json"))

# family -> (file, sub-discriminator, schema $id)
FAMILIES = {
    "diode": ("diodes.ndjson", "diode", "https://psma.com/sas/diode.json"),
    "igbt": ("igbts.ndjson", "igbt", "https://psma.com/sas/igbt.json"),
    "bjt": ("bjts.ndjson", "bjt", "https://psma.com/sas/bjt.json"),
    "mosfet": ("mosfets.ndjson", "mosfet", "https://psma.com/sas/mosfet.json"),
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


def canonical_of(case, family):
    res = normalize_package(case, family)
    if not res:
        return None
    return res[0] if isinstance(res, (tuple, list)) else res


def apply_family(family, registry, dry_run):
    from jsonschema import Draft202012Validator
    fname, sub, schema_id = FAMILIES[family]
    path = REPO / "data" / fname
    validator = Draft202012Validator(registry.get_or_retrieve(schema_id).value.contents, registry=registry)
    lsof = subprocess.run(["lsof", "-F", "a", str(path)], capture_output=True, text=True)
    if [l for l in lsof.stdout.splitlines() if l.startswith("a") and ("w" in l or "u" in l)]:
        sys.exit(f"REFUSING: {fname} is open for writing by another process")

    stats = Counter()
    tmp = path.with_suffix(".ndjson.sas_tmp")
    size0 = path.stat().st_size
    with open(path, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            stats["lines"] += 1
            try:
                doc = json.loads(raw)
                comp = doc["semiconductor"][sub]
                di = comp["manufacturerInfo"]["datasheetInfo"]
            except Exception:
                out.write(raw)
                continue
            mech = di.setdefault("mechanical", {}) if isinstance(di.get("mechanical", {}), dict) else None
            part = di.get("part", {}) or {}
            if mech is None:
                out.write(raw)
                continue
            canon = canonical_of(part.get("case"), family)
            if not canon:
                out.write(raw)
                continue
            modified = False
            if canon in LP and "landPattern" not in mech:
                mech["landPattern"] = LP[canon]["landPattern"]
                di.setdefault("provenance", []).append({
                    "source": "derived",
                    "sourceName": f"OpenConverters SAS landPattern 2026-08 (#473, {canon})",
                    "retrievedDate": TODAY,
                    "fields": ["mechanical.landPattern"],
                    "derivation": f"recommended land pattern from the package '{part.get('case')}' "
                                  f"-> {canon}; {LP[canon].get('source','')[:100]}",
                })
                stats["landpattern"] += 1
                modified = True
            pk = f"{canon}|{family}"
            if pk in PO and "pinout" not in mech:
                e = PO[pk]
                mech["pinout"] = e["pinout"]
                di.setdefault("provenance", []).append({
                    "source": "manual",
                    "sourceName": f"OpenConverters SAS pinout 2026-08 (#473, {canon} {family}, "
                                  f"{e.get('confidence','')})",
                    "retrievedDate": TODAY,
                    "fields": ["mechanical.pinout"],
                    "derivation": (f"{e.get('confidence','')} package pinout for {canon}; "
                                   f"{e.get('source','')[:120]}"
                                   + (f"; tab={e['tab']}" if e.get("tab") else "")),
                })
                stats["pinout"] += 1
                modified = True
            if not modified:
                out.write(raw)
                continue
            errs = list(validator.iter_errors(comp))
            if errs:
                tmp.unlink(missing_ok=True)
                sys.exit(f"ABORT {family}: schema fail at line {stats['lines']}: {errs[0].message[:300]}")
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
    print(f"{family}: lines={stats['lines']} landPattern={stats['landpattern']} "
          f"pinout={stats['pinout']} modified={stats['modified']} tail={stats['tail']}")
    if dry_run:
        tmp.unlink()
    else:
        os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="clean")  # clean = diode+igbt+bjt (mosfet skipped)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    registry = build_registry()
    fams = ["diode", "igbt", "bjt"] if args.family == "clean" else [args.family]
    for f in fams:
        apply_family(f, registry, args.dry_run)
    if args.dry_run:
        print("dry-run: no swap")


if __name__ == "__main__":
    main()
