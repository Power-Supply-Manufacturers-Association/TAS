#!/usr/bin/env python3
"""Build MAS `cableCore` records for Kitagawa Industries clamp-on ferrite cores
(sleeve / toroidal / low-cut ferrite clamps) from their EMC catalog PDFs.

DATA DISCIPLINE — digitize-and-verify-back (user authorised):
Each Kitagawa catalog publishes, per part, ONE guaranteed-minimum impedance spot
(|Z| ≧ N at a spec frequency — 100 MHz for most, 10 MHz for the low-cut MRFC /
RFC-MA / RFCW-MA, 1 MHz for the BFCWN low-freq variants) PLUS a per-part measured
|Z|(f) log-log graph ("measured data for reference, not guaranteed"). The graphs
were pixel-digitized (colour extraction + log-log axis calibration, not eyeballed)
into a per-series SHAPE normalized to 1.0 at the spec frequency. Reconstruction:

    |Z|(f) = z_spot_min * shape_norm(f)

so the magnitude sits exactly on the PUBLISHED GUARANTEED MINIMUM at the spec
frequency (conservative — never overstates), and the frequency dependence is the
real measured shape. VERIFY-BACK (recorded in each source file's verify_note): the
digitized typical curves read ~1.3-2x the ≧ minima consistently across every part
of a series — the expected typical-vs-guaranteed-floor guard band, which confirms
the digitization tracks the published values rather than drifting. Nothing is
invented: shape from the measured graph, magnitude from the published spot. null
shape points (curve did not extend that far) are dropped, never extrapolated into
the emitted curve.

    python3 kitagawa_cable_cores_import.py            # DRY RUN
    python3 kitagawa_cable_cores_import.py --apply    # write data/kitagawa_cable_cores.ndjson
"""
import json, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RAW = DATA / "kitagawa_curves.json"
OUT = DATA / "kitagawa_cable_cores.ndjson"
APPLY = "--apply" in sys.argv
RETRIEVED = "2026-07-28"
URL = "https://global.techno-kitagawa.com/product/emc-list/emc_filters"

FAMILY = {
    "GRFC": "Sleeve Ferrite Clamp Cable Core", "RFC-A": "Sleeve Ferrite Clamp Cable Core",
    "RFCW": "Sleeve Ferrite Clamp Cable Core", "KRFC": "High-µ Ferrite Clamp Cable Core",
    "KTFC": "High-µ Ferrite Clamp Cable Core", "BFCWN": "Ferrite Clamp Cable Core",
    "MRFC": "Low-Cut Ferrite Clamp Cable Core", "RFC-MA": "Low-Cut Ferrite Clamp Cable Core",
    "GTFC": "Toroidal Ferrite Clamp Cable Core", "GTFCK": "Toroidal Ferrite Clamp Cable Core",
    "GTFCR": "Toroidal Ferrite Clamp Cable Core",
    # non-split (solid-ring) core families
    "GTR": "Toroidal Ferrite Cable Core", "GTRE": "Toroidal Ferrite Cable Core",
    "GTRCA": "Toroidal Ferrite Cable Core", "KTR": "Toroidal Ferrite Cable Core",
    "TRCB": "Low-Cut Toroidal Cable Core", "TRM": "Low-Cut Toroidal Cable Core",
    "TRMH": "Low-Cut High-µ Toroidal Cable Core", "GRI": "Sleeve Ferrite Cable Core",
    "GRIB": "Rib Ferrite Cable Core", "GRIP": "Grip Ferrite Cable Core",
    "GSSH": "Flat Cable Ferrite Core", "GSSC": "Flat Cable Ferrite Core",
    "BCN": "Block Ferrite Cable Core", "GFPC": "FPC Ferrite Cable Core",
    "GFPH": "FPC Ferrite Cable Core", "GFPO": "FPC Ferrite Cable Core",
    "BRE": "Broadband Ferrite Cable Core", "BREK": "Broadband Ferrite Cable Core",
    "MPTR": "Metal-Composite Cable Core",
}
# the 11 split-clamp series default to snapOn; everything else is non-split -> solidRing
CLAMP_SERIES = {"GRFC", "RFC-A", "RFCW", "KRFC", "KTFC", "BFCWN", "MRFC", "RFC-MA", "GTFC", "GTFCK", "GTFCR"}
VALID_FORM = {"solidRing", "snapOn", "split", "screwable"}
TURNS = {"TRM": 2, "MPTR": 5}          # per-series spec turn count (default 1)


def form_for(series, series_obj):
    f = series_obj.get("form")
    if f in VALID_FORM:
        return f
    return "snapOn" if series in CLAMP_SERIES else "solidRing"


def part_shape(series_obj, part):
    """Return (shape_dict, spec_freq_mhz) for a part, handling per-part and per-series shapes."""
    mpn = part["mpn"]
    spec = part.get("spec_freq_mhz") or series_obj.get("spec_freq_mhz") or 100
    # per-part shapes (BFCWN)
    if "shapes" in series_obj and mpn in series_obj["shapes"]:
        s = series_obj["shapes"][mpn]
        spec = s.get("spec_freq_mhz", spec)
        shape = s.get("shape_norm100") or s.get("shape_norm") or {}
        return shape, spec
    # RFCW low-freq variant carries a distinct shape
    if series_obj["series"] == "RFCW" and spec == 10 and "shape_13ma_norm10" in series_obj:
        return series_obj["shape_13ma_norm10"], 10
    # generic: first key starting with shape_norm that is a freq->value dict
    for k, v in series_obj.items():
        if k.startswith("shape_norm") and isinstance(v, dict) and v:
            return v, spec
    return {}, spec


def build(series, series_obj, part):
    shape, spec = part_shape(series_obj, part)
    pts = []
    for k, v in shape.items():
        if v is None:
            continue
        f = float(k) * 1e6
        z = float(part["z_spot_ohm"]) * float(v)
        if f > 0 and z > 0:
            pts.append({"impedance": {"magnitude": round(z, 3)}, "frequency": f})
    pts.sort(key=lambda p: p["frequency"])
    if len(pts) < 3:
        return None
    mpn = part["mpn"]
    family = FAMILY.get(series, "Ferrite Clamp Cable Core")
    cm = part.get("cable_max_mm")
    zspec = float(part["z_spot_ohm"])
    turns = TURNS.get(series, 1)
    form = form_for(series, series_obj)
    desc = (f"Kitagawa {family} ("
            f"{'≤%g mm cable, ' % cm if cm else ''}"
            f"|Z|≥{zspec:g} Ω @ {spec:g} MHz published min, {turns} turn{'s' if turns > 1 else ''})")
    electrical = {"subtype": "cableCore", "numberTurns": turns, "impedancePoints": pts, "mountingForm": form}
    if cm and cm > 0:
        electrical["maximumCableOuterDiameter"] = float(cm) / 1000.0
    return {"magnetic": {"manufacturerInfo": {
        "name": "Kitagawa", "reference": mpn, "status": "production", "family": family, "datasheetUrl": URL,
        "datasheetInfo": {
            "part": {"description": desc, "material": "Ferrite", "shielded": False},
            "electrical": [electrical],
            "provenance": [{"source": "manufacturerParametric",
                            "sourceName": (f"Kitagawa EMC catalog ({series}) — measured |Z|(f) graph "
                                           f"pixel-digitized for shape, magnitude anchored to the published "
                                           f"guaranteed-minimum |Z|≥{zspec:g} Ω @ {spec:g} MHz"),
                            "sourceUrl": URL, "retrievedDate": RETRIEVED}],
        }}}}


def main():
    data = json.load(open(RAW))
    built, skipped = [], []
    for series, obj in data.items():
        for part in obj["parts"]:
            rec = build(series, obj, part)
            (built.append(rec) if rec else skipped.append(part["mpn"]))
    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — Kitagawa clamp cores from {RAW.name}")
    print(f"  built: {len(built)}   skipped (<3 curve points): {len(skipped)} {skipped}")
    print(f"  by family: {dict(Counter(b['magnetic']['manufacturerInfo']['family'] for b in built))}")
    npts = [len(b['magnetic']['manufacturerInfo']['datasheetInfo']['electrical'][0]['impedancePoints']) for b in built]
    print(f"  curve points/part: min={min(npts)} max={max(npts)}")
    if built:
        e = built[0]["magnetic"]["manufacturerInfo"]
        pts = e["datasheetInfo"]["electrical"][0]["impedancePoints"]
        crv = ", ".join(f"{p['frequency']/1e6:g}M:{p['impedance']['magnitude']:g}" for p in pts)
        print(f"  sample: {e['reference']} -> [{crv}]")
    if not APPLY:
        print(f"\n(dry run — --apply writes {OUT.name})"); return
    with open(OUT, "w") as f:
        for b in built:
            f.write(json.dumps(b) + "\n")
    print(f"\nwrote {len(built)} Kitagawa cableCore records to {OUT}")


if __name__ == "__main__":
    main()
