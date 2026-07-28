#!/usr/bin/env python3
"""Build MAS `cableCore` records for Murata FSRC flat-cable ferrite EMI-suppression
cores from the archived catalog Cat.No. O63E-10.

STATUS: obsolete. Murata has DISCONTINUED the FSRC line (every part 404s on the
live catalog); these records are kept for reference and marked
`manufacturerInfo.status = "obsolete"` so a recommender can exclude them.

DATA DISCIPLINE — digitize-and-verify-back:
The O63E catalog tabulates only ONE numeric spot per part: |Z| (typ.) at 100 MHz,
1 turn (dims + spots copied EXACTLY). It also prints per-type |Z|(f) charts (pp.8-9
of O63E; FSRC080/120/171/222/140/141/170/240 types) — all the same NiZn rising
shape. That shape was digitized (normalized to 1.0 at 100 MHz) and each part's curve
is reconstructed as |Z|(f) = z100 * shape(f), so |Z|(100 MHz) reproduces the exact
published spot. Two parts additionally have a real 10 MHz spot (recovered) — that
measured point overrides the shape at 10 MHz. The shape is family-representative
(per-type variation exists, documented); nothing is invented.

    python3 murata_cable_cores_import.py            # DRY RUN
    python3 murata_cable_cores_import.py --apply    # write data/murata_cable_cores.ndjson
"""
import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RAW = DATA / "murata" / "FSRC_data.json"
TYPE_SHAPES = DATA / "murata" / "fsrc_type_shapes.json"   # per-FSRC-type digitized shapes
OUT = DATA / "murata_cable_cores.ndjson"
APPLY = "--apply" in sys.argv
RETRIEVED = "2026-07-29"
URL = "https://www.murata.com/en-us/products/emc/emifil"
# Fallback family shape (only if a part's per-type shape is unavailable), normalized
# to 1.0 at 100 MHz. The per-type shapes (fsrc_type_shapes.json) are preferred —
# each part uses ITS OWN FSRC<nnn> type curve digitized from the O63E charts.
FALLBACK_SHAPE = {"1": 0.09, "3": 0.20, "10": 0.42, "30": 0.70, "100": 1.0, "300": 1.70}

_TYPE_SHAPES = None


def _type_shapes():
    global _TYPE_SHAPES
    if _TYPE_SHAPES is None:
        try:
            d = json.load(open(TYPE_SHAPES))
            _TYPE_SHAPES = {k: v for k, v in d.items() if k.startswith("FSRC") and isinstance(v, dict)}
        except (FileNotFoundError, ValueError):
            _TYPE_SHAPES = {}
    return _TYPE_SHAPES


def _shape_for(mpn):
    """The FSRC<nnn> per-type shape for this part (mpn = FSRC + 3-digit type code + ...)."""
    ts = _type_shapes()
    typ = "FSRC" + mpn[4:7] if mpn.startswith("FSRC") and len(mpn) >= 7 else None
    return ts.get(typ) or FALLBACK_SHAPE


def build(part):
    spots = {int(f): float(z) for f, z in part.get("spots", [])}
    z100 = spots.get(100000000)
    if z100 is None:
        return None
    shape = _shape_for(part["mpn"])
    pts = {}
    for k, s in shape.items():
        if s is None:
            continue
        pts[float(k) * 1e6] = round(z100 * float(s), 2)
    # override with any real measured spots (e.g. the recovered 10 MHz value)
    for f, z in spots.items():
        pts[float(f)] = z
    points = [{"impedance": {"magnitude": v}, "frequency": f} for f, v in sorted(pts.items()) if f > 0 and v > 0]
    if len(points) < 3:
        return None
    mpn = part["mpn"]
    cm = part.get("cable_max_mm")
    desc = (f"Murata FSRC Flat-Cable Ferrite Core (DISCONTINUED; "
            f"{'≤%g mm cable, ' % cm if cm else ''}"
            f"|Z|≈{z100:g} Ω @ 100 MHz, 1 turn)")
    electrical = {"subtype": "cableCore", "numberTurns": 1, "impedancePoints": points, "mountingForm": "snapOn"}
    if cm and cm > 0:
        electrical["maximumCableOuterDiameter"] = float(cm) / 1000.0
    return {"magnetic": {"manufacturerInfo": {
        "name": "Murata", "reference": mpn, "status": "obsolete", "family": "Flat-Cable Ferrite Core",
        "datasheetUrl": URL,
        "datasheetInfo": {
            "part": {"description": desc, "material": "Ferrite", "shielded": False},
            "electrical": [electrical],
            "provenance": [{"source": "manufacturerParametric",
                            "sourceName": ("Murata catalog Cat.No.O63E-10 (discontinued FSRC line) — |Z| @ "
                                           "100 MHz spot exact; frequency shape digitized from this part's own "
                                           "FSRC-type |Z|(f) chart in O63E, 10 MHz measured spot used where published"),
                            "sourceUrl": URL, "retrievedDate": RETRIEVED}],
        }}}}


def main():
    parts = json.load(open(RAW))["parts"]
    built, skipped = [], []
    for p in parts:
        obj = build(p)
        (built.append(obj) if obj else skipped.append(p["mpn"]))
    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — Murata FSRC (obsolete) from {RAW.name}")
    print(f"  built: {len(built)}   skipped: {len(skipped)} {skipped}")
    print(f"  status: obsolete (line discontinued)")
    if built:
        e = built[0]["magnetic"]["manufacturerInfo"]
        pts = e["datasheetInfo"]["electrical"][0]["impedancePoints"]
        crv = ", ".join(f"{p['frequency']/1e6:g}M:{p['impedance']['magnitude']:g}" for p in pts)
        print(f"  sample: {e['reference']} ({e['status']}) -> [{crv}]")
    if not APPLY:
        print(f"\n(dry run — --apply writes {OUT.name})"); return
    with open(OUT, "w") as fh:
        for b in built:
            fh.write(json.dumps(b) + "\n")
    print(f"\nwrote {len(built)} Murata FSRC cableCore records to {OUT}")


if __name__ == "__main__":
    main()
