#!/usr/bin/env python3
"""Remove fields that assert a physically impossible zero (ABT #391).

    python3 scripts/drop_impossible_zeros.py [--dry-run]

A stored 0 is a CLAIM, and for these fields it is a claim that cannot be true:

    rippleCurrent          an electrolytic rated for 0 A of ripple is not a part
    leakageCurrent         every real capacitor leaks something
    esr                    zero equivalent series resistance is superconduction
    dcResistance           a winding with no resistance
    selfResonantFrequency  a part that never resonates
    saturationCurrentPeak  a core that saturates at no current

5,923 rows carry one. They are placeholder zeros — an importer with no value writing
0 rather than leaving the field out — and they are the same fault as the Abracon
width=0 and Panasonic length=0 of ABT #386, and as the leakageCurrent=0 that helped
expose the fabricated Murata batch. The field is REMOVED rather than corrected: a
gap is honest and a zero is not, and nothing here knows the real value.

0 OHM RESISTORS ARE EXCLUDED, and are the reason this script has a whitelist at all.
108 resistor rows store resistance = 0 and every one is a genuine zero-ohm jumper —
YC162-JR-070RL, AR1206JR-070RL, where the "0R" in the part number means exactly that.
Yageo and others sell them by the reel. Sweeping "impossible zeros" without checking
would have destroyed 108 correct records, which is the failure mode this whole ticket
exists to prevent: a rule that is right in general is still wrong on the cases it was
never checked against.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "impossible_zeros_audit.json"
TODAY = "2026-07-31"

FILES = {
    "capacitors": ("capacitor",), "magnetics": ("magnetic",),
    "resistors": ("resistor",), "varistors": ("varistor",),
    "mosfets": ("semiconductor", "mosfet"), "diodes": ("semiconductor", "diode"),
    "igbts": ("semiconductor", "igbt"), "controllers": ("controller",),
}

# NOT "resistance": 0 ohm jumpers are a real product.
DROP = ["rippleCurrent", "leakageCurrent", "esr", "dcResistance",
        "selfResonantFrequency", "saturationCurrentPeak"]

NOTE = ("field removed 2026-07-31: it stored a physically impossible 0, which is a "
        "placeholder an importer wrote in place of a value it did not have. A gap is "
        "honest; a zero is a false measurement. (ABT #391)")


def is_zero(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return v == 0
    if isinstance(v, dict):
        vals = [v.get(k) for k in ("nominal", "maximum", "minimum")]
        vals = [x for x in vals if isinstance(x, (int, float)) and not isinstance(x, bool)]
        return bool(vals) and all(x == 0 for x in vals)
    if isinstance(v, list) and v:
        return is_zero(v[0])
    return False


def main(argv):
    dry = "--dry-run" in argv
    audit = {"ticket": "ABT #391 (impossible zeros)", "date": TODAY,
             "note": NOTE, "dropped": Counter(), "rowsTouched": Counter(),
             "excluded": "resistance (0 ohm jumpers are real: YC162-JR-070RL etc.)"}

    for fname, keys in FILES.items():
        path = DATA / f"{fname}.ndjson"
        if not path.exists():
            continue
        tmp = path.with_suffix(".ndjson.tmp")
        touched = 0
        with open(path, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                line = raw
                if b"0" in raw:
                    try:
                        rec = json.loads(raw)
                        o = rec
                        for k in keys:
                            o = o[k]
                        di = o["manufacturerInfo"]["datasheetInfo"]
                    except Exception:                             # noqa: BLE001
                        out.write(line)
                        continue
                    el = di.get("electrical")
                    points = el if isinstance(el, list) else ([el] if isinstance(el, dict) else [])
                    changed = False
                    for pt in points:
                        if not isinstance(pt, dict):
                            continue
                        for f in DROP:
                            if f in pt and is_zero(pt[f]):
                                pt.pop(f)
                                audit["dropped"][f] += 1
                                changed = True
                    if changed:
                        touched += 1
                        line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
                out.write(line)
            out.flush()
            os.fsync(out.fileno())
        audit["rowsTouched"][fname] = touched
        if touched:
            print(f"  {fname:12} {touched} rows cleaned")
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, path)

    print(f"\nrows touched: {sum(audit['rowsTouched'].values())}")
    print("fields dropped:", dict(audit["dropped"]))
    if dry:
        print("--dry-run: nothing written")
    else:
        audit["dropped"] = dict(audit["dropped"])
        audit["rowsTouched"] = dict(audit["rowsTouched"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
