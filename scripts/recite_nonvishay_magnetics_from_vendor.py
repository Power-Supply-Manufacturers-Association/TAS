#!/usr/bin/env python3
"""Point Sumida and Abracon inductors at their own datasheets (ABT #451, remainder).

    python3 scripts/recite_nonvishay_magnetics_from_vendor.py [--dry-run]

The last 27 of the 83 magnetics rows whose citation resolves to a real datasheet for a
DIFFERENT part. 9 are repaired here; the other 18 are reported, and the reasons differ
enough to be worth stating individually.

REPAIRED, 9 rows, each verified to name its part VERBATIM in the fetched text:

  Sumida, 8 rows, which were citing four different competitors:
      CDRH74NP-121MC-B      cited Abracon ASPI-0704S.pdf     -> sumida CDRH74.pdf
      CDRH8D43RT125NP-331MC cited Abracon ASPI-8040S.pdf     -> sumida CDRH8D43RT125.pdf
      CDRR75NP-680MC        cited TDK b82472p6.pdf           -> sumida CDRR75.pdf
      CDRH3D28NP-3R3NC      cited Bourns SRR4028.pdf         -> sumida CDRH3D28.pdf
      CDRH127NP-391MC       cited Bourns SRR1280.pdf         -> sumida CDRH127.pdf
      CR105NP-270MC         cited Bourns SDR1006.pdf         -> sumida CR105.pdf
      CDRH64BNP-101MC-B     cited Bourns SRR0604.pdf         -> sumida CDRH64B.pdf
      CDRH5D18NP-101NC      cited Bourns SRR5018.pdf         -> sumida CDRH5D18.pdf

    Sumida publishes these at products.sumida.com/products/pdf/<SERIES>.pdf with the
    series named WITHOUT the "NP" the order code carries - CDRH74.pdf, not CDRH74NP.pdf.

  Abracon, 1 row: AMELH6030S-R22MT cited AMELH6020S.pdf, the 6020 size. Its own
  AMELH6030S.pdf names it.

NOT REPAIRED, 18 rows, in three groups.

  THE VENDOR SERVES THE WRONG FILE - 8 rows, and this is not our error to fix.
  bourns.com/docs/Product-Datasheets/srp2512a.pdf contains only SRP2510A parts, so the six
  SRP2512A rows cite the URL Bourns publishes for their series and Bourns' file is wrong.
  we-online.com serves 74406032033.pdf and 74406042470.pdf whose contents are a different
  order code (a digit transposition), and both codes are real in Wuerth's own catalogue.
  Changing our citation would hide a vendor defect that should be reported upstream.

  NO EVIDENCE OF EQUIVALENCE - 2 YAGEO rows. PBY160808T-601Y-N and PBY321611T-601Y cite
  Yageo's BBPY series documents, which print part numbers of the form BBPY00321611601 and
  never the string "PBY" at all (checked: 0 occurrences). The size and impedance codes line
  up, so PBY is plausibly the legacy naming of the same part - but plausible is not
  verified, and a citation that rests on a rename I inferred is exactly the kind this
  campaign exists to remove.

  NO DOCUMENT FOUND - 8 rows: Murata Power Solutions 26S470C / 26S101C / 49100SC / 34103C
  (citing Bourns SRR/SDR sheets), Murata DD1217AS-H-1R5N=P3 and -2R2N=P3, Littelfuse
  LPWI160808HR47T (citing a CVH-series sheet) and Taiyo Yuden NR4018T220M (citing Abracon
  ASPI-4020S.pdf). Candidate vendor URLs were probed and none named the part, so they keep
  their present, wrong citation rather than a guessed one.

Every URL below was fetched, its text searched for the part number, and its SHA-256
recorded. Nothing is cited on the strength of a filename.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "nonvishay_magnetics_recitation_audit.json"
TODAY = "2026-08-01"

# reference -> (url, sha256) — each fetched 2026-08-01 and confirmed to name the part.
VERIFIED = {
    "CDRR75NP-680MC": ("https://products.sumida.com/products/pdf/CDRR75.pdf",
                       "762aaf6a841fc83f"),
    "CDRH74NP-121MC-B": ("https://products.sumida.com/products/pdf/CDRH74.pdf",
                         "f4e6125772b471c8"),
    "CDRH8D43RT125NP-331MC": ("https://products.sumida.com/products/pdf/CDRH8D43RT125.pdf",
                              "6f39a9a50ef8b37a"),
    "CDRH3D28NP-3R3NC": ("https://products.sumida.com/products/pdf/CDRH3D28.pdf",
                         "d143c17648e803cf"),
    "CDRH127NP-391MC": ("https://products.sumida.com/products/pdf/CDRH127.pdf",
                        "96383a41e9d3e293"),
    "CR105NP-270MC": ("https://products.sumida.com/products/pdf/CR105.pdf",
                      "7e6a4b4727200bc6"),
    "CDRH64BNP-101MC-B": ("https://products.sumida.com/products/pdf/CDRH64B.pdf",
                          "df74c1dace0ac517"),
    "CDRH5D18NP-101NC": ("https://products.sumida.com/products/pdf/CDRH5D18.pdf",
                         "f34edfbcba905d2b"),
    "AMELH6030S-R22MT": ("https://abracon.com/datasheets/AMELH6030S.pdf",
                         "2e26acc264faae2e"),
}

NOT_REPAIRED = {
    "vendor-serves-wrong-file": [
        "SRP2512A-1R0M", "SRP2512A-1R5M", "SRP2512A-2R2M", "SRP2512A-4R7M",
        "SRP2512A-R47M", "SRP2512A-R68M", "74406032033", "74406042470"],
    "no-evidence-of-rename": ["PBY160808T-601Y-N", "PBY321611T-601Y"],
    "no-vendor-document-found": [
        "26S470C", "26S101C", "49100SC", "34103C",
        "DD1217AS-H-1R5N=P3", "DD1217AS-H-2R2N=P3",
        "LPWI160808HR47T", "NR4018T220M"],
}


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #451 (magnetics remainder)", "date": TODAY,
             "recited": [], "byManufacturer": Counter(),
             "notRepaired": NOT_REPAIRED}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            try:
                rec = json.loads(raw)
                mi = rec["magnetic"]["manufacturerInfo"]
            except Exception:                                     # noqa: BLE001
                out.write(line)
                continue
            ref = str(mi.get("reference") or "")
            got = VERIFIED.get(ref)
            if got:
                url, sha = got
                di = mi.setdefault("datasheetInfo", {})
                old = mi.get("datasheetUrl")
                prov = [p for p in (di.get("provenance") or [])
                        if p.get("sourceUrl") != url]
                prov.append({
                    "source": "manufacturerDatasheet", "sourceUrl": url,
                    "sourceName": (
                        f"{mi.get('name')} datasheet for this series, fetched and confirmed "
                        f"to name this part number verbatim. Replaces a citation to another "
                        f"manufacturer's document (ABT #451). PDF sha256 begins {sha}"),
                    "retrievedDate": TODAY})
                di["provenance"] = prov
                mi["datasheetUrl"] = url
                audit["recited"].append({"reference": ref, "manufacturer": mi.get("name"),
                                         "wasCiting": old, "nowCiting": url})
                audit["byManufacturer"][str(mi.get("name"))] += 1
                line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows re-cited to their own vendor: {len(audit['recited'])}")
    for k, v in audit["byManufacturer"].most_common():
        print(f"     {v:4}  {k}")
    for k, v in NOT_REPAIRED.items():
        print(f"   not repaired [{k}]: {len(v)}")
    for r in audit["recited"][:3]:
        print(f"       {r['reference']:24} {str(r['wasCiting'])[:40]:40} -> {r['nowCiting'][-38:]}")
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
