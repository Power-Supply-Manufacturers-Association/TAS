#!/usr/bin/env python3
"""Write phase-2's verdicts into the records they are about (ABT #391).

    python3 scripts/promote_verified_provenance.py [--dry-run] [--map PATH]

248,741 rows still carry the marker

    [inferred from the record's own URL - this record was not verified against that source]

which relabel_url_inferred_provenance.py wrote when it discovered that a back-fill script
had invented those citations. At the time that sentence was exactly true. It no longer is
for most of them: phase 2 has since FETCHED each cited document and looked for the part in
it, and the answer is sitting in a scratchpad file rather than in the data.

    FAMILY_ONLY  189,588   the document covers this part's series
    FOUND         46,246   the document names this part outright
    ABSENT           401   the document was read and does NOT mention this part
    no verdict    12,506   the URL never resolved in phase 1, so nothing was fetched

Leaving it there means the corpus disclaims provenance it actually has, and - worse - says
the same thing about a citation that was checked and passed as about one that was checked
and FAILED. Those are three different states and they now read differently.

WHAT EACH VERDICT BECOMES, and the wording is the point:

  FOUND        "the cited document was fetched on <date> and names this part number"
  FAMILY_ONLY  "...was fetched and covers this part's series, but does not print this
                individual order code"
  ABSENT       "...was fetched and does NOT mention this part at any level" - a stronger
               negative than the marker it replaces. These rows are not unverified, they
               are DISPROVEN, and they need re-sourcing rather than re-checking.
  no verdict   left exactly as it was. Nothing was fetched, so nothing is known.

THE VALUES ARE NOT CLAIMED TO BE VERIFIED, and every entry says so. What phase 2
established is that the CITATION points at a document containing this part - not that the
capacitance in the row was read from it. The distinction matters enough that it survived
into every sourceName here, the same way it did in the ABT #451 citation repair. A
corrected or confirmed citation is not a re-derived record.

retrievedDate is restored, because there now IS a retrieval: the date phase 2 fetched the
document. The relabel pass removed it precisely because no fetch had happened.

FAMILY_ONLY IS NOT PROMOTED TO "VERIFIED" IN THE LOOSE SENSE. It is the honest majority
here (189,588 of 236,235) and it means the document is the right series datasheet without
listing the order code - which is how vendors publish most passive series. It is better
than unverified and weaker than FOUND, and flattening the two would overstate the
corpus's provenance quality by 4x, which is the failure this whole campaign exists to stop.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "promote_verified_provenance_audit.json"
TODAY = "2026-08-01"
DEFAULT_MAP = ("/tmp/claude-1000/-home-alf/3b0ca11a-b277-41ee-9b13-661c75a962cb/"
               "scratchpad/verdict_map.json")

MARKER = "[inferred from the record's own URL — this record was not verified against that source]"

NOT_REDERIVED = ("The stored VALUES were not re-derived from it; only the citation was checked.")

TEXT = {
    "FOUND": ("[citation verified {date}: the cited document was fetched and NAMES this part "
              "number{how}. " + NOT_REDERIVED + "]"),
    "FAMILY_ONLY": ("[citation verified {date}: the cited document was fetched and covers this "
                    "part's SERIES{how}, but does not print this individual order code. "
                    + NOT_REDERIVED + "]"),
    "ABSENT": ("[citation DISPROVEN {date}: the cited document was fetched and does not mention "
               "this part at any level — this row needs re-sourcing, not re-checking]"),
}

DISC = {"capacitors": ("capacitor",), "resistors": ("resistor",), "varistors": ("varistor",),
        "magnetics": ("magnetic",), "connectors": ("connector",), "controllers": ("controller",),
        "mosfets": ("semiconductor", "mosfet"), "diodes": ("semiconductor", "diode"),
        "igbts": ("semiconductor", "igbt"), "bjts": ("semiconductor", "bjt"),
        "analog_ics": ("analog",)}


def unwrap(rec, keys):
    o = rec
    for k in keys:
        o = o.get(k) if isinstance(o, dict) else None
        if o is None:
            return {}
    return o if isinstance(o, dict) else {}


def main(argv):
    dry = "--dry-run" in argv
    mp = Path(argv[argv.index("--map") + 1]) if "--map" in argv else Path(DEFAULT_MAP)
    verdicts = json.loads(mp.read_text())
    audit = {"ticket": "ABT #391", "date": TODAY, "byVerdict": Counter(),
             "byCatalogue": Counter(), "leftAlone": Counter(), "disproven": []}

    for cat, keys in DISC.items():
        path = DATA / f"{cat}.ndjson"
        if not path.exists():
            continue
        vmap = verdicts.get(cat, {})
        tmp = path.with_suffix(".ndjson.tmp")
        touched = 0
        with open(path, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                line = raw
                # Prefilter on a plain-ASCII fragment. MARKER contains an em-dash, which
                # most rows store ESCAPED as \u2014 while rows rewritten with
                # ensure_ascii=False store it literally — so matching MARKER's own bytes
                # finds only the second kind. It found 10,221 of 235,834 before this.
                if b"was not verified against that source" in raw:
                    try:
                        rec = json.loads(raw)
                        o = unwrap(rec, keys)
                        mi = o.get("manufacturerInfo") or {}
                        di = mi.get("datasheetInfo") or {}
                        prov = di.get("provenance") or []
                    except Exception:                             # noqa: BLE001
                        out.write(line)
                        continue
                    ref = str(mi.get("reference") or "")
                    got = vmap.get(ref)
                    if got and got["v"] in TEXT:
                        how = ""
                        if got["v"] in ("FOUND", "FAMILY_ONLY") and got.get("m"):
                            how = f" (matched as {got['m']})"
                        note = TEXT[got["v"]].format(date=TODAY, how=how)
                        changed = False
                        for p in prov:
                            sn = str(p.get("sourceName", ""))
                            if MARKER in sn:
                                p["sourceName"] = sn.replace(MARKER, note)
                                if got["v"] != "ABSENT":
                                    p["retrievedDate"] = TODAY
                                changed = True
                        if changed:
                            audit["byVerdict"][got["v"]] += 1
                            audit["byCatalogue"][cat] += 1
                            touched += 1
                            if got["v"] == "ABSENT":
                                audit["disproven"].append(
                                    {"catalogue": cat, "reference": ref, "url": got.get("u")})
                            line = json.dumps(rec, separators=(",", ":"),
                                              ensure_ascii=False).encode() + b"\n"
                    else:
                        audit["leftAlone"][cat] += 1
                out.write(line)
            out.flush()
            os.fsync(out.fileno())
        if touched:
            print(f"  {cat:12} {touched:7,} rows updated"
                  + (f", {audit['leftAlone'][cat]:,} left (no verdict)"
                     if audit["leftAlone"][cat] else ""))
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, path)

    print()
    for k, v in audit["byVerdict"].most_common():
        print(f"   {v:8,}  {k}")
    print(f"   {sum(audit['leftAlone'].values()):8,}  left as unverified (no phase-2 verdict)")
    if dry:
        print("\n--dry-run: nothing written")
    else:
        audit["byVerdict"] = dict(audit["byVerdict"])
        audit["byCatalogue"] = dict(audit["byCatalogue"])
        audit["leftAlone"] = dict(audit["leftAlone"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
