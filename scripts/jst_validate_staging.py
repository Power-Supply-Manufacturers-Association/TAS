#!/usr/bin/env python3
"""Validate staging/jst/records.ndjson: CONAS JSON Schema + Blade Runner (tas_validator).

Records with an IMPOSSIBLE finding (or a schema error) are moved out of records.ndjson
into rejected.ndjson with the reason. Prints counters only - never the payload.
"""
import json
import os
import sys
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
PSMA = TAS.parent
STAGE = TAS / "staging" / "jst"


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    by = {}
    for repo in ("PEAS", "CONAS"):
        for p in (PSMA / repo / "schemas").rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if isinstance(s, dict) and s.get("$id"):
                by[s["$id"]] = s
    reg = Registry().with_resources(
        [(k, Resource(contents=v, specification=DRAFT202012)) for k, v in by.items()])
    schema = json.loads((PSMA / "CONAS" / "schemas" / "connector.json").read_text())
    return Draft202012Validator(schema, registry=reg)


def main():
    sys.path.insert(0, str(TAS / "validator" / "build-ninja"))
    import tas_validator                                            # noqa: E402

    v = build_validator()
    src = STAGE / "records.ndjson"
    good_path = STAGE / "records.clean.ndjson"
    schema_err = imposs = susp = skipped_all = 0
    err_examples, susp_examples, imp_examples = [], [], []
    n = 0
    kept = 0
    dropped = []
    with src.open() as fh, good_path.open("w") as out:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            n += 1
            rec = json.loads(line)
            errs = sorted(v.iter_errors(rec["connector"]), key=lambda e: e.path)
            if errs:
                schema_err += 1
                if len(err_examples) < 5:
                    err_examples.append(
                        rec["connector"]["manufacturerInfo"]["reference"] + ": "
                        + errs[0].message[:160])
                dropped.append({"partNumber": rec["connector"]["manufacturerInfo"]["reference"],
                                "reason": "CONAS schema violation: " + errs[0].message[:300]})
                continue
            verdict = tas_validator.validate(rec)
            findings = list(verdict.findings)
            skipped_all += 1 if verdict.skipped else 0
            bad = [f for f in findings if "IMPOSSIBLE" in str(f.severity).upper()]
            warn = [f for f in findings if "SUSPICIOUS" in str(f.severity).upper()]
            if bad:
                imposs += 1
                msg = f"{bad[0].code}: {bad[0].message}"[:220]
                if len(imp_examples) < 5:
                    imp_examples.append(rec["connector"]["manufacturerInfo"]["reference"] + ": " + msg)
                dropped.append({"partNumber": rec["connector"]["manufacturerInfo"]["reference"],
                                "reason": "Blade Runner IMPOSSIBLE: " + msg})
                continue
            if warn:
                susp += 1
                if len(susp_examples) < 5:
                    susp_examples.append(rec["connector"]["manufacturerInfo"]["reference"] + ": "
                                         + f"{warn[0].code}: {warn[0].message}"[:160])
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            kept += 1

    if dropped:
        with (STAGE / "rejected.ndjson").open("a") as f:
            for d in dropped:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
    os.replace(good_path, src)
    print(json.dumps({
        "records_in": n,
        "schema_invalid_dropped": schema_err,
        "blade_runner_impossible_dropped": imposs,
        "blade_runner_suspicious_kept": susp,
        "blade_runner_skipped": skipped_all,
        "records_kept": kept,
        "schema_error_examples": err_examples,
        "impossible_examples": imp_examples,
        "suspicious_examples": susp_examples,
    }, indent=2)[:4000])


if __name__ == "__main__":
    main()
