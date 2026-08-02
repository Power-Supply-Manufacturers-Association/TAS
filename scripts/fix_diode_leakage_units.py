#!/usr/bin/env python3
"""Reverse leakage stored in uA/mA inside an amps field (ABT #524).

    python3 scripts/fix_diode_leakage_units.py [--dry-run]

WHAT WENT WRONG. Two importers, two different ways of losing the same prefix.

Infineon (54 records, scripts/infineon_sas_finders_import.py). The Diode Rectifier
Finder xlsx carries the unit INSIDE the cell -- 'IR max' reads "40 uA", "20 uA",
"100 uA" -- and the importer pulled it through `val()`, which keeps only the number.
The 54 rows that populate that column are exactly the 54 records at 20.0 / 40.0 /
100.0 in the catalogue. IDV08E65D2, a 650 V / 8 A Si Diode Rapid 2, therefore asserts
40 A of reverse leakage: 5x its own forward rating, and 650 V x 40 A = 26 kW
dissipated while blocking. The datasheet figure is 40 uA, a factor of 1e6.

Bourns (129 records, scripts/extract_bourns_diodes.py). The parametric export's
column is headed "IRRM (mA)" and the extractor honoured it with a x1e-3. The column
is MICROamps. Every one of Bourns' 32 diode series in that export was checked against
its own datasheet and the export matches uA digit for digit:

    CD1408-FU1200  export 2     datasheet "IR 2.0 uA @ 25 C"      (and FU1400+ 5 / 5.0)
    CD0603-B0240R  export 0.5   datasheet "IRRM 0.5 uA typ"       (B0340R 3 / 3 uA)
    CD214A-S1x     export 0.1   datasheet "IR 0.10 uA typ"
    CD-MBL1xxS     export 5     datasheet "IRRM 5.0 uA max"
    CD214A-B340LR  export 550   datasheet "IR 0.55 mA typ"        = 550 uA
    CD214A-B140R   export 20    datasheet "IR 0.02 mA typ"        = 20 uA
    CD2010-B160    export 500   datasheet "IR 0.5 mA @ TJ=25 C"   = 500 uA

so the mA-spec'd Schottkys and the uA-spec'd rectifiers are one consistent uA column.
20 of the 129 land at 0.1-0.55 A, i.e. up to 55 % of the part's forward rating; those
are the ones the ticket's population query returns. The other 109 are wrong by the
same 1e3 and were simply too small to trip a >= 0.1 A predicate.

THE REPAIR is the one field, on the records those two import runs wrote, matched on
the exact provenance sourceName each stamped. Infineon /1e6, Bourns /1e3 (the
extractor already applied 1e-3 of the 1e-6 the column needed). Division, not
multiplication, so 40/1e6 is exactly 4e-05 and not 3.9999999999999996e-05. No other
value in these records is touched, and nothing is deleted: every record stays in
data/diodes.ndjson because the part is real and only its leakage was mis-scaled.

Both importers are fixed in the same change so the next run cannot reintroduce it:
infineon_sas_finders_import.py reads the unit off what remains after the number
(never by substring -- 'ma' is a substring of 'max', which is how the Nexperia
importer lost a 1e3 on forwardCurrent), and a cell with no unit now yields nothing
rather than being assumed base SI. extract_bourns_diodes.py divides by 1e6 with the
datasheet evidence recorded at the line.

The standing guard is in Blade Runner, not in either importer, so it covers every
future source: DIO_LEAKAGE_VS_IF compares reverseLeakageCurrent with forwardCurrent
(both amps, so the ratio is unit-free). Across the 3,219 catalogue diodes carrying
both fields the ratio tops out at 0.0167 -- onsemi RB751V40T1G, 0.5 mA on a 30 mA
small-signal Schottky -- with p99.9 at 0.0025, so 0.02 is Suspicious and 0.05, 3x
anything real, is Impossible.

Both gates run per rewritten record: JSON Schema (SAS/diode.json) and Blade Runner
(tas_validator). A failure of either aborts the run and leaves the file untouched.
"""
from __future__ import annotations

import argparse
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
DATA = REPO / "data" / "diodes.ndjson"
AUDIT = REPO / "staging" / "abt524_diode_leakage_units_audit.json"

TICKET = "ABT #524"
TODAY = "2026-08-02"

# The two import runs that wrote the defect, keyed by the exact provenance sourceName
# each stamped, mapped to what the stored number must be divided by to reach amps.
# The match cannot reach records sourced elsewhere: 37 further Infineon diodes carry
# "Infineon parametric finder (xlsx export)" and are correctly scaled (1e-08..1e-03),
# and they are left alone.
COHORTS = {
    "Infineon Diode Rectifier Finder (xlsx export)": (
        1e6,
        "Infineon Diode Rectifier Finder (xlsx export) — the 'IR max' cell carries "
        "its unit inline ('40 uA') and the 2026-06-24 import kept only the number, "
        f"storing microamps as amps [{TICKET}]",
    ),
    "Bourns parametric Excel export": (
        1e3,
        "Bourns parametric Excel export — the column headed 'IRRM (mA)' is "
        "microamps, verified against Bourns' datasheets for all 32 series in the "
        "export (CD1408-FU1200 '2.0 uA', CD0603-B0340R '3 uA', CD214A-B340LR "
        f"'0.55 mA' = 550 uA); the import applied the header's mA [{TICKET}]",
    ),
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
    schema = json.loads((PSMA / "SAS" / "schemas" / "diode.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def errors_of(validator, diode):
    return sorted(f"{list(e.absolute_path)}: {e.message}"
                  for e in validator.iter_errors(diode))


def cohort_of(sheet):
    """The import run this record came from, or None. sourceName is matched by
    PREFIX because the citation-verification pass appends its own note to it."""
    for prov in sheet.get("provenance") or []:
        name = str(prov.get("sourceName") or "")
        for key, spec in COHORTS.items():
            if name.startswith(key):
                return key, spec
    return None, None


def stamp(path):
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


def unmoved(path, before):
    """diodes.ndjson is appended to concurrently. Every line this script does not
    touch is copied through byte-for-byte, but the final os.replace would still drop
    anything appended while the temp file was being built. Refuse rather than lose an
    append; re-running picks up the new lines."""
    if stamp(path) != before:
        print(f"ABORT {path.name}: appended to while this run was in flight; re-run")
        return False
    return True


def repair(validator, gate, audit):
    before_stamp = stamp(DATA)
    tmp = DATA.with_suffix(".ndjson.tmp")
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            if b'"reverseLeakageCurrent"' not in raw:
                out.write(raw)
                continue
            record = json.loads(raw)
            diode = record.get("semiconductor", {}).get("diode")
            if not isinstance(diode, dict):
                out.write(raw)
                continue
            info = diode.get("manufacturerInfo") or {}
            sheet = info.get("datasheetInfo") or {}
            elec = sheet.get("electrical") or {}
            leak = elec.get("reverseLeakageCurrent")
            if not isinstance(leak, (int, float)) or isinstance(leak, bool):
                out.write(raw)
                continue
            key, spec = cohort_of(sheet)
            if key is None:
                out.write(raw)
                continue

            divisor, why = spec
            ref = str(info.get("reference") or "").strip()
            elec["reverseLeakageCurrent"] = leak / divisor
            sheet["provenance"] = (sheet.get("provenance") or []) + [{
                "source": "manufacturerDatasheet",
                "sourceName": why,
                "retrievedDate": TODAY,
                "fields": ["electrical.reverseLeakageCurrent"],
            }]
            errs = errors_of(validator, diode)
            if errs:
                print(f"ABORT {ref}: schema {errs[0][:160]}")
                out.close()
                tmp.unlink(missing_ok=True)
                return 1
            ok, blade = gate.check(diode)
            if not ok:
                print(f"ABORT {ref}: blade runner {blade}")
                out.close()
                tmp.unlink(missing_ok=True)
                return 1
            out.write(json.dumps(record, ensure_ascii=False).encode() + b"\n")
            audit["rescaled"].append({
                "reference": ref, "cohort": key,
                "was": leak, "now": elec["reverseLeakageCurrent"],
                "forwardCurrent": elec.get("forwardCurrent"),
            })
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
    gate = BladeGate(("semiconductor", "diode"))
    audit = {"ticket": TICKET, "rescaled": []}
    if repair(validator, gate, audit):
        return 1

    tmp = Path(audit.pop("_tmp"))
    per = {}
    for row in audit["rescaled"]:
        per[row["cohort"]] = per.get(row["cohort"], 0) + 1
    print(f"rescaled {len(audit['rescaled'])} reverse-leakage values")
    for key, n in sorted(per.items()):
        print(f"  {n:>4}  {key}  (/{COHORTS[key][0]:.0e})")
    print(gate.summary())
    if args.dry_run:
        tmp.unlink(missing_ok=True)
        print("--dry-run: nothing replaced")
        return 0

    os.replace(tmp, DATA)
    AUDIT.write_text(json.dumps(audit, indent=1))
    print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
