#!/usr/bin/env python3
"""ABT #287: repair the four iNRCORE R810x-NL ratedCurrents from the DATASHEET.

Per the approved decision the values come from the source, not from dividing the
Pulse twins by 100. Verified against the iNRCORE R81xx-NL series datasheet
(https://iNRCORE.com/download/10685/), table "Part | Inductance per Winding |
Irated | DCR per winding":

    R8100NL  470 uH  14.0 A   8 mOhm
    R8101NL  630 uH  11.6 A  10 mOhm
    R8102NL  810 uH   9.70 A 14 mOhm
    R8103NL  534 uH   7.20 A 15 mOhm

ROOT CAUSE (worth recording): iNRCORE's own product pages render these as
"1160%" / "970%" / "720%" -- the decimal dropped and the unit mangled to a percent
sign by their CMS. The scrape ingested the vendor's corrupt value faithfully, which
is why the stored figures are exactly 100x. R8104-R8109NL were unaffected and their
stored values already match the datasheet exactly.

Also captures the rating BASIS, which the datasheet states and which ABT #251 asks
for: "The current rating (irated) is based upon the temperature rise ... typical
temperature rise of 55C with 50LFM forced cooling." -> ratedCurrentPoints[] with
temperatureRise = 55.

Line-patch; every touched record is validated against MAS magnetic.json before the
file is atomically replaced.

Usage: fix_287_inrcore_rated_currents.py [--apply]
"""
import argparse
import json
import os
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

PSMA = Path.home() / "PSMA"
SRC = PSMA / "TAS" / "data" / "magnetics.ndjson"

DATASHEET = "https://iNRCORE.com/download/10685/"
TEMP_RISE = 55.0                     # K, stated basis for Irated
TRUE = {                             # part -> (ratedCurrent A, inductance H, dcr Ohm)
    "R8100NL": (14.0, 470e-6, 0.008),
    "R8101NL": (11.6, 630e-6, 0.010),
    "R8102NL": (9.70, 810e-6, 0.014),
    "R8103NL": (7.20, 534e-6, 0.015),
}


def build_validator():
    by_id = {}
    for repo in ("PEAS", "MAS"):
        d = PSMA / repo / "schemas"
        for p in d.rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by_id[s["$id"]] = s
    res = [Resource(contents=s, specification=DRAFT202012) for s in by_id.values()]
    reg = Registry().with_resources([(r.contents["$id"], r) for r in res])
    return Draft202012Validator(
        json.loads((PSMA / "MAS" / "schemas" / "magnetic.json").read_text()), registry=reg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    v = build_validator()
    out, patched, rejected = [], [], []

    with SRC.open(encoding="utf-8") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            if not any(k in s for k in TRUE):
                out.append(s)
                continue
            obj = json.loads(s)
            mag = obj.get("magnetic") or {}
            mi = mag.get("manufacturerInfo") or {}
            ref = mi.get("reference")
            if ref not in TRUE:
                out.append(s)
                continue

            amps, _L, _dcr = TRUE[ref]
            ds = mi.setdefault("datasheetInfo", {})
            el = ds.get("electrical") or []
            before = None
            for e in el:
                if not isinstance(e, dict):
                    continue
                before = e.get("ratedCurrents")
                e["ratedCurrents"] = [amps]
                # ABT #251: the datasheet states the dT basis for Irated
                e["ratedCurrentPoints"] = [{"current": amps, "temperatureRise": TEMP_RISE}]
            mi.setdefault("datasheetUrl", DATASHEET)
            ds.setdefault("provenance", []).append({
                "source": "manufacturerDatasheet",
                "sourceName": "iNRCORE R81xx-NL series datasheet",
                "sourceUrl": DATASHEET,
                "retrievedDate": "2026-07-28",
            })

            errs = sorted(v.iter_errors(mag), key=lambda e: e.path)
            if errs:
                rejected.append(f"{ref}: {errs[0].message[:160]}")
                out.append(s)                      # leave original untouched
                continue
            patched.append(f"{ref}: {before} -> [{amps}] A  (+ratedCurrentPoints dT={TEMP_RISE}K)")
            out.append(json.dumps(obj, ensure_ascii=False))

    print("DRY RUN — nothing written" if not a.apply else "APPLIED")
    for p in patched:
        print(f"  {p}")
    for r in rejected:
        print(f"  REJECTED (left unpatched): {r}")
    print(f"\npatched {len(patched)}, rejected {len(rejected)}")

    if not a.apply:
        print("Re-run with --apply to write.")
        return 0
    tmp = SRC.with_suffix(".ndjson.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for line in out:
            fh.write(line + "\n")
    os.replace(tmp, SRC)
    print(f"atomically replaced {SRC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
