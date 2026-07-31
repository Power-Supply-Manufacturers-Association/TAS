#!/usr/bin/env python3
"""Assemble the Amphenol staging set: RF + CS -> records / rejected / incomplete.

    python3 scripts/amphenol_finalize.py

Reads staging/amphenol/{rf,cs}.records.ndjson, validates EVERY record against
CONAS/schemas/connector.json (draft 2020-12, registry built from the sibling repos) and
against the TAS physics validator ("Blade Runner"), then writes:
    staging/amphenol/records.ndjson     schema-valid, no IMPOSSIBLE finding
    staging/amphenol/rejected.ndjson    schema failure or IMPOSSIBLE finding, with the reason
    staging/amphenol/incomplete.ndjson  parts the source could not describe completely
Nothing is written to data/connectors.ndjson -- a human merges serially.
"""
import collections, json, os, sys
from pathlib import Path

STAGE = Path("/home/alf/PSMA/TAS/staging/amphenol")


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
    reg = Registry().with_resources(
        [(s["$id"], Resource(contents=s, specification=DRAFT202012)) for s in by.values()])
    return Draft202012Validator(
        json.loads((PSMA / "CONAS" / "schemas" / "connector.json").read_text()), registry=reg)


def main():
    v = build_validator()
    sys.path.insert(0, "/home/alf/PSMA/TAS/validator/build-ninja")
    import tas_validator

    recs = STAGE / "records.ndjson"
    rej = STAGE / "rejected.ndjson"
    inc = STAGE / "incomplete.ndjson"
    stats = collections.Counter()
    fams = collections.Counter()
    schema_err = collections.Counter()
    findings = collections.Counter()
    seen = set()

    with recs.open("w") as fo, rej.open("w") as fr:
        for src, vendor in (("rf.records.ndjson", "Amphenol RF"),
                            ("cs.records.ndjson", "Amphenol CS")):
            p = STAGE / src
            if not p.exists():
                continue
            for line in p.open():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                mi = rec["connector"]["manufacturerInfo"]
                key = (mi["name"], mi["reference"])
                if key in seen:
                    stats["duplicate_skipped"] += 1
                    continue
                seen.add(key)
                stats[vendor + "_in"] += 1
                errs = sorted(v.iter_errors(rec["connector"]), key=lambda e: list(e.path))
                if errs:
                    msg = ("/".join(str(x) for x in errs[0].absolute_path) + ": "
                           + errs[0].message)
                    schema_err[msg[:110]] += 1
                    stats["schema_failed"] += 1
                    fr.write(json.dumps({"record": rec, "reason": "schema: " + msg[:300]},
                                        ensure_ascii=False) + "\n")
                    continue
                verdict = tas_validator.validate(rec)
                imp = []
                for f in verdict.findings:
                    sev = str(f.severity)
                    findings[f"{sev} {f.code}"] += 1
                    if "IMPOSSIBLE" in sev.upper():
                        imp.append(f"{f.code}: {f.message}")
                if imp:
                    stats["blade_impossible"] += 1
                    fr.write(json.dumps({"record": rec,
                                         "reason": "IMPOSSIBLE: " + "; ".join(imp)[:300]},
                                        ensure_ascii=False) + "\n")
                    continue
                stats["staged"] += 1
                fams[rec["connector"]["manufacturerInfo"]["datasheetInfo"]
                     ["familyDetails"]["family"]] += 1
                fo.write(json.dumps(rec, ensure_ascii=False) + "\n")

    with inc.open("w") as fi:
        for src in ("rf.incomplete.ndjson", "cs.incomplete.ndjson"):
            p = STAGE / src
            if not p.exists():
                continue
            for line in p.open():
                if line.strip():
                    fi.write(line if line.endswith("\n") else line + "\n")
                    stats["incomplete"] += 1

    print(json.dumps({"stats": dict(stats), "families": fams.most_common(),
                      "top_schema_errors": schema_err.most_common(6),
                      "blade_findings": findings.most_common(8)}, indent=1)[:4000])


if __name__ == "__main__":
    main()
