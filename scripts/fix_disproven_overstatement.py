#!/usr/bin/env python3
"""Not every ABSENT is a disproven citation — 102 are known matcher limits (ABT #391).

    python3 scripts/fix_disproven_overstatement.py [--dry-run]

promote_verified_provenance.py wrote, onto every row whose phase-2 verdict was ABSENT:

    [citation DISPROVEN <date>: the cited document was fetched and does not mention this
     part at any level — this row needs re-sourcing, not re-checking]

For 299 of the 401 that is exactly right. For the other 102 it is false, and checking the
rows against the ABT #391 classification is what showed it:

    126  TRUE_ABSENCE / not-named-at-any-level        disproven
     82  TRUE_ABSENCE / different-ordering-system     disproven
     69  WRONG_PART_CITATION / different-manufacturer disproven
      8  TRUE_ABSENCE / series-doc-omits-code         disproven
     14  WRONG_PART_CITATION / other                  disproven
    ---
    100  MATCHER_GAP / decoder-only                   NOT disproven
      1  MATCHER_GAP / family-attested-by-document    NOT disproven
      1  MATCHER_GAP / split-ordering-code            NOT disproven

The 100 decoder-only rows cite documents that DO describe them - KEMET's ordering tables
decode the code field by field (05 = 500 V | Series HV | Style/Size 10 | B = X7R | 103 |
K = +-10 % | N = Nickel) and simply never print the fields joined. That is a recorded
known-false-negative of the matcher, pinned as a test, and ABT #450 says explicitly it
needs a decision rather than a regex. Telling a reader those citations were disproven and
need re-sourcing would send them to re-source a correct citation, and would do it in the
authoritative voice of the provenance field.

05HV10B103KN is among them - the same row whose absence I asserted too strongly once
before, corrected in ABT #391, and very nearly asserted too strongly again here in the
opposite direction. The document decodes it; the matcher cannot assemble it. Those are
different sentences and the record now says the second one.

The 102 get language that states the limitation instead of a verdict about the data.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "disproven_overstatement_audit.json"
TODAY = "2026-08-01"
CLASSIFIED = ("/tmp/claude-1000/-home-alf/3b0ca11a-b277-41ee-9b13-661c75a962cb/"
              "scratchpad/absent_classified.jsonl")

OLD = ("[citation DISPROVEN 2026-08-01: the cited document was fetched and does not mention "
       "this part at any level — this row needs re-sourcing, not re-checking]")

NEW = ("[citation NOT CONFIRMABLE by the current matcher, {date}: the cited document was "
       "fetched and is this part's own datasheet, but it {why}, so no string form of the "
       "part number can be matched against it. This is a known matcher limitation "
       "(ABT #450), NOT evidence against the citation.]")

WHY = {
    "decoder-only": "decodes the order code field by field and never prints the fields joined",
    "family-attested-by-document": "attests the part's family in a form the matcher does not reach",
    "split-ordering-code": "prints the ordering code split across a column header and its rows",
}

DISC = {"capacitors": ("capacitor",), "resistors": ("resistor",), "varistors": ("varistor",),
        "magnetics": ("magnetic",), "connectors": ("connector",), "controllers": ("controller",),
        "mosfets": ("semiconductor", "mosfet"), "diodes": ("semiconductor", "diode"),
        "igbts": ("semiconductor", "igbt")}


def unwrap(rec, keys):
    o = rec
    for k in keys:
        o = o.get(k) if isinstance(o, dict) else None
        if o is None:
            return {}
    return o if isinstance(o, dict) else {}


def main(argv):
    dry = "--dry-run" in argv
    gaps = {}
    for line in open(CLASSIFIED, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:                                         # noqa: BLE001
            continue
        if r.get("klass") == "MATCHER_GAP" and r.get("subclass") in WHY:
            gaps[r["reference"]] = r["subclass"]
    print(f"known matcher-gap references: {len(gaps):,}")

    audit = {"ticket": "ABT #391 correction", "date": TODAY,
             "corrected": [], "bySubclass": Counter()}

    for cat, keys in DISC.items():
        path = DATA / f"{cat}.ndjson"
        if not path.exists():
            continue
        tmp = path.with_suffix(".ndjson.tmp")
        n = 0
        with open(path, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                line = raw
                if b"citation DISPROVEN" in raw:
                    try:
                        rec = json.loads(raw)
                        o = unwrap(rec, keys)
                        mi = o.get("manufacturerInfo") or {}
                        prov = (mi.get("datasheetInfo") or {}).get("provenance") or []
                    except Exception:                             # noqa: BLE001
                        out.write(line)
                        continue
                    ref = str(mi.get("reference") or "")
                    sub = gaps.get(ref)
                    if sub:
                        note = NEW.format(date=TODAY, why=WHY[sub])
                        hit = False
                        for p in prov:
                            sn = str(p.get("sourceName", ""))
                            if OLD in sn:
                                p["sourceName"] = sn.replace(OLD, note)
                                hit = True
                        if hit:
                            n += 1
                            audit["corrected"].append({"catalogue": cat, "reference": ref,
                                                       "subclass": sub})
                            audit["bySubclass"][sub] += 1
                            line = json.dumps(rec, separators=(",", ":"),
                                              ensure_ascii=False).encode() + b"\n"
                out.write(line)
            out.flush()
            os.fsync(out.fileno())
        if n:
            print(f"  {cat:12} {n:5} corrected")
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, path)

    print(f"\ntotal corrected: {len(audit['corrected'])}")
    for k, v in audit["bySubclass"].most_common():
        print(f"   {v:5}  {k}")
    if dry:
        print("--dry-run: nothing written")
    else:
        audit["bySubclass"] = dict(audit["bySubclass"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
