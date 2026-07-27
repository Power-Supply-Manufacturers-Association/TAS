#!/usr/bin/env python3
"""Build schema-correct MAS `cableCore` records for Würth Elektronik clamp-on /
cable ferrite cores from their measured REDEXPERT impedance data.

Source: WE REDEXPERT module 2 "Ferrites for Cable Assembly", characteristic 136
(|Z| magnitude), measurement 143138 (1 turn). Pulled via the in-page fetch (MCP
trick) into data/we_cable_ferrites_raw.json — each record carries the part's
measured |Z|(f) curve (1 turn) joined to its product row (curve.ID == product.ID,
verified 219/219). The curves were cross-checked against the datasheet spot value
Impedance @ 25 MHz (agreement within ~10%), confirming the join.

Uses the MAS `cableCore` electrical variant (subtype 'cableCore' with
impedancePoints + numberTurns), NOT chipBead — these are threaded/clamp-on cable
cores, a distinct part class. Only sourced fields are written; nothing is
fabricated (rated current / DCR / SRF are omitted — not published for a passive
core the cable is threaded through). The measured curve is kept as-is (real
points, never resampled); sub-1 MHz noise-floor points (|Z| < ~0.1 Ω) are
dropped, not interpolated.

    python3 we_cable_cores_import.py            # DRY RUN: build + sanity-check + show
    python3 we_cable_cores_import.py --apply    # write data/we_cable_cores.ndjson
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RAW = DATA / "we_cable_ferrites_raw.json"
OUT = DATA / "we_cable_cores.ndjson"
APPLY = "--apply" in sys.argv
RETRIEVED = "2026-07-28"

# Physics sanity gates (a real cable ferrite curve must satisfy these; anything
# failing is a data-transcription error, quarantined and reported — not shipped).
Z_MIN_OHM, Z_MAX_OHM = 0.001, 20000.0   # |Z| stays positive and physically bounded
F_MIN_HZ, F_MAX_HZ = 1e3, 5e9            # 1 kHz .. 5 GHz measurement window
PEAK_F_LO, PEAK_F_HI = 3e6, 2.5e9        # a cable ferrite peaks somewhere in HF (NiZn parts peak ~2 GHz)
NOISE_FLOOR_OHM = 0.1                    # drop sub-|Z| noise-floor points (<1 MHz)


def material_name(permeability):
    """Nanocrystalline cores run µi in the thousands; ferrites a few hundred.
    Discriminate on the published permeability rather than guessing from the
    series name (WE-NCF is a µ=620 ferrite despite the 'NC' prefix)."""
    try:
        return "Nanocrystalline" if int(float(permeability)) >= 5000 else "Ferrite"
    except (TypeError, ValueError):
        return "Ferrite"


def build_impedance_points(curve_mhz_ohm):
    """Measured [f_MHz, |Z|_Ω] -> MAS impedancePoints [{impedance:{magnitude},
    frequency}]. Keeps real measured points (sorted, positive), drops the
    sub-1 MHz noise floor. No resampling, no extrapolation."""
    pts = []
    for f_mhz, z in sorted(curve_mhz_ohm):
        f_hz = float(f_mhz) * 1e6
        if not (f_hz > 0 and z > 0):
            continue
        if f_hz < 1e6 and z < NOISE_FLOOR_OHM:   # sub-MHz measurement noise floor
            continue
        pts.append({"impedance": {"magnitude": float(z)}, "frequency": f_hz})
    return pts


def sanity(mpn, pts):
    """Return a list of physics findings (empty == valid)."""
    findings = []
    if len(pts) < 8:
        findings.append(f"only {len(pts)} usable curve points")
        return findings
    freqs = [p["frequency"] for p in pts]
    mags = [p["impedance"]["magnitude"] for p in pts]
    if any(b <= a for a, b in zip(freqs, freqs[1:])):
        findings.append("frequencies not strictly increasing")
    if not all(Z_MIN_OHM <= m <= Z_MAX_OHM for m in mags):
        findings.append(f"|Z| out of [{Z_MIN_OHM},{Z_MAX_OHM}] Ω (min {min(mags):.3g}, max {max(mags):.3g})")
    if not (F_MIN_HZ <= freqs[0] and freqs[-1] <= F_MAX_HZ):
        findings.append(f"frequency span {freqs[0]:.3g}..{freqs[-1]:.3g} Hz outside window")
    f_peak = freqs[mags.index(max(mags))]
    if not (PEAK_F_LO <= f_peak <= PEAK_F_HI):
        findings.append(f"|Z| peak at {f_peak/1e6:.1f} MHz outside {PEAK_F_LO/1e6:g}..{PEAK_F_HI/1e6:g} MHz")
    return findings


def build(rec):
    mpn = rec["mpn"]
    series = rec.get("series") or ""
    material = material_name(rec.get("permeability"))
    z100 = rec.get("z100_ohm")
    url = f"https://www.we-online.com/components/products/datasheet/{mpn}.pdf"
    pts = build_impedance_points(rec["curve_MHz_ohm"])
    desc = f"{series} cable ferrite core ({rec.get('type','')}), " \
           f"|Z|≈{z100} Ω @ 100 MHz, 1 turn" if z100 else f"{series} cable ferrite core"
    mech = {}
    for key, src in (("length", "sizeL_m"), ("width", "sizeW_m"), ("height", "sizeH_m")):
        v = rec.get(src)
        if isinstance(v, (int, float)) and v > 0:
            mech[key] = {"nominal": float(v)}
    obj = {"magnetic": {"manufacturerInfo": {
        "name": "Würth Elektronik",
        "reference": mpn,
        "status": "production",
        "family": series,
        "datasheetUrl": url,
        "datasheetInfo": {
            "part": {"description": desc, "material": material, "shielded": False},
            "electrical": [{
                "subtype": "cableCore",
                "numberTurns": 1,
                "impedancePoints": pts,
            }],
            "provenance": [{
                "source": "manufacturerDatabase",
                "sourceName": "Würth Elektronik REDEXPERT (Ferrites for Cable Assembly, |Z| 1 turn)",
                "sourceUrl": url,
                "retrievedDate": RETRIEVED,
            }],
        },
    }}}
    if mech:
        obj["magnetic"]["manufacturerInfo"]["datasheetInfo"]["mechanical"] = mech
    return obj, pts


def main():
    raw = json.load(open(RAW))
    records = raw["records"]
    built, invalid, dup = [], 0, 0
    seen = set()
    quarantine = []
    for rec in records:
        mpn = str(rec.get("mpn") or "").strip()
        if not mpn or mpn in seen:
            dup += 1
            continue
        obj, pts = build(rec)
        findings = sanity(mpn, pts)
        if findings:
            invalid += 1
            quarantine.append(f"{mpn}: {findings}")
            continue
        seen.add(mpn)
        built.append(obj)

    from collections import Counter
    by_series = Counter(o["magnetic"]["manufacturerInfo"]["family"] for o in built)
    by_mat = Counter(o["magnetic"]["manufacturerInfo"]["datasheetInfo"]["part"]["material"] for o in built)
    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — WE cable-core (cableCore) population from {RAW.name}")
    print(f"  built+sane: {len(built)}   quarantined(physics): {invalid}   skipped(dup/no-mpn): {dup}")
    print(f"  by series: {dict(by_series.most_common(10))}")
    print(f"  by material: {dict(by_mat)}")
    if built:
        s = built[0]["magnetic"]["manufacturerInfo"]
        e = s["datasheetInfo"]["electrical"][0]
        print(f"  sample: {s['reference']} {s['family']} — {len(e['impedancePoints'])} pts, "
              f"f {e['impedancePoints'][0]['frequency']/1e6:.3g}..{e['impedancePoints'][-1]['frequency']/1e6:.0f} MHz")
    if quarantine:
        print(f"  QUARANTINED {len(quarantine)}: {quarantine[:8]}")
    if not APPLY:
        print(f"\n(dry run — re-run with --apply to write {OUT.name})")
        return
    with open(OUT, "w") as f:
        for o in built:
            f.write(json.dumps(o) + "\n")
    print(f"\nwrote {len(built)} cableCore records to {OUT}")


if __name__ == "__main__":
    main()
