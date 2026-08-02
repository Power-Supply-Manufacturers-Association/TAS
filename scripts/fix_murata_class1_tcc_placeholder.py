#!/usr/bin/env python3
"""A U2J filed with C0G's temperature coefficient (ABT #517).

    python3 scripts/fix_murata_class1_tcc_placeholder.py [--dry-run]

WHAT WENT WRONG. The Murata product-list import
(OpenConverters/Heaviside/scripts/convert_murata.py) built thermal like this:

    if temp_characteristic:
        thermal["tcc"] = {"nominal": 0}   # Placeholder

The CSV row it was reading NAMES the characteristic in its own `tcc` column -- 'C0G',
'U2J', 'X7R', 'ZLM' -- and the importer used that column only as a boolean: if the part
has a temperature characteristic at all, assert a zero temperature coefficient. For the
11,455 C0G rows the placeholder happens to be right (C0G is 0 +/-30 ppm/K), which is why
it survived three years. For the 401 U2J rows it is a POSITIVE FALSE ASSERTION: it says
the part has the one property a U2J does not have.

WHY IT MATTERS. Qarlos put GRM2167U1H153JA01 up as a substitute for a KEMET C0G and the
ranker could not tell them apart: same technology 'ceramic-class-1', same tcc
{nominal: 0}, no dielectricCode on either -- the record's only tempco-bearing field
asserted it WAS a C0G. Over the original's own -55..+125 C band a U2J moves -7.5 % hot
and +6.0 % cold against +/-0.3 % for the C0G it was offered against, and stability is
the entire reason a C0G gets specified.

THE SOURCE IS MURATA'S OWN COLUMN, the one the importer threw away. staging/murata/
mlcc.csv is Murata's published MLCC product list; for each part it states `tcc` and
`publicstandard`, and it settles both cases here without a single inference from us:

    GRM2167U1H153JA01   tcc U2J   publicstandard EIA      temperature_compensating y
    GCM2199E2A152MA05   tcc ZLM   publicstandard MURATA   temperature_compensating y

  * U2J is an EIA RS-198 code and decodes arithmetically: U = 7.5, 2 = x(-100),
    J = +/-120 ppm/K, so -750 ppm/K over [-870, -630]. Nothing is estimated -- the code
    IS the specification. dielectricCode is set to U2J at the same time, because a
    ranker that cannot see the code has to report 'unverified' where the honest answer
    is 'mismatch'.

  * ZLM is Murata's OWN designation (publicstandard MURATA), not EIA, and no coefficient
    can be decoded from it. Those 10 rows get tcc null -- the false zero is removed and
    nothing is invented in its place. dielectricCode stays absent: the schema documents
    it as the standard EIA/MIL code and Murata says this one is neither.

An EIA characteristic this script has no coefficient for ABORTS the run. Guessing the
tempco of an unhandled class-1 code is the same mistake as the placeholder, one code
later.

THE UNIT IS WRITTEN DOWN. `tcc` is used with two different meanings in this catalogue --
a ppm/K coefficient on class-1 rows (Murata C0G rows carry {min -30, max 30}) and a
percent capacitance-change band on class-2 rows (X7R rows carry {min -15, max 15}) --
and CAS's own docs describe both. The rewritten rows therefore carry the optional
dimensionWithTolerance `unit`, "ppm/K", so this value cannot be read as a percentage.

THE IMPORTER IS FIXED IN THE SAME CHANGE: convert_murata.py now decodes the CSV's `tcc`
column into a real coefficient and a dielectricCode, and a characteristic it does not
know yields NO tcc at all instead of a zero.

NOT TOUCHED, REPORTED INSTEAD (both are the same placeholder, neither is this ticket's
class-1 population):
  * 7,146 Murata ceramic-class-2 rows carry the same tcc {nominal: 0}. No class-2
    dielectric has a zero coefficient, but the correct value there is a percent band per
    EIA code, and four of Murata's codes (B, R, X8M, X8N) have no EIA band to read.
  * 2,162 rows whose Murata characteristic is X8G -- which Murata files as
    temperature_compensating, i.e. Class 1 -- are stored as ceramic-class-2, because the
    importer decided the class with `technology = Class II if tcc.startswith("X")`.

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
AUDIT = REPO / "staging" / "abt517_murata_class1_tcc_audit.json"

TICKET = "ABT #517"
TODAY = "2026-08-02"

# EIA RS-198 class-1 three-character codes, decoded rather than tabulated by hand:
# 1st char = significant figure of the coefficient, 2nd = multiplier, 3rd = tolerance
# in ppm/K. Only the codes Murata's product list actually carries on class-1 parts are
# listed; an EIA code that is not here aborts the run rather than being guessed at.
EIA_SIGNIFICANT = {"C": 0.0, "B": 0.3, "L": 0.8, "A": 0.9, "M": 1.0,
                   "P": 1.5, "R": 2.2, "S": 3.3, "T": 4.7, "V": 5.6, "U": 7.5}
EIA_MULTIPLIER = {"0": -1, "1": -10, "2": -100, "3": -1000,
                  "4": 1, "6": 10, "7": 100, "8": 1000}
EIA_TOLERANCE = {"G": 30, "H": 60, "J": 120, "K": 250,
                 "L": 500, "M": 1000, "N": 2500}

# The unit of the value written below. `tcc` carries no unit of its own in CAS, and this
# catalogue uses it for both a ppm/K coefficient (class 1) and a percent band (class 2),
# so the rows this script rewrites say which one they mean.
TCC_UNIT = "ppm/K"


def eia_tempco(code):
    """(nominal, minimum, maximum) in ppm/K for an EIA RS-198 class-1 code, or None."""
    if len(code) != 3:
        return None
    figure = EIA_SIGNIFICANT.get(code[0])
    multiplier = EIA_MULTIPLIER.get(code[1])
    tolerance = EIA_TOLERANCE.get(code[2])
    if figure is None or multiplier is None or tolerance is None:
        return None
    nominal = figure * multiplier or 0.0        # C0G is 0.0 x -1; keep it off -0.0
    return nominal, nominal - tolerance, nominal + tolerance


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
    (GRM1555C1H222JA01D -> GRM1555C1H222JA01). Same local resolution
    scripts/murata_bias_harvest.py uses; a strip that does not land on a real
    catalogue entry is not a match."""
    if part in catalogue:
        return part
    for k in range(1, 4):
        if len(part) - k >= 12 and part[:-k] in catalogue:
            return part[:-k]
    return None


def provenance_for(characteristic, standard, tempco):
    if tempco is None:
        what = (f"'{characteristic}' is Murata's own designation (publicstandard "
                f"{standard}), not an EIA/MIL code, and states no decodable temperature "
                "coefficient — the stored zero is withdrawn rather than replaced")
        fields = ["thermal.tcc"]
    else:
        nominal, minimum, maximum = tempco
        what = (f"this part's temperature characteristic is '{characteristic}' "
                f"(publicstandard {standard}), not C0G; EIA RS-198 decodes it as "
                f"{nominal:g} ppm/K over [{minimum:g}, {maximum:g}]")
        fields = ["thermal.tcc", "part.dielectricCode"]
    return {
        "source": "manufacturerParametric",
        "sourceName": (
            "Murata MLCC product list (staging/murata/mlcc.csv, the same published "
            "product list scripts/convert_murata.py imported from): " + what + ". The "
            "stored tcc {nominal: 0} was that importer's placeholder — it used the "
            f"characteristic column as a yes/no flag and discarded the value [{TICKET}]"),
        "sourceUrl": None,
        "retrievedDate": TODAY,
        "fields": fields,
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
    """A class-1 row whose only tempco evidence is the importer's zero."""
    if (sheet.get("part") or {}).get("technology") != "ceramic-class-1":
        return False
    tcc = (sheet.get("thermal") or {}).get("tcc")
    return isinstance(tcc, dict) and tcc.get("nominal") == 0


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

            part = sheet["part"]
            ref = str(info.get("reference") or part.get("partNumber") or "").strip()
            characteristic, standard = catalogue.get(resolve(ref, catalogue), ("", ""))
            if not characteristic:
                # Not in Murata's product list, so there is nothing to correct it
                # against. Left exactly as found and counted, never guessed at.
                audit["notInProductList"].append(ref)
                out.write(raw)
                continue
            if characteristic == "C0G":
                audit["zeroIsCorrect"] += 1        # C0G really is 0 +/-30 ppm/K
                out.write(raw)
                continue

            tempco = eia_tempco(characteristic)
            if tempco is None and standard == "EIA":
                print(f"ABORT {ref}: EIA characteristic '{characteristic}' has no "
                      "decodable RS-198 coefficient — refusing to guess one")
                out.close()
                tmp.unlink(missing_ok=True)
                return 1

            thermal = sheet["thermal"]
            was = dict(thermal["tcc"])
            if tempco is None:
                thermal["tcc"] = None              # Murata-proprietary code: withdraw
            else:
                nominal, minimum, maximum = tempco
                thermal["tcc"] = {"nominal": nominal, "minimum": minimum,
                                  "maximum": maximum, "unit": TCC_UNIT}
                part["dielectricCode"] = characteristic
            sheet["provenance"] = (sheet.get("provenance") or []) + [
                provenance_for(characteristic, standard, tempco)]

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
                                      "standard": standard, "was": was,
                                      "now": thermal["tcc"]})
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
             "zeroIsCorrect": 0, "notInProductList": []}
    if repair(validator, gate, catalogue, audit):
        return 1

    tmp = Path(audit.pop("_tmp"))
    print(f"class-1 rows whose placeholder zero was corrected: {len(audit['repaired'])}")
    for code, n in sorted(audit["byCharacteristic"].items()):
        print(f"     {n:4}  {code}")
    print(f"class-1 rows where tcc 0 is the true C0G value, left alone: "
          f"{audit['zeroIsCorrect']}")
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
