#!/usr/bin/env python3
"""ABT #279: repair magnetics whose electrical subtype says 'inductor' while the
datasheet description says common-mode choke/filter.

The Würth .mdb import (and, it turns out, the TDK/Bourns/KEMET/... imports too)
defaulted electrical[].subtype to 'inductor'; the descriptions carry the truth
("WE-CMBH Horizontal Common Mode Power Line Choke", "Common Mode Chokes /
Filters ..."). Rule, deliberately narrow:

  - every electrical entry of the row is currently subtype 'inductor'
    (multi-wiring rows with a legitimate inductor entry are left alone), and
  - the part description matches /common[ -]?mode/i AND /choke|filter/i.

Matching rows get every electrical entry set to 'commonModeChoke' with
'dcResistance' mapped to the CMC variant's per-winding 'dcResistances: [value]'
(the shape every correctly-tagged CMC row in this catalogue already uses).
Rows carrying a field the CMC variant has no slot for are left UNCHANGED and
counted — dropping a datasheet value or
inventing a mapping is a decision for the ticket, not for a script. Every
modified document is validated against the MAS magnetic schema before it is
written; a row that fails validation is left unchanged and reported.

Usage: fix_cmc_subtype_abt279.py [--data DIR] [--schemas DIR] [--dry-run]
"""

import argparse
import collections
import json
import os
import re
import sys
import tempfile

COMMON_MODE = re.compile(r"common[ -]?mode", re.IGNORECASE)
CHOKE_OR_FILTER = re.compile(r"choke|filter", re.IGNORECASE)


def load_validator(schema_dirs):
    import jsonschema
    from referencing import Registry, Resource

    resources = []
    for schema_dir in schema_dirs:
        for root, _dirs, files in os.walk(schema_dir):
            for name in files:
                if not name.endswith(".json"):
                    continue
                with open(os.path.join(root, name)) as handle:
                    try:
                        schema = json.load(handle)
                    except json.JSONDecodeError:
                        continue
                if "$id" in schema:
                    resources.append((schema["$id"], Resource.from_contents(schema)))
    registry = Registry().with_resources(resources)
    magnetic = "https://psma.com/mas/magnetic.json"
    return jsonschema.Draft202012Validator({"$ref": magnetic}, registry=registry)


def is_candidate(magnetic):
    info = magnetic.get("manufacturerInfo", {})
    datasheet = info.get("datasheetInfo", {})
    electrical = datasheet.get("electrical")
    if not isinstance(electrical, list) or not electrical:
        return False
    if any(entry.get("subtype") != "inductor" for entry in electrical):
        return False
    description = datasheet.get("part", {}).get("description") or ""
    return bool(COMMON_MODE.search(description) and CHOKE_OR_FILTER.search(description))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=os.path.join(os.path.dirname(__file__), "..", "data"))
    parser.add_argument("--schemas", nargs="+",
                        default=[os.path.expanduser("~/OpenConverters/Kirchhoff/deps/MAS/schemas"),
                                 os.path.expanduser("~/OpenConverters/Kirchhoff/deps/PEAS/schemas")])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = os.path.join(args.data, "magnetics.ndjson")
    validator = load_validator(args.schemas)

    flipped = collections.Counter()
    deferred = collections.Counter()
    invalid_after_flip = []
    total = 0
    out = tempfile.NamedTemporaryFile("w", dir=args.data, delete=False, suffix=".tmp")
    with open(path) as source:
        for line in source:
            total += 1
            try:
                row = json.loads(line)
                magnetic = row["magnetic"]
            except (json.JSONDecodeError, KeyError):
                out.write(line)
                continue
            if not is_candidate(magnetic):
                out.write(line)
                continue
            candidate = json.loads(line)  # untouched copy in case validation fails
            unmappable = None
            for entry in candidate["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"]:
                for field in ("saturationCurrents", "inductancePoints", "maximumImpedance"):
                    if field in entry:
                        unmappable = field
                entry["subtype"] = "commonModeChoke"
                if "dcResistance" in entry:
                    entry["dcResistances"] = [entry.pop("dcResistance")]
            info = magnetic["manufacturerInfo"]
            if unmappable is not None:
                deferred[unmappable] += 1
                out.write(line)
                continue
            errors = list(validator.iter_errors(candidate["magnetic"]))
            if errors:
                invalid_after_flip.append((info.get("reference"), errors[0].message[:140]))
                out.write(line)  # leave unchanged, report below
                continue
            flipped[info.get("name") or "?"] += 1
            out.write(json.dumps(candidate, separators=(",", ":"), ensure_ascii=False) + "\n")
    out.close()

    print(f"scanned {total} rows; flipped {sum(flipped.values())} to commonModeChoke:")
    for manufacturer, count in flipped.most_common():
        print(f"  {manufacturer}: {count}")
    if deferred:
        print(f"\nDEFERRED (field with no CMC-variant slot — decide on the ticket): {dict(deferred)}")
    if invalid_after_flip:
        print(f"\nLEFT UNCHANGED — invalid against MAS after flip ({len(invalid_after_flip)}):")
        for reference, message in invalid_after_flip[:20]:
            print(f"  {reference}: {message}")
    if args.dry_run:
        os.unlink(out.name)
        print("\ndry run — no file written")
    else:
        os.replace(out.name, path)
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
