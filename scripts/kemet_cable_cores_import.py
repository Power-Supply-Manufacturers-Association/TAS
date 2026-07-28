#!/usr/bin/env python3
"""Build MAS `cableCore` records for KEMET / NEC-Tokin ESD-SR clamp-on ferrite
cable cores (round-cable snap-on ESD-SR / -H / -XVE and flat-cable ESD-FPD) from
the KEMET/Yageo datasheets.

DATA DISCIPLINE — digitize-and-verify-back, with a caveat:
KEMET publishes NO numeric |Z| spot for these parts — only frequency-range marks
and a per-part |Z|(f) CHART. So (unlike the other vendors) the whole curve is
digitized from the manufacturer's published chart: `z_spot_ohm` is the digitized
1-turn |Z| at the part's spec frequency, and the per-material `shape_norm*` is the
digitized curve shape. Reconstruction |Z|(f) = z_spot * shape_norm(f). The verify-
back here is internal (shape*spot reproduces the published chart it came from; large
cores cross-read twice) rather than against a separate published number — recorded
in provenance. Nothing is invented: every value is read off KEMET's chart.

Routing: ESD-SR E5005 carries TWO FM shapes — a large-core peak shape and a small-
core monotonic shape (whose `applies_to` lists the small MPNs). AM-band MnZn parts
(spec at 1-10 MHz) have NO digitized shape and are skipped (can't make >=3 points
without fabricating). `G` suffix = case colour only (identical core) -> deduped.

    python3 kemet_cable_cores_import.py            # DRY RUN
    python3 kemet_cable_cores_import.py --apply    # write data/kemet_cable_cores.ndjson
"""
import json, sys, glob
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
SRC = DATA / "kemet"
OUT = DATA / "kemet_cable_cores.ndjson"
APPLY = "--apply" in sys.argv
RETRIEVED = "2026-07-28"
URL = "https://www.kemet.com/en/us/emi-cores.html"

FAMILY = {"ESD-SR": "Snap-On Ferrite Cable Core", "ESD-SR-H": "Snap-On Ferrite Cable Core",
          "ESD-SR-XVE": "Snap-On Ferrite Cable Core (high-temp)", "ESD-FPD-1": "Flat-Cable Ferrite Core"}


def get_shape(d, part):
    """Pick the shape dict + spec freq for a part; None if no usable shape."""
    mpn = part["mpn"]
    spec = part.get("spec_freq_mhz") or d.get("spec_freq_mhz") or 100
    for k, v in d.items():
        if k.startswith("shape") and isinstance(v, dict) and mpn in (v.get("applies_to") or []):
            return v, spec
    main = d.get("shape_norm100")
    if main and spec == 100:          # FM parts use the main 100 MHz shape
        return main, spec
    return None, spec                 # AM parts (spec 1-10 MHz) have no digitized shape -> skip


def build(series, d, part):
    shape, spec = get_shape(d, part)
    if shape is None:
        return None, "no-shape(AM band)"
    pts = []
    for k, v in shape.items():
        if k == "applies_to" or v is None:
            continue
        f = float(k) * 1e6
        z = float(part["z_spot_ohm"]) * float(v)
        if f > 0 and z > 0:
            pts.append({"impedance": {"magnitude": round(z, 2)}, "frequency": f})
    pts.sort(key=lambda p: p["frequency"])
    if len(pts) < 3:
        return None, "<3pts"
    mpn = part["mpn"]
    family = FAMILY.get(series, "Ferrite Cable Core")
    cm = part.get("cable_max_mm")
    mat = part.get("material", "")
    zspec = next(p["impedance"]["magnitude"] for p in pts if abs(p["frequency"] - spec * 1e6) < spec * 1e4) \
        if any(abs(p["frequency"] - spec * 1e6) < spec * 1e4 for p in pts) else pts[-1]["impedance"]["magnitude"]
    form = "snapOn"
    desc = (f"KEMET {family} ("
            f"{mat + ', ' if mat else ''}"
            f"{'≤%g mm cable, ' % cm if cm else ''}"
            f"|Z|≈{zspec:g} Ω @ {spec:g} MHz (chart), 1 turn)")
    electrical = {"subtype": "cableCore", "numberTurns": 1, "impedancePoints": pts, "mountingForm": form}
    if cm and cm > 0:
        electrical["maximumCableOuterDiameter"] = float(cm) / 1000.0
    obj = {"magnetic": {"manufacturerInfo": {
        "name": "KEMET", "reference": mpn, "status": "production", "family": family, "datasheetUrl": URL,
        "datasheetInfo": {
            "part": {"description": desc, "material": "Ferrite", "shielded": False},
            "electrical": [electrical],
            "provenance": [{"source": "manufacturerParametric",
                            "sourceName": (f"KEMET {d.get('datasheet', series)} — per-part |Z|(f) chart "
                                           f"pixel-digitized (KEMET publishes no numeric |Z| spot; the chart "
                                           f"is the source, verified internally)"),
                            "sourceUrl": URL, "retrievedDate": RETRIEVED}],
        }}}}
    return obj, None


def main():
    files = sorted(glob.glob(str(SRC / "*.json")))
    # global set of MPNs for G-variant dedup
    allmpn = set()
    docs = []
    for f in files:
        d = json.load(open(f))
        docs.append(d)
        for p in d["parts"]:
            allmpn.add(p["mpn"])
    built, skipped = [], []
    for d in docs:
        series = d["series"]
        for part in d["parts"]:
            mpn = part["mpn"]
            if mpn.endswith("G") and mpn[:-1] in allmpn:        # case-colour dup
                skipped.append((mpn, "G-color-dup")); continue
            obj, why = build(series, d, part)
            if obj is None:
                skipped.append((mpn, why)); continue
            built.append(obj)
    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — KEMET ESD-SR cores from {SRC.name}/")
    print(f"  built: {len(built)}   skipped: {len(skipped)}")
    for m, w in skipped:
        print(f"    skip {m}: {w}")
    print(f"  by family: {dict(Counter(b['magnetic']['manufacturerInfo']['family'] for b in built))}")
    if built:
        e = built[0]["magnetic"]["manufacturerInfo"]
        pts = e["datasheetInfo"]["electrical"][0]["impedancePoints"]
        crv = ", ".join(f"{p['frequency']/1e6:g}M:{p['impedance']['magnitude']:g}" for p in pts)
        print(f"  sample: {e['reference']} -> [{crv}]")
    if not APPLY:
        print(f"\n(dry run — --apply writes {OUT.name})"); return
    with open(OUT, "w") as fh:
        for b in built:
            fh.write(json.dumps(b) + "\n")
    print(f"\nwrote {len(built)} KEMET cableCore records to {OUT}")


if __name__ == "__main__":
    main()
