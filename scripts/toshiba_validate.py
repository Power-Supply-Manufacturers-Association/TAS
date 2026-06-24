#!/usr/bin/env python3
"""Validate staged Toshiba main records: JSON Schema (SAS per-type) + C++ physics.

Usage: python3 scripts/toshiba_validate.py
Reads staging/toshiba/{mosfets,diodes,igbts}.main.ndjson, validates each record,
prints a per-file pass/fail report. Records that fail are written to
staging/toshiba/<type>.rejected.ndjson with the reason.
"""
import json, sys
from pathlib import Path

REPO = Path("/home/alf/PSMA/TAS")
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "validator" / "build"))

from test_data import _build_full_registry  # reuse the harness registry
from jsonschema import Draft202012Validator
import tas_validator

REG = _build_full_registry()
SCHEMA_ID = {"mosfet": "https://psma.com/sas/mosfet.json",
             "diode": "https://psma.com/sas/diode.json",
             "igbt": "https://psma.com/sas/igbt.json"}
VALIDATORS = {d: Draft202012Validator(REG.get_or_retrieve(sid).value.contents, registry=REG)
              for d, sid in SCHEMA_ID.items()}

OUT = REPO / "staging" / "toshiba"

def disc_of(rec):
    return next(iter(rec["semiconductor"]))

for typ in ("mosfets", "diodes", "igbts"):
    path = OUT / f"{typ}.main.ndjson"
    if not path.exists():
        continue
    ok = schema_fail = phys_fail = 0
    rejected = []
    for ln, line in enumerate(path.open(), 1):
        rec = json.loads(line)
        d = disc_of(rec)
        comp = rec["semiconductor"][d]
        mpn = comp["manufacturerInfo"]["reference"]
        errs = sorted(VALIDATORS[d].iter_errors(comp), key=lambda e: e.path)
        if errs:
            schema_fail += 1
            msg = "; ".join(f"{list(e.path)}: {e.message}" for e in errs[:3])
            rejected.append((mpn, "schema: " + msg, line))
            continue
        v = tas_validator.validate(rec)
        bad = [f for f in v.findings if f.severity == "IMPOSSIBLE"]
        if not v.valid or bad:
            phys_fail += 1
            msg = "; ".join(f"{f.code}:{f.message}" for f in bad[:3])
            rejected.append((mpn, "physics: " + msg, line))
            continue
        ok += 1
    if rejected:
        with (OUT / f"{typ}.rejected.ndjson").open("w") as f:
            for mpn, reason, line in rejected:
                r = json.loads(line)
                r["_rejectReason"] = reason
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{typ:8s} ok={ok:4d} schema_fail={schema_fail:4d} physics_fail={phys_fail:4d}")
    for mpn, reason, _ in rejected[:8]:
        print(f"    REJECT {mpn}: {reason[:160]}")
