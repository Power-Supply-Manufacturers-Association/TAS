#!/usr/bin/env python3
"""Verify TDK rows whose code is a truncation of one TDK publishes (ABT #391).

    python3 scripts/verify_tdk_by_code_prefix.py [--dry-run] [--db PATH]

TDK's URLs are unreachable - phase 1 found 30,482 of them BLOCKED, and the block is one
this project earned by sending TDK 30,495 requests in a single sweep. The right response is
not a different IP; it is TDK's own licensed offline catalogue, which is already how 435
magnetics values were corrected and 294 varistors verified.

That left 2,629 TDK capacitor rows unmatched, and the reason turned out to be a code FORMAT
difference rather than missing parts:

    TDK   C0402 C0G 1C 010 C 020 BC     size | dielectric | voltage | cap | tol | thickness | packaging
    ours  C0402 C0G 0J 100 J 0

Ours is TDK's code truncated after the tolerance character, with a trailing "0" where TDK
carries thickness and packaging. So our code should be a PREFIX of a code TDK publishes,
and it is: C0402X7R0J101K0 -> C0402X7R0J101K020BC.

WHAT THE MATCH IS AND IS NOT. The prefix fixes size, dielectric, rated voltage, capacitance
and tolerance - the entire electrical specification. What it leaves open is thickness and
packaging, which do not change any electrical value. So a prefix match is sufficient
evidence that TDK publishes THIS ELECTRICAL PART, which is what the citation claims, and it
is NOT evidence about the row's physical dimensions. The provenance says exactly that.

A prefix is only accepted when every TDK code extending it agrees on the electrical
specification, which is guaranteed by construction here since the prefix contains all of
it. Where our code already carries a thickness (C2012X7R1H104K125AB) the match still runs
on the electrical prefix, because TDK stocks that electrical part in several thickness and
packaging variants and our row's own variant may be one Meister does not list.

AND THE HONEST RESULT IS THAT THIS ONLY REACHES 65 ROWS. TDK Meister carries 32,592 parts,
of which 4,408 are MLCCs with a decodable code. TDK's actual MLCC catalogue is far larger,
so 2,471 of our prefixes are simply not in the database - not mismatched, absent. The
offline route cannot verify them, and neither can the web route while the block stands.
That is a coverage fact worth recording so nobody retries it expecting a different answer.
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "tdk_prefix_verification_audit.json"
TODAY = "2026-08-01"
DEFAULT_DB = "/mnt/c/ProgramData/TDK/TDKMeister/tdkData/TstDB.tmdb"
MARKER = "[inferred from the record's own URL — this record was not verified against that source]"

# size | dielectric | voltage | capacitance | tolerance — the whole electrical spec
CODE = re.compile(r"^(C\d{4}[A-Z0-9]{3}[0-9][A-Z]\d{3}[A-Z])")

NOTE = ("[citation verified {date} against TDK's own catalogue database (TDK Meister TstDB.tmdb). "
        "Our code is TDK's truncated after the tolerance character; the shared prefix "
        "'{prefix}' fixes size, dielectric, rated voltage, capacitance and tolerance - the "
        "entire electrical specification - and TDK publishes it as {full}. This confirms the "
        "ELECTRICAL part exists in the manufacturer's catalogue; it says nothing about this "
        "row's thickness or packaging, and the stored VALUES were not re-derived. TDK's website "
        "could not be used: its URLs are BLOCKED, a block this project's own sweep caused.]")

DISC = {"capacitors": ("capacitor",), "magnetics": ("magnetic",), "varistors": ("varistor",)}


def tdk_index(db: Path):
    r = subprocess.run(["mdb-export", str(db), "part"], capture_output=True, text=True,
                       errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"mdb-export failed: {r.stderr[:200]}")
    idx = defaultdict(list)
    for row in csv.reader(r.stdout.splitlines()):
        if len(row) > 1 and row[0].isdigit():
            pn = row[1].strip('"')
            m = CODE.match(pn)
            if m:
                idx[m.group(1)].append(pn)
    return idx


def key_of(mi):
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
    idx = tdk_index(db)
    print(f"TDK codes with a decodable electrical prefix: "
          f"{sum(len(v) for v in idx.values()):,} in {len(idx):,} prefixes")

    audit = {"ticket": "ABT #391", "date": TODAY, "verified": [],
             "unreachable": Counter(), "byCatalogue": Counter()}

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
                        code = key_of(mi)
                        m = CODE.match(code)
                        hits = idx.get(m.group(1)) if m else None
                        if m and hits:
                            note = NOTE.format(date=TODAY, prefix=m.group(1), full=hits[0])
                            prov = (mi.get("datasheetInfo") or {}).get("provenance") or []
                            done = False
                            for p in prov:
                                sn = str(p.get("sourceName", ""))
                                if MARKER in sn:
                                    p["sourceName"] = sn.replace(MARKER, note)
                                    p["retrievedDate"] = TODAY
                                    done = True
                            if done:
                                n += 1
                                audit["verified"].append({"catalogue": cat, "code": code,
                                                          "prefix": m.group(1),
                                                          "tdkPublishes": hits[0]})
                                audit["byCatalogue"][cat] += 1
                                line = json.dumps(rec, separators=(",", ":"),
                                                  ensure_ascii=False).encode() + b"\n"
                        else:
                            audit["unreachable"][
                                f"{cat}: prefix absent from Meister" if m
                                else f"{cat}: code not decodable"] += 1
                out.write(line)
            out.flush()
            os.fsync(out.fileno())
        if n:
            print(f"  {cat:12} {n:5} verified by electrical prefix")
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, path)

    print(f"\nverified: {len(audit['verified'])}")
    print("still unreachable (TDK's database does not carry them, and its site is blocked):")
    for k, v in audit["unreachable"].most_common():
        print(f"   {v:6,}  {k}")
    if dry:
        print("--dry-run: nothing written")
    else:
        audit["unreachable"] = dict(audit["unreachable"])
        audit["byCatalogue"] = dict(audit["byCatalogue"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
