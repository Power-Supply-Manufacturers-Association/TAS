#!/usr/bin/env python3
"""Re-cite TDK parts against TDK's own installed database — offline (ABT #391).

    python3 scripts/resource_tdk_citations.py CHECK.json [--dry-run]

WHY OFFLINE. 30,503 TDK citations came back BLOCKED in phase-1 verification, and the
reason is this session's own doing: the sweep sent tdk.com 16,928 requests and
product.tdk.com 13,555, and TDK now returns 403 for their own front page. A host that
starts refusing is asking us to stop, so the answer is not to find another way in —
it is to stop knocking and use a source we already have.

TDK distributes TDK Meister, a desktop tool whose product database is installed
locally at C:\\ProgramData\\TDK\\TDKMeister\\tdkData\\TstDB.tmdb. That is TDK's own
data, shipped by TDK for exactly this kind of lookup, and reading it costs them
nothing. It is a Microsoft Access file; mdbtools reads it, and its `part` table lists
32,592 TDK part numbers.

    18,421 of the 21,627 cited TDK parts are present -> RE-CITED to that database

WHAT THE REMAINING 3,206 ARE NOT. They are NOT condemned. TDK Meister is a SIMULATION
library — it carries the parts TDK publishes S-parameters and characteristic curves
for, not the whole catalogue. The misses include C2012X7R1H104K125AB (a stock 0805
100 nF 50 V X7R) and B32621A6104K (an EPCOS film capacitor); these are ordinary real
parts that simply have no simulation model. Treating absence from a modelling subset
as evidence of non-existence is the same mistake that would have wrongly quarantined
15 real Würth parts absent from REDEXPERT. They stay marked inferred-not-verified
until TDK is reachable again or another TDK-supplied export covers them.

The provenance written here carries no sourceUrl, deliberately: nothing was fetched.
It names the database file and the date it was read, which is a claim anyone with TDK
Meister installed can check for themselves.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "tdk_recitation_audit.json"
TODAY = "2026-07-31"
TMDB = r"C:\ProgramData\TDK\TDKMeister\tdkData\TstDB.tmdb"

PATHS = {"capacitors": ("capacitor",), "magnetics": ("magnetic",)}


def ref_of(mi):
    r = mi.get("reference")
    if r:
        return str(r)
    part = (mi.get("datasheetInfo") or {}).get("part") or {}
    p = part.get("partNumber")
    return str(p) if p else None


def main(argv):
    dry = "--dry-run" in argv
    check = json.loads(Path(argv[0]).read_text())
    work = check["work"]
    confirmed = {r: work[r][0] for r in check["hit"]}
    print(f"{len(confirmed)} TDK parts confirmed in the local Meister database, "
          f"{len(check['miss'])} not modelled there (left unverified, NOT condemned)")

    audit = {"ticket": "ABT #391 (TDK, offline)", "date": TODAY, "database": TMDB,
             "recited": Counter(), "notInMeister": len(check["miss"])}

    for cat, keys in PATHS.items():
        want = {r for r, c in confirmed.items() if c == cat}
        path = DATA / f"{cat}.ndjson"
        if not want or not path.exists():
            continue
        tmp = path.with_suffix(".ndjson.tmp")
        hit = 0
        with open(path, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                wrote = False
                if b"TDK" in raw or b"EPCOS" in raw:
                    try:
                        rec = json.loads(raw)
                        o = rec
                        for k in keys:
                            o = o[k]
                        mi = o["manufacturerInfo"]
                        ref = ref_of(mi)
                    except Exception:                             # noqa: BLE001
                        ref = None
                    if ref in want:
                        mi["datasheetInfo"]["provenance"] = [{
                            "source": "manufacturerDatabase",
                            "sourceName": "TDK Meister product database (TstDB.tmdb, installed "
                                          "locally) — this part number confirmed present in "
                                          "TDK's own `part` table, read offline "
                                          "(electrical values not re-read)",
                            "retrievedDate": TODAY}]
                        out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                        wrote = True
                        hit += 1
                if not wrote:
                    out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        audit["recited"][cat] = hit
        print(f"  {cat:12} {hit} rows re-cited")
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, path)

    if dry:
        print("--dry-run: nothing replaced")
    else:
        audit["recited"] = dict(audit["recited"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
