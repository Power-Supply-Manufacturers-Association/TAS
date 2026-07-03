#!/usr/bin/env python3
"""Convert a directory of per-series Hirose parametric CSVs (pulled via the
www.hirose.com/en/product/c/all/csvdownload API) -> CONAS connector NDJSON.

Same column shape / conversion rules as hirose_connectors_import.py (row 0 of each
CSV is a human-readable label row and is skipped); this variant reads MANY csv files
(one per series) and dedupes both within the pull and against parts already present
in TAS/data/connectors.ndjson (+ its quarantine siblings) so re-running a broader
series sweep never reintroduces already-merged Hirose parts.

Usage: python3 hirose_bulk_series_import.py <csv_dir> <out_dir>
Writes <out_dir>/connectors.main.ndjson and <out_dir>/connectors.incomplete.ndjson.
"""
import csv, glob, json, os, sys

sys.path.insert(0, os.path.dirname(__file__))
from hirose_connectors_import import convert  # noqa: E402

PSMA = "/home/alf/PSMA"
TAS = f"{PSMA}/TAS"


def existing_part_numbers():
    seen = set()
    for f in glob.glob(f"{TAS}/data/connectors*.ndjson"):
        for line in open(f, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if not line or line.startswith("version https://git-lfs"):
                break
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            mi = rec.get("connector", {}).get("manufacturerInfo", {})
            ref = mi.get("reference")
            if ref:
                seen.add(ref)
    return seen


def main():
    csv_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    already = existing_part_numbers()

    mains, inc, seen, skipped_dup = [], [], set(), 0
    files = sorted(glob.glob(f"{csv_dir}/*.csv"))
    for path in files:
        rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))[1:]  # drop label row
        for row in rows:
            pn = (row.get("name") or "").strip()
            if not pn or pn in seen:
                continue
            seen.add(pn)
            if pn in already:
                skipped_dup += 1
                continue
            rec, missing = convert(row)
            if rec is None:
                continue
            if missing:
                rec["quarantineReason"] = (
                    "incomplete Hirose connector; missing: " + "; ".join(missing)
                )
                inc.append(rec)
            else:
                mains.append(rec)

    for nm, recs in [("connectors.main", mains), ("connectors.incomplete", inc)]:
        with open(f"{out_dir}/{nm}.ndjson", "w") as fo:
            for r in recs:
                fo.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(json.dumps({
        "files": len(files), "unique_parts_seen": len(seen),
        "already_in_db_skipped": skipped_dup,
        "main": len(mains), "incomplete": len(inc),
    }))


if __name__ == "__main__":
    main()
