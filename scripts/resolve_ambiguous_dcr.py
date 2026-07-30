#!/usr/bin/env python3
"""Resolve the D2/D4-ambiguous implausible-dissipation rows using every field the
record itself carries, judged by Blade Runner's own physics thresholds (ABT #351).

    python3 scripts/resolve_ambiguous_dcr.py [out.json]

Diagnostic only — changes no data. The v1 classifier left 788 rows "ambiguous
D2+D4" because at a wattage threshold a 1000x error in the CURRENT and a 1000x
error in the DCR are indistinguishable. But the records carry more than the two
fields in question, and Blade Runner already encodes the physics that couples
them:

  * inductance          -> MAG_DCR_PER_H:  DCR/L bounds (1e6 SUS / 1e9 IMP ohm/H)
  * mechanical dims     -> MAG_DCR_GEOM:   DCR*size_mm^2/L_uH bounds
                           (100 SUS / 1000 IMP / 1e-6 low)
  * saturationCurrent   -> MAG_ISAT_POWER: Isat^2*DCR bounds (50 SUS / 500 IMP W)
                           MAG_RATED_LE_SAT: rated/Isat bounds (5 SUS / 50 IMP)

None of the first three involve the rated current at all — so they test the DCR
alone. If the DCR is inconsistent with the row's OWN inductance and package size,
the DCR is the wrong field (D4-side); if it is consistent, the current is (D2).
The dimensions are present on essentially every ambiguous row and were previously
unused.

Also fixes a v1 classifier bug: the EIA-code detector only matched the
letter-suffix style (SRF1206A-172Y), missing TDK's dash style (ACM12V-351-2PL,
code 351 -> 350). Those rows have the code stamped into the DCR — and usually
into the INDUCTANCE as well (350 nH for code 350) — and v1 mis-filed them as
"D2 single signature". Repairing that verdict would have rescaled a correct
current and kept a garbage DCR. Here, code-in-DCR is D1 and decisive, and
code-in-L is recorded too since it means the row has no real L either.

DECISION RULES (conservative — a row is resolved only when the evidence is
one-sided; anything mixed stays ambiguous):

  D1  DCR equals the part-number code (either style). Decisive by itself.
  D4  at least one current-independent check (DCR/L, DCR*size^2/L, Isat^2*DCR)
      violated as-is, ALL of them cleared by DCR/1000, and NONE cleared by
      I/1000; and DCR/1000 must not fall below the geometric low bound.
  D2  every current-independent check clean as-is (with dims present, so the
      cleanliness is meaningful), and either rated/Isat > 50 (the validator's
      own unit-error tier) or the package cannot carry the stated current
      (> 8 A per mm of largest dimension — XAL1010 at 10 mm / 62 A sets the
      generous end of real parts), and DCR/1000 WOULD violate the geometric
      low bound (so the D4 reading is affirmatively excluded).

Every resolved verdict is then CONFIRMED by running the real tas_validator on the
hypothetically-repaired record: the winning repair must produce zero findings
among the checks above and zero IMPOSSIBLE overall, and the losing repair must
not. Rows failing confirmation are demoted back to ambiguous.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validator" / "build-ninja"))
import tas_validator  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
MANIFEST = REPO / "staging" / "dcr_defect_classification.json"

# Blade Runner's own thresholds (validator/include/tas_validator/thresholds.hpp)
DCR_PER_H_SUS, DCR_PER_H_IMP = 1e6, 1e9
GEOM_SUS, GEOM_IMP, GEOM_LOW = 100.0, 1000.0, 1e-6
ISAT_POWER_SUS = 50.0
RATED_ISAT_IMP = 50.0
AMPS_PER_MM = 8.0            # generous bound from the largest real SMD power parts
DCR_CHECKS = {"MAG_DCR_PER_H", "MAG_DCR_GEOM", "MAG_ISAT_POWER"}


def eia_code(reference: str):
    """Both encodings: letter-suffix (SRF1206A-172Y) and dash-delimited
    (ACM12V-351-2PL). 172 -> 1700, 351 -> 350."""
    for pat in (r"[-_](\d{3})[A-Z]", r"[-_](\d{3})(?=[-_]|$)"):
        m = re.search(pat, reference or "")
        if m:
            d = m.group(1)
            return int(d[:2]) * 10 ** int(d[2])
    return None


def nominal(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("nominal", "maximum", "minimum"):
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
    return None


def side_checks(dcr, L, size_mm, isat):
    """Which current-independent checks the (dcr, L, dims, isat) combination
    violates, using Blade Runner's thresholds. Returns (violated, low_geom)."""
    violated = set()
    low = False
    if L and L > 0:
        if dcr / L > (DCR_PER_H_SUS if L > 1e-6 else DCR_PER_H_IMP):
            violated.add("MAG_DCR_PER_H")
        if size_mm:
            geom = dcr * size_mm * size_mm / (L * 1e6)
            if geom > GEOM_SUS:
                violated.add("MAG_DCR_GEOM")
            if geom < GEOM_LOW:
                low = True
    if isat and isat > 0 and isat * isat * dcr > ISAT_POWER_SUS:
        violated.add("MAG_ISAT_POWER")
    return violated, low


def validator_confirms(rec, el_key_plural, dcr_scaled, rated_scaled):
    """Run the real Blade Runner on a hypothetical repair; True when it emits no
    DCR-side findings and nothing IMPOSSIBLE."""
    trial = json.loads(json.dumps(rec))
    el = trial["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]
    if dcr_scaled is not None:
        if el_key_plural:
            el["dcResistances"][0] = {"maximum": dcr_scaled}
        else:
            el["dcResistance"] = {"maximum": dcr_scaled}
    if rated_scaled is not None:
        el["ratedCurrents"] = [rated_scaled] + list(el.get("ratedCurrents", [])[1:])
    v = tas_validator.validate(json.dumps(trial))
    for f in v.findings:
        code = str(f.code)
        if code in DCR_CHECKS:
            return False
        if str(f.severity).upper() == "IMPOSSIBLE":
            return False
    return True


def main(argv):
    out_path = Path(argv[0]) if argv else REPO / "staging" / "dcr_ambiguous_resolution.json"
    man = json.loads(MANIFEST.read_text())
    targets = {r["reference"] for r in man["rows"]
               if r["verdict"].startswith(("AMBIGUOUS", "D2 single"))}
    # D2-single is re-examined too: the v1 bug mis-filed dash-code rows there.

    resolved, tally = [], Counter()
    seen = set()
    for line in open(DATA):
        try:
            rec = json.loads(line)
            info = rec["magnetic"]["manufacturerInfo"]
            di = info["datasheetInfo"]
            el = di["electrical"][0]
        except Exception:
            continue
        ref = str(info.get("reference"))
        if ref not in targets or ref in seen:
            continue
        d = el.get("dcResistances")
        plural = bool(d)
        d = d[0] if d else el.get("dcResistance")
        if not isinstance(d, dict):
            continue
        dcr = d.get("maximum") if d.get("maximum") is not None else d.get("nominal")
        rated = (el.get("ratedCurrents") or [None])[0]
        if not dcr or not rated or dcr * rated * rated <= 5.0:
            continue
        seen.add(ref)
        L = nominal(el.get("inductance"))
        isat = nominal(el.get("saturationCurrentPeak"))
        mech = di.get("mechanical") or {}
        sizes = [nominal(mech.get(k)) for k in ("length", "width", "height")]
        sizes = [s for s in sizes if s]
        size_mm = max(sizes) * 1000 if sizes else None

        code = eia_code(ref)
        code_in_dcr = code is not None and abs(dcr - code) < 0.01
        code_in_L = code is not None and L is not None and abs(L * 1e9 - code) < 0.01 * max(code, 1)

        base_viol, _ = side_checks(dcr, L, size_mm, isat)
        d4_viol, d4_low = side_checks(dcr / 1000, L, size_mm, isat)
        d2_viol, _ = side_checks(dcr, L, size_mm, isat)   # D2 leaves DCR untouched

        verdict, why = None, {}
        # MAG_ISAT_POWER alone cannot indict the DCR: Isat may carry the SAME
        # unit corruption as the rated current (both scraped from one source),
        # making Isat^2*DCR wrong by 1e6 around a perfectly correct DCR. The WE
        # 7608012xx rows demonstrated exactly this. A D4 verdict needs at least
        # one check that involves ONLY the DCR and vendor-stable fields:
        # DCR/L or DCR*size^2/L.
        dcr_only_viol = base_viol & {"MAG_DCR_PER_H", "MAG_DCR_GEOM"}
        if code_in_dcr:
            verdict = "D1"
            why["D1"] = f"DCR {dcr:g} equals the part-number code"
            if code_in_L:
                why["D1_L"] = f"inductance {L*1e9:g} nH carries the SAME code — no real L either"
        elif dcr_only_viol and not d4_viol and d2_viol == base_viol and not d4_low:
            verdict = "D4"
            why["D4"] = (f"current-independent checks violated as-is ({', '.join(sorted(base_viol))}), "
                         f"all cleared by DCR/1000, none by I/1000")
        elif not base_viol and size_mm:
            current_absurd = rated > AMPS_PER_MM * size_mm
            isat_absurd = isat and isat > 0 and rated / isat > RATED_ISAT_IMP
            if (current_absurd or isat_absurd) and d4_low:
                verdict = "D2"
                bits = []
                if isat_absurd:
                    bits.append(f"rated/Isat {rated/isat:.0f}x (validator IMP tier)")
                if current_absurd:
                    bits.append(f"{rated:g} A through a {size_mm:g} mm package")
                why["D2"] = ("DCR consistent with L and size; " + " and ".join(bits) +
                             "; DCR/1000 would violate the geometric low bound")

        if verdict in ("D4", "D2"):
            ok_win = validator_confirms(rec, plural,
                                        dcr / 1000 if verdict == "D4" else None,
                                        rated / 1000 if verdict == "D2" else None)
            ok_lose = validator_confirms(rec, plural,
                                         dcr / 1000 if verdict == "D2" else None,
                                         rated / 1000 if verdict == "D4" else None)
            if not ok_win or ok_lose:
                why["demoted"] = f"validator confirmation failed (win={ok_win}, lose-also-passes={ok_lose})"
                verdict = None

        tally[verdict or "still ambiguous"] += 1
        resolved.append({"reference": ref, "manufacturer": str(info.get("name")),
                         "dcResistanceOhm": dcr, "ratedCurrentA": rated,
                         "inductanceH": L, "maxDimMm": size_mm,
                         "saturationCurrentA": isat,
                         "verdict": verdict, "evidence": why})

    print(f"re-examined {len(seen)} rows\n")
    per = defaultdict(Counter)
    for r in resolved:
        per[r["verdict"] or "still ambiguous"][r["manufacturer"][:20]] += 1
    for v, n in tally.most_common():
        print(f"{v or 'still ambiguous':<18} {n:5}")
        print("      " + ", ".join(f"{m} {c}" for m, c in per[v or 'still ambiguous'].most_common(6)))
    out_path.write_text(json.dumps({"ticket": "ABT #351", "rows": resolved}, indent=1))
    print(f"\nresolution -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
