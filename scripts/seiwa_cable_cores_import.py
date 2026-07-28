#!/usr/bin/env python3
"""Build MAS `cableCore` records for Seiwa Electric MFG clamp-on / ring ferrite
cable cores from the Seiwa "EMC Products" catalog (Cat. E04).

DATA DISCIPLINE — digitize-and-verify-back:
Seiwa publishes a per-part |Z|(f) chart (with 1/2/3-turn traces). The 1-turn trace
(single conductor pass-through — the cable-core case) was pixel-digitized per part;
`z_spot_ohm` is that part's digitized |Z| at the family spec frequency (100 MHz for
SR/SS/ST/RC, 10 MHz for the low-freq SRM/RM), and `shape_norm100` is the family-
representative normalized shape (=1.0 at the spec freq). Reconstruction
|Z|(f) = z_spot * shape(f). Because the shape is normalized to 1.0 at the spec
frequency, |Z|(spec) reproduces the per-part digitized spot exactly (verify-back
ratio 1.0); the family-shape RMS refit error is recorded per family in the source
verify_note (small for SS/SRM/RM/RC/ST; larger for SR which mixes core types).

Data gaps are honored (no fabrication): parts with a null z_spot (Seiwa's RA family
has no published curve at all; 16 of RC's 19 have no representative curve) are
SKIPPED and never invented.

    python3 seiwa_cable_cores_import.py            # DRY RUN
    python3 seiwa_cable_cores_import.py --apply    # write data/seiwa_cable_cores.ndjson
"""
import json, sys, glob
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
SRC = DATA / "seiwa"
OUT = DATA / "seiwa_cable_cores.ndjson"
APPLY = "--apply" in sys.argv
RETRIEVED = "2026-07-28"
URL = "https://www.seiwa.co.jp/en/product/emc/"

FAMILY = {"SR": "Snap-On Ferrite Cable Core", "SS": "Snap-On Ferrite Cable Core",
          "SRM": "Low-Cut Snap-On Cable Core", "ST": "Snap-On Ferrite Cable Core",
          "RA": "Toroidal Ferrite Cable Core", "RC": "Toroidal Ferrite Cable Core",
          "RM": "Low-Cut Toroidal Cable Core"}


def shape_of(d):
    for k, v in d.items():
        if k.startswith("shape") and isinstance(v, dict) and any(x is not None for x in v.values()):
            return v
    return None


def build(series, d, part, shape):
    z = part.get("z_spot_ohm")
    if z is None or shape is None:
        return None
    spec = part.get("spec_freq_mhz") or d.get("spec_freq_mhz") or 100
    pts = []
    for k, v in shape.items():
        if v is None:
            continue
        f = float(k) * 1e6
        zz = float(z) * float(v)
        if f > 0 and zz > 0:
            pts.append({"impedance": {"magnitude": round(zz, 2)}, "frequency": f})
    pts.sort(key=lambda p: p["frequency"])
    if len(pts) < 3:
        return None
    mpn = part["mpn"]
    family = FAMILY.get(series, "Ferrite Cable Core")
    form = d.get("form", "snapOn")
    cm = part.get("cable_max_mm")
    desc = (f"Seiwa {family} ("
            f"{'≤%g mm cable, ' % cm if cm else ''}"
            f"|Z|≈{float(z):g} Ω @ {spec:g} MHz, 1 turn)")
    electrical = {"subtype": "cableCore", "numberTurns": 1, "impedancePoints": pts, "mountingForm": form}
    if cm and cm > 0:
        electrical["maximumCableOuterDiameter"] = float(cm) / 1000.0
    return {"magnetic": {"manufacturerInfo": {
        "name": "Seiwa", "reference": mpn, "status": "production", "family": family, "datasheetUrl": URL,
        "datasheetInfo": {
            "part": {"description": desc, "material": "Ferrite", "shielded": False},
            "electrical": [electrical],
            "provenance": [{"source": "manufacturerParametric",
                            "sourceName": ("Seiwa EMC Products catalog (E04) — per-part 1-turn |Z|(f) chart "
                                           "pixel-digitized; magnitude = per-part digitized spot at the family "
                                           "spec frequency, frequency shape = family-representative curve"),
                            "sourceUrl": URL, "retrievedDate": RETRIEVED}],
        }}}}


def main():
    built, skipped = [], []
    for f in sorted(glob.glob(str(SRC / "*.json"))):
        d = json.load(open(f))
        series = d["series"]
        shape = shape_of(d)
        for part in d["parts"]:
            rec = build(series, d, part, shape)
            (built.append(rec) if rec else skipped.append((part["mpn"], series)))
    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — Seiwa cores from {SRC.name}/")
    print(f"  built: {len(built)}   skipped (null Z / no shape): {len(skipped)}")
    bys = Counter(s for _, s in skipped)
    print(f"  skipped by family: {dict(bys)}")
    print(f"  built by family: {dict(Counter(b['magnetic']['manufacturerInfo']['family'] for b in built))}")
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
    print(f"\nwrote {len(built)} Seiwa cableCore records to {OUT}")


if __name__ == "__main__":
    main()
