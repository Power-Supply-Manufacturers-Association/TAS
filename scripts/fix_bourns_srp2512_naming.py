#!/usr/bin/env python3
"""Bourns omits the series "A" here too — SRP2512A-* are SRP2512-* (ABT #459 / #460).

    python3 scripts/fix_bourns_srp2512_naming.py [--dry-run]

Six rows were filed under "the vendor serves the wrong file" because
bourns.com/docs/product-datasheets/srp2512a.pdf contains only SRP2510A parts and never the
string SRP2512A. That is true, and it turned out to be a red herring: Bourns ALSO serves
SRP2512.pdf - a different, 230 KB file - and that one lists

    SRP2512-R47M   0.47 uH  +-20 %   25 mohm   4.5 A   5.3 A
    SRP2512-R68M   0.68 uH  +-20 %   35 mohm   3.7 A   4.1 A
    SRP2512-1R0M   1.0  uH  +-20 %   49 mohm   3.4 A   3.4 A
    SRP2512-1R5M   1.5  uH  +-20 %   77 mohm   2.5 A   3.2 A
    SRP2512-2R2M   2.2  uH  +-20 %  104 mohm   2.1 A   3.0 A

Our five rows carry exactly those inductances, and four of the five carry exactly those DC
resistances (0.025, 0.035, 0.049, 0.077 ohm). They are these parts. The "A" in our
reference is spurious - the SAME defect already settled for SRF2012A in ABT #431, where
Bourns' own datasheet spells the order code without the series letter while a sibling
document uses it.

SO THE BOURNS HALF OF ABT #459 DISSOLVES. Whether srp2512a.pdf is a misconfigured alias on
Bourns' server is now beside the point for us: our parts' datasheet is SRP2512.pdf and we
were citing the wrong file of the two. Nothing needs reporting to Bourns on our account.
The Wuerth half of #459 stands - those two links genuinely serve a different order code.

TWO ROWS ARE NOT REPAIRED and are reported instead:

  SRP2512A-4R7M - there is no 4.7 uH part in SRP2512.pdf at all (the series runs
  0.47 to 2.2 uH; searching the document for 4R7M or a 4.7 value returns nothing). So this
  is not a naming difference, it is a part number Bourns does not publish - the ABT #460
  class, and it moves there.

  SRP2512A (no suffix) - a series-level stub, not an orderable part: no datasheetUrl, and
  an inductance of nominal 4.7 uH with MAXIMUM 0.47 H. That maximum is the datasheet's
  "0.47" (microhenries, the R47M row) stored as henries, a 10^6 scale error, and it is
  what makes Blade Runner call the tolerance band very wide. A stub with an impossible
  bound is a quarantine question, not a citation one.

The five repaired rows also take the datasheet's rated and saturation currents, and
SRP2512A-2R2M's DC resistance moves from 0.098 to the published 0.104 ohm maximum - the
only one of the five that disagreed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "bourns_srp2512_naming_audit.json"
TODAY = "2026-08-01"

URL = "https://www.bourns.com/docs/product-datasheets/SRP2512.pdf"
SHA = "ee8e3e1937e4a62680ac9ca845d50a9075c993b2298d9154ad0e3084ff81fd29"

# our reference -> (name printed by Bourns, L H, DCR max ohm, Irms A, Isat A)
ROWS = {
    "SRP2512A-R47M": ("SRP2512-R47M", 0.47e-6, 0.025, 4.5, 5.3),
    "SRP2512A-R68M": ("SRP2512-R68M", 0.68e-6, 0.035, 3.7, 4.1),
    "SRP2512A-1R0M": ("SRP2512-1R0M", 1.0e-6, 0.049, 3.4, 3.4),
    "SRP2512A-1R5M": ("SRP2512-1R5M", 1.5e-6, 0.077, 2.5, 3.2),
    "SRP2512A-2R2M": ("SRP2512-2R2M", 2.2e-6, 0.104, 2.1, 3.0),
}

REPORTED = {
    "SRP2512A-4R7M": "no 4.7 uH part exists in SRP2512.pdf; belongs with the ABT #460 class",
    "SRP2512A": "series-level stub with no datasheetUrl and an inductance maximum of 0.47 H "
                "against a 4.7 uH nominal (the datasheet's 0.47 microhenries stored as henries)",
}


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #459 / #460 (SRP2512 naming)", "date": TODAY,
             "sourceUrl": URL, "repaired": [], "reported": REPORTED}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"SRP2512A" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["magnetic"]["manufacturerInfo"]
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                ref = str(mi.get("reference") or "")
                spec = ROWS.get(ref)
                if spec and "Bourns" in str(mi.get("name")):
                    printed, L, dcr, irms, isat = spec
                    di = mi.setdefault("datasheetInfo", {})
                    el = di.get("electrical")
                    e = (el if isinstance(el, list) else [el])[0] or {}
                    was = {"dcResistance": e.get("dcResistance"),
                           "ratedCurrents": e.get("ratedCurrents"),
                           "saturationCurrentPeak": e.get("saturationCurrentPeak")}
                    e.pop("dcResistances", None)
                    e["dcResistance"] = {"maximum": dcr}
                    e["ratedCurrents"] = [irms]
                    e["saturationCurrentPeak"] = isat
                    di["electrical"] = [e]
                    prov = [p for p in (di.get("provenance") or [])
                            if p.get("sourceUrl") != URL]
                    prov.append({
                        "source": "manufacturerDatasheet", "sourceUrl": URL,
                        "sourceName": (
                            f"Bourns SRP2512 Series datasheet, 'Electrical Specifications' "
                            f"table: inductance, DCR max, Irms and Isat columns. The document "
                            f"prints this part as '{printed}' - Bourns omits the series 'A' "
                            f"from the order code, as it does for SRF2012 (ABT #431). Our "
                            f"previous citation, srp2512a.pdf, is a different Bourns file "
                            f"containing only SRP2510A parts. PDF sha256 {SHA}"),
                        "retrievedDate": TODAY,
                        "fields": ["electrical.dcResistance", "electrical.ratedCurrents",
                                   "electrical.saturationCurrentPeak"]})
                    di["provenance"] = prov
                    mi["datasheetUrl"] = URL
                    audit["repaired"].append({"reference": ref, "printedAs": printed,
                                              "was": was, "dcResistanceOhm": dcr,
                                              "ratedCurrentA": irms, "isatA": isat})
                    line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows repaired: {len(audit['repaired'])}")
    for r in audit["repaired"]:
        w = r["was"].get("dcResistance") or {}
        print(f"   {r['reference']:16} (printed {r['printedAs']:14}) "
              f"DCR {w.get('maximum')} -> {r['dcResistanceOhm']}  "
              f"Irms {r['ratedCurrentA']}  Isat {r['isatA']}")
    print("reported, not repaired:")
    for k, v in REPORTED.items():
        print(f"   {k:16} {v[:88]}")
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
