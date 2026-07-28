#!/usr/bin/env python3
"""Build MAS `cableCore` records for Laird ferrite cable / clamp-on cores from
their PUBLISHED typical impedance (Laird "Ferrite EMI Cable Cores" catalog PDF,
`Catalog_FERRITE CORES 0717.pdf`).

Data-source discipline (same as the Fair-Rite importer): Laird publishes typical
|Z| spot values per part in the catalog tables — those are the accurate ground
truth and are used verbatim. Nothing is computed, interpolated, or invented.

Three families, each with its OWN impedance frequency columns (verified against
the catalog headers):
  * 28A  Broadband Split, Snap-On cores      -> snapOn,    @ 25 / 100 / 300 MHz
  * HFA  High-Frequency Split, Snap-On cores -> snapOn,    @ 300 / 500 / 800 / 1000 MHz
  * HFB  High-Frequency Cylindrical cores    -> solidRing, @ 300 / 500 / 800 / 1000 MHz

HFA appears in two catalog tables (page 5 + page 10) with identical impedance —
deduplicated by MPN. 28A `-0A0/-0A2/-0A4/-0B2` case-color suffixes are the same
ferrite in a different plastic case colour (electrically identical, like TDK's
-M/-BK/-VT); the catalog table already lists one electrical part per size, so no
extra dedup is needed there.

    python3 laird_cable_cores_import.py            # DRY RUN
    python3 laird_cable_cores_import.py --apply    # write data/laird_cable_cores.ndjson

The raw parsed table (laird_parts.json) is produced from the catalog PDF text; it
carries mpn, series, form, spots [[f_Hz, |Z|_ohm], ...], maxCable_mm, dims, xref.
"""
import json, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RAW = DATA / "laird_parts.json"
OUT = DATA / "laird_cable_cores.ndjson"
APPLY = "--apply" in sys.argv
RETRIEVED = "2026-07-28"
CATALOG_URL = "https://www.laird.com/sites/default/files/2018-11/Catalog_FERRITE%20CORES%200717.pdf"

FAMILY = {
    "28A": "Broadband Snap-On Cable Core",
    "HFA": "High-Frequency Snap-On Cable Core",
    "HFB": "High-Frequency Cylindrical Cable Core",
}
FORM_LABEL = {"snapOn": "snap-on clip", "solidRing": "solid ring / cylindrical", "split": "clip-on (split)"}


def build(rec):
    mpn = str(rec["mpn"]).strip()
    series = rec.get("series")
    form = rec.get("form")
    family = FAMILY.get(series, "Cable Core")
    # spots: [[f_Hz, |Z|_ohm], ...] -> impedancePoints, sorted, strictly positive
    pts = [{"impedance": {"magnitude": float(z)}, "frequency": float(f)}
           for f, z in sorted(rec.get("spots") or []) if f > 0 and z > 0]
    if len(pts) < 3:
        return None
    max_cable = rec.get("maxCable_mm")
    z_ref = next((p for p in pts if abs(p["frequency"] - 1e8) < 1), pts[0])  # @100 MHz if present
    electrical = {"subtype": "cableCore", "numberTurns": 1, "impedancePoints": pts}
    if form:
        electrical["mountingForm"] = form
    if isinstance(max_cable, (int, float)) and max_cable > 0:
        electrical["maximumCableOuterDiameter"] = float(max_cable) / 1000.0
    extra = []
    if form in FORM_LABEL:
        extra.append(FORM_LABEL[form])
    if isinstance(max_cable, (int, float)) and max_cable > 0:
        extra.append(f"≤{max_cable:g} mm cable")
    desc = f"Laird {family}"
    if extra:
        desc += " (" + ", ".join(extra) + ")"
    desc += (f", |Z|≈{z_ref['impedance']['magnitude']:g} Ω @ "
             f"{z_ref['frequency']/1e6:g} MHz (published), 1 turn")
    obj = {"magnetic": {"manufacturerInfo": {
        "name": "Laird", "reference": mpn, "status": "production", "family": family,
        "datasheetUrl": CATALOG_URL,
        "datasheetInfo": {
            "part": {"description": desc, "material": "Ferrite", "shielded": False},
            "electrical": [electrical],
            "provenance": [{"source": "manufacturerParametric",
                            "sourceName": "Laird Ferrite EMI Cable Cores catalog (published typical impedance)",
                            "sourceUrl": CATALOG_URL, "retrievedDate": RETRIEVED}],
        },
    }}}
    dims = rec.get("dims") or {}
    mech = {}
    if dims.get("length_mm"):
        mech["length"] = {"nominal": float(dims["length_mm"]) / 1000.0}
    if dims.get("OD_mm"):
        mech["width"] = {"nominal": float(dims["OD_mm"]) / 1000.0}
    if mech:
        obj["magnetic"]["manufacturerInfo"]["datasheetInfo"]["mechanical"] = mech
    return obj


def main():
    parts = json.load(open(RAW))
    if isinstance(parts, dict):
        parts = parts.get("parts") or parts.get("records") or []
    built, skipped, seen = [], 0, set()
    for rec in parts:
        mpn = str(rec.get("mpn") or "").strip()
        if not mpn or mpn in seen:      # dedup HFA duplicate tables (identical impedance)
            skipped += 1; continue
        obj = build(rec)
        if obj is None:
            skipped += 1; continue
        seen.add(mpn); built.append(obj)
    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — Laird cable cores from {RAW.name}")
    print(f"  built: {len(built)}   skipped (dup/<3 spots): {skipped}")
    print(f"  by family: {dict(Counter(o['magnetic']['manufacturerInfo']['family'] for o in built))}")
    print(f"  by form:   {dict(Counter(o['magnetic']['manufacturerInfo']['datasheetInfo']['electrical'][0].get('mountingForm') for o in built))}")
    if built:
        e = built[0]["magnetic"]["manufacturerInfo"]
        print(f"  sample: {e['reference']} — {e['datasheetInfo']['part']['description']}")
    if not APPLY:
        print(f"\n(dry run — --apply writes {OUT.name})"); return
    with open(OUT, "w") as f:
        for o in built:
            f.write(json.dumps(o) + "\n")
    print(f"\nwrote {len(built)} Laird cableCore records to {OUT}")


if __name__ == "__main__":
    main()
