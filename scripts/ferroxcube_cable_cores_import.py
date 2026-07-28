#!/usr/bin/env python3
"""Build MAS `cableCore` records for Ferroxcube cable shields (CST tubular /
CSA·CSC snap-on / CSF flat) from the Ferroxcube "Soft Ferrites and Accessories"
data handbook (2013).

DATA DISCIPLINE — reconstruct-and-verify-back (authorised graph/curve reconstruction):
Ferroxcube publishes, per part, only TWO impedance spots (|Ztyp| at 25 and 100
MHz) — below the >=3-point minimum. But the material datasheets give a per-material
reference |Z|(f) curve (measured on a standard bead) as a numeric table:
  * 3S4 (opt. 10-300 MHz): |Z| = {3:25, 30:60, 100:80, 300:90} Ω
  * 4S2 (opt. 30-1000 MHz): |Z| = {30:50, 300:90} Ω  (+100 MHz log-interpolated = 68)
For a solid toroid of a given material, |Z|(f) = k * shape(f) where k = N²·Ae/le is
a geometry constant and shape(f) = ω·µ0·|µ(f)| is a MATERIAL property (geometry-
independent). So each part's curve is the material shape scaled by a single k. We
fit k by the GEOMETRIC MEAN of the two per-part anchors (k = sqrt((a25/R25)·(a100/
R100))) and then VERIFY BACK: the reconstructed k·shape must reproduce BOTH published
spots within tolerance (default 25%). Parts whose own 25/100 ratio is inconsistent
with the material shape (small cores with dimensional resonance, split cores with an
air gap) FAIL verification and are EXCLUDED and reported — never force-fitted. Every
value used is real Ferroxcube data (material shape + the part's two spots); nothing
is invented.

    python3 ferroxcube_cable_cores_import.py            # DRY RUN (shows pass/fail)
    python3 ferroxcube_cable_cores_import.py --apply    # write data/ferroxcube_cable_cores.ndjson
"""
import json, re, sys, math
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RAW = DATA / "ferrox_hb.txt"          # pdftotext -layout of the handbook (staged alongside)
OUT = DATA / "ferroxcube_cable_cores.ndjson"
APPLY = "--apply" in sys.argv
TOL = 0.25
RETRIEVED = "2026-07-28"
URL = "https://www.ferroxcube.com/en-global/products_ferroxcube/detail/emi/cable_shield"

# per-material reference |Z|(f) shape (Ω) at frequency (Hz), from the material datasheets
SHAPE = {
    "3S4": {3e6: 25.0, 30e6: 60.0, 100e6: 80.0, 300e6: 90.0},
    "4S2": {30e6: 50.0, 100e6: 68.0, 300e6: 90.0},   # 100 MHz log-log interpolated from 30/300 anchors
}
FAMILY = {"CST": ("High-Frequency Tubular Cable Shield", "solidRing"),
          "CSF": ("Flat Cable Shield", "solidRing"),
          "CSU": ("Cylindrical Cable Shield", "solidRing"),
          "CSA": ("Snap-On Cable Shield", "snapOn"),
          "CSC": ("Snap-On Cable Shield", "snapOn")}
PART_RE = re.compile(r"^\s*(CS[TFUAC])([\d.]+)/([\d.]+)/([\d.]+)-(\dS\d)(-EN)?\b(.*)$")


def loglog_at(shape, f):
    """|Z| of the material reference at frequency f (Hz) by log-log interp/extrap."""
    fs = sorted(shape)
    if f <= fs[0]:
        (f0, f1) = fs[0], fs[1]
    elif f >= fs[-1]:
        (f0, f1) = fs[-2], fs[-1]
    else:
        f0 = max(x for x in fs if x <= f); f1 = min(x for x in fs if x >= f)
        if f0 == f1:
            return shape[f0]
    z0, z1 = shape[f0], shape[f1]
    s = (math.log(z1) - math.log(z0)) / (math.log(f1) - math.log(f0))
    return z0 * math.exp(s * (math.log(f) - math.log(f0)))


def parse():
    parts, seen = [], set()
    for line in open(RAW, encoding="utf-8", errors="replace"):
        m = PART_RE.match(line)
        if not m:
            continue
        fam, od, idd, L, mat, en, rest = m.groups()
        if mat not in SHAPE:
            continue
        rest = re.sub(r"\(\d+\)", "", rest)                 # strip footnote markers e.g. 36(2)
        ints = re.findall(r"(?<![\d.])(\d+)(?![\d.])", rest)  # bare integers only (dims are decimals)
        if len(ints) < 2:
            continue
        z25, z100 = float(ints[-2]), float(ints[-1])
        if z25 <= 0 or z100 <= 0:
            continue
        base = f"{fam}{od}/{idd}/{L}-{mat}"                 # dedup -EN (same ferrite, encapsulated)
        if base in seen:
            continue
        seen.add(base)
        parts.append(dict(fam=fam, mpn=f"{base}{en or ''}", od=float(od), idd=float(idd),
                          L=float(L), mat=mat, z25=z25, z100=z100))
    return parts


def reconstruct(p):
    shape = SHAPE[p["mat"]]
    r25, r100 = loglog_at(shape, 25e6), loglog_at(shape, 100e6)
    k = math.sqrt((p["z25"] / r25) * (p["z100"] / r100))    # geometric-mean calibration
    # verify back: reproduce BOTH published anchors within TOL
    e25 = abs(k * r25 - p["z25"]) / p["z25"]
    e100 = abs(k * r100 - p["z100"]) / p["z100"]
    ok = e25 <= TOL and e100 <= TOL
    freqs = sorted(set(list(shape) + [25e6, 100e6]))
    pts = [{"impedance": {"magnitude": round(k * loglog_at(shape, f), 2)}, "frequency": f} for f in freqs]
    return ok, max(e25, e100), pts


def build(p, pts):
    family, form = FAMILY[p["fam"]]
    z100 = next(x["impedance"]["magnitude"] for x in pts if abs(x["frequency"] - 1e8) < 1)
    desc = (f"Ferroxcube {family} ({p['mat']} material, ≤{p['idd']:g} mm cable), "
            f"|Z|≈{z100:g} Ω @ 100 MHz, 1 turn")
    return {"magnetic": {"manufacturerInfo": {
        "name": "Ferroxcube", "reference": p["mpn"], "status": "production", "family": family,
        "datasheetUrl": URL,
        "datasheetInfo": {
            "part": {"description": desc, "material": "Ferrite", "shielded": False},
            "electrical": [{"subtype": "cableCore", "numberTurns": 1, "impedancePoints": pts,
                            "mountingForm": form, "maximumCableOuterDiameter": p["idd"] / 1000.0}],
            "provenance": [{"source": "manufacturerParametric",
                            "sourceName": ("Ferroxcube Soft Ferrites & Accessories handbook — per-part "
                                           "|Z| spots (25/100 MHz) reconstructed onto the material's "
                                           "measured |Z|(f) reference curve, verified back to both spots"),
                            "sourceUrl": URL, "retrievedDate": RETRIEVED}],
            "mechanical": {"width": {"nominal": p["od"] / 1000.0}, "length": {"nominal": p["L"] / 1000.0}},
        }}}}


def main():
    parts = parse()
    built, excluded = [], []
    for p in parts:
        ok, err, pts = reconstruct(p)
        (built if ok else excluded).append((p, err, pts))
    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — Ferroxcube cable shields from {RAW.name}")
    print(f"  parsed: {len(parts)}   verified (reconstruct hits both spots ≤{TOL:.0%}): {len(built)}   excluded: {len(excluded)}")
    print(f"  by family: {dict(Counter(FAMILY[p['fam']][0] for p,_,_ in built))}")
    print(f"  by material: {dict(Counter(p['mat'] for p,_,_ in built))}")
    if excluded:
        print("  EXCLUDED (own 25/100 ratio inconsistent with material shape — not force-fitted):")
        for p, err, _ in excluded:
            print(f"    {p['mpn']}: z25={p['z25']:g} z100={p['z100']:g} (ratio {p['z25']/p['z100']:.2f}), verify err {err:.0%}")
    if built:
        p, err, pts = built[0]
        curve = ", ".join(f"{x['frequency']/1e6:g}MHz:{x['impedance']['magnitude']:g}Ω" for x in pts)
        print(f"  sample: {p['mpn']} -> [{curve}]  (verify err {err:.1%})")
    if not APPLY:
        print(f"\n(dry run — --apply writes {OUT.name})"); return
    with open(OUT, "w") as f:
        for p, _, pts in built:
            f.write(json.dumps(build(p, pts)) + "\n")
    print(f"\nwrote {len(built)} Ferroxcube cableCore records to {OUT}")


if __name__ == "__main__":
    main()
