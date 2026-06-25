#!/usr/bin/env python3
"""Schema-validate (CONAS) + Blade Runner (tas_validator) the Hirose main staging records,
then merge the clean ones into TAS/data/connectors.ndjson (append-only).

Records that fail JSON Schema -> connectors.quarantine_schema_fail.ndjson (should be none).
Records with a Blade Runner Impossible finding -> connectors.quarantine_bladerunner.ndjson.
Everything else is appended to the live catalog.
"""
import glob, json, os, sys

PSMA = "/home/alf/PSMA"
TAS = f"{PSMA}/TAS"
STAGING = f"{TAS}/staging/hirose/connectors.main.ndjson"
LIVE = f"{TAS}/data/connectors.ndjson"
sys.path.insert(0, f"{TAS}/validator/build")

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
import tas_validator


def registry():
    res = []
    for repo in ("PEAS", "MAS", "CAS", "SAS", "RAS", "CONAS"):
        for f in glob.glob(f"{PSMA}/{repo}/schemas/**/*.json", recursive=True):
            try:
                doc = json.load(open(f))
            except (json.JSONDecodeError, OSError):
                continue
            if "$id" in doc:
                res.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(res)


def main():
    reg = registry()
    conn_schema = reg.get_or_retrieve("https://psma.com/conas/connector.json").value.contents
    validator = Draft202012Validator(conn_schema, registry=reg)

    recs = [json.loads(l) for l in open(STAGING)]
    schema_fail, blade_fail, clean = [], [], []
    blade_findings = []
    for rec in recs:
        inst = {"connector": rec["connector"]}  # Blade Runner wants the wrapped record
        errs = sorted(validator.iter_errors(rec["connector"]), key=lambda e: list(e.path))
        if errs:
            rec["quarantineReason"] = "CONAS schema fail: " + "; ".join(
                f"{list(e.path)}: {e.message}" for e in errs[:3])
            schema_fail.append(rec)
            continue
        verdict = tas_validator.validate(inst)
        imposs = [f for f in verdict.findings if f.severity == "Impossible"]
        if imposs:
            rec["quarantineReason"] = "Blade Runner Impossible: " + "; ".join(
                f"{f.code}:{f.message}" for f in imposs)
            blade_fail.append(rec)
            blade_findings += [(rec["connector"]["manufacturerInfo"]["reference"], f.code, f.message)
                               for f in imposs]
            continue
        clean.append(rec)

    print(json.dumps({"main": len(recs), "schema_fail": len(schema_fail),
                      "blade_fail": len(blade_fail), "clean": len(clean)}))
    if schema_fail:
        print("\nSCHEMA FAILURES:")
        for r in schema_fail[:10]:
            print("  ", r["connector"]["manufacturerInfo"]["reference"], "->", r["quarantineReason"][:200])
    if blade_findings:
        print("\nBLADE RUNNER IMPOSSIBLE:")
        for ref, code, msg in blade_findings[:20]:
            print(f"   {ref}: {code} {msg}")

    # write quarantine sidecars
    for nm, rs in [("connectors.quarantine_schema_fail", schema_fail),
                   ("connectors.quarantine_bladerunner", blade_fail)]:
        if rs:
            with open(f"{TAS}/data/{nm}.ndjson", "a") as fo:
                for r in rs:
                    fo.write(json.dumps(r, ensure_ascii=False) + "\n")

    if "--merge" in sys.argv and clean:
        before = sum(1 for _ in open(LIVE))
        with open(LIVE, "a") as fo:
            for r in clean:
                fo.write(json.dumps({"connector": r["connector"]}, ensure_ascii=False) + "\n")
        after = sum(1 for _ in open(LIVE))
        print(f"\nMERGED into connectors.ndjson: {before} -> {after} (+{after-before})")
    elif clean:
        print("\n(dry run — pass --merge to append clean records to connectors.ndjson)")


if __name__ == "__main__":
    main()
