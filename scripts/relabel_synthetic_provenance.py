#!/usr/bin/env python3
"""Relabel the "SYNTHETIC / generated placeholder record" provenance.

708 records (diodes, igbts, magnetics) carry provenance sourceName
"SYNTHETIC / generated placeholder record (example-domain URL)". That label is
wrong and actively misleading: the records are REAL parts with REAL electrical
data — spot-checked C4D05065 (Wolfspeed 650 V/5 A SiC), 2MBI150U4-060 (Fuji
600 V/150 A), LPD6235C-102 (Coilcraft 1 uH). What is actually synthetic is only
the datasheet URL, an example.com placeholder.

The label was applied by backfill_provenance.py's SYNTHETIC_HOSTS rule, which
fires on an example.com host and stamps the whole record synthetic — conflating
"the datasheet link is a placeholder" with "the data was generated". A cross-
reference consumer reading `source: manual, "SYNTHETIC / generated placeholder"`
reasonably distrusts good parts.

This corrects the record to what is actually true:
  * sourceName -> states the data is present but UNTRACED and the URL was a
    placeholder — no claim that the data is fabricated.
  * datasheetUrl -> cleared. An example.com link is worse than no link: it is
    misinformation. (Kelvin's datasheet_unusable() already treats "" and
    example.com identically, so this changes no downstream behaviour, only
    honesty.)
  * source stays "manual" — the honest enum for hand-entered, untraced data.

Does NOT touch electrical/mechanical values — those are real and stay. Schema-
safe: sourceName is free text, datasheetUrl accepts an empty string, and no
key is added or removed. Idempotent.
"""
import json
from pathlib import Path

DATA = Path("/home/alf/PSMA/TAS/data")
FILES = ["diodes.ndjson", "igbts.ndjson", "magnetics.ndjson"]

OLD_LABEL = "SYNTHETIC / generated placeholder record (example-domain URL)"
NEW_LABEL = ("untraced: electrical data present but not traced to a source; "
             "datasheet URL was an example-domain placeholder")


def each_info(node):
    if isinstance(node, dict):
        info = node.get("manufacturerInfo")
        if isinstance(info, dict):
            yield info
        for value in node.values():
            if isinstance(value, dict):
                yield from each_info(value)


def fix_record(record):
    changed = False
    for info in each_info(record):
        datasheet = info.get("datasheetInfo")
        if not isinstance(datasheet, dict):
            continue
        for prov in datasheet.get("provenance") or []:
            if isinstance(prov, dict) and prov.get("sourceName") == OLD_LABEL:
                prov["sourceName"] = NEW_LABEL
                changed = True
        # A placeholder datasheet URL is misinformation; drop it wherever it appears.
        url = str(info.get("datasheetUrl") or "")
        if "example.com" in url:
            info["datasheetUrl"] = ""
            changed = True
    return changed


def main():
    for name in FILES:
        path = DATA / name
        if not path.exists():
            print(f"skip (missing): {name}")
            continue
        out, fixed = [], 0
        for line in path.open(encoding="utf-8"):
            if not line.strip():
                continue
            record = json.loads(line)
            if fix_record(record):
                fixed += 1
            out.append(json.dumps(record, ensure_ascii=False))
        if fixed:
            path.write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"{name}: relabelled {fixed} record(s)")


if __name__ == "__main__":
    main()
