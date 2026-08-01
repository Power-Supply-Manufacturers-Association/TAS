#!/usr/bin/env python3
"""Remove TDK's placeholder series names from the family field and the description.

    python3 scripts/strip_tdk_sentinel_series.py [--dry-run] [--db PATH]

TDK's Meister database keeps its series list in a `series` table that a class row points
at by `series_id`. Two of its rows are sentinels, not products:

    series_id  series_name
       -1      dummy
       -2      TBD

They mean "this class has no series assigned" - the same role a NULL would play - and 33
of the 861 class rows carry one. The magnetics extractor joined class -> series and wrote
whatever name came back straight into `manufacturerInfo.family` and onto the front of
`datasheetInfo.part.description`, so those sentinels were published as if they were real
device families:

    "family": "dummy"
    "description": "dummy CLF10040T-100M-CA 10.0uH"

`family` is the device-class evidence the cross-reference ranker reads, and it is shown to
the engineer next to genuine families (ASPI-104S, WE-XHMI), so a reader sees a placeholder
presented as a series.

There is no real series to put back. `class` is the only part -> series link in the
database and for these parts it resolves to the sentinel, which is TDK stating it has no
series for them. So the field is REMOVED rather than guessed: MAS requires only `name` on
manufacturerInfo and nothing on datasheetInfo.part, and absent is the truth here. Deriving
one from the part number ("MLG1608B8N2DT000" -> "MLG") would be a new invented value.

The description keeps everything after the sentinel token - "CLF10040T-100M-CA 10.0uH" is
the part number and its own inductance, both real.

Only live catalogues are touched. Quarantined records keep their text as evidence.
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AUDIT = REPO / "staging" / "tdk_sentinel_series_audit.json"
TODAY = "2026-08-02"
DEFAULT_DB = "/mnt/c/ProgramData/TDK/TDKMeister/tdkData/TstDB.tmdb"

# (file, discriminator) - live catalogues only, never a *.quarantine_*.ndjson
TARGETS = [("magnetics.ndjson", "magnetic"), ("varistors.ndjson", "varistor"),
           ("capacitors.ndjson", "capacitor")]


def sentinel_series(db: Path) -> set[str]:
    """The series names TDK uses to mean 'no series' - its negative series_ids."""
    out = subprocess.run(["mdb-export", str(db), "series"], check=True,
                         capture_output=True, text=True).stdout
    names = {r["series_name"] for r in csv.DictReader(io.StringIO(out))
             if int(r["series_id"]) < 0}
    if not names:
        raise SystemExit("no sentinel series rows found in the TDK database")
    return names


def strip(rec: dict, disc: str, sentinels: set[str]) -> tuple[bool, dict | None]:
    part = rec.get(disc)
    if not isinstance(part, dict):
        return False, None
    mi = part.get("manufacturerInfo")
    if not isinstance(mi, dict) or str(mi.get("name")) != "TDK":
        return False, None
    fam = mi.get("family")
    if fam not in sentinels:
        return False, None

    note = {"reference": mi.get("reference"), "family": fam}
    mi.pop("family")
    p = (mi.get("datasheetInfo") or {}).get("part")
    if isinstance(p, dict) and isinstance(p.get("description"), str):
        desc = p["description"]
        if desc.startswith(fam + " "):
            rest = desc[len(fam) + 1:].strip()
            note["description"] = {"was": desc, "now": rest or None}
            if rest:
                p["description"] = rest
            else:
                p.pop("description")
    return True, note


def main(argv):
    dry = "--dry-run" in argv
    db = Path(argv[argv.index("--db") + 1]) if "--db" in argv else Path(DEFAULT_DB)
    if not db.exists():
        raise SystemExit(f"TDK Meister database not found at {db}")
    sentinels = sentinel_series(db)
    print(f"TDK sentinel series names: {sorted(sentinels)}")

    audit = {"ticket": "ABT #514", "date": TODAY, "sentinels": sorted(sentinels),
             "byFile": {}, "fixed": []}
    counts = Counter()

    for fname, disc in TARGETS:
        data = REPO / "data" / fname
        if not data.exists():
            raise SystemExit(f"catalogue not found: {data}")
        tmp = data.with_suffix(".ndjson.tmp")
        n = 0
        with open(data, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                line = raw
                if b"TDK" in raw:
                    try:
                        rec = json.loads(raw)
                    except Exception:                                 # noqa: BLE001
                        out.write(line)
                        continue
                    changed, note = strip(rec, disc, sentinels)
                    if changed:
                        n += 1
                        counts[(fname, note["family"])] += 1
                        if len(audit["fixed"]) < 40:
                            audit["fixed"].append(dict(note, file=fname))
                        line = json.dumps(rec, separators=(",", ":"),
                                          ensure_ascii=False).encode() + b"\n"
                out.write(line)
            out.flush()
            os.fsync(out.fileno())
        audit["byFile"][fname] = n
        print(f"  {fname:22} {n:5} records cleaned")
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, data)

    for (f, fam), v in sorted(counts.items()):
        print(f"     {v:5}  {f} family={fam!r}")
    total = sum(audit["byFile"].values())
    print(f"\ntotal: {total} records")
    if dry:
        print("--dry-run: nothing written")
    else:
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
