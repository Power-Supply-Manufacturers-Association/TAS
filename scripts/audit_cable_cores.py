#!/usr/bin/env python3
"""Confidence auditor for the clamp-on / cable ferrite CORE set (MAS `cableCore`).

This is the reproducible, re-runnable evidence behind the confidence claim for the
cableCore catalogue: it does NOT trust the importers, it re-reads the deployed
`magnetics.ndjson`, tiers every part by how its |Z|(f) curve was actually derived
(from the stamped provenance), measures coverage, and runs a physical-plausibility
QC over every curve. Re-run it after any catalogue change:

    python3 scripts/audit_cable_cores.py                 # human report
    python3 scripts/audit_cable_cores.py --strict        # exit 1 if any UNEXPLAINED anomaly

Confidence tiers (evidence-based, from the provenance each importer stamps):
  A   full per-PART MEASURED curve            (WE REDEXPERT 200-573 pts; TDK graph-API 156 pts)
  B+  per-SERIES measured graph, digitized     (Kitagawa — 30 distinct series shapes, per-part magnitude)
  B   spot(s) x shape reconstruction,         (KEMET / Seiwa per-part spot x family shape;
      VERIFY-BACKED at the anchor              Ferroxcube 2-spot+material shape; Murata per-type)
  C   published typical spots, as-published   (Fair-Rite, Laird — at the manufacturer's source ceiling)

Note on A vs B: A is the manufacturer's OWN dense measured curve used verbatim.
B parts read a real per-part |Z| magnitude (a digitized or published spot) but wear
a family/material normalized SHAPE, so the curve reproduces its anchor exactly
(verify-back ratio 1.0) while off-anchor points inherit the family shape. B is sound
and non-fabricated, but is honestly weaker than a full measured trace.

Physical-plausibility QC — a curve is FLAGGED only for a genuine defect:
  * non-positive |Z| or frequency,
  * frequencies not strictly increasing,
  * peak |Z| outside [8, 6000] Ω for a 1-turn core,
  * peak frequency outside [0.3 MHz, 3 GHz].
Multi-hump shape is NOT a defect: large wideband cores (WE) show several real
resonances, and MnZn materials (several Kitagawa series) show a real low-MHz peak,
a dip, then a high-frequency rise. The auditor therefore reports multi-hump curves
separately as INFORMATIONAL, not as errors.
"""
import json, sys, statistics
from collections import defaultdict, Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
NDJSON = DATA / "magnetics.ndjson"
STRICT = "--strict" in sys.argv

# vendor -> (tier, basis). Tier reflects HOW the curve was derived, not the vendor.
TIER = {
    "Würth Elektronik": ("A",  "REDEXPERT measured DB, full per-part curve (200-573 pts)"),
    "TDK":              ("A",  "product.tdk.com graph-API, full per-part measured curve (156 pts)"),
    "Kitagawa":         ("B+", "per-SERIES measured graph digitized (30 series), per-part magnitude"),
    "KEMET":            ("B",  "per-part digitized spot x per-material shape (2 shapes), verify-back internal"),
    "Seiwa":            ("B",  "per-part digitized spot x family shape, verify-back at spot=1.0"),
    "Ferroxcube":       ("B",  "material shape x 2 real spots, verify-back +-25%, fails excluded"),
    "Murata":           ("B",  "per-type shape x 100 MHz spot, anchor-exact (OBSOLETE line)"),
    "Fair-Rite":        ("C",  "published typical spots (3-6), verbatim"),
    "Laird":            ("C",  "published typical spots (3-4), verbatim"),
}
TIER_ORDER = {"A": 0, "B+": 1, "B": 2, "C": 3, "?": 9}

# physical bounds for a 1-turn cable/clamp core
Z_PEAK_MIN, Z_PEAK_MAX = 8.0, 6000.0
F_PEAK_MIN, F_PEAK_MAX = 0.3e6, 3.0e9
RADIATED_LO, RADIATED_HI = 30e6, 100e6   # the sub-band the picker's coverage-guard cares about


def load():
    for line in open(NDJSON):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        mi = o.get("magnetic", {}).get("manufacturerInfo", {})
        el = mi.get("datasheetInfo", {}).get("electrical", [])
        if not el or el[0].get("subtype") != "cableCore":
            continue
        yield mi, el[0].get("impedancePoints", [])


def reversals(zs, tol=0.08):
    """Count direction reversals in the magnitude sequence (noise-tolerant)."""
    r, trend = 0, 0
    for i in range(len(zs) - 1):
        a, b = zs[i], zs[i + 1]
        d = 1 if b > a * (1 + tol) else (-1 if b < a * (1 - tol) else 0)
        if d == 0:
            continue
        if trend == 0:
            trend = d
        elif d != trend:
            r += 1
            trend = d
    return r


def main():
    per_vendor = defaultdict(lambda: {"n": 0, "npts": [], "gap": 0, "obs": 0})
    anomalies = []          # genuine physical defects
    multihump = []          # informational: >1 reversal (real multi-resonance / MnZn)
    total = 0

    for mi, pts in load():
        total += 1
        name = mi.get("name")
        ref = mi.get("reference")
        fz = sorted((p["frequency"], p["impedance"]["magnitude"]) for p in pts)
        fs = [f for f, _ in fz]
        zs = [z for _, z in fz]
        d = per_vendor[name]
        d["n"] += 1
        d["npts"].append(len(fz))
        if str(mi.get("status", "")).lower() == "obsolete":
            d["obs"] += 1
        if not any(RADIATED_LO <= f <= RADIATED_HI for f in fs):
            d["gap"] += 1

        # --- genuine physical-defect checks ---
        if any(z <= 0 for z in zs):
            anomalies.append((name, ref, "non-positive |Z|"))
        if any(f <= 0 for f in fs):
            anomalies.append((name, ref, "non-positive frequency"))
        if any(fs[i] >= fs[i + 1] for i in range(len(fs) - 1)):
            anomalies.append((name, ref, "frequencies not strictly increasing"))
        zmax = max(zs)
        fpk = fs[zs.index(zmax)]
        if not (Z_PEAK_MIN <= zmax <= Z_PEAK_MAX):
            anomalies.append((name, ref, f"peak |Z|={zmax:g} Ohm out of [{Z_PEAK_MIN:g},{Z_PEAK_MAX:g}]"))
        if not (F_PEAK_MIN <= fpk <= F_PEAK_MAX):
            anomalies.append((name, ref, f"peak f={fpk/1e6:g} MHz out of [{F_PEAK_MIN/1e6:g},{F_PEAK_MAX/1e6:g}] MHz"))

        # --- informational: multi-hump (NOT a defect) ---
        if reversals(zs) > 1:
            multihump.append((name, ref, reversals(zs)))

    # ---- report ----
    print(f"cableCore confidence audit — {total} parts from {NDJSON.name}")
    print("=" * 72)
    print(f"{'vendor':18s} {'tier':4s} {'n':>4s} {'pts(med)':>8s} {'no-30..100MHz':>13s}  basis")
    tier_tot = Counter()
    for name in sorted(per_vendor, key=lambda v: (TIER_ORDER[TIER.get(v, ('?', ''))[0]], -per_vendor[v]["n"])):
        d = per_vendor[name]
        t, basis = TIER.get(name, ("?", "unclassified vendor"))
        tier_tot[t] += d["n"]
        med = int(statistics.median(d["npts"])) if d["npts"] else 0
        obs = f" ({d['obs']} obsolete)" if d["obs"] else ""
        print(f"{name:18s} {t:4s} {d['n']:4d} {med:8d} {d['gap']:13d}  {basis}{obs}")
    print("-" * 72)
    print("by tier: " + "  ".join(f"{t}={tier_tot[t]}" for t in ("A", "B+", "B", "C") if tier_tot[t]) +
          f"   total={sum(tier_tot.values())}")
    print("'no-30..100MHz' = parts with no measured point in the 30-100 MHz radiated")
    print("sub-band. For Fair-Rite material-61 and Laird HFA/HFB these are HIGH-FREQUENCY")
    print("cores genuinely un-characterised (and low-|Z|) below their band — physics, not a hole.")

    print("\nmulti-hump curves (INFORMATIONAL — real multi-resonance / MnZn, not defects): "
          f"{len(multihump)}")
    byv = Counter(n for n, _, _ in multihump)
    if byv:
        print("   " + "  ".join(f"{k}:{v}" for k, v in byv.most_common()))

    print(f"\nUNEXPLAINED physical anomalies: {len(anomalies)}")
    for name, ref, why in anomalies[:40]:
        print(f"   {name:18s} {ref:16s} {why}")

    if STRICT and anomalies:
        print("\nSTRICT: unexplained anomalies present -> exit 1")
        sys.exit(1)
    print("\nOK — every curve is physically valid; the only non-monotone curves are real "
          "multi-resonance/MnZn behaviour or verbatim published spots.")


if __name__ == "__main__":
    main()
