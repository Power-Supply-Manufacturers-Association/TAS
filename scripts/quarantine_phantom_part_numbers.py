#!/usr/bin/env python3
"""Quarantine part numbers their own manufacturer does not publish (ABT #460).

    python3 scripts/quarantine_phantom_part_numbers.py [--dry-run]

These 20 rows were refused by the ABT #451 citation repair because no correct document
could be written for them - and the reason turned out not to be a missing document but a
missing PART. Each was tested against a CONTROL that discriminates, never a bare failed
search, because "I could not find it" and "it does not exist" are different claims and this
campaign exists to keep them apart:

  Infineon, 5 - their own Coveo index (IFXGlobalSearchHub) returns 0 hits for IPB090N10N3,
  IPI40CN05S4, IPD50R380C6, IPP50R99C7 and IKM120R060M1H, while returning the product page
  for IPB090N06N3 in the SAME run - which is exactly why that one was repaired instead.
  "IPD50R380" returns only IPD50R380CE; IKM120R060M1H has no hit but IMZ120R060M1H - the
  part its bad citation pointed at - does.

  TI, 1 - CSD86320Q5D returns "Results 1-0 of 0" and ti.com/lit/gpn/CSD86320Q5D 404s. The
  cited SLPS223F is the datasheet for CSD86350Q5D.

  onsemi, 2 - their download endpoint serves a PDF for FPF2G120BF07AS and NVMFD5C466NL but
  redirects NVMFD7N06CL and NTHS4H080N065M2C to notFound. NTHS4H080N065M2C also carries
  series "NexFET", which is a TI trademark.

  Vishay, 1 - SS5* returns SS5N42, SS5NH102S, SS5P3..SS5P10 and MOSFETs; the cited document
  88751 covers SS32-SS36 only. SS54 is a generic 5 A / 40 V SMC Schottky sold by several
  makers, plausibly never a Vishay part.

  Wolfspeed, 1 - the GANxxx-650WSy naming is Nexperia's; Nexperia serves GAN041-650WSB and
  GAN063-650WSA but not GAN033-650WSP.

  Yageo, 8 - their COMPLETE resource library was pulled through their own graphql
  (getResourceLibraryAssetItemListing, 7,332 items). It contains exactly one anti-sulfurated
  array datasheet, PYU-AF122_124_162_164, whose size codes are 12 and 16. There is no
  AF102* or AF104* key in Yageo's document index at all.

  Bourns, 2 - SRP2512A-4R7M has no counterpart in SRP2512.pdf, whose series runs 0.47 to
  2.2 uH (the other five SRP2512A rows WERE repaired, being SRP2512-* under a spurious
  series letter). And "SRP2512A" with no suffix is a series stub rather than an orderable
  part: no datasheetUrl, and an inductance of nominal 4.7 uH with maximum 0.47 H - the
  datasheet's 0.47 microhenries stored as henries.

WHY QUARANTINE RATHER THAN STRIP THE CITATION. Removing the false citation would leave a
row that still claims to be a part, now with no provenance at all, and would lose the trail
that leads back to what it actually is. Quarantining keeps the row, its values and the
evidence together and is reversible: if any of these turns out to be real - a vendor index
can be incomplete - the record comes back intact with the reason that removed it.

WHY NOT RE-IDENTIFY. For several the likely real part is visible (IKM120R060M1H is probably
IMZ120R060M1H; SS54 is probably another vendor's). Rewriting the reference would assert
that the stored VALUES describe that other part, which nothing here establishes - it is a
re-identification, a different and larger claim than a citation fix. Left for whoever has
the vendor data to do it properly.

NOT INCLUDED, on purpose: 561KN20-P12.5 and 751KN20-P12.5. Their cited Yageo TMOV 20M(E,N)
sheet IS their datasheet - it prints "561KM(E,N)20", one row standing for the M, E and N
variants. That is the decoder-only matcher gap recorded on ABT #391, not a phantom part.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "phantom_part_numbers_audit.json"
TODAY = "2026-08-01"
SUFFIX = "quarantine_not_in_manufacturer_catalogue"

DISC = {"mosfets": ("semiconductor", "mosfet"), "diodes": ("semiconductor", "diode"),
        "resistors": ("resistor",), "magnetics": ("magnetic",)}

# catalogue -> {reference: evidence}
TARGETS = {
    "mosfets": {
        "IPB090N10N3": "Infineon's Coveo index returns 0 hits; it returns the product page for "
                       "IPB090N06N3 in the same run",
        "IPI40CN05S4": "Infineon's Coveo index returns 0 hits for the part and for the stem "
                       "IPI40CN05; /part/ 404s",
        "IPD50R380C6": "Infineon's Coveo index returns 0 hits; the stem IPD50R380 returns only "
                       "IPD50R380CE",
        "IPP50R99C7": "Infineon's Coveo index returns 0 hits for the part or the stem IPP50R99",
        "IKM120R060M1H": "Infineon's Coveo index returns 0 hits; IMZ120R060M1H - the part the "
                         "cited document is for - does return",
        "CSD86320Q5D": "TI's site search returns 'Results 1-0 of 0' and ti.com/lit/gpn 404s; the "
                       "cited SLPS223F is CSD86350Q5D's datasheet",
        "NVMFD7N06CL": "onsemi's endpoint serves a PDF for FPF2G120BF07AS and NVMFD5C466NL but "
                       "redirects this one to notFound",
        "NTHS4H080N065M2C": "onsemi redirects to notFound; the record also carries series "
                            "'NexFET', a TI trademark",
        "GAN033-650WSP": "the GANxxx-650WSy naming is Nexperia's, not Wolfspeed's, and Nexperia "
                         "does not list this code either",
    },
    "diodes": {
        "SS54": "Vishay's part search returns SS5N42, SS5NH102S, SS5P3..SS5P10 and MOSFETs but no "
                "SS54; the cited doc 88751 covers SS32-SS36",
    },
    "resistors": {r: "Yageo's complete resource library (7,332 items via their own graphql) has no "
                     "AF102*/AF104* key; the only anti-sulfurated array sheet is "
                     "PYU-AF122_124_162_164, size codes 12 and 16"
                  for r in ["AF102MJR-0715RL", "AF102MJR-0722RL", "AF102MJR-0730RL",
                            "AF102MJR-0733RL", "AF102MJR-0743RL", "AF102MJR-0747RL",
                            "AF104MJR-0736RL", "AF104MJR-0739RL"]},
    "magnetics": {
        "SRP2512A-4R7M": "Bourns' SRP2512.pdf has no 4.7 uH part; the series runs 0.47 to 2.2 uH "
                         "(the other five SRP2512A rows were repaired as SRP2512-*)",
        "SRP2512A": "series stub, not an orderable part: no datasheetUrl and an inductance maximum "
                    "of 0.47 H against a 4.7 uH nominal (0.47 uH stored as henries)",
    },
}

REASON = ("part number does not appear in its own manufacturer's catalogue; its citation pointed "
          "at a genuine datasheet for a different part (ABT #460)")


def unwrap(rec, keys):
    o = rec
    for k in keys:
        o = o.get(k) or {}
    return o


def main(argv):
    dry = "--dry-run" in argv
    audit = {"ticket": "ABT #460", "date": TODAY, "quarantined": [],
             "byCatalogue": Counter(), "notFound": []}

    for cat, refs in TARGETS.items():
        path = DATA / f"{cat}.ndjson"
        qpath = DATA / f"{cat}.{SUFFIX}.ndjson"
        if not path.exists():
            continue
        tmp = path.with_suffix(".ndjson.tmp")
        moved = []
        seen = set()
        with open(path, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                keep = True
                try:
                    rec = json.loads(raw)
                    o = unwrap(rec, DISC[cat])
                    ref = str((o.get("manufacturerInfo") or {}).get("reference") or "")
                except Exception:                                 # noqa: BLE001
                    ref = ""
                if ref in refs:
                    seen.add(ref)
                    rec["_validatorQuarantine"] = {
                        "date": TODAY, "reason": REASON, "ticket": "ABT #460",
                        "evidence": refs[ref]}
                    moved.append(rec)
                    keep = False
                if keep:
                    out.write(raw)
            out.flush()
            os.fsync(out.fileno())

        missing = sorted(set(refs) - seen)
        audit["notFound"].extend(f"{cat}:{m}" for m in missing)
        for rec in moved:
            o = unwrap(rec, DISC[cat])
            audit["quarantined"].append(
                {"catalogue": cat,
                 "reference": (o.get("manufacturerInfo") or {}).get("reference"),
                 "manufacturer": (o.get("manufacturerInfo") or {}).get("name"),
                 "evidence": rec["_validatorQuarantine"]["evidence"]})
        audit["byCatalogue"][cat] = len(moved)
        print(f"  {cat:11} quarantined {len(moved)}"
              + (f"   NOT FOUND: {missing}" if missing else ""))
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            with open(qpath, "ab") as q:
                for rec in moved:
                    q.write(json.dumps(rec, separators=(",", ":"),
                                       ensure_ascii=False).encode() + b"\n")
                q.flush()
                os.fsync(q.fileno())
            os.replace(tmp, path)

    print(f"\ntotal quarantined: {len(audit['quarantined'])}")
    if dry:
        print("--dry-run: nothing written")
    else:
        audit["byCatalogue"] = dict(audit["byCatalogue"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
