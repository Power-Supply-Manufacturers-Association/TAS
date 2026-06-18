#!/usr/bin/env python3
"""Validate every record in TAS/data/*.ndjson against the consolidated PEAS-family
schemas. Per-library pass/fail counts plus the top error categories (values
normalized so they aggregate). Mirrors MAS/scripts/validate-db.py.

Needs the sibling repos (PEAS, SAS, CAS, RAS, MAS) checked out alongside TAS.
Run from the TAS repo root:  python3 scripts/validate_db.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
PSMA = REPO.parent
DATA = REPO / "data"

# library file -> (discriminator key path, repo, schema file)
LIBS = [
    ("mosfets.ndjson",    ["semiconductor", "mosfet"], "SAS", "mosfet.json"),
    ("diodes.ndjson",     ["semiconductor", "diode"],  "SAS", "diode.json"),
    ("igbts.ndjson",      ["semiconductor", "igbt"],   "SAS", "igbt.json"),
    ("capacitors.ndjson", ["capacitor"],               "CAS", "capacitor.json"),
    ("resistors.ndjson",  ["resistor"],                "RAS", "resistor.json"),
    ("magnetics.ndjson",  ["magnetic"],                "MAS", "magnetic.json"),
]


def _walk(d: Path):
    for p in d.rglob("*.json"):
        try:
            yield p, json.loads(p.read_text())
        except json.JSONDecodeError:
            continue


def build_registry() -> Registry:
    """PEAS/SAS/CAS/RAS/MAS schemas, inlining pure-$ref shim files (e.g. CAS/utils -> PEAS/utils)."""
    by_id: dict[str, dict] = {}
    by_path: dict[Path, dict] = {}
    for repo in ("PEAS", "SAS", "CAS", "RAS", "MAS"):
        rd = PSMA / repo / "schemas"
        if not rd.is_dir():
            continue
        for path, schema in _walk(rd):
            path = path.resolve()
            by_path[path] = schema
            if "$id" in schema:
                by_id[schema["$id"]] = schema
    META = {"$schema", "$id", "title", "description", "$comment"}
    for sid, schema in list(by_id.items()):
        if set(schema) - META != {"$ref"}:
            continue
        path = next((p for p, s in by_path.items() if s is schema), None)
        if path is None:
            continue
        target = (path.parent / schema["$ref"]).resolve()
        ts = by_path.get(target)
        if ts is None:
            continue
        inl = {k: v for k, v in ts.items() if k not in ("$id", "$schema")}
        inl["$id"] = sid
        inl["$schema"] = schema.get("$schema", "https://json-schema.org/draft/2020-12/schema")
        by_id[sid] = inl
    return Registry().with_resources(
        [(k, Resource(contents=v, specification=DRAFT202012)) for k, v in by_id.items()]
    )


def _norm(err) -> str:
    msg = re.sub(r"'[^']*'", "'X'", err.message)
    path = "/".join(str(x) for x in err.absolute_path)
    return f"{msg} @ {path}" if path else msg


def main() -> int:
    reg = build_registry()
    rc = 0
    grand_n = grand_valid = 0
    for fname, disc, repo, sch in LIBS:
        path = DATA / fname
        if not path.exists() or path.stat().st_size < 1000:
            print(f"SKIP {fname} (missing or LFS pointer)")
            continue
        validator = Draft202012Validator(
            json.loads((PSMA / repo / "schemas" / sch).read_text()), registry=reg
        )
        n = valid = 0
        errcat: Counter = Counter()
        for line in path.open():
            line = line.strip()
            if not line or line.startswith("version https"):
                continue
            n += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                errcat["<json parse error>"] += 1
                continue
            body, ok = rec, True
            for k in disc:
                if isinstance(body, dict) and list(body.keys()) == [k]:
                    body = body[k]
                else:
                    errcat[f"<wrong wrapper: expected key '{k}'>"] += 1
                    ok = False
                    break
            if not ok:
                continue
            errs = list(validator.iter_errors(body))
            if not errs:
                valid += 1
            else:
                errcat[_norm(errs[0])] += 1
        grand_n += n
        grand_valid += valid
        status = "OK  " if valid == n else "FAIL"
        if valid != n:
            rc = 1
        print(f"\n{status} {fname}: {valid}/{n} valid  ({n - valid} invalid)")
        for msg, c in errcat.most_common(6):
            print(f"   {c:7d}  {msg}")
    print(f"\n==== TOTAL: {grand_valid}/{grand_n} valid ({grand_n - grand_valid} invalid) ====")
    return rc


if __name__ == "__main__":
    sys.exit(main())
