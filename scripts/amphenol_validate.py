#!/usr/bin/env python3
"""Validate staged Amphenol CONAS connector records: JSON Schema + Blade Runner.

  python3 scripts/amphenol_validate.py <records.ndjson> [rejected.ndjson]

Prints counters only (never the payload). Records with a schema error or an IMPOSSIBLE
Blade Runner finding are written to the rejected file with the reason.
"""
import json, sys, collections
from pathlib import Path

SRC = Path(sys.argv[1])
REJ = Path(sys.argv[2]) if len(sys.argv) > 2 else SRC.with_name("rejected.ndjson")


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    PSMA = Path.home() / "PSMA"
    by = {}
    for repo in ("PEAS", "CONAS", "CAS", "SAS", "RAS", "MAS", "CTAS", "AAS", "CIAS"):
        d = PSMA / repo / "schemas"
        if not d.exists():
            continue
        for p in d.rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by[s["$id"]] = s
    res = [Resource(contents=s, specification=DRAFT202012) for s in by.values()]
    reg = Registry().with_resources([(r.contents["$id"], r) for r in res])
    schema = json.loads((PSMA / "CONAS" / "schemas" / "connector.json").read_text())
    return Draft202012Validator(schema, registry=reg)


def main():
    v = build_validator()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "validator" / "build-ninja"))
    import tas_validator

    ok = 0
    schema_bad = collections.Counter()
    findings = collections.Counter()
    impossible = collections.Counter()
    suspicious_parts = 0
    impossible_parts = 0
    rej = REJ.open("w")
    n = 0
    for line in SRC.open():
        line = line.strip()
        if not line:
            continue
        n += 1
        rec = json.loads(line)
        errs = sorted(v.iter_errors(rec["connector"]), key=lambda e: e.path)
        if errs:
            msg = f"{'/'.join(str(x) for x in errs[0].absolute_path)}: {errs[0].message}"
            schema_bad[msg[:110]] += 1
            rej.write(json.dumps({"record": rec, "reason": "schema: " + msg[:300]},
                                 ensure_ascii=False) + "\n")
            continue
        try:
            res = tas_validator.validate(rec)
        except Exception as e:
            schema_bad["blade-runner threw: " + str(e)[:80]] += 1
            rej.write(json.dumps({"record": rec, "reason": "bladeRunner exception: " + str(e)[:200]},
                                 ensure_ascii=False) + "\n")
            continue
        imp, sus = [], []
        for f in res.findings:
            sev = str(f.severity)
            desc = f"{f.code}: {f.message}"
            findings[f"{sev}: {desc[:70]}"] += 1
            if "IMPOSSIBLE" in sev.upper():
                imp.append(desc[:120])
            elif "SUSPICIOUS" in sev.upper():
                sus.append(desc[:120])
        if imp:
            impossible_parts += 1
            for i in imp:
                impossible[i[:80]] += 1
            rej.write(json.dumps({"record": rec, "reason": "IMPOSSIBLE: " + "; ".join(imp)[:300]},
                                 ensure_ascii=False) + "\n")
            continue
        if sus:
            suspicious_parts += 1
        ok += 1
    rej.close()
    print(json.dumps({
        "records": n, "schema_ok_and_no_impossible": ok,
        "schema_failures": sum(schema_bad.values()),
        "impossible_parts": impossible_parts, "suspicious_parts": suspicious_parts,
        "top_schema_errors": schema_bad.most_common(8),
        "top_findings": findings.most_common(8),
    }, indent=1)[:4000])


if __name__ == "__main__":
    main()
