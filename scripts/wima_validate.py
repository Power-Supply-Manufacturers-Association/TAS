#!/usr/bin/env python3
"""Validate staged WIMA capacitors: JSON Schema (CAS capacitor.json) + Blade Runner.

Usage: python3 scripts/wima_validate.py [limit]
"""
import json, sys
from pathlib import Path

REPO = Path("/home/alf/PSMA/TAS")
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "validator" / "build"))
from test_data import _build_full_registry
from jsonschema import Draft202012Validator
import tas_validator

REG = _build_full_registry()
CAP = Draft202012Validator(REG.get_or_retrieve("https://psma.com/cas/capacitor.json").value.contents,
                           registry=REG)
OUT = REPO / "staging" / "wima"
limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

path = OUT / "capacitors.main.ndjson"
ok = schema_fail = phys_fail = 0
rejected = []
for i, line in enumerate(path.open()):
    if limit and i >= limit:
        break
    rec = json.loads(line)
    comp = rec["capacitor"]
    mpn = comp["manufacturerInfo"]["reference"]
    errs = sorted(CAP.iter_errors(comp), key=lambda e: list(e.path))
    if errs:
        schema_fail += 1
        rejected.append((mpn, "schema: " + "; ".join(f"{list(e.path)}: {e.message}" for e in errs[:2]), line))
        continue
    v = tas_validator.validate(rec)
    bad = [f for f in v.findings if f.severity == "IMPOSSIBLE"]
    if not v.valid or bad:
        phys_fail += 1
        rejected.append((mpn, "physics: " + "; ".join(f"{f.code}:{f.message}" for f in bad[:2]), line))
        continue
    ok += 1
if rejected:
    with (OUT / "capacitors.rejected.ndjson").open("w") as f:
        for mpn, reason, line in rejected:
            r = json.loads(line); r["_rejectReason"] = reason
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"capacitors ok={ok} schema_fail={schema_fail} physics_fail={phys_fail}")
for mpn, reason, _ in rejected[:12]:
    print(f"   REJECT {mpn}: {reason[:170]}")
