#!/usr/bin/env python3
"""Apply staged connector contactSystem (standards-pinout expansion) + sParameters.

pinout_expand.json (keyed '<mfr>|<reference>') -> connector.contactSystem, when
absent, source='manual' + the standard citation.
sparams_by_part.json (keyed partNumber) -> connector.manufacturerInfo.datasheetInfo
.sParameters[] (PEAS sParameterReference, pointer-only), when absent,
source='manufacturerParametric' + the report URL.

Safe-write (refuse on active writer, byte-identical untouched lines, per-record
CONAS validation before atomic swap, line-count check).

Usage: apply_connector_pinout_sparams.py [--pinout FILE] [--sparams FILE] [--dry-run]
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
DATA = REPO / "data" / "connectors.ndjson"
TODAY = "2026-08-02"


def build_validator():
    from jsonschema import Draft202012Validator
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
    reg = Registry().with_resources(res)
    return Draft202012Validator(json.loads((WORKSPACE / "CONAS" / "schemas" / "connector.json").read_text()), registry=reg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pinout")
    ap.add_argument("--sparams")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lsof = subprocess.run(["lsof", "-F", "a", str(DATA)], capture_output=True, text=True)
    if [l for l in lsof.stdout.splitlines() if l.startswith("a") and ("w" in l or "u" in l)]:
        sys.exit("REFUSING: connectors.ndjson is open for writing by another process")

    pinout = json.load(open(args.pinout)) if args.pinout else {}
    sparams = json.load(open(args.sparams)) if args.sparams else {}
    print(f"pinout staged: {len(pinout)} | sparams staged: {len(sparams)}")
    validator = build_validator()
    stats = Counter()
    tmp = DATA.with_suffix(".ndjson.ps_tmp")
    size0 = DATA.stat().st_size
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            stats["lines"] += 1
            try:
                doc = json.loads(raw)
                c = doc["connector"]
                mi = c["manufacturerInfo"]
                di = mi.get("datasheetInfo", {})
            except Exception:
                out.write(raw)
                continue
            modified = False
            key = f"{mi.get('name')}|{mi.get('reference')}"
            if key in pinout and "contactSystem" not in c:
                e = pinout[key]
                c["contactSystem"] = e["contactSystem"]
                di.setdefault("provenance", []).append({
                    "source": "manual",
                    "sourceName": f"Standards pinout join: {e['cite']}",
                    "retrievedDate": TODAY,
                    "fields": ["contactSystem"],
                })
                stats["pinout"] += 1
                modified = True
            pn = di.get("part", {}).get("partNumber") or mi.get("reference")
            if pn and pn in sparams and "sParameters" not in di:
                di["sParameters"] = sparams[pn]
                di.setdefault("provenance", []).append({
                    "source": "manufacturerParametric",
                    "sourceName": "Vendor S-parameter reference (pointer-only, #470)",
                    "retrievedDate": TODAY,
                    "fields": ["sParameters"],
                })
                stats["sparams"] += 1
                modified = True
            if not modified:
                out.write(raw)
                continue
            errs = list(validator.iter_errors(c))
            if errs:
                tmp.unlink(missing_ok=True)
                sys.exit(f"ABORT: record fails CONAS at line {stats['lines']}: {errs[0].message[:300]}")
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
        sys.exit(f"ABORT: line count {n_out} != {stats['lines'] + stats['tail']}")
    print(f"lines={stats['lines']} pinout={stats['pinout']} sparams={stats['sparams']} "
          f"modified={stats['modified']} tail={stats['tail']}")
    if args.dry_run:
        tmp.unlink()
        print("dry-run: no swap")
    else:
        os.replace(tmp, DATA)
        print("swapped")


if __name__ == "__main__":
    main()
