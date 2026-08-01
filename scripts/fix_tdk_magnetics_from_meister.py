#!/usr/bin/env python3
"""Correct TDK magnetics values against TDK's own catalogue database (ABT #387 follow-on).

    python3 scripts/fix_tdk_magnetics_from_meister.py [--dry-run] [--db PATH]

Found while triaging the 47 findings ABT #387 un-hid. Three TDK common-mode chokes were
flagged for an absurd DCR*size^2/L, and the cause turned out to be that the IMPEDANCE
code in the part number had been written into the record as a DC resistance in ohms:

    ACM55V-701-2PL-TL00   stored 700 ohm and 0.7 uH
                          TDK publishes 0.017 ohm and 5.7 uH  ("701" = 700 ohm @ 100 MHz)

TDK Meister ships the whole catalogue as a local Access database, so rather than trust
three flagged rows this compares EVERY TDK magnetics row in the corpus against TDK's own
numbers. 10,097 of 12,727 rows match a part in the database.

THE DCR COMPARISON NEARLY PRODUCED 6,459 FALSE FINDINGS, which is the important part of
this script's history. TDK's field is "DC Resistance(Max.)" and many of our rows hold a
TYPICAL value, so a naive comparison flags thousands of perfectly good rows. The ratios
gave it away by clustering on exact reciprocals - 777 rows at 1/1.20, 479 at 1/1.30,
252 at 1/1.10, 238 at 1/2.00 - which is a spec convention, not corruption. So a stored
DCR BELOW TDK's maximum is left alone, always. Only a DCR that EXCEEDS the manufacturer's
published maximum is a contradiction, and that is 332 rows, not 6,459. Several are exact
decimal-point slips: RLF10160T-470M1R5-D holds 1.116 ohm against TDK's 0.1116 ohm.

WHAT IS CORRECTED, and how each was confirmed.

  Inductance, 39 rows. TDK's value is ground truth, and the part number independently
  corroborates it in 36 of them:

    * 31 rows had the RATED CURRENT code stored as the inductance. SLF7045T-221MR33-PF
      is 220 uH ("221") at 0.33 A ("R33"); the record held 0.33 uH. TDK's database says
      220 uH, the part number's own inductance code says 220 uH, and our value is exactly
      the current code - three independent statements, all agreeing.
    * 5 rows were out by exactly 1000: the code was read as nH where the series uses uH
      (NLV32T-471J-PF stored 0.47 uH against TDK's 470 uH).
    * 2 rows are the ACM impedance-code case above.
    * 1 row, B82477R4333M100, has no explanation for its 82 mH against TDK's 33 uH. It
      is corrected on TDK's authority alone and called out in the audit, not quietly
      folded in with the rest.

  DC resistance, only where ours exceeds TDK's published maximum. The maximum becomes
  TDK's figure; a nominal that also exceeds it is REMOVED rather than replaced, because
  "we do not know the typical value" is true and "the typical equals the maximum" is not.

Nothing is written for a part TDK's database does not carry, and nothing is written from
a part number - the part numbers here only corroborate, they never supply a value.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "tdk_meister_correction_audit.json"
TODAY = "2026-08-01"

DEFAULT_DB = "/mnt/c/ProgramData/TDK/TDKMeister/tdkData/TstDB.tmdb"
L_SPECS = {"302000000", "303000430"}      # "Inductance"
R_SPECS = {"302000050", "303000140"}      # "DC Resistance(Max.)"
TOL = 0.05

# A stored DCR only contradicts TDK's maximum if it exceeds it by a real margin. A strict
# `>` counted float representation noise as a defect - 0.35100000000000003 against TDK's
# 0.351 - and would have "corrected" 375 rows that already hold exactly the right value,
# discarding their nominal in the process. The genuine cases exceed the maximum by 35 %
# to 41,000x, so 1 % separates them from arithmetic dust with room to spare.
OVER = 1.01

PROV_L = ("TDK Meister catalogue database (TstDB.tmdb, spec 'Inductance') - the stored value "
          "disagreed with TDK's own figure for this part number")
PROV_R = ("TDK Meister catalogue database (TstDB.tmdb, spec 'DC Resistance(Max.)') - the stored "
          "value exceeded TDK's published maximum, which cannot be correct for this part")
URL = "https://product.tdk.com/en/search/"


def load_tdk(db: Path):
    """part number -> {'L': float, 'DCR': float} straight from TDK's own database."""
    def export(table):
        r = subprocess.run(["mdb-export", str(db), table],
                           capture_output=True, text=True, errors="replace")
        if r.returncode != 0:
            raise SystemExit(f"mdb-export {table} failed: {r.stderr[:200]}")
        return csv.reader(r.stdout.splitlines())

    pid2pn = {}
    for row in export("part"):
        if len(row) > 1 and row[0].isdigit():
            pid2pn[row[0]] = row[1].strip('"')
    out = {}
    for row in export("specification"):
        if len(row) < 5 or row[1] not in (L_SPECS | R_SPECS):
            continue
        pn = pid2pn.get(row[0])
        if not pn:
            continue
        try:
            v = float((row[3] or row[4]).strip('"'))
        except ValueError:
            continue
        out.setdefault(pn, {})["L" if row[1] in L_SPECS else "DCR"] = v
    return out


def variants(e):
    el = e.get("electrical")
    return el if isinstance(el, list) else ([el] if el else [])


def build_prefix_index(tdk):
    """Group TDK part numbers by every prefix, for packaging-suffix matching.

    Our ACM2012-201-2P is not in TDK's database; ACM2012-201-2P-T001 and -T002 are, and
    they are the same choke on different tape. Both record 0.25 ohm, against the 200 ohm
    the corpus holds, so the value is not in doubt - only the exact order code is.

    The rule is therefore narrow: a prefix may supply a value ONLY when every TDK part
    that extends it agrees on that value. Where the suffix changes the part (a different
    inductance or resistance) the siblings disagree and nothing is taken. That keeps this
    from becoming a fuzzy match that quietly picks a neighbour's numbers - which is the
    exact defect ABT #385 was about.
    """
    idx = {}
    for pn, vals in tdk.items():
        for cut in range(6, len(pn)):
            idx.setdefault(pn[:cut], []).append(vals)
    out = {}
    for prefix, group in idx.items():
        agreed = {}
        for key in ("L", "DCR"):
            seen = {g[key] for g in group if key in g}
            if len(seen) == 1:
                agreed[key] = seen.pop()
        if agreed:
            out[prefix] = agreed
    return out


def main(argv):
    dry = "--dry-run" in argv
    db = Path(argv[argv.index("--db") + 1]) if "--db" in argv else Path(DEFAULT_DB)
    if not db.exists():
        raise SystemExit(f"TDK Meister database not found at {db} - pass --db PATH")
    tdk = load_tdk(db)
    prefixes = build_prefix_index(tdk)
    print(f"TDK database: {len(tdk)} parts carry an inductance or DC resistance")

    audit = {"ticket": "ABT #387 follow-on", "date": TODAY, "database": str(db),
             "inductanceFixed": [], "dcrFixed": [], "counts": Counter()}
    tmp = DATA.with_suffix(".ndjson.tmp")

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"TDK" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["magnetic"]["manufacturerInfo"]
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                ref = str(mi.get("reference") or "")
                t = None
                if mi.get("name") == "TDK":
                    t = tdk.get(ref)
                    if t is None and len(ref) >= 6:
                        t = prefixes.get(ref)          # packaging-suffix siblings, if unanimous
                        if t:
                            audit["counts"]["viaPackagingSibling"] += 1
                if t:
                    di = mi.setdefault("datasheetInfo", {})
                    changed = []
                    for e in variants(di):
                        if not isinstance(e, dict):
                            continue
                        ind = e.get("inductance")
                        if t.get("L") and isinstance(ind, dict):
                            nom = ind.get("nominal")
                            if isinstance(nom, (int, float)) and not isinstance(nom, bool) \
                                    and abs(nom - t["L"]) > t["L"] * TOL:
                                ind["nominal"] = t["L"]
                                for b in ("minimum", "maximum"):
                                    ind.pop(b, None)
                                audit["inductanceFixed"].append(
                                    {"reference": ref, "was": nom, "now": t["L"]})
                                changed.append("L")
                        if t.get("DCR"):
                            # BOTH shapes, deliberately. ABT #387 exists because three
                            # validator checks read only the singular field and silently
                            # skipped 4,450 rows; a repair script that reads only the
                            # plural one repeats the same mistake in the other direction
                            # and would have corrected 1 row instead of 326.
                            entries = list(e.get("dcResistances") or [])
                            single = e.get("dcResistance")
                            if isinstance(single, dict):
                                entries.append(single)
                            elif isinstance(single, (int, float)) and not isinstance(single, bool):
                                if single > t["DCR"] * OVER:
                                    e["dcResistance"] = t["DCR"]
                                    audit["dcrFixed"].append(
                                        {"reference": ref, "wasNominal": single,
                                         "wasMaximum": None, "nowMaximum": t["DCR"]})
                                    changed.append("DCR")
                            for entry in entries:
                                if not isinstance(entry, dict):
                                    continue
                                hi, lo = entry.get("maximum"), entry.get("nominal")
                                over = [v for v in (hi, lo)
                                        if isinstance(v, (int, float))
                                        and not isinstance(v, bool)
                                        and v > t["DCR"] * OVER]
                                if not over:
                                    continue
                                # TDK's figure is a MAXIMUM. Adopting it as the maximum is
                                # exact; claiming it as the typical would not be, so a
                                # contradicted nominal is dropped rather than rewritten.
                                if isinstance(lo, (int, float)) and lo > t["DCR"] * OVER:
                                    entry.pop("nominal", None)
                                entry["maximum"] = t["DCR"]
                                audit["dcrFixed"].append(
                                    {"reference": ref, "wasNominal": lo, "wasMaximum": hi,
                                     "nowMaximum": t["DCR"]})
                                changed.append("DCR")
                    if changed:
                        prov = di.setdefault("provenance", [])
                        if "L" in changed:
                            prov.append({"source": "manufacturerDatabase", "sourceName": PROV_L,
                                         "sourceUrl": URL, "retrievedDate": TODAY,
                                         "fields": ["electrical.inductance.nominal"]})
                        if "DCR" in changed:
                            prov.append({"source": "manufacturerDatabase", "sourceName": PROV_R,
                                         "sourceUrl": URL, "retrievedDate": TODAY,
                                         "fields": ["electrical.dcResistances"]})
                        audit["counts"]["rows"] += 1
                        line = json.dumps(rec, separators=(",", ":"),
                                          ensure_ascii=False).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows corrected:        {audit['counts']['rows']}")
    print(f"  inductance values:   {len(audit['inductanceFixed'])}")
    print(f"  DC resistances:      {len(audit['dcrFixed'])}")
    for f in audit["inductanceFixed"][:5]:
        print(f"     L  {f['reference']:28} {f['was']:.4g} -> {f['now']:.4g}")
    for f in audit["dcrFixed"][:5]:
        print(f"     R  {f['reference']:28} {f['wasNominal'] or f['wasMaximum']:.4g} "
              f"-> max {f['nowMaximum']:.4g}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        audit["counts"] = dict(audit["counts"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
