#!/usr/bin/env python3
"""Verify TDK varistor citations against TDK's own database instead of its website (ABT #391).

    python3 scripts/verify_tdk_varistors_from_meister.py [--dry-run] [--db PATH]

294 TDK varistor rows still say "this record was not verified against that source", and
they say it for a reason nobody can fix by trying harder: phase 1 classified 30,482 TDK
URLs as BLOCKED, and they are blocked because an earlier sweep in this campaign sent TDK
30,495 requests in one pass. The block is ours. No amount of re-fetching will clear it,
and hammering the host again to clear a provenance flag would be worse than the flag.

TDK ships its whole catalogue as a local Access database with TDK Meister, and these 294
part numbers are IN it, matched exactly. A manufacturer's own catalogue database is a
manufacturer source - the same one already used to correct 435 TDK magnetics values under
ABT #387 - so these citations can be verified without touching the network at all.

WHAT IS AND IS NOT CLAIMED. The database confirms TDK publishes this exact part number. It
does not re-derive the row's stored values, and the provenance says so, exactly as the
phase-2 promotions do. The web citation is kept alongside: it is not wrong, merely
unreachable from here, and deleting a correct URL because we provoked a rate limit would
lose information.

THE OTHER 3,107 TDK ROWS ARE NOT DONE THIS WAY, and the reason is worth recording. Their
part numbers are not in Meister, and not because of a suffix: our C0402C0G0J100J0 is 15
characters where every C0402 code in TDK's database is 19 (C0402C0G1C010C020BC). The
format differs, so matching them would be re-identification - deciding that our row
DESCRIBES some other code - and that is a larger claim than a citation check. Filed, not
guessed.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "tdk_varistor_verification_audit.json"
TODAY = "2026-08-01"
DEFAULT_DB = "/mnt/c/ProgramData/TDK/TDKMeister/tdkData/TstDB.tmdb"
MARKER = "[inferred from the record's own URL — this record was not verified against that source]"

NOTE = ("[citation verified {date} against TDK's own catalogue database (TDK Meister "
        "TstDB.tmdb), which publishes this exact part number. TDK's website could not be "
        "used: phase 1 found its URLs BLOCKED, a block this project's own earlier sweep "
        "caused. The stored VALUES were not re-derived; only the part's existence in the "
        "manufacturer's catalogue was checked.]")

DISC = {"varistors": ("varistor",), "capacitors": ("capacitor",), "magnetics": ("magnetic",)}


def tdk_part_numbers(db: Path):
    r = subprocess.run(["mdb-export", str(db), "part"], capture_output=True, text=True,
                       errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"mdb-export failed: {r.stderr[:200]}")
    out = set()
    for row in csv.reader(r.stdout.splitlines()):
        if len(row) > 1 and row[0].isdigit():
            out.add(row[1].strip('"'))
    return out


def key_of(mi):
    """Reference, or the part number when there is no reference.

    Keying on `reference` alone has produced a wrong answer three times in this campaign:
    a large share of rows carry their identifier only at datasheetInfo.part.partNumber.
    """
    r = mi.get("reference")
    if r:
        return str(r)
    p = ((mi.get("datasheetInfo") or {}).get("part") or {}).get("partNumber")
    return str(p) if p else ""


def main(argv):
    dry = "--dry-run" in argv
    db = Path(argv[argv.index("--db") + 1]) if "--db" in argv else Path(DEFAULT_DB)
    if not db.exists():
        raise SystemExit(f"TDK Meister database not found at {db}")
    parts = tdk_part_numbers(db)
    print(f"TDK Meister part numbers: {len(parts):,}")

    audit = {"ticket": "ABT #391", "date": TODAY, "database": str(db),
             "verified": [], "byCatalogue": Counter(), "notInDatabase": Counter()}
    note = NOTE.format(date=TODAY)

    for cat, keys in DISC.items():
        path = DATA / f"{cat}.ndjson"
        if not path.exists():
            continue
        tmp = path.with_suffix(".ndjson.tmp")
        n = 0
        with open(path, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                line = raw
                if b"was not verified against that source" in raw and b"TDK" in raw:
                    try:
                        rec = json.loads(raw)
                        o = rec
                        for k in keys:
                            o = (o or {}).get(k) or {}
                        mi = o.get("manufacturerInfo") or {}
                    except Exception:                             # noqa: BLE001
                        out.write(line)
                        continue
                    if str(mi.get("name")) == "TDK":
                        k = key_of(mi)
                        if k and k in parts:
                            prov = (mi.get("datasheetInfo") or {}).get("provenance") or []
                            hit = False
                            for p in prov:
                                sn = str(p.get("sourceName", ""))
                                if MARKER in sn:
                                    p["sourceName"] = sn.replace(MARKER, note)
                                    p["retrievedDate"] = TODAY
                                    hit = True
                            if hit:
                                n += 1
                                audit["verified"].append({"catalogue": cat, "partNumber": k})
                                audit["byCatalogue"][cat] += 1
                                line = json.dumps(rec, separators=(",", ":"),
                                                  ensure_ascii=False).encode() + b"\n"
                        elif k:
                            audit["notInDatabase"][cat] += 1
                out.write(line)
            out.flush()
            os.fsync(out.fileno())
        if n:
            print(f"  {cat:12} {n:5} verified against TDK's database")
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, path)

    print(f"\nverified: {len(audit['verified'])}")
    print(f"left unverified (part number not in TDK's database): "
          f"{sum(audit['notInDatabase'].values()):,}  {dict(audit['notInDatabase'])}")
    if dry:
        print("--dry-run: nothing written")
    else:
        audit["byCatalogue"] = dict(audit["byCatalogue"])
        audit["notInDatabase"] = dict(audit["notInDatabase"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
