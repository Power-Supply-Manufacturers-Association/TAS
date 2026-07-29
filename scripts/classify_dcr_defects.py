#!/usr/bin/env python3
"""Classify every magnetics row that implies implausible dissipation, by WHICH
defect it is (ABT #351).

    python3 scripts/classify_dcr_defects.py [out.json]

This is a DIAGNOSTIC pass. It changes no data. It exists because the campaign kept
discovering that one symptom — dcResistance x ratedCurrent^2 being absurd — is
produced by several unrelated causes whose repairs are mutually destructive:
correcting a D1 row's DCR would corrupt a D2 row's correct DCR, and a magnitude
rule on a current transformer deletes correct data. Repair order should follow the
EVIDENCE TYPE, not the vendor.

CLASSES, each with a testable signature:

  D1 impedance-as-DCR      dcResistance equals the impedance code in the part
                           number (SRF1206A-172Y -> 1700). Proven on TDK, Laird,
                           Bourns. Fix: file it as an impedancePoint if the
                           datasheet states a frequency, else remove it.
  D2 milliamps-as-amps     the RATED CURRENT is 1000x too large; the DCR is fine.
                           Proven on Vanguard (the vendor's own website mislabels
                           the unit) and Bourns CVH. Fix: rescale the current.
  D3 field-copied          dcResistance is EXACTLY the rated current — one column
                           written into another. Proven on Bourns 1110/SRP.
                           Fix: read the real DCR off the datasheet.
  D4 milliohms-as-ohms     the DCR is 1000x too large. Fix: rescale, but only with
                           the datasheet's column header as proof.

FALSE POSITIVES — the signature is meaningless for these, and "fixing" them
damages correct data:

  F1 current-sense part    rated current is the PRIMARY current and the DC
                           resistance is a WINDING resistance. Their product is
                           not a physical quantity.
  F2 saturation current    ratedCurrents holds Isat, not the thermal rating.
                           I^2R at Isat is not a dissipation claim. Signature: a
                           very low DCR with a very high current, i.e. a large
                           moulded power part.

A row matching several signatures is reported as AMBIGUOUS with all of them, not
forced into one — D2 and D4 in particular both "explain" a 1000x error, and only a
datasheet says which field is wrong.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"

CT_TEXT = re.compile(r"current[\s-]?sense|current[\s-]?transformer", re.I)
CLASS_W = 5.0            # the threshold that defines the defect class
PLAUSIBLE_W = 5.0        # a candidate repair has succeeded when it drops below this


def eia_code(reference: str) -> float | None:
    """The impedance/inductance code manufacturers embed in a part number:
    -172Y -> 17 x 10^2 = 1700."""
    m = re.search(r"[-_]?(\d{3})[A-Z]", reference or "")
    if not m:
        return None
    d = m.group(1)
    return int(d[:2]) * 10 ** int(d[2])


def family(ref: str) -> str:
    """The part-number stem shared by a family: letters plus any leading digits,
    before the value/packaging suffix."""
    m = re.match(r"^([A-Za-z]+\d*)", ref or "")
    return m.group(1) if m else ref


def sibling_scale(ref: str, mk: str, dcr: float, rated: float, sane: dict):
    """Which field is the wrong MAGNITUDE, judged against SANE parts of the same
    family already in the corpus. Returns ('D4'|'D2', evidence) or (None, None).

    This is the only corpus-internal way to break the D2/D4 tie: physics cannot,
    because a 1000x error in either field looks the same. If the family's healthy
    parts carry DC resistances ~1000x smaller than this row, the DCR is the wrong
    field; if their rated currents are ~1000x smaller, the current is."""
    peers = sane.get((mk, family(ref)))
    if not peers or len(peers) < 3:
        return None, None
    med_dcr = sorted(p[0] for p in peers)[len(peers) // 2]
    med_rated = sorted(p[1] for p in peers)[len(peers) // 2]
    dcr_ratio = dcr / med_dcr if med_dcr else 0
    rated_ratio = rated / med_rated if med_rated else 0
    if dcr_ratio > 100 and rated_ratio < 10:
        return "D4", (f"family {family(ref)} median DCR {med_dcr:g} ohm — this row is "
                      f"{dcr_ratio:.0f}x that, while its current is in family range")
    if rated_ratio > 100 and dcr_ratio < 10:
        return "D2", (f"family {family(ref)} median rated current {med_rated:g} A — this row is "
                      f"{rated_ratio:.0f}x that, while its DCR is in family range")
    return None, None


def classify(ref: str, dcr: float, rated: float, subtype: str, desc: str):
    hits, why = [], {}

    if subtype == "transformer" or CT_TEXT.search(desc) or CT_TEXT.search(ref):
        hits.append("F1")
        why["F1"] = "current-sense part: primary current x winding resistance is not a physical quantity"

    if dcr <= 0.01 and rated >= 50:
        hits.append("F2")
        why["F2"] = f"very low DCR ({dcr} ohm) with very high current ({rated} A) — reads as a saturation rating"

    if abs(dcr - rated) < 1e-9:
        hits.append("D3")
        why["D3"] = f"dcResistance ({dcr}) is EXACTLY the rated current"

    code = eia_code(ref)
    if code and abs(dcr - code) < 0.01:
        hits.append("D1")
        why["D1"] = f"dcResistance ({dcr}) equals the code in the part number ({code})"

    if rated >= 1.0 and dcr * (rated / 1000) ** 2 <= PLAUSIBLE_W:
        hits.append("D2")
        why["D2"] = f"rated current /1000 makes it physical ({dcr * (rated/1000)**2:.4f} W)"

    if (dcr / 1000) * rated * rated <= PLAUSIBLE_W:
        hits.append("D4")
        why["D4"] = f"dcResistance /1000 makes it physical ({(dcr/1000)*rated*rated:.4f} W)"

    return hits, why


def main(argv: list[str]) -> int:
    out_path = Path(argv[0]) if argv else None
    # First pass: collect the (dcr, rated) of every SANE part, per manufacturer and
    # family, to serve as the magnitude reference for the ambiguous ones.
    sane = defaultdict(list)
    for line in open(DATA):
        try:
            info = json.loads(line)["magnetic"]["manufacturerInfo"]
            el = info["datasheetInfo"]["electrical"][0]
        except Exception:
            continue
        d = el.get("dcResistances")
        d = d[0] if d else el.get("dcResistance")
        if not isinstance(d, dict):
            continue
        dcr = d.get("maximum") if d.get("maximum") is not None else d.get("nominal")
        rated = (el.get("ratedCurrents") or [None])[0]
        if not dcr or not rated or dcr * rated * rated > CLASS_W:
            continue
        sane[(str(info.get("name")), family(str(info.get("reference"))))].append((dcr, rated))

    rows = []
    for line in open(DATA):
        try:
            rec = json.loads(line)
            info = rec["magnetic"]["manufacturerInfo"]
            di = info["datasheetInfo"]
            el = di["electrical"][0]
        except Exception:
            continue
        d = el.get("dcResistances")
        d = d[0] if d else el.get("dcResistance")
        if not isinstance(d, dict):
            continue
        dcr = d.get("maximum") if d.get("maximum") is not None else d.get("nominal")
        rated = (el.get("ratedCurrents") or [None])[0]
        if not dcr or not rated:
            continue
        w = dcr * rated * rated
        if w <= CLASS_W:
            continue
        ref = str(info.get("reference"))
        hits, why = classify(ref, dcr, rated, str(el.get("subtype")),
                             str((di.get("part") or {}).get("description", "")))
        resolved, evidence = sibling_scale(ref, str(info.get("name")), dcr, rated, sane)
        if resolved:
            why["siblings"] = evidence
        rows.append({"reference": ref, "manufacturer": str(info.get("name")),
                     "dcResistanceOhm": dcr, "ratedCurrentA": rated,
                     "inductanceH": (el.get("inductance") or {}).get("nominal"),
                     "impliedWatts": round(w, 1), "classes": hits, "evidence": why,
                     "resolvedBySiblings": resolved,
                     "datasheetUrl": info.get("datasheetUrl")})

    def verdict(r):
        if "F1" in r["classes"]:
            return "F1 current-sense (not a defect)"
        if "F2" in r["classes"]:
            return "F2 saturation current (not a defect)"
        defects = [c for c in r["classes"] if c.startswith("D")]
        if not defects:
            return "UNCLASSIFIED"
        if len(defects) == 1:
            return f"{defects[0]} single signature"
        # a decisive signature outranks the generic 1000x pair
        if "D3" in defects:
            return "D3 field-copied (siblings/exact match)"
        if "D1" in defects:
            return "D1 impedance-as-DCR (part-number code)"
        if r.get("resolvedBySiblings"):
            return f'{r["resolvedBySiblings"]} resolved by family siblings'
        return "AMBIGUOUS " + "+".join(defects)

    for r in rows:
        r["verdict"] = verdict(r)

    tally = Counter(r["verdict"] for r in rows)
    print(f"rows implying more than {CLASS_W:.0f} W: {len(rows)}\n")
    print(f'{"verdict":34} {"rows":>5}')
    for v, n in tally.most_common():
        print(f"{v:34} {n:5}")

    print("\nby verdict and manufacturer:")
    per = defaultdict(Counter)
    for r in rows:
        per[r["verdict"]][r["manufacturer"][:20]] += 1
    for v, n in tally.most_common():
        print(f"  {v}\n      " + ", ".join(f"{m} {c}" for m, c in per[v].most_common(5)))

    if out_path:
        out_path.write_text(json.dumps({"ticket": "ABT #351", "thresholdW": CLASS_W,
                                        "rows": rows}, indent=1))
        print(f"\nmanifest -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
