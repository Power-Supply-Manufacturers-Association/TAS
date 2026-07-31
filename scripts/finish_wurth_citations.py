#!/usr/bin/env python3
"""Finish the Würth pass: re-cite what exists, quarantine what does not (ABT #391).

    python3 scripts/finish_wurth_citations.py MISS_PROBE.json [--dry-run]

resource_wurth_citations.py confirmed 2,828 of 3,055 cited Würth parts against
REDEXPERT and re-cited them. This handles the 227 it could not confirm.

REDEXPERT IS NOT A COMPLETE CATALOGUE, so absence from it proves nothing on its own —
it is Würth's SIMULATION tool and carries 13,182 order codes, far fewer than Würth
sells. Every one of the 227 was therefore probed individually against Würth's
datasheet endpoint, and a control run first established that the endpoint actually
discriminates: 20 of 20 REDEXPERT-confirmed parts return a PDF there, against 1 of 20
of the unconfirmed ones.

    15 return a real PDF          -> RE-CITED to that datasheet. Real parts that
                                     REDEXPERT simply does not model.
    212 return 404                -> QUARANTINED. Not in Würth's catalogue, and no
                                     datasheet at Würth's own datasheet endpoint,
                                     which serves one for every part known to exist.

Those 212 are quarantined rather than deleted, and the reason records both misses, so
a later reader can re-test the claim rather than take it on trust. If Würth restores
a datasheet the row can come straight back.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "wurth_finish_audit.json"
TODAY = "2026-07-31"

PATHS = {"capacitors": ("capacitor",), "magnetics": ("magnetic",),
         "resistors": ("resistor",)}

REASON = (
    "not found at the manufacturer - this Würth order code is absent from Würth's own "
    "REDEXPERT catalogue (13,182 order codes pulled in full across all 57 modules) AND "
    "its datasheet endpoint returns 404. That endpoint discriminates: in a control of "
    "20 REDEXPERT-confirmed parts it served a PDF for 20/20, against 1/20 for the "
    "unconfirmed group. The record's original citation was already dead (ABT #391 "
    "phase-1 verification found all 3,055 Würth citations unreachable, 2,961 of them "
    "pointing at the retired /redexpert/spec/ URL form). Quarantined rather than "
    "deleted: if Würth restores a datasheet for this code the row can be reinstated."
)
CODES = ["GEN_NOT_AT_MANUFACTURER"]


def ref_of(mi):
    r = mi.get("reference")
    if r:
        return str(r)
    part = (mi.get("datasheetInfo") or {}).get("part") or {}
    p = part.get("partNumber")
    return str(p) if p else None


def main(argv):
    dry = "--dry-run" in argv
    probe = json.loads(Path(argv[0]).read_text())
    recite = {str(r["reference"]): r for r in probe["real"]}
    drop = {str(r["reference"]): r for r in probe["gone"]}
    print(f"{len(recite)} to re-cite, {len(drop)} to quarantine")

    audit = {"ticket": "ABT #391 (Würth finish)", "date": TODAY, "reason": REASON,
             "recited": Counter(), "quarantined": Counter(), "quarantinedRefs": []}

    for cat, keys in PATHS.items():
        path = DATA / f"{cat}.ndjson"
        if not path.exists():
            continue
        want_r = {k for k, v in recite.items() if v["catalogue"] == cat}
        want_d = {k for k, v in drop.items() if v["catalogue"] == cat}
        if not (want_r or want_d):
            continue
        quar = DATA / f"{cat}.quarantine_fabricated.ndjson"
        tmp = path.with_suffix(".ndjson.tmp")
        taken = []
        n_recited = 0
        with open(path, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                keep = True
                if b"rth" in raw:
                    try:
                        rec = json.loads(raw)
                        o = rec
                        for k in keys:
                            o = o[k]
                        mi = o["manufacturerInfo"]
                        ref = ref_of(mi)
                    except Exception:                             # noqa: BLE001
                        ref = None
                    if ref in want_r:
                        mi["datasheetUrl"] = recite[ref]["url"]
                        mi["datasheetInfo"]["provenance"] = [{
                            "source": "manufacturerDatasheet",
                            "sourceName": "Würth datasheet endpoint — fetched and confirmed to "
                                          "serve a PDF for this order code; the part is real but "
                                          "is not modelled in REDEXPERT "
                                          "(electrical values not re-read)",
                            "sourceUrl": recite[ref]["url"],
                            "retrievedDate": TODAY}]
                        out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                        keep = False
                        n_recited += 1
                    elif ref in want_d:
                        rec["_validatorQuarantine"] = {
                            "date": TODAY, "reason": REASON, "codes": CODES,
                            "messages": [f"order code {ref} absent from REDEXPERT and its "
                                         f"datasheet endpoint returns 404"]}
                        taken.append(json.dumps(rec, separators=(",", ":")))
                        keep = False
                if keep:
                    out.write(raw)
            out.flush()
            os.fsync(out.fileno())

        audit["recited"][cat] = n_recited
        audit["quarantined"][cat] = len(taken)
        audit["quarantinedRefs"].extend(sorted(want_d)[:300])
        print(f"  {cat:12} re-cited {n_recited:4}   quarantined {len(taken):4}")
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            if taken:
                with open(quar, "a", encoding="utf-8") as q:
                    for line in taken:
                        q.write(line + "\n")
                    q.flush()
                    os.fsync(q.fileno())
            os.replace(tmp, path)

    if dry:
        print("--dry-run: nothing written")
    else:
        audit["recited"] = dict(audit["recited"])
        audit["quarantined"] = dict(audit["quarantined"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
