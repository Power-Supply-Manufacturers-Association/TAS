#!/usr/bin/env python3
"""Point Vishay inductors at Vishay's documents instead of other vendors' (ABT #451).

    python3 scripts/recite_vishay_magnetics_from_vendor.py [--dry-run]

The ABT #391 classification found 83 magnetics rows whose citation resolves to a real
datasheet for a DIFFERENT part, 56 of them Vishay/Dale inductors citing ABRACON, BOURNS
and WUERTH documents. IHTH0750JZEB220M5A cited abracon.com/Magnetics/radial/AIRD01.pdf;
IMC0603/IMC1008 rows cited Bourns CW252016.pdf and Wuerth 744762xxx.pdf. Those documents
are genuine - they are simply somebody else's.

52 of the 56 are re-cited here to the Vishay document that covers them, at one of two
confidence levels, and the provenance says which:

  EXACT, 38 rows - the document prints the order code verbatim.
      IMC-1008     doc 34041   32 rows
      IMC-0805-01  doc 34115    6 rows

  SERIES, 14 rows - the right series AND variant datasheet, which names the series but
  not each order code, so this is family-level provenance and is described as such.
      IMC-0603-01     doc 34164   8 rows
      IHLP-4040DZ-A1  doc 34245   3 rows
      IHLP-4040DZ-1A  doc 34246   2 rows
      IHLP-2525BD-A1  doc 34240   1 row

THE VARIANT SUFFIX IS THE WHOLE POINT OF THE SERIES MATCH. Vishay publishes IHLP-4040DZ as
-01, -11, -51, -1A, -A1, -5A, -8A, -1L, -5L ... each its own document with its own ratings.
A first attempt matched on the base "IHLP4040DZ" alone and handed all six rows the -5L
datasheet - which would have replaced a citation to the wrong VENDOR with a citation to the
wrong VARIANT, the same defect wearing a better disguise. A part is only matched to a
document whose variant suffix it also ends with: IHLP4040DZER2R2M**A1** -> IHLP-4040DZ-**A1**,
IHLP4040DZERR56M**1A** -> IHLP-4040DZ-**1A**. Where that leaves more than one candidate, the
row is left alone rather than assigned to one of them.

FOUR ROWS ARE NOT RE-CITED, because no Vishay document was found that covers them:
IHTH0750JZEB220M5A, IHLP4040DZRZ2R2ML1 (suffix L1; Vishay publishes 1L, not L1, so the
reference itself may be wrong), ILC0402ER3N3S and ILC0402ER3N9S. They keep their present
citation - which is wrong - because replacing a known-wrong citation with a guess is worse
than leaving one that is visibly wrong and recorded.

The 27 non-Vishay rows of the same class are out of scope here and reported in the audit:
Sumida, Murata, Murata Power Solutions, Wuerth, YAGEO, Taiyo Yuden, and 8 Bourns rows that
are not our error at all - bourns.com serves srp2512a.pdf containing only SRP2510A parts.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "vishay_magnetics_recitation_audit.json"
TODAY = "2026-08-01"

DOC = "https://www.vishay.com/doc?{}"

# doc id -> (title, sha256 of the PDF read, level, references)
DOCS = {
    "34041": ("IMC-1008", "14992f3ede1ae20cbec25f293df93044a9c58c3d6a60e476ac19df89ab7d91b1", "exact"),
    "34115": ("IMC-0805-01", "487c157da9301a7b3f4d942ae1939d068dc48eb03ad597057bfd4737da425efc", "exact"),
    "34164": ("IMC-0603-01", "f97fe856e4a54f61a1038b4bc5e37b034a7eb7c159720924e060700fec7963e6", "series"),
    "34245": ("IHLP-4040DZ-A1", "7b7a0f6c1b3f1df94b8c8fa5d8131f14c714bca524564e2abf437c2dffeeb1a6", "series"),
    "34246": ("IHLP-4040DZ-1A", "3e4093be46a831a0e1a93709014d90625d93aa35c99b2daea826b990e6df569b", "series"),
    "34240": ("IHLP-2525BD-A1", "8f488c98978774dd8de03b37e4c9493d1678ed86a4d4cdf8a7e427594bf14e59", "series"),
}

MAP_FILE = Path("/tmp/claude-1000/-home-alf/3b0ca11a-b277-41ee-9b13-661c75a962cb/"
                "scratchpad/vishay_recite_map.json")


def build_map():
    m = json.loads(MAP_FILE.read_text())
    out = {}
    for doc, refs in m.get("exact", {}).items():
        for r in refs:
            out[r] = (doc, "exact")
    for doc, refs in m.get("family", {}).items():
        for r in refs:
            out[r] = (doc, "series")
    return out, m.get("miss", [])


def sourcename(doc, level):
    title, sha, _ = DOCS[doc]
    if level == "exact":
        how = ("the 'Standard Electrical Specifications' table names this part number "
               "verbatim")
    else:
        how = ("the datasheet for this exact series AND variant; it specifies the series "
               "and does not print each order code individually, so this is family-level "
               "provenance")
    return (f"Vishay document {doc}, '{title}': {how}. Replaces a citation to another "
            f"manufacturer's datasheet (ABT #451). PDF sha256 {sha}")


def main(argv):
    dry = "--dry-run" in argv
    mapping, unresolved = build_map()
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #451 (magnetics subset)", "date": TODAY,
             "recited": [], "byDoc": Counter(), "byLevel": Counter(),
             "notRecitedNoVishayDocument": unresolved}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"ishay" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["magnetic"]["manufacturerInfo"]
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                ref = str(mi.get("reference") or "")
                got = mapping.get(ref)
                if got and "Vishay" in str(mi.get("name")):
                    doc, level = got
                    url = DOC.format(doc)
                    di = mi.setdefault("datasheetInfo", {})
                    old = mi.get("datasheetUrl")
                    prov = [p for p in (di.get("provenance") or [])
                            if p.get("sourceUrl") != url]
                    prov.append({"source": "manufacturerDatasheet", "sourceUrl": url,
                                 "sourceName": sourcename(doc, level),
                                 "retrievedDate": TODAY})
                    di["provenance"] = prov
                    mi["datasheetUrl"] = url
                    audit["recited"].append({"reference": ref, "document": doc,
                                             "title": DOCS[doc][0], "level": level,
                                             "wasCiting": old})
                    audit["byDoc"][f"{doc} {DOCS[doc][0]}"] += 1
                    audit["byLevel"][level] += 1
                    line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows re-cited to a Vishay document: {len(audit['recited'])}")
    for k, v in audit["byLevel"].most_common():
        print(f"     {v:4}  {k}")
    for k, v in audit["byDoc"].most_common():
        print(f"       {v:4}  {k}")
    print(f"left alone (no Vishay document found): {len(unresolved)}  {unresolved}")
    for r in audit["recited"][:3]:
        print(f"       {r['reference']:22} {str(r['wasCiting'])[:44]:44} -> doc {r['document']}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        audit["byDoc"] = dict(audit["byDoc"])
        audit["byLevel"] = dict(audit["byLevel"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
