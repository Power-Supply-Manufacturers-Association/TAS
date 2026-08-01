#!/usr/bin/env python3
"""Validate a staged CONAS connector NDJSON: JSON Schema + Blade Runner (tas_validator).

  python3 scripts/conas_stage_validate.py staging/<vendor>/records.ndjson

Schema: /home/alf/PSMA/CONAS/schemas/connector.json, Draft 2020-12, registry built from
the sibling repos in /home/alf/PSMA (absolute $id -> local file).
Blade Runner: validator/build-ninja/tas_validator.validate(record).

Records with a schema error or an IMPOSSIBLE finding are moved to
staging/<vendor>/rejected.ndjson (with a rejectionReason); the good ones are rewritten
back to the input path. Prints counts only.
"""
import json
import sys
from pathlib import Path

PSMA = Path("/home/alf/PSMA")
TAS = PSMA / "TAS"


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    by = {}
    for repo in ("PEAS", "CONAS", "MAS", "CAS", "SAS", "RAS"):
        d = PSMA / repo / "schemas"
        if not d.is_dir():
            continue
        for p in d.rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(s, dict) and s.get("$id"):
                by.setdefault(s["$id"], s)
    reg = Registry().with_resources(
        [(i, Resource(contents=s, specification=DRAFT202012)) for i, s in by.items()])
    schema = json.loads((PSMA / "CONAS" / "schemas" / "connector.json").read_text())
    return Draft202012Validator(schema, registry=reg)


def main(path):
    src = Path(path)
    rejected = src.with_name("rejected.ndjson")
    v = build_validator()
    sys.path.insert(0, str(TAS / "validator" / "build-ninja"))
    import tas_validator

    good, bad = [], []
    n = n_schema = n_impossible = n_suspicious = 0
    findings = {}
    with src.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            n += 1
            rec = json.loads(line)
            payload = {k: val for k, val in rec.items() if k != "quarantineReason"}
            errs = sorted(v.iter_errors(payload["connector"]), key=lambda e: e.path)
            if errs:
                n_schema += 1
                e = errs[0]
                rec["rejectionReason"] = f"schema: {'/'.join(map(str, e.path))}: {e.message[:180]}"
                bad.append(rec)
                continue
            verdict = tas_validator.validate(payload)
            imp = [f for f in verdict.findings if str(f.severity).endswith("IMPOSSIBLE")]
            sus = [f for f in verdict.findings if str(f.severity).endswith("SUSPICIOUS")]
            for f in verdict.findings:
                findings[f"{f.severity}:{f.code}"] = findings.get(f"{f.severity}:{f.code}", 0) + 1
            if imp:
                n_impossible += 1
                rec["rejectionReason"] = ("blade-runner IMPOSSIBLE: "
                                          + "; ".join(f"{f.code}: {f.message}" for f in imp)[:300])
                bad.append(rec)
                continue
            if sus:
                n_suspicious += 1
            good.append(line)

    with src.open("w", encoding="utf-8") as fh:
        for line in good:
            fh.write(line + "\n")
    mode = "a" if rejected.exists() and rejected.stat().st_size and "--append" in sys.argv else "w"
    with rejected.open(mode, encoding="utf-8") as fh:
        for rec in bad:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps({"file": str(src), "input": n, "kept": len(good),
                      "schema_invalid": n_schema, "blade_impossible": n_impossible,
                      "blade_suspicious_kept": n_suspicious,
                      "findings": dict(sorted(findings.items(), key=lambda x: -x[1])[:10])}))


if __name__ == "__main__":
    main(sys.argv[1])
