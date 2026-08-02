#!/usr/bin/env python3
"""The manufacturer name TE's brand column reports when there is no brand (ABT #506).

    python3 scripts/fix_te_series_manufacturer_name.py [--dry-run]

WHAT WENT WRONG. scripts/te_connectors_import.py copies TE's parametric `brand`
column into two slots without asking what it holds:

    if r.get("brand"): part["series"] = r["brand"]
    ...
    if r.get("brand"): mi["family"] = r["brand"]

That column is a SUB-BRAND column. For most TE parts it carries a real product line —
AMP (25,315 source rows), Holsworthy, Neohm, DEUTSCH, Raychem, Alcoswitch, Buchanan —
and copying it is right. But TE files every part that belongs to no sub-brand under the
house name, so 34,985 of the 105,449 source rows have brand 'TE Connectivity', and the
importer wrote the manufacturer's own name into the series and family slots. 12,914 of
those reached data/connectors.ndjson; the rest are already quarantined (21,877
incomplete + 194 current/pitch conflict) and stay there.

A manufacturer name is not a series. It carries no device-class information, it is
identical for every part the vendor sells, and any series-based family match reads it as
evidence anyway — Qarlos surfaced 2-1546566-1 offered as a substitute with series 'TE
Connectivity' beside sibling TE terminal blocks correctly carrying 'Buchanan'. It is a
silent ingestion fallback where the honest value is null.

THE CRITERION IS EQUALITY WITH THE RECORD'S OWN MANUFACTURER, not a list of vendor
names. The ticket queried 14 named manufacturers; the property "series (or family) is a
copy of this record's manufacturerInfo.name" finds the same 12,914 rows, does not go
stale as vendors are added, and does not fire on a sub-brand that merely CONTAINS the
house name ('Amphenol RF' under 'Amphenol' is a real series). Comparison is
punctuation-and-case-insensitive, the same normalisation Blade Runner's new
GEN_SERIES_IS_MANUFACTURER check uses.

WHAT IS WRITTEN. part.series null (PEAS types it ["string","null"] and documents null
as "the part has no distinct series"); manufacturerInfo.family removed, because PEAS
types that one as a bare string and a null there would not validate. Nothing is invented
in its place: TE's feed genuinely reports no sub-brand for these parts, and guessing a
series from the description is the same mistake one column over. The fixed importer
likewise emits neither field when brand is the house name, so the catalogue and its
producer now agree.

Both gates run per rewritten record: JSON Schema (CONAS/connector.json) and Blade Runner
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
DATA = REPO / "data" / "connectors.ndjson"

TICKET = "ABT #506"
TODAY = "2026-08-02"


def fold(value):
    """Lowercase, alphanumerics only — 'TE Connectivity ' and 'te-connectivity' are
    the same name. Mirrors norm_tech() in the validator so the repair and the check
    that guards it cannot disagree. Non-strings fold to '', which never matches."""
    if not isinstance(value, str):
        return ""
    return "".join(c.lower() for c in value if c.isalnum())


def defective_slots(info):
    """Which of the two slots hold a copy of this record's own manufacturer name."""
    name = fold(info.get("name"))
    if not name:
        return []
    sheet = info.get("datasheetInfo") or {}
    slots = []
    if fold((sheet.get("part") or {}).get("series")) == name:
        slots.append("part.series")
    if fold(info.get("family")) == name:
        slots.append("manufacturerInfo.family")
    return slots


def provenance_for(name, slots):
    return {
        "source": "manufacturerParametric",
        "sourceName": (
            f"TE Connectivity parametric search (api.te.com), the same feed "
            f"scripts/te_connectors_import.py imported from: this part belongs to no "
            f"TE sub-brand, so the feed's `brand` column repeats the house name "
            f"'{name}' rather than naming a product line. The importer copied that "
            f"column into {' and '.join(slots)}; the manufacturer name is withdrawn "
            f"from the series slot rather than replaced, because the source publishes "
            f"no series for this part [{TICKET}]"),
        "sourceUrl": None,
        "retrievedDate": TODAY,
        "fields": slots,
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
    schema = json.loads((PSMA / "CONAS" / "schemas" / "connector.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def errors_of(validator, connector):
    return sorted(f"{list(e.absolute_path)}: {e.message}"
                  for e in validator.iter_errors(connector))


def stamp(path):
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


def unmoved(path, before):
    """connectors.ndjson is appended to concurrently. Untouched lines are copied
    through byte-for-byte, but the final os.replace would still drop anything appended
    while the temp file was being built. Refuse rather than lose an append."""
    if stamp(path) != before:
        print(f"ABORT {path.name}: appended to while this run was in flight; re-run")
        return False
    return True


def repair(validator, gate, audit):
    before_stamp = stamp(DATA)
    tmp = DATA.with_suffix(".ndjson.tmp")
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            if b'"series"' not in raw and b'"family"' not in raw:
                out.write(raw)
                continue
            record = json.loads(raw)
            connector = record.get("connector")
            if not isinstance(connector, dict):
                out.write(raw)
                continue
            info = connector.get("manufacturerInfo") or {}
            slots = defective_slots(info)
            if not slots:
                out.write(raw)
                continue

            name = info["name"]
            sheet = info["datasheetInfo"]
            if "part.series" in slots:
                sheet["part"]["series"] = None
            if "manufacturerInfo.family" in slots:
                del info["family"]
            sheet["provenance"] = (sheet.get("provenance") or []) + [
                provenance_for(name, slots)]

            ref = str(info.get("reference")
                      or (sheet.get("part") or {}).get("partNumber") or "").strip()
            errs = errors_of(validator, connector)
            if errs:
                print(f"ABORT {ref}: schema {errs[0][:160]}")
                out.close()
                tmp.unlink(missing_ok=True)
                return 1
            ok, why = gate.check(connector)
            if not ok:
                print(f"ABORT {ref}: blade runner {why}")
                out.close()
                tmp.unlink(missing_ok=True)
                return 1

            out.write(json.dumps(record, ensure_ascii=False).encode() + b"\n")
            audit["repaired"] += 1
            audit["byManufacturer"][name] = audit["byManufacturer"].get(name, 0) + 1
            audit["bySlots"][", ".join(slots)] = \
                audit["bySlots"].get(", ".join(slots), 0) + 1
            if len(audit["samples"]) < 5:
                audit["samples"].append({"reference": ref, "was": name, "slots": slots})
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
    gate = BladeGate("connector")
    audit = {"ticket": TICKET, "date": TODAY, "repaired": 0,
             "byManufacturer": {}, "bySlots": {}, "samples": []}
    if repair(validator, gate, audit):
        return 1

    tmp = Path(audit.pop("_tmp"))
    print(f"records whose series/family copy of the manufacturer name was withdrawn: "
          f"{audit['repaired']}")
    for name, n in sorted(audit["byManufacturer"].items(), key=lambda kv: -kv[1]):
        print(f"     {n:6}  {name}")
    for slots, n in sorted(audit["bySlots"].items(), key=lambda kv: -kv[1]):
        print(f"     {n:6}  {slots}")
    print(gate.summary())
    for s in audit["samples"]:
        print(f"       {s['reference']:22} {s['was']!r} -> null  ({', '.join(s['slots'])})")
    if args.dry_run:
        tmp.unlink(missing_ok=True)
        print("dry run — data/connectors.ndjson untouched")
        return 0
    os.replace(tmp, DATA)
    print(f"wrote {DATA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
