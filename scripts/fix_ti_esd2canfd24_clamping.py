#!/usr/bin/env python3
"""One TVS row, two fields, both confirmed against TI's datasheet (ABT #465).

    python3 scripts/fix_ti_esd2canfd24_clamping.py [--dry-run]

Blade Runner reports DIO_TVS_ORDERING IMPOSSIBLE on ESD2CANFD24: standoffVoltage 24 is not
less than clampingVoltage 24. A TVS whose clamping voltage equals its stand-off voltage
would conduct at its own working voltage.

TI's datasheet (ti.com/lit/ds/symlink/esd2canfd24.pdf, fetched 2026-08-01) gives:

    VRWM    Reverse stand-off voltage                          -24 ... 24 V
    VBRF    Breakdown voltage, IIO = 10 mA                    25.5 ... 35.5 V
    VCLAMP  Clamping voltage, IPP = 3.5 A, tp = 8/20 us              37 V
    VCLAMP  Clamping voltage, IPP = 16 A, TLP                        36 V

The stand-off is right. The clamping voltage is not 24 - it is 37 V at the 8/20 us pulse,
and the row already stores peakPulseCurrent 3.5, which is exactly the current that figure
is specified at. So the correct clamping value was determinable from the row's own
companion field.

AND THE 36 V WAS ALREADY IN THE RECORD, IN THE WRONG FIELD. The row carries
forwardVoltage 36.0. A TVS diode's forward voltage is a diode drop, around 0.9 V; 36 is
TI's TLP clamping figure. The sourcing pass put a clamping voltage into forwardVoltage and
a stand-off voltage into clampingVoltage, so both errors are one mistake seen twice.

forwardVoltage is REMOVED rather than set to a textbook 0.9 V: TI's datasheet does not
state a forward voltage for this part in the table read, and substituting a plausible diode
drop would be exactly the invention this campaign exists to remove. Absent is the honest
state.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "diodes.ndjson"
AUDIT = REPO / "staging" / "ti_esd2canfd24_audit.json"
TODAY = "2026-08-01"

REF = "ESD2CANFD24"
URL = "https://www.ti.com/lit/ds/symlink/esd2canfd24.pdf"
CLAMP = 37.0

NOTE = ("TI datasheet ESD2CANFD24, Electrical Characteristics: VRWM stand-off 24 V, VCLAMP 37 V "
        "at IPP = 3.5 A / 8-20 us (the row's own peakPulseCurrent is 3.5 A). clampingVoltage was "
        "holding the stand-off value; forwardVoltage was holding TI's 36 V TLP clamping figure "
        "and is removed rather than replaced with a textbook diode drop TI does not state")


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #465", "date": TODAY, "sourceUrl": URL, "fixed": []}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if REF.encode() in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["semiconductor"]["diode"]["manufacturerInfo"]
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                if str(mi.get("reference")) == REF:
                    di = mi.setdefault("datasheetInfo", {})
                    e = di.setdefault("electrical", {})
                    was = {"clampingVoltage": e.get("clampingVoltage"),
                           "forwardVoltage": e.get("forwardVoltage")}
                    e["clampingVoltage"] = CLAMP
                    e.pop("forwardVoltage", None)
                    di.setdefault("provenance", []).append(
                        {"source": "manufacturerDatasheet", "sourceUrl": URL,
                         "sourceName": NOTE, "retrievedDate": TODAY,
                         "fields": ["electrical.clampingVoltage", "electrical.forwardVoltage"]})
                    audit["fixed"].append({"reference": REF, "was": was,
                                           "nowClampingVoltage": CLAMP,
                                           "forwardVoltageRemoved": True})
                    line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows fixed: {len(audit['fixed'])}")
    for f in audit["fixed"]:
        print(f"   {f['reference']}: clampingVoltage {f['was']['clampingVoltage']} -> {CLAMP}, "
              f"forwardVoltage {f['was']['forwardVoltage']} removed")
    if dry:
        tmp.unlink(missing_ok=True)
        print("--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
