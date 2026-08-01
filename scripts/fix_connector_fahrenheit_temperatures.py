#!/usr/bin/env python3
"""Convert connector operating temperatures stored in Fahrenheit (ABT #435).

    python3 scripts/fix_connector_fahrenheit_temperatures.py [--dry-run]

Blade Runner's CONN_TEMPERATURE_UNIT check fires on 67 TE Connectivity rows whose
environmental.operatingTemperature.maximum is a Fahrenheit value sitting in a Celsius
field. Converting with (F - 32) * 5/9 lands every one of them exactly on a value the
catalogue already uses thousands of times:

    392 -> 200 C   58 rows        (200 C: 7,543 genuine parts)
    302 -> 150 C    7 rows        (150 C: 17,069)
    221 -> 105 C    2 rows        (105 C: 150,618)

VERIFIED AGAINST THE VENDOR, not just arithmetic. TE's own specification 108-160675, the
document already cited by row 2454594-1, states "Temperature : - 40°C to +105°C". That row
stores maximum 221 - which is 105 C in Fahrenheit, exactly. The conversion reproduces the
manufacturer's published range.

THE TICKET SAID THE MINIMA WERE FINE. THEY ARE NOT, and that is the substantive addition
here. ABT #435 reasoned that no minimum-side fires were expected "because -40 C = -40 F",
which is true only for the rows whose minimum IS -40. The other 66 store:

    -67 -> -55 C   59 rows        (-55 C: 130,481 genuine parts;  -67 C: 59, all of these)
    -85 -> -65 C    8 rows        (-65 C:  79,462 genuine parts;  -85 C:  8, all of these)

-67 C and -85 C occur nowhere else in the catalogue. -55 C and -65 C are the two most
common minima in it. Both ends of these rows were read from a Fahrenheit column, and
fixing only the maximum would have left every one of them half wrong - with the half that
remained wrong now invisible, because the check that found them only looks at the maximum.

ONE ROW IS MIXED, AND IS NOT TE'S. Amphenol RF 031-6290 stores minimum -85 with maximum
165. Its maximum is genuine: 3,548 other Amphenol RF rows carry exactly (-65, 165), which
is that vendor's standard RF range, and -85 F is -65 C exactly. So its minimum alone came
from a Fahrenheit column. It is converted; its maximum is untouched.

NOT TOUCHED, and filed separately: 255 connector rows whose operating-temperature MINIMUM
is positive - 108 at (65, 165) against 3,548 siblings at (-65, 165), 122 at (40, 105), and
so on, 240 of the 255 Amphenol RF. A connector that cannot operate below +65 C is not a
real product and these are almost certainly lost minus signs, but "almost certainly" is not
the standard here: amphenolrf.com serves 403 to every request we can make, so the claim
cannot be checked against the vendor the way the TE rows just were. Guessing a sign is how
this catalogue acquired its problems.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "connectors.ndjson"
AUDIT = REPO / "staging" / "connector_fahrenheit_audit.json"
TODAY = "2026-08-01"

# Fahrenheit values seen in the Celsius field -> their exact Celsius equivalent.
# Listed rather than computed so the set is auditable and cannot widen by accident.
F_MAX = {392.0: 200.0, 302.0: 150.0, 221.0: 105.0}
F_MIN = {-67.0: -55.0, -85.0: -65.0}

NOTE = ("operatingTemperature was stored in Fahrenheit in a Celsius field and converted with "
        "(F-32)*5/9 (ABT #435). Verified against TE specification 108-160675, which states "
        "'-40 C to +105 C' for part 2454594-1 whose stored maximum was 221")


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #435", "date": TODAY, "note": NOTE,
             "fixed": [], "byConversion": Counter(), "byManufacturer": Counter()}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"operatingTemperature" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["connector"]["manufacturerInfo"]
                    env = (mi.get("datasheetInfo") or {}).get("environmental") or {}
                    ot = env.get("operatingTemperature") or {}
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                mx, mn = ot.get("maximum"), ot.get("minimum")
                new_mx = F_MAX.get(mx) if isinstance(mx, (int, float)) else None
                new_mn = F_MIN.get(mn) if isinstance(mn, (int, float)) else None
                if new_mx is not None or new_mn is not None:
                    rec_note = dict(was={"minimum": mn, "maximum": mx})
                    if new_mx is not None:
                        ot["maximum"] = new_mx
                        audit["byConversion"][f"max {mx} -> {new_mx}"] += 1
                    if new_mn is not None:
                        ot["minimum"] = new_mn
                        audit["byConversion"][f"min {mn} -> {new_mn}"] += 1
                    prov = (mi.setdefault("datasheetInfo", {})).setdefault("provenance", [])
                    prov.append({"source": "manual", "sourceName": NOTE,
                                 "retrievedDate": TODAY,
                                 "fields": ["environmental.operatingTemperature"]})
                    rec_note.update(reference=mi.get("reference"),
                                    manufacturer=mi.get("name"),
                                    now={"minimum": ot.get("minimum"),
                                         "maximum": ot.get("maximum")})
                    audit["fixed"].append(rec_note)
                    audit["byManufacturer"][str(mi.get("name"))] += 1
                    line = json.dumps(rec, separators=(",", ":"),
                                      ensure_ascii=False).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows converted: {len(audit['fixed'])}")
    for k, v in audit["byManufacturer"].most_common():
        print(f"     {v:4}  {k}")
    for k, v in audit["byConversion"].most_common():
        print(f"       {v:4}  {k}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        audit["byConversion"] = dict(audit["byConversion"])
        audit["byManufacturer"] = dict(audit["byManufacturer"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
