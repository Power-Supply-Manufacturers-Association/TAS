#!/usr/bin/env python3
"""Remove winding resistances stored as exactly zero (ABT #387 follow-on).

    python3 scripts/drop_zero_winding_resistance.py [--dry-run]

Found while triaging the findings ABT #387 un-hid: 22 of the 48 were Pulse Electronics
chokes reported as "DCR*size^2/L suspiciously low (value=0)". The reason is literal - the
record says the winding has 0 ohm of resistance:

    PE-0805CCMC261STS   dcResistances: [{"nominal": 0.0}]

A winding is a length of wire. Zero resistance is not a small resistance, it is a claim
that the part is a superconductor, and it silently poisons anything downstream that
divides by it or sums a loss term with it - a copper-loss figure of exactly zero looks
like a great part rather than a missing measurement.

44 entries across the corpus, 26 Pulse Electronics and 18 iNRCORE.

DELETING THE FIELD IS THE FIX, not substituting a plausible resistance. We do not know
what these windings measure, and a made-up milliohm figure would be indistinguishable
from data. An absent field is honest and every consumer already handles absence, because
most of the catalogue has no DCR at all. This mirrors drop_impossible_zeros.py, which
made the same choice for other fields.

WHY THIS DOES NOT TOUCH `resistance`. drop_impossible_zeros.py deliberately excludes the
generic `resistance` field, because a 0 ohm resistor is a real orderable product - the
jumper. That reasoning does not extend here: `dcResistance`/`dcResistances` describe the
winding of a wound component, and no wound component has zero winding resistance. Both
the singular and plural shapes are read, since ABT #387 was caused by code that saw only
one of them.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "zero_winding_resistance_audit.json"
TODAY = "2026-08-01"

NOTE = ("winding resistance stored as exactly 0 ohm was removed - a winding cannot have zero "
        "resistance, and no replacement value was invented (ABT #387 follow-on)")


def variants(di):
    el = di.get("electrical")
    return el if isinstance(el, list) else ([el] if el else [])


def is_zero(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #387 follow-on", "date": TODAY, "note": NOTE,
             "removed": [], "byManufacturer": Counter()}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b'"dcResistance' in raw and b"0" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["magnetic"]["manufacturerInfo"]
                    di = mi.get("datasheetInfo") or {}
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                hit = []
                for e in variants(di):
                    if not isinstance(e, dict):
                        continue
                    plural = e.get("dcResistances")
                    if isinstance(plural, list):
                        kept = []
                        for entry in plural:
                            if isinstance(entry, dict):
                                for k in ("nominal", "minimum", "maximum"):
                                    if is_zero(entry.get(k)):
                                        hit.append(f"dcResistances[].{k}")
                                        entry.pop(k)
                                if not entry:
                                    continue
                            elif is_zero(entry):
                                hit.append("dcResistances[]")
                                continue
                            kept.append(entry)
                        if kept:
                            e["dcResistances"] = kept
                        else:
                            e.pop("dcResistances")
                    single = e.get("dcResistance")
                    if isinstance(single, dict):
                        for k in ("nominal", "minimum", "maximum"):
                            if is_zero(single.get(k)):
                                hit.append(f"dcResistance.{k}")
                                single.pop(k)
                        if not single:
                            e.pop("dcResistance")
                    elif is_zero(single):
                        hit.append("dcResistance")
                        e.pop("dcResistance")
                if hit:
                    audit["removed"].append({"reference": mi.get("reference"),
                                             "manufacturer": mi.get("name"), "fields": hit})
                    audit["byManufacturer"][str(mi.get("name"))] += 1
                    line = json.dumps(rec, separators=(",", ":"),
                                      ensure_ascii=False).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows with a zero winding resistance removed: {len(audit['removed'])}")
    for k, v in audit["byManufacturer"].most_common():
        print(f"     {v:4}  {k}")
    for r in audit["removed"][:4]:
        print(f"       {str(r['reference'])[:26]:26} {r['fields']}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        audit["byManufacturer"] = dict(audit["byManufacturer"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
