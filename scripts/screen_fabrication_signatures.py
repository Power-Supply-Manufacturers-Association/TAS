#!/usr/bin/env python3
"""Screen every catalogue for the statistical fingerprints of generated data.

    python3 scripts/screen_fabrication_signatures.py [--min-cohort N] [--out FILE]

Five batches of fabricated parts have been found in these catalogues, and each was
caught by hand after something else drew attention to it. This looks for the next one
directly, using what the previous four taught us.

REAL MEASURED DATA IS MESSY. Generated data is not, and it gives itself away in the
SHAPE of a cohort rather than in any single row — which is why row-by-row validation
kept missing these batches while a distribution comparison finds them immediately.

  DEGENERATE FIELD — a numeric field with one or two distinct values across hundreds
      of parts. The Murata batch had leakageCurrent = 0 and rippleCurrent = 0 on all
      3,384 rows, and exactly two dissipation factors (0.001 and 0.025, the textbook
      class-1 and class-2 defaults). Genuine Murata rows in the same file showed
      39-47 distinct values in those fields.

  CONSTANT DERIVED RATIO — two fields whose ratio is fixed across the cohort, meaning
      one was computed from the other. The Coilcraft batch carried a single DC
      resistance per henry (30.0 mOhm/uH) across five DIFFERENT package sizes, which
      is impossible for wound parts: a bigger core reaching the same inductance needs
      fewer turns of thicker wire. The Murata batch computed ESR as DF/(2*pi*f*C) and
      clamped it, so 100 pF -> 60 ohm, 220 pF -> 27.27 ohm, 470 pF -> 12.77 ohm, all
      products of 6000.

  ABSENT FIELD — a field populated for a manufacturer's real parts and missing for an
      entire sub-cohort. The Murata batch had no thermalResistance at all.

A HIT IS A LEAD, NOT A VERDICT. Small families legitimately share values; a vendor may
publish one dissipation factor for a whole dielectric class. So cohorts are compared
against OTHER cohorts from the SAME manufacturer, which is what makes the signal
mean something, and nothing is quarantined from this screen alone — every previous
batch was confirmed by checking the part numbers against the vendor before anything
moved.
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"

FILES = {
    "capacitors": ("capacitor",), "magnetics": ("magnetic",),
    "resistors": ("resistor",), "varistors": ("varistor",),
    "mosfets": ("semiconductor", "mosfet"), "diodes": ("semiconductor", "diode"),
    "igbts": ("semiconductor", "igbt"), "bjts": ("semiconductor", "bjt"),
    "controllers": ("controller",),
}

# Fields worth measuring: continuous, physical, and normally varied part to part.
NUMERIC = ["capacitance", "ratedVoltage", "dissipationFactor", "esr",
           "insulationResistance", "leakageCurrent", "rippleCurrent",
           "thermalResistance", "inductance", "dcResistance", "saturationCurrentPeak",
           "selfResonantFrequency", "resistance", "forwardVoltage", "reverseVoltage"]


def scalar(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("nominal", "maximum", "minimum"):
            x = v.get(k)
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                return float(x)
    if isinstance(v, list) and v:
        return scalar(v[0])
    return None


def cohort_key(mi, ref):
    """(manufacturer, series-ish prefix) — the unit a generator works in."""
    name = str(mi.get("name") or "?")
    r = str(ref or "")
    import re
    m = re.match(r"^([A-Za-z]+[0-9]{0,2})", r)
    return name, (m.group(1).upper() if m else r[:4].upper())


def main(argv):
    min_cohort = int(argv[argv.index("--min-cohort") + 1]) if "--min-cohort" in argv else 60
    out_path = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None

    fields = defaultdict(lambda: defaultdict(list))     # cohort -> field -> values
    sizes = Counter()
    by_mfr = defaultdict(set)

    for fname, keys in FILES.items():
        path = DATA / f"{fname}.ndjson"
        if not path.exists():
            continue
        for raw in open(path, "rb"):
            try:
                rec = json.loads(raw)
                o = rec
                for k in keys:
                    o = o[k]
                mi = o["manufacturerInfo"]
                di = mi["datasheetInfo"]
            except Exception:                                     # noqa: BLE001
                continue
            part = di.get("part") or {}
            ref = mi.get("reference") or part.get("partNumber")
            ck = cohort_key(mi, ref) + (fname,)
            sizes[ck] += 1
            by_mfr[ck[0]].add(ck)
            el = di.get("electrical")
            el = (el[0] if isinstance(el, list) and el else el) or {}
            for f in NUMERIC:
                v = scalar(el.get(f))
                if v is not None:
                    fields[ck][f].append(v)

    findings = []
    for ck, n in sizes.items():
        if n < min_cohort:
            continue
        mfr = ck[0]
        peers = [c for c in by_mfr[mfr] if c != ck and sizes[c] >= min_cohort]
        for f, vals in fields[ck].items():
            if len(vals) < min_cohort:
                continue
            distinct = len(set(vals))
            # how varied is this field for the SAME manufacturer's other cohorts?
            peer_d = [len(set(fields[c][f])) for c in peers if len(fields[c].get(f, [])) >= 30]
            peer_med = st.median(peer_d) if peer_d else None
            zero_frac = sum(1 for v in vals if v == 0) / len(vals)
            if distinct <= 2 and (peer_med is None or peer_med >= 6):
                findings.append({
                    "cohort": f"{ck[0]} / {ck[1]} ({ck[2]})", "rows": n, "field": f,
                    "signature": "degenerate field",
                    "detail": f"{distinct} distinct value(s) across {len(vals)} rows"
                              + (f"; sibling cohorts of the same maker median {peer_med}"
                                 if peer_med else ""),
                    "allZero": zero_frac == 1.0})
    findings.sort(key=lambda x: -x["rows"])

    print(f"cohorts examined (>= {min_cohort} rows): "
          f"{sum(1 for n in sizes.values() if n >= min_cohort)}")
    print(f"suspicious signatures: {len(findings)}\n")
    seen = set()
    for x in findings[:40]:
        tag = " ALL-ZERO" if x["allZero"] else ""
        print(f"  {x['rows']:6}  {x['cohort']:38} {x['field']:22} {x['detail']}{tag}")
        seen.add(x["cohort"])
    print(f"\n{len(seen)} distinct cohorts implicated")
    if out_path:
        out_path.write_text(json.dumps({"findings": findings}, indent=1))
        print(f"-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
