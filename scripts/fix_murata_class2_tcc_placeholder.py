#!/usr/bin/env python3
"""The other half of the placeholder zero: 7,146 class-2 rows (ABT #517).

    python3 scripts/fix_murata_class2_tcc_placeholder.py [--dry-run]

WHAT WENT WRONG. The same line of the same importer
(OpenConverters/Heaviside/scripts/convert_murata.py) that put a false zero on 401 U2J
rows put it on every class-2 row too:

    if temp_characteristic:
        thermal["tcc"] = {"nominal": 0}   # Placeholder

scripts/fix_murata_class1_tcc_placeholder.py repaired the class-1 population the ticket
queried and REPORTED these rows rather than touching them, because the correct class-2
value is a percent band per EIA code and several of Murata's codes have no EIA band to
read. That was a reason to be careful, not a reason to leave the zero standing: a bare
tcc {nominal: 0} on an X7R asserts the one property a class-2 dielectric most conspicuously
does not have. X7R is +/-15 % over -55..+125 C, X7T is +22/-33 %, X8L is +15/-40 %. Zero
is not the midpoint of those, and on the symmetric codes it is still a claim of stability
made by an importer that never read the column.

THE CRITERION IS THE SHAPE OF THE VALUE, NOT A LIST OF PART NUMBERS. This catalogue
already stores three different things in class-2 `tcc`:

    {"nominal": 0}                              7146 Murata  <- the placeholder
    {"nominal": 0, "minimum": -1, "maximum": 1}    39 Murata  <- a real measured band
    {"nominal": 15}                              3149 TDK/Samsung/KEMET/YAGEO
                                                            <- the +/-% magnitude

Only the first is this defect. A row carrying minimum/maximum has a real band behind it
(EPCOS/KEMET film rows sourced from a manufacturer database land here too) and is left
alone; so is every non-Murata row, whose convention is different and whose value is not
zero. The test is therefore "Murata, class 2, tcc is a dict whose only key is nominal and
that nominal is 0" -- a property of the record, so it stays true as the catalogue grows.

WHAT IS WRITTEN. tcc null. The zero is withdrawn and nothing is invented in its place,
which is exactly what the class-1 script did for the 10 ZLM rows whose Murata-proprietary
code decodes to no coefficient. Writing a decoded EIA band instead was considered and
rejected: the fixed importer emits NO tcc for a class-2 characteristic, so a decoded band
would disagree with its own producer and be stripped on the next import, and it would add
a fourth convention to a field that already carries three. `dielectricCode` is likewise
not filled -- distinguishing a real EIA code from Murata's own (X8G, X8L, X8M, X8N are
publicstandard MURATA, not EIA) needs the `publicstandard` column, and inventing the
distinction here is the placeholder mistake one column over.

NOT TOUCHED, REPORTED INSTEAD (a different field, so a different defect):
  * 2,162 rows whose characteristic is X8G are stored technology 'ceramic-class-2' though
    Murata files X8G as temperature_compensating, i.e. Class 1. The importer still decides
    the class with `technology = Class II if tcc.startswith("X")`. Filed separately.

Both gates run per rewritten record: JSON Schema (CAS/capacitor.json) and Blade Runner
(tas_validator). A failure of either aborts the run and leaves the file untouched.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blade_gate import BladeGate  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PSMA = REPO.parent
DATA = REPO / "data" / "capacitors.ndjson"
PRODUCT_LIST = REPO / "staging" / "murata" / "mlcc.csv"
AUDIT = REPO / "staging" / "abt517_murata_class2_tcc_audit.json"

TICKET = "ABT #517"
TODAY = "2026-08-02"


def product_list():
    """{part number: (characteristic, standards body)} from Murata's own MLCC list."""
    if not PRODUCT_LIST.exists():
        raise SystemExit(f"Murata product list missing: {PRODUCT_LIST}")
    out = {}
    with PRODUCT_LIST.open(encoding="utf-8-sig", errors="replace") as fh:
        for row in csv.DictReader(fh):
            part = (row.get("part_number") or "").strip()
            if part:
                out[part] = ((row.get("tcc") or "").strip(),
                             (row.get("publicstandard") or "").strip())
    return out


def resolve(part, catalogue):
    """Our rows often carry a trailing packaging letter the product list omits
    (GRM1555C1H222JA01D -> GRM1555C1H222JA01). Same local resolution the class-1
    script and scripts/murata_bias_harvest.py use; a strip that does not land on a
    real catalogue entry is not a match."""
    if part in catalogue:
        return part
    for k in range(1, 4):
        if len(part) - k >= 12 and part[:-k] in catalogue:
            return part[:-k]
    return None


def provenance_for(characteristic, standard):
    return {
        "source": "manufacturerParametric",
        "sourceName": (
            "Murata MLCC product list (staging/murata/mlcc.csv, the same published "
            f"product list scripts/convert_murata.py imported from): this part's "
            f"temperature characteristic is '{characteristic}' (publicstandard "
            f"{standard}), a class-2 code whose specification is a percent "
            "capacitance-change band and not a temperature coefficient, so no zero is "
            "decodable from it. The stored tcc {nominal: 0} was that importer's "
            "placeholder — it used the characteristic column as a yes/no flag and "
            f"discarded the value; the false zero is withdrawn rather than replaced "
            f"[{TICKET}]"),
        "sourceUrl": None,
        "retrievedDate": TODAY,
        "fields": ["thermal.tcc"],
    }


def load_validator():
    resources = []
    for repo in ("PEAS", "CIAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS"):
        root = PSMA / repo / "schemas"
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            doc = json.loads(path.read_text())
            if "$id" in doc:
                resources.append((doc["$id"], Resource.from_contents(doc)))
    registry = Registry().with_resources(resources)
    schema = json.loads((PSMA / "CAS" / "schemas" / "capacitor.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def errors_of(validator, capacitor):
    return sorted(f"{list(e.absolute_path)}: {e.message}"
                  for e in validator.iter_errors(capacitor))


def placeholder_zero(sheet):
    """A class-2 row whose tcc is the importer's bare zero and nothing else.

    A dict carrying minimum/maximum is a real band from a real source and is not this
    defect, so the key set is part of the test, not just the value."""
    if (sheet.get("part") or {}).get("technology") != "ceramic-class-2":
        return False
    tcc = (sheet.get("thermal") or {}).get("tcc")
    return (isinstance(tcc, dict) and set(tcc) == {"nominal"}
            and tcc.get("nominal") == 0)


def stamp(path):
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


def unmoved(path, before):
    """capacitors.ndjson is appended to concurrently. Untouched lines are copied
    through byte-for-byte, but the final os.replace would still drop anything appended
    while the temp file was being built. Refuse rather than lose an append."""
    if stamp(path) != before:
        print(f"ABORT {path.name}: appended to while this run was in flight; re-run")
        return False
    return True


def repair(validator, gate, catalogue, audit):
    before_stamp = stamp(DATA)
    tmp = DATA.with_suffix(".ndjson.tmp")
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            if b'"Murata"' not in raw or b'"tcc"' not in raw:
                out.write(raw)
                continue
            record = json.loads(raw)
            capacitor = record.get("capacitor")
            if not isinstance(capacitor, dict):
                out.write(raw)
                continue
            info = capacitor.get("manufacturerInfo") or {}
            sheet = info.get("datasheetInfo") or {}
            if info.get("name") != "Murata" or not placeholder_zero(sheet):
                out.write(raw)
                continue

            ref = str(info.get("reference")
                      or (sheet.get("part") or {}).get("partNumber") or "").strip()
            characteristic, standard = catalogue.get(resolve(ref, catalogue), ("", ""))
            if not characteristic:
                # Not in Murata's product list, so there is nothing to name the code
                # this zero was invented from. Left exactly as found and counted.
                audit["notInProductList"].append(ref)
                out.write(raw)
                continue

            thermal = sheet["thermal"]
            was = dict(thermal["tcc"])
            thermal["tcc"] = None
            sheet["provenance"] = (sheet.get("provenance") or []) + [
                provenance_for(characteristic, standard)]

            errs = errors_of(validator, capacitor)
            if errs:
                print(f"ABORT {ref}: schema {errs[0][:160]}")
                out.close()
                tmp.unlink(missing_ok=True)
                return 1
            ok, why = gate.check(capacitor)
            if not ok:
                print(f"ABORT {ref}: blade runner {why}")
                out.close()
                tmp.unlink(missing_ok=True)
                return 1

            out.write(json.dumps(record, ensure_ascii=False).encode() + b"\n")
            audit["repaired"].append({"reference": ref, "characteristic": characteristic,
                                      "standard": standard, "was": was, "now": None})
            audit["byCharacteristic"][characteristic] = \
                audit["byCharacteristic"].get(characteristic, 0) + 1
        out.flush()
        os.fsync(out.fileno())

    if not unmoved(DATA, before_stamp):
        tmp.unlink(missing_ok=True)
        return 1
    audit["_tmp"] = str(tmp)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    validator = load_validator()
    gate = BladeGate("capacitor")
    catalogue = product_list()
    audit = {"ticket": TICKET, "date": TODAY, "repaired": [], "byCharacteristic": {},
             "notInProductList": []}
    if repair(validator, gate, catalogue, audit):
        return 1

    tmp = Path(audit.pop("_tmp"))
    print(f"class-2 rows whose placeholder zero was withdrawn: {len(audit['repaired'])}")
    for code, n in sorted(audit["byCharacteristic"].items(), key=lambda kv: -kv[1]):
        print(f"     {n:5}  {code}")
    if audit["notInProductList"]:
        print(f"LEFT ALONE (not in Murata's product list): "
              f"{len(audit['notInProductList'])} {audit['notInProductList'][:4]}")
    print(gate.summary())
    for r in audit["repaired"][:3]:
        print(f"       {r['reference']:20} {r['characteristic']}  "
              f"{r['was']} -> {r['now']}")
    if args.dry_run:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
        return 0

    os.replace(tmp, DATA)
    AUDIT.write_text(json.dumps(audit, indent=1))
    print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
