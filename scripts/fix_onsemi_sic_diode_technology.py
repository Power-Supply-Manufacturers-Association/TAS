#!/usr/bin/env python3
"""onsemi's SiC Schottky diodes were filed as silicon rectifiers (ABT #523).

    python3 scripts/fix_onsemi_sic_diode_technology.py [--dry-run]

WHAT WENT WRONG. The 2026-06-24 onsemi parametric run (scripts/onsemi_csv_import.py)
read the technology out of `r.get("Type","configuration","family")`, and Row.get
matches a header by SUBSTRING. onsemi's SiC-diode export has no Type column at all --
its columns are

    Product Group | Status | Compliance | Family | Configuration | VRRM (V) | ...
                                          D1/D2/    Single/
                                          D3/Q1     Common Cathode

where Family is a die generation and Configuration is a package configuration -- so
"Type" matched **Package Type** and the technology was read off "D2PAK2 (TO-263-2L)".
Nothing in it says "sic" or "schottky", so the importer's `else` branch asserted
subType "rectifier" and technology "Si" for all 117 rows of onsemi's silicon-carbide
diode portfolio. 92 of them were new and reached data/diodes.ndjson.

WHY IT MATTERS. Kelvin's DiodeRow.technology IS part.subType, so a mislabelled SiC
Schottky reaches the cross-reference ranker indistinguishable from a mains rectifier:
PCFFS08120AF (1200 V SiC Schottky) was offered as a same-technology substitute for a
Vishay S8J 600 V silicon rectifier with no technology caveat, and the 2.75x IFSM
derating that comes with it (80 A on an 8 A part, 10x, versus the S8J's 220 A, 27x --
the Schottky signature) was never mentioned.

THE REPAIR is part.technology "Si" -> "SiC" and part.subType "rectifier" ->
"sicSchottky", nothing else. Every electrical value in these records is the vendor's
and stays untouched; only the two fields the importer invented are rewritten. The
correction is sourced from onsemi's product family, not from the export that lacks
the column -- every family below is on onsemi's "Silicon Carbide (SiC) Diodes" page
and its datasheets are titled "<part> - Silicon Carbide (SiC) Schottky Diode"
(verified for FFSB0665A, FFSD0665A, NDSH20120C, NDC25170A, PCFFS2065AF; UJ3D is the
ex-UnitedSiC MPS/JBS line onsemi acquired from Qorvo in 2025). The records' own
electricals corroborate it: 650/1200/1700 V with V_F 1.6-1.75 V, 40-400 uA reverse
leakage and IFSM/IF of 7-12x is a SiC Schottky, not a silicon PN rectifier.

THREE MORE records match the ticket's population query and are NOT this import:
FFSD1065C, FFSD2045C and FFSD4045S carry 45-65 V ratings with silicon-Schottky
forward drops under onsemi's SiC family prefix. onsemi's FFSD family is EliteSiC
Schottky at 650 V and 1200 V only (the real 10 A 650 V parts are FFSD1065A and
FFSD1065B); none of the three appears in any of the 28 onsemi parametric exports,
including the complete 117-row SiC-diode page; two carry an empty datasheetUrl and
the third's URL 403s; and 20 A / 40 A in an SMB (DO-214AA) body is several times
what that package can carry. There is no value here to correct -- the part numbers
themselves are unattested -- so they are QUARANTINED into
data/diodes.quarantine_not_in_manufacturer_catalogue.ndjson beside ABT #460's SS54,
intact, with the evidence recorded. Nothing is deleted.

The importer is fixed in the same change: onsemi_csv_import.py now reads the
technology from a column that NAMES it, by exact header, and an export that states
no technology yields no subType and no technology -- the row goes to the librarian
instead of being asserted as a silicon rectifier.

Both gates run per rewritten record: JSON Schema (SAS/diode.json) and Blade Runner
(tas_validator). A failure of either aborts the run and leaves both files untouched.
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
QUARANTINE = REPO / "data" / "diodes.quarantine_not_in_manufacturer_catalogue.ndjson"
AUDIT = REPO / "staging" / "abt523_onsemi_sic_diode_audit.json"

TICKET = "ABT #523"
TODAY = "2026-08-02"

# The import run that wrote the defect. Its provenance string is exact and unique to
# scripts/onsemi_csv_import.py, so the match cannot reach records sourced elsewhere.
IMPORT_SOURCE = "onsemi parametric export (CSV)"

# onsemi's silicon-carbide diode families, i.e. every part-number prefix on
# onsemi.com/products/discrete-power-modules/silicon-carbide-sic/silicon-carbide-sic-diodes
# that the export carries. FFS*/PCFFS* are the EliteSiC Schottky families, NDSH/NVDSH
# (automotive)/NDC (bare die) the 1200-1700 V ones, UJ3D the ex-UnitedSiC MPS line.
SIC_FAMILIES = ("FFSB", "FFSD", "FFSH", "FFSM", "FFSP", "PCFFS",
                "NDSH", "NVDSH", "NDC", "UJ3D")

# Corroboration, not a source: onsemi's SiC diode portfolio starts at 650 V. A record
# from these families below it is not one of them (the three quarantined parts sit at
# 45-65 V), so it is never relabelled by this script.
SIC_VR_FLOOR = 650.0

FIX = {"technology": "SiC", "subType": "sicSchottky"}
WAS = {"technology": "Si", "subType": "rectifier"}

PROV = {
    "source": "manufacturerDatasheet",
    "sourceName": "onsemi Silicon Carbide (SiC) Diodes family — EliteSiC Schottky; the "
                  "parametric export this record was built from carries no technology "
                  "column (Family is a die generation, Configuration a package "
                  "configuration) and the importer defaulted it to Si/rectifier "
                  f"[{TICKET}]",
    "sourceUrl": "https://www.onsemi.com/products/discrete-power-modules/"
                 "silicon-carbide-sic/silicon-carbide-sic-diodes",
    "retrievedDate": TODAY,
    "fields": ["part.technology", "part.subType"],
}

# reference -> why the part number itself is unattested. Quarantined, not corrected:
# no value in these records can be trusted once the part number is not onsemi's.
UNATTESTED = {
    "FFSD1065C": "record is 65 V / 10 A, V_F 0.75 V in SMB; onsemi's FFSD family is "
                 "EliteSiC SiC Schottky at 650 V and 1200 V only and publishes "
                 "FFSD1065A and FFSD1065B, no -C; the cited "
                 "onsemi.com/pub/Collateral/FFSD1065C-D.PDF returns 403",
    "FFSD2045C": "record is 45 V / 20 A, V_F 0.65 V in SMB; no such onsemi part number, "
                 "datasheetUrl is empty, and 20 A is several times what an SMB "
                 "(DO-214AA) body carries",
    "FFSD4045S": "record is 45 V / 40 A, V_F 0.72 V in DO-214AA; no such onsemi part "
                 "number, datasheetUrl is empty, and 40 A is an order above what a "
                 "DO-214AA (SMB) body carries",
}
UNATTESTED_REASON = ("part number does not appear in its own manufacturer's catalogue; "
                     "it uses onsemi's SiC-Schottky family prefix with ratings that "
                     f"family has never carried ({TICKET})")
UNATTESTED_EVIDENCE = ("none of the three appears in any of the 28 onsemi parametric "
                       "exports of 2026-06-24, including the complete 117-row Silicon "
                       "Carbide (SiC) Diodes page, nor in onsemi's product search")


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


def sheet_of(record):
    diode = record.get("semiconductor", {}).get("diode")
    if not isinstance(diode, dict):
        return None, None, None
    info = diode.get("manufacturerInfo") or {}
    return diode, info, (info.get("datasheetInfo") or {})


def is_mislabelled(info, sheet):
    """A record the 2026-06-24 onsemi CSV run wrote from the SiC-diode export."""
    part = sheet.get("part") or {}
    prov = sheet.get("provenance") or [{}]
    pn = str(part.get("partNumber") or "").upper()
    vr = (sheet.get("electrical") or {}).get("reverseVoltage")
    return (info.get("name") == "onsemi"
            and prov[0].get("sourceName") == IMPORT_SOURCE
            and pn.startswith(SIC_FAMILIES)
            and part.get("technology") == WAS["technology"]
            and part.get("subType") == WAS["subType"]
            and isinstance(vr, (int, float)) and vr >= SIC_VR_FLOOR)


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
    pulled = []
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            if b'"onsemi"' not in raw:
                out.write(raw)
                continue
            record = json.loads(raw)
            diode, info, sheet = sheet_of(record)
            if diode is None:
                out.write(raw)
                continue
            ref = str(info.get("reference") or "").strip()

            if ref in UNATTESTED:
                record["_validatorQuarantine"] = {
                    "date": TODAY,
                    "reason": UNATTESTED_REASON,
                    "ticket": TICKET,
                    "evidence": f"{UNATTESTED[ref]}; {UNATTESTED_EVIDENCE}",
                }
                pulled.append(record)
                audit["quarantined"].append({"reference": ref, "why": UNATTESTED[ref]})
                continue  # not written back to the live file

            if not is_mislabelled(info, sheet):
                out.write(raw)
                continue

            part = sheet["part"]
            changed = {k: {"was": part.get(k), "now": v} for k, v in FIX.items()}
            part.update(FIX)
            sheet["provenance"] = (sheet.get("provenance") or []) + [dict(PROV)]
            errs = errors_of(validator, diode)
            if errs:
                print(f"ABORT {ref}: schema {errs[0][:160]}")
                out.close()
                tmp.unlink(missing_ok=True)
                return 1
            ok, why = gate.check(diode)
            if not ok:
                print(f"ABORT {ref}: blade runner {why}")
                out.close()
                tmp.unlink(missing_ok=True)
                return 1
            out.write(json.dumps(record, ensure_ascii=False).encode() + b"\n")
            audit["relabelled"].append({"reference": ref, "changed": changed})
        out.flush()
        os.fsync(out.fileno())

    missing = sorted(set(UNATTESTED) - {q["reference"] for q in audit["quarantined"]})
    if missing:
        print(f"ABORT: unattested parts never seen in {DATA.name}: {missing}")
        tmp.unlink(missing_ok=True)
        return 1
    if not unmoved(DATA, before_stamp):
        tmp.unlink(missing_ok=True)
        return 1
    audit["_tmp"] = str(tmp)
    audit["_pulled"] = pulled
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    validator = load_validator()
    gate = BladeGate(("semiconductor", "diode"))
    audit = {"ticket": TICKET, "relabelled": [], "quarantined": []}
    if repair(validator, gate, audit):
        return 1

    tmp = Path(audit.pop("_tmp"))
    pulled = audit.pop("_pulled")
    print(f"relabelled {len(audit['relabelled'])} SiC Schottky records, "
          f"quarantined {len(pulled)} unattested part numbers")
    print(gate.summary())
    if args.dry_run:
        tmp.unlink(missing_ok=True)
        print("--dry-run: nothing replaced")
        return 0

    # Quarantine first: an append that lands before a failed replace leaves a
    # duplicate, which is recoverable. The other order loses the record.
    with open(QUARANTINE, "a", encoding="utf-8") as qf:
        for record in pulled:
            qf.write(json.dumps(record, ensure_ascii=False) + "\n")
        qf.flush()
        os.fsync(qf.fileno())
    os.replace(tmp, DATA)
    AUDIT.write_text(json.dumps(audit, indent=1))
    print(f"replaced {DATA}\nappended {len(pulled)} to {QUARANTINE}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
