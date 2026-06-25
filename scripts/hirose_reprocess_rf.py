#!/usr/bin/env python3
"""Re-process the Hirose RF connectors that were quarantined only for lacking
ratedCurrentPerContact, now that CONAS exempts the `rf` family from that requirement.

For every rf-family record in staging/hirose/connectors.incomplete.ndjson:
  - strip the staging-only quarantineReason
  - re-validate against the patched CONAS connector.json + Blade Runner
  - merge the clean ones into TAS/data/connectors.ndjson (append)
Then rewrite TAS/data/connectors.quarantine_incomplete.ndjson with those rf records removed
(atomic: temp file + os.replace). Non-rf incompletes stay quarantined.
"""
import glob, json, os, sys, tempfile

PSMA = "/home/alf/PSMA"
TAS = f"{PSMA}/TAS"
INCOMPLETE_STAGING = f"{TAS}/staging/hirose/connectors.incomplete.ndjson"
LIVE = f"{TAS}/data/connectors.ndjson"
QUAR = f"{TAS}/data/connectors.quarantine_incomplete.ndjson"
sys.path.insert(0, f"{TAS}/validator/build")

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
import tas_validator


def is_hirose_rf(rec):
    mi = rec["connector"]["manufacturerInfo"]
    if mi.get("name") != "Hirose Electric":
        return False
    fd = mi["datasheetInfo"].get("familyDetails")
    return bool(fd) and fd.get("family") == "rf"


def registry():
    res = []
    for repo in ("PEAS", "MAS", "CAS", "SAS", "RAS", "CONAS"):
        for f in glob.glob(f"{PSMA}/{repo}/schemas/**/*.json", recursive=True):
            try:
                d = json.load(open(f))
            except (json.JSONDecodeError, OSError):
                continue
            if "$id" in d:
                res.append((d["$id"], Resource.from_contents(d)))
    return Registry().with_resources(res)


def main():
    reg = registry()
    schema = reg.get_or_retrieve("https://psma.com/conas/connector.json").value.contents
    validator = Draft202012Validator(schema, registry=reg)

    clean, still_bad = [], []
    refs_recovered = set()
    for line in open(INCOMPLETE_STAGING):
        rec = json.loads(line)
        if not is_hirose_rf(rec):
            continue
        rec = {"connector": rec["connector"]}  # drop quarantineReason
        errs = list(validator.iter_errors(rec["connector"]))
        verdict = tas_validator.validate(rec)
        imposs = [f for f in verdict.findings if f.severity == "Impossible"]
        if errs or imposs:
            still_bad.append((rec["connector"]["manufacturerInfo"]["reference"],
                              [e.message for e in errs[:2]] + [f"BR:{f.code}" for f in imposs]))
            continue
        clean.append(rec)
        refs_recovered.add(rec["connector"]["manufacturerInfo"]["reference"])

    print(json.dumps({"rf_candidates": len(clean) + len(still_bad),
                      "clean": len(clean), "still_bad": len(still_bad)}))
    if still_bad:
        print("STILL BAD (NOT merged):")
        for ref, why in still_bad[:20]:
            print("  ", ref, why)

    if "--merge" not in sys.argv:
        print("\n(dry run — pass --merge to apply)")
        return

    # 1. append clean records to the live catalog
    before = sum(1 for _ in open(LIVE))
    with open(LIVE, "a") as fo:
        for r in clean:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    after = sum(1 for _ in open(LIVE))
    print(f"\nMERGED into connectors.ndjson: {before} -> {after} (+{after-before})")

    # 2. rewrite quarantine_incomplete WITHOUT the recovered Hirose rf records (atomic)
    removed = kept = 0
    fd, tmp = tempfile.mkstemp(dir=f"{TAS}/data", suffix=".tmp")
    with os.fdopen(fd, "w") as fo:
        for line in open(QUAR):
            rec = json.loads(line)
            ref = rec.get("connector", {}).get("manufacturerInfo", {}).get("reference")
            if is_hirose_rf(rec) and ref in refs_recovered:
                removed += 1
                continue
            fo.write(line if line.endswith("\n") else line + "\n")
            kept += 1
    os.replace(tmp, QUAR)
    print(f"quarantine_incomplete: removed {removed} recovered rf, kept {kept}")


if __name__ == "__main__":
    main()
