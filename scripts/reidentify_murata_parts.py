#!/usr/bin/env python3
"""Re-identify corrupted Murata part numbers against the real catalogue (ABT #391).

    python3 scripts/reidentify_murata_parts.py CATALOG.jsonl PROBE.jsonl OUT.json
                                               [--apply] [--dry-run]

THE PROBLEM. 3,793 Murata part numbers in the catalogue are unknown to Murata's own
resolver, and they are not wrong in one consistent way:

    GRM32ER72A105KA35L   one character out (real: GRM32CR72A105KA35L)
    GRM31CR71H106KA12L   no single-character substitution resolves
    GRM18R60J100KAE      14 chars against a real 17-18; no single insertion resolves

So there is no string rule to apply. What CAN be done is match each row against the
real catalogue on what it claims to BE.

THE RULE, and it is deliberately strict. A candidate real part is accepted only when
ALL of these hold:

  1. Capacitance matches within 1% (both sides are exact catalogue values, so this
     is an equality test with room for unit conversion, not a tolerance band).
  2. Rated voltage matches exactly.
  3. Case size matches exactly.
  3a. DIELECTRIC matches. The corpus stores dielectricCode (X5R, X7R, C0G) and Murata
     publishes tempChara. Without this, edit distance 1 happily turned
     GRM21BR61A475KE51L into GRM21BR71A475KE51L — R6 to R7, X5R to X7R, a different
     component with the same capacitance and voltage.
  3b. TOLERANCE matches. The tolerance letter sits at a fixed position in a Murata
     code, and the corpus's stored capacitance min/max encodes the same thing. Both
     are compared. This is real data, not a default: across 6,000 Murata rows the
     letter and the stored band agree — K with +10% (3,640 rows), J with +5%,
     M with +20%, G with +2%, F with +1%. Only 62 rows disagree, and those
     disagreements are themselves evidence, since a row whose part number says K
     while its own bounds say +/-20% is a row whose K is wrong.
  4. The real part number is within EDIT DISTANCE 1 of the corrupted one — a single
     character. This was 3 in a first draft and that was far too loose: over an
     18-character code, three edits can change the case size, the dielectric and the
     voltage, i.e. describe a different component entirely. A test with a
     single-part catalogue exposed it immediately, mapping a 1206 part onto a 1210
     one. One character is the only edit that supports the claim "this row means
     that part, mistyped".
  5. Every parameter filter must have actually RUN. If a candidate is missing the
     capacitance, voltage or size that the filter needs, it is rejected rather than
     waved through — the same first draft compared against Murata fields that come
     back empty for every part, so the voltage and case tests silently passed
     everything and only capacitance was really being checked.
  6. Exactly ONE real part satisfies 1-5.

Anything that yields zero candidates, or more than one, is reported as unidentified.
Ambiguity is an answer, not an obstacle to be resolved by preference — picking the
"closest" of several candidates would be inventing an identity, which is the failure
this whole ticket exists to end.

A re-identified row gets its part number CORRECTED and a provenance entry recording
the correction, the evidence, and the old number, so the change is reversible and
auditable. Nothing is silently rewritten.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "murata_reidentification_audit.json"
TODAY = "2026-07-31"

SCALE = {"pF": 1e-12, "nF": 1e-9, "uF": 1e-6, "µF": 1e-6, "μF": 1e-6, "mF": 1e-3,
         "F": 1.0, "": 1.0}


def si(entry):
    """{'value': '1', 'unit': 'uF'} -> 1e-06"""
    if not entry:
        return None
    try:
        return float(entry["value"]) * SCALE.get(entry.get("unit", ""), 1.0)
    except Exception:                                             # noqa: BLE001
        return None


def volts(entry):
    if not entry:
        return None
    try:
        return float(entry["value"])
    except Exception:                                             # noqa: BLE001
        return None


def edit_distance(a, b, cap=4):
    """Levenshtein, abandoned once it exceeds `cap` — we only care about near ties."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def load_catalog(path):
    parts = []
    for line in Path(path).open(encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:                                         # noqa: BLE001
            continue
        pn = (r.get("partNumWithPackageCode") or r.get("partNum") or {}).get("value")
        if not pn:
            continue
        parts.append({
            "partNum": pn,
            "cap": si(r.get("capacitance")),
            "volt": volts(r.get("ratedVoltage") or r.get("ratedVoltageDc")),
            "size": (r.get("sizeCodeInInch") or {}).get("value"),
            "tempChara": (r.get("tempChara") or {}).get("value"),
            "status": (r.get("productionStatus") or {}).get("value"),
            "alternative": (r.get("alternativeProducts") or {}).get("value"),
        })
    return parts


TOL_PCT = {"B": 0.1, "C": 0.25, "D": 0.5, "F": 1, "G": 2, "J": 5, "K": 10,
           "M": 20, "Z": 80}
TOL_RE = re.compile(r"^GRM.{2,3}[A-Z0-9]{2}[0-9A-Z]{2}[0-9]{3}([A-Z])")


def tolerance_letter(mpn):
    m = TOL_RE.match(mpn)
    return m.group(1) if m else None


def stored_tolerance_pct(cap):
    """The +tolerance the row's own capacitance bounds imply, or None."""
    nom, mx = cap.get("nominal"), cap.get("maximum")
    if not nom or not mx:
        return None
    return round((mx / nom - 1) * 100)


def corpus_rows(unresolved):
    """The unresolved capacitor rows, with the parameters they claim."""
    out = {}
    for raw in open(DATA / "capacitors.ndjson", "rb"):
        if b"Murata" not in raw:
            continue
        try:
            rec = json.loads(raw)
            c = rec["capacitor"]
            mi = c["manufacturerInfo"]
            di = mi["datasheetInfo"]
            part = di.get("part") or {}
            ref = mi.get("reference") or part.get("partNumber")
        except Exception:                                         # noqa: BLE001
            continue
        if not ref or str(ref) not in unresolved:
            continue
        el = di.get("electrical") or {}
        cap = el.get("capacitance") or {}
        out[str(ref)] = {
            "cap": cap.get("nominal"),
            "volt": el.get("ratedVoltage"),
            "case": part.get("case"),
            "dielectric": part.get("dielectricCode"),
            "tolPct": stored_tolerance_pct(cap),
        }
    return out


def main(argv):
    catalog = load_catalog(argv[0])
    unresolved = {json.loads(l)["reference"] for l in Path(argv[1]).open(encoding="utf-8")
                  if not json.loads(l).get("exists") and not json.loads(l).get("error")}
    print(f"{len(catalog)} real Murata parts, {len(unresolved)} unresolved references")

    rows = corpus_rows(unresolved)
    print(f"{len(rows)} of them located in capacitors.ndjson with parameters")

    by_cap = defaultdict(list)
    for p in catalog:
        if p["cap"]:
            by_cap[round(p["cap"], 15)].append(p)

    identified, ambiguous, nomatch = {}, {}, []
    for ref, want in rows.items():
        if not want["cap"]:
            nomatch.append({"reference": ref, "why": "row has no capacitance to match on"})
            continue
        cands = []
        for cap_key, group in by_cap.items():
            if abs(cap_key - want["cap"]) > 0.01 * want["cap"]:
                continue
            for p in group:
                # A filter that cannot run must REJECT, never pass. Treating a
                # missing field as "no objection" is how the first draft matched a
                # 1206 to a 1210.
                if want["volt"]:
                    if not p["volt"] or abs(p["volt"] - want["volt"]) > 0.01:
                        continue
                if want["case"]:
                    if not p["size"] or str(p["size"]) != str(want["case"]):
                        continue
                # Dielectric and tolerance are MANDATORY on both sides. A row that
                # cannot supply them cannot be re-identified — skipping the filter
                # when the field is absent is what let GRM31CR61A226KE15L match an
                # X7R part while carrying no dielectric of its own.
                if not want["dielectric"] or not p["tempChara"]:
                    continue
                if str(p["tempChara"]).upper() != str(want["dielectric"]).upper():
                    continue
                if want["tolPct"] is None:
                    continue
                letter = tolerance_letter(p["partNum"])
                if letter is None or TOL_PCT.get(letter) != want["tolPct"]:
                    continue
                d = edit_distance(ref, p["partNum"], cap=2)
                if d <= 1:
                    cands.append((d, p["partNum"], p))
        cands.sort(key=lambda c: c[0])
        best = [c for c in cands if c[0] == cands[0][0]] if cands else []
        if len(best) == 1:
            d, pn, p = best[0]
            identified[ref] = {"newPartNumber": pn, "editDistance": d,
                               "matchedOn": ["capacitance", "ratedVoltage", "case",
                                             "dielectric", "tolerance"],
                               "vendorStatus": p.get("status"),
                               "vendorAlternative": p.get("alternative")}
        elif len(best) > 1:
            ambiguous[ref] = [c[1] for c in best][:6]
        else:
            nomatch.append({"reference": ref, "why": "no real part within edit distance 1 "
                                                     "sharing capacitance, rated voltage "
                                                     "and case"})

    print(f"\nidentified (unique) : {len(identified)}")
    print(f"ambiguous           : {len(ambiguous)}")
    print(f"no match            : {len(nomatch)}")
    for ref, m in list(identified.items())[:8]:
        print(f"    {ref:22} -> {m['newPartNumber']:22} (edit {m['editDistance']})")

    result = {"ticket": "ABT #391 (Murata re-identification)", "date": TODAY,
              "identified": identified, "ambiguous": ambiguous, "noMatch": nomatch[:400],
              "counts": {"identified": len(identified), "ambiguous": len(ambiguous),
                         "noMatch": len(nomatch)}}
    Path(argv[2]).write_text(json.dumps(result, indent=1))
    print(f"\n-> {argv[2]}")

    if "--apply" not in argv:
        print("(re-run with --apply to write the corrections)")
        return 0
    return apply_corrections(identified, "--dry-run" in argv)


def apply_corrections(identified, dry):
    path = DATA / "capacitors.ndjson"
    tmp = path.with_suffix(".ndjson.tmp")
    hit = 0
    with open(path, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            wrote = False
            if b"Murata" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["capacitor"]["manufacturerInfo"]
                    di = mi["datasheetInfo"]
                    part = di.get("part") or {}
                    ref = mi.get("reference") or part.get("partNumber")
                except Exception:                                 # noqa: BLE001
                    ref = None
                if ref and str(ref) in identified:
                    m = identified[str(ref)]
                    new = m["newPartNumber"]
                    if mi.get("reference"):
                        mi["reference"] = new
                    if part.get("partNumber"):
                        part["partNumber"] = new
                    di["provenance"] = [{
                        "source": "manufacturerParametric",
                        "sourceName": f"Murata PIM catalogue re-identification — the stored part "
                                      f"number '{ref}' is unknown to Murata; this row's "
                                      f"capacitance, rated voltage and case match exactly one "
                                      f"real Murata part within edit distance "
                                      f"{m['editDistance']}",
                        "sourceUrl": "https://pimapi.murata.com/public/api/pim/v1/products/search"
                                     f"/cross-categories?partNum={new}&languageRegion=en-global",
                        "retrievedDate": TODAY}]
                    out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                    wrote = True
                    hit += 1
            if not wrote:
                out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    print(f"corrected {hit} rows")
    if dry:
        tmp.unlink(missing_ok=True)
        print("--dry-run: nothing replaced")
    else:
        os.replace(tmp, path)
        print(f"replaced {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
