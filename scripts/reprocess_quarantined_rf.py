#!/usr/bin/env python3
"""Recover rf-family connectors from connectors.quarantine_incomplete.ndjson now that CONAS
exempts the `rf` family from ratedCurrentPerContact.

Single streaming pass over the quarantine file: every rf-family record (familyDetails.family
== 'rf', i.e. it already carries characteristicImpedance) gets its quarantineReason stripped and
is re-validated against the patched CONAS connector.json + Blade Runner. Clean ones are appended
to connectors.ndjson; everything else is written back to the quarantine file (atomic temp+replace).
A clean record that somehow still fails stays quarantined.

NB run when the librarian loop is idle — the atomic rewrite would drop any concurrent appends.
"""
import glob, json, os, sys, tempfile

PSMA = "/home/alf/PSMA"
TAS = f"{PSMA}/TAS"
LIVE = f"{TAS}/data/connectors.ndjson"
QUAR = f"{TAS}/data/connectors.quarantine_incomplete.ndjson"
sys.path.insert(0, f"{TAS}/validator/build")

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
import tas_validator


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


def is_rf(rec):
    fd = rec.get("connector", {}).get("manufacturerInfo", {}).get("datasheetInfo", {}).get("familyDetails")
    return bool(fd) and fd.get("family") == "rf"


def main():
    merge = "--merge" in sys.argv
    reg = registry()
    validator = Draft202012Validator(
        reg.get_or_retrieve("https://psma.com/conas/connector.json").value.contents, registry=reg)

    fd, tmp = tempfile.mkstemp(dir=f"{TAS}/data", suffix=".tmp")
    clean, still_bad_refs, kept, by_vendor = [], [], 0, {}
    with os.fdopen(fd, "w") as keep_fo:
        for line in open(QUAR):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                keep_fo.write(line if line.endswith("\n") else line + "\n"); kept += 1; continue
            if not is_rf(rec):
                keep_fo.write(line if line.endswith("\n") else line + "\n"); kept += 1; continue
            wrapped = {"connector": rec["connector"]}  # drop quarantineReason
            ok = not list(validator.iter_errors(wrapped["connector"]))
            ok = ok and not any(f.severity == "Impossible" for f in tas_validator.validate(wrapped).findings)
            if ok:
                clean.append(wrapped)
                v = rec["connector"]["manufacturerInfo"].get("name", "?")
                by_vendor[v] = by_vendor.get(v, 0) + 1
            else:
                still_bad_refs.append(rec["connector"]["manufacturerInfo"].get("reference"))
                keep_fo.write(line if line.endswith("\n") else line + "\n"); kept += 1

    print(json.dumps({"recoverable_clean": len(clean), "rf_still_bad": len(still_bad_refs),
                      "kept_in_quarantine": kept, "by_vendor": by_vendor}))

    if not merge:
        os.unlink(tmp)
        print("\n(dry run — pass --merge to apply)")
        return

    before = sum(1 for _ in open(LIVE))
    with open(LIVE, "a") as fo:
        for r in clean:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    after = sum(1 for _ in open(LIVE))
    os.replace(tmp, QUAR)
    print(f"\nMERGED into connectors.ndjson: {before} -> {after} (+{after-before})")
    print(f"quarantine_incomplete now: {kept} lines")


if __name__ == "__main__":
    main()
