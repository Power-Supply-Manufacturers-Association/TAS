#!/usr/bin/env python3
"""Resolve the SRF2012A naming question ABT #431 left open, and repair the three rows.

    python3 scripts/fix_bourns_srf2012a_naming.py [--dry-run]

ABT #431 repaired 27 Bourns/J.W. Miller rows that stored the datasheet's IMPEDANCE column
as a DC resistance, and withheld three - SRF2012A-201YA, SRF2012A-361YA, SRF2012A-900YA -
because Bourns' own document did not name them in the form the corpus uses. Citing it
would have been a false provenance entry, so the rows were left visibly wrong rather than
quietly repaired against a document that does not mention them.

THE ANSWER: Bourns prints the order code WITHOUT the series "A". bourns.com's
SRF2012A.pdf lists SRF2012-121YA, SRF2012-201YA, SRF2012-301YA, SRF2012-361YA,
SRF2012-900YA and eleven more - every part in the file, in that form. The "A" belongs to
the series and the filename, not to the order code. (A second Bourns document,
SRF2012A-801Y.pdf, does use SRF2012A-801Y, so the vendor is not consistent with itself.)
The corpus proves the point from the other side: it holds the SAME parts a second time
under manufacturer "J.W. Miller" - a Bourns brand - spelled SRF2012-301YA / -361YA /
-900YA exactly as the datasheet does, and those were repaired in #431 without difficulty.

So the document IS the right one; only the spelling of the reference differs. The
provenance written here says that in as many words, rather than implying the file names
the string the corpus stores.

WHAT THE DOCUMENT ACTUALLY SPECIFIES, and why more changes than the DCR:

    Part Number      Impedance @ 100 MHz   Tolerance   Insulation   DCR Max.   IDC Max.
    SRF2012-900YA           90 ohm            +-25 %      10 Mohm     0.30 ohm    400 mA
    SRF2012-201YA          200 ohm            +-25 %      10 Mohm     0.40 ohm    300 mA
    SRF2012-361YA          360 ohm            +-25 %      10 Mohm     0.50 ohm    300 mA

There is NO INDUCTANCE COLUMN - this is a signal-line common-mode choke specified by
impedance. The corpus rows nevertheless carried an inductance of 200 uH, 360 uH-worth and
90 uH, which are the IMPEDANCE figures wearing the wrong unit, and SRF2012A-361YA also
carried 360 ohm as its DC resistance. One number, copied into two wrong fields.

So each row loses its invented inductance, gains the published DCR and rated current, and
records the impedance where MAS already has a place for it (electrical.impedancePoints, at
the document's own 100 MHz). Nothing is substituted for the absent inductance: the vendor
does not publish one, and absence is the truthful state.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "bourns_srf2012a_naming_audit.json"
TODAY = "2026-08-01"

URL = "https://www.bourns.com/docs/product-datasheets/SRF2012A.pdf"

# corpus reference -> (name printed in the datasheet, Z ohm @100 MHz, DCR max ohm, IDC max A)
ROWS = {
    "SRF2012A-201YA": ("SRF2012-201YA", 200.0, 0.40, 0.300),
    "SRF2012A-361YA": ("SRF2012-361YA", 360.0, 0.50, 0.300),
    "SRF2012A-900YA": ("SRF2012-900YA", 90.0, 0.30, 0.400),
}


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #431 (SRF2012A naming)", "date": TODAY, "sourceUrl": URL,
             "repaired": []}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"SRF2012A-" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["magnetic"]["manufacturerInfo"]
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                ref = str(mi.get("reference") or "")
                spec = ROWS.get(ref)
                if spec and "Bourns" in str(mi.get("name")):
                    printed, z, dcr, idc = spec
                    di = mi.setdefault("datasheetInfo", {})
                    el = di.get("electrical")
                    e = (el if isinstance(el, list) else [el])[0] or {}
                    was = {"inductance": e.get("inductance"),
                           "dcResistance": e.get("dcResistance"),
                           "dcResistances": e.get("dcResistances")}
                    e.pop("inductance", None)      # the series publishes none
                    e.pop("dcResistance", None)
                    e["subtype"] = e.get("subtype") or "commonModeChoke"
                    e["dcResistances"] = [{"maximum": dcr}]
                    e["ratedCurrents"] = [idc]
                    e["impedancePoints"] = [{"impedance": {"magnitude": z},
                                             "frequency": 1.0e8, "winding": "common"}]
                    di["electrical"] = [e]
                    prov = [p for p in (di.get("provenance") or [])
                            if p.get("sourceUrl") != URL]
                    prov.append({
                        "source": "manufacturerDatasheet",
                        "sourceUrl": URL,
                        "sourceName": (
                            f"Bourns SRF2012A Series datasheet, 'Electrical Specifications' "
                            f"table: impedance @ 100 MHz, DCR Max. and IDC Max. columns. The "
                            f"document prints this part as '{printed}' - Bourns omits the "
                            f"series 'A' from the order code, and the same part is held "
                            f"elsewhere in this catalogue under that spelling"),
                        "retrievedDate": TODAY,
                        "fields": ["electrical.dcResistances", "electrical.ratedCurrents",
                                   "electrical.impedancePoints"]})
                    di["provenance"] = prov
                    mi["datasheetUrl"] = URL
                    audit["repaired"].append({"reference": ref, "printedAs": printed,
                                              "was": was, "impedanceOhm": z,
                                              "dcResistanceOhm": dcr, "ratedCurrentA": idc})
                    line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows repaired: {len(audit['repaired'])}")
    for r in audit["repaired"]:
        w = r["was"]
        old_l = (w.get("inductance") or {}).get("nominal") if isinstance(w.get("inductance"), dict) else None
        old_r = (w.get("dcResistance") or {}).get("maximum") if isinstance(w.get("dcResistance"), dict) else None
        print(f"   {r['reference']:18} (printed {r['printedAs']:14}) "
              f"was L={old_l} DCR={old_r} -> DCR={r['dcResistanceOhm']} ohm, "
              f"Z={r['impedanceOhm']} ohm @100 MHz, IDC={r['ratedCurrentA']} A")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
