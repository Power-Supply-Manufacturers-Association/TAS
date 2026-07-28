#!/usr/bin/env python3
"""Build MAS `cableCore` records for Fair-Rite cable / suppression cores from
their PUBLISHED typical impedance (fair-rite.com product pages, `tab_electical`).

Data source discipline (important — see the session notes): Fair-Rite's own
Complex Impedance Calculator and an OpenMagnetics sweep of MAS's Fair-Rite
materials BOTH fail to reproduce Fair-Rite's published typical impedance (the
calculator is ~15-40% low with a flatter shape; MAS's Fair-Rite complex-µ is
approximate). So the only accurate source is Fair-Rite's PUBLISHED spot values
themselves — |Z| at 100 / 250 / 500 / 1000 MHz. Those 4 measured/typical points
are used verbatim as the impedance curve (coverage 100 MHz-1 GHz); nothing is
computed, extrapolated, or invented.

    python3 fairrite_cable_cores_import.py            # DRY RUN
    python3 fairrite_cable_cores_import.py --apply    # write data/fairrite_cable_cores.ndjson
"""
import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RAW = DATA / "fairrite_parts.json"
OUT = DATA / "fairrite_cable_cores.ndjson"
APPLY = "--apply" in sys.argv
RETRIEVED = "2026-07-28"

FAMILY = {
    "round-cable-snap-it-core": "Round Cable Snap-It", "oval-cores/oval-snap-its": "Oval Cable Snap-It",
    "flat-cable-snap-it-cores": "Flat Cable Snap-It", "oval-cores/oval-clip-its": "Oval Cable Clip-It",
    "round-cable-emi-suppression-cores": "Round Cable Core", "flat-cable-emi-suppression-cores": "Flat Cable Core",
    "toroidal-suppression-cores": "Toroidal Suppression Core",
}
FORM_LABEL = {"solidRing": "solid ring", "snapOn": "snap-on clip", "split": "clip-on (split)"}


def build(rec):
    mpn = str(rec["mpn"])
    form = rec.get("form")
    grade = rec.get("material")
    family = FAMILY.get(rec.get("slug"), "Cable Core")
    # spots: [[f_Hz, |Z|_ohm], ...] -> impedancePoints, sorted, positive
    pts = [{"impedance": {"magnitude": float(z)}, "frequency": float(f)}
           for f, z in sorted(rec["spots"]) if f > 0 and z > 0]
    if len(pts) < 3:
        return None
    z100 = next((p["impedance"]["magnitude"] for p in pts if abs(p["frequency"] - 1e8) < 1), None)
    max_cable = rec.get("maxCable_mm")
    url = f"https://fair-rite.com/?s={mpn}"
    electrical = {"subtype": "cableCore", "numberTurns": 1, "impedancePoints": pts}
    if form:
        electrical["mountingForm"] = form
    if isinstance(max_cable, (int, float)) and max_cable > 0:
        electrical["maximumCableOuterDiameter"] = float(max_cable) / 1000.0
    extra = []
    if grade:
        extra.append(f"{grade} material")
    if form and form in FORM_LABEL:
        extra.append(FORM_LABEL[form])
    if isinstance(max_cable, (int, float)) and max_cable > 0:
        extra.append(f"≤{max_cable:g} mm cable")
    desc = f"Fair-Rite {family}"
    if extra:
        desc += " (" + ", ".join(extra) + ")"
    if z100:
        desc += f", |Z|≈{z100:g} Ω @ 100 MHz (published), 1 turn"
    obj = {"magnetic": {"manufacturerInfo": {
        "name": "Fair-Rite", "reference": mpn, "status": "production", "family": family, "datasheetUrl": url,
        "datasheetInfo": {
            "part": {"description": desc, "material": "Ferrite", "shielded": False},
            "electrical": [electrical],
            "provenance": [{"source": "manufacturerParametric",
                            "sourceName": "Fair-Rite product page (published typical impedance)",
                            "sourceUrl": url, "retrievedDate": RETRIEVED}],
        },
    }}}
    if rec.get("dim"):
        d = rec["dim"]
        mech = {k: {"nominal": float(d[a]) / 1000.0} for k, a in (("length", "C"), ("width", "A")) if d.get(a)}
        if mech:
            obj["magnetic"]["manufacturerInfo"]["datasheetInfo"]["mechanical"] = mech
    return obj


def main():
    parts = json.load(open(RAW))["parts"]
    built, skipped, seen = [], 0, set()
    for rec in parts:
        mpn = str(rec.get("mpn") or "")
        if not mpn or mpn in seen:
            skipped += 1; continue
        obj = build(rec)
        if obj is None:
            skipped += 1; continue
        seen.add(mpn); built.append(obj)
    from collections import Counter
    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — Fair-Rite cable cores from {RAW.name}")
    print(f"  built: {len(built)}   skipped: {skipped}")
    print(f"  by form: {dict(Counter(o['magnetic']['manufacturerInfo']['datasheetInfo']['electrical'][0].get('mountingForm') for o in built))}")
    print(f"  by family: {dict(Counter(o['magnetic']['manufacturerInfo']['family'] for o in built))}")
    if built:
        e = built[0]["magnetic"]["manufacturerInfo"]
        print(f"  sample: {e['reference']} — {e['datasheetInfo']['part']['description']}")
    if not APPLY:
        print(f"\n(dry run — --apply writes {OUT.name})"); return
    with open(OUT, "w") as f:
        for o in built:
            f.write(json.dumps(o) + "\n")
    print(f"\nwrote {len(built)} Fair-Rite cableCore records to {OUT}")


if __name__ == "__main__":
    main()
