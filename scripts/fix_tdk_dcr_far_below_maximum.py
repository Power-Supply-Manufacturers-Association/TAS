#!/usr/bin/env python3
"""Close the other half of the TDK DC-resistance gap: values far BELOW the maximum.

    python3 scripts/fix_tdk_dcr_far_below_maximum.py [--dry-run] [--db PATH]

fix_tdk_magnetics_from_meister.py corrected TDK rows whose stored DC resistance EXCEEDED
TDK's published maximum, and deliberately left everything below it alone. That rule was
right and it was incomplete: it exists because TDK's field is a MAXIMUM and many of our
rows hold a TYPICAL, so a naive comparison condemned 6,459 good rows. But "below the
maximum" covers both a typical value and a unit error, and only one of those is data.

The ratio distribution separates them cleanly:

    TDK max / our stored DCR      rows
    >= 1.0 (at or above max)     9,129   corrected by the earlier pass
    1.0 - 1.5x                      80   normal typical-vs-maximum
    1.5 - 3x                        41   normal typical-vs-maximum
    3 - 10x                         74   wide, but a real part could sit there
    10 - 50x                       170   not a spec margin
    50 - 500x                       50   not a spec margin
    > 500x                          11   plain unit error

Copper resistance does not vary by 10x between typical and maximum. The rows at 10x and
beyond are 231 wrong values that the earlier pass could not see because it only looked in
one direction, and the threshold is put at 10x rather than 3x so the ambiguous band is
left alone rather than swept in.

FOUND BY A SINGLE IMPOSSIBLE FINDING. BCM605040-57N reported MAG_ENERGY_DENSITY IMPOSSIBLE
after its inductance was corrected to TDK's 57 uH. Reading TDK's full record for it showed
the inductance was right and the DC resistance was 0.00035 ohm against a published 0.35 -
1000x - which the earlier rule had passed over as "below the maximum, therefore a typical".
One finding, one row, and behind it 230 more of the same shape.

Each corrected row takes TDK's published figure as its MAXIMUM, which is what TDK calls it,
and a contradicted nominal is removed rather than rewritten: we know the maximum, we do not
know the typical, and "the typical equals the maximum" would be a new invented number.
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
AUDIT = REPO / "staging" / "tdk_dcr_far_below_maximum_audit.json"
TODAY = "2026-08-01"
DEFAULT_DB = "/mnt/c/ProgramData/TDK/TDKMeister/tdkData/TstDB.tmdb"
R_SPECS = {"302000050", "303000140"}          # "DC Resistance(Max.)"

# A typical-vs-maximum ratio never reaches this. Measured: the real spread stops by 3x,
# with a thin ambiguous tail to 10x that is deliberately NOT touched.
FACTOR = 10.0

PROV = ("TDK Meister catalogue database (TstDB.tmdb, spec 'DC Resistance(Max.)') - the stored "
        "value was more than {f:.0f}x BELOW TDK's published maximum, which is a unit or "
        "transcription error rather than a typical-vs-maximum margin (a real one stops near 3x)")


def tdk_dcr(db: Path):
    def export(table):
        r = subprocess.run(["mdb-export", str(db), table], capture_output=True, text=True,
                           errors="replace")
        if r.returncode != 0:
            raise SystemExit(f"mdb-export {table} failed: {r.stderr[:200]}")
        return csv.reader(r.stdout.splitlines())
    pid2pn = {}
    for row in export("part"):
        if len(row) > 1 and row[0].isdigit():
            pid2pn[row[0]] = row[1].strip('"')
    out = {}
    for row in export("specification"):
        if len(row) < 5 or row[1] not in R_SPECS:
            continue
        pn = pid2pn.get(row[0])
        if not pn:
            continue
        try:
            out[pn] = float((row[3] or row[4]).strip('"'))
        except ValueError:
            pass
    return out


def variants(di):
    el = di.get("electrical")
    return el if isinstance(el, list) else ([el] if el else [])


def read_dcr(e):
    d = e.get("dcResistances")
    if isinstance(d, list) and d and isinstance(d[0], dict):
        for k in ("maximum", "nominal"):
            if isinstance(d[0].get(k), (int, float)):
                return d[0][k], ("dcResistances", 0)
    s = e.get("dcResistance")
    if isinstance(s, dict):
        for k in ("maximum", "nominal"):
            if isinstance(s.get(k), (int, float)):
                return s[k], ("dcResistance", None)
    if isinstance(s, (int, float)):
        return s, ("dcResistanceScalar", None)
    return None, None


def main(argv):
    dry = "--dry-run" in argv
    db = Path(argv[argv.index("--db") + 1]) if "--db" in argv else Path(DEFAULT_DB)
    if not db.exists():
        raise SystemExit(f"TDK Meister database not found at {db}")
    tdk = tdk_dcr(db)
    print(f"TDK parts with a published DC resistance: {len(tdk):,}")

    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #387 follow-on", "date": TODAY, "factor": FACTOR,
             "fixed": [], "byBand": Counter()}

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
                if str(mi.get("name")) == "TDK":
                    ref = str(mi.get("reference") or "")
                    pub = tdk.get(ref)
                    di = mi.setdefault("datasheetInfo", {})
                    changed = False
                    if pub:
                        for e in variants(di):
                            if not isinstance(e, dict):
                                continue
                            cur, where = read_dcr(e)
                            if cur is None or cur <= 0 or pub / cur < FACTOR:
                                continue
                            ratio = pub / cur
                            # PHYSICS GUARD, added after this script corrupted 6 rows.
                            # TDK's database is reliable for ACTIVE parts and demonstrably
                            # not for DISCONTINUED ones - the CK45 tolerance anomalies and
                            # these both sit on negative class_ids. So its number is not
                            # accepted when doing so would make the part impossible.
                            # B82559A4322A033 is a 33 x 33 x 15 mm power choke rated 95 A:
                            # at the stored 0.85 mohm that is 7.7 W, at TDK's "0.85 ohm" it
                            # is 7,671 W. The stored value was right and TDK's was the unit
                            # error. Package size and current are the arbiter, not the
                            # source's authority.
                            isat = e.get("saturationCurrentPeak")
                            if isinstance(isat, (int, float)) and isat > 0 and \
                                    isat * isat * pub > 500.0:
                                audit.setdefault("refusedAsImplausible", []).append(
                                    {"reference": ref, "stored": cur, "tdk": pub,
                                     "isat": isat, "wattsIfApplied": round(isat * isat * pub)})
                                continue
                            if where[0] == "dcResistances":
                                entry = e["dcResistances"][0]
                                entry.pop("nominal", None)
                                entry["maximum"] = pub
                            else:
                                e.pop("dcResistance", None)
                                e["dcResistance"] = {"maximum": pub}
                            audit["fixed"].append({"reference": ref, "was": cur, "now": pub,
                                                   "ratio": round(ratio, 1)})
                            audit["byBand"]["10-50x" if ratio < 50 else
                                            ("50-500x" if ratio < 500 else ">500x")] += 1
                            changed = True
                    if changed:
                        di.setdefault("provenance", []).append({
                            "source": "manufacturerDatabase",
                            "sourceName": PROV.format(f=FACTOR),
                            "sourceUrl": "https://product.tdk.com/en/search/",
                            "retrievedDate": TODAY,
                            "fields": ["electrical.dcResistance"]})
                        line = json.dumps(rec, separators=(",", ":"),
                                          ensure_ascii=False).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows corrected: {len(audit['fixed'])}")
    for k, v in audit["byBand"].most_common():
        print(f"     {v:5}  {k}")
    for f in audit["fixed"][:5]:
        print(f"       {f['reference']:26} {f['was']} -> {f['now']}  ({f['ratio']}x)")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        audit["byBand"] = dict(audit["byBand"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
