#!/usr/bin/env python3
"""Build schema-correct chip-bead records for manufacturers under-represented in
the catalog, from specs sourced online (manufacturer + cross-distributor data).

Uses the proper MAS chipBead electrical variant (subtype 'chipBead' with
impedancePoints), NOT the inductor-variant misuse seen in legacy bead records.
Only the verified fields are written; nothing is fabricated (height/SRF/thermal
are omitted where not sourced; case footprint L/W are standard EIA nominals).

    python3 build_beads.py            # DRY RUN: build, validate, show
    python3 build_beads.py --apply    # append validated records to magnetics.ndjson
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "build"))
import tas_validator  # noqa: E402

DATA = HERE.parent / "data"
APPLY = "--apply" in sys.argv

# Standard EIA case -> nominal footprint (length, width) in metres. Height is
# part-specific and not sourced, so it is omitted rather than invented.
CASE_LW = {
    "0402": (0.0010, 0.0005), "0603": (0.0016, 0.0008), "0805": (0.0020, 0.00125),
    "1206": (0.0032, 0.0016), "1210": (0.0032, 0.0025), "1812": (0.0045, 0.0032),
}

# (ref, case, Z@100MHz [ohm], rated current [A], DCR max [ohm])
SERIES = [
    ("TDK", "MPZ2012", "https://product.tdk.com/en/search/inductor/beads/beads/list", [
        ("MPZ2012S101A", "0805", 100, 4.0, 0.020),
        ("MPZ2012S221A", "0805", 220, 3.0, 0.040),
        ("MPZ2012S601A", "0805", 600, 2.0, 0.100),
        ("MPZ2012S102A", "0805", 1000, 1.5, 0.150),
    ]),
    ("Bourns", "MH", "https://www.bourns.com/docs/Product-Datasheets/mh.pdf", [
        ("MH2029-100Y", "0805", 10, 6.0, 0.030),
    ]),
    ("Bourns", "MH1005", "https://www.bourns.com/docs/product-datasheets/MH1005.pdf", [
        ("MH1005-300Y", "0402", 30, 3.0, 0.022),
    ]),
    ("Taiyo Yuden", "FBMH", "https://ds.yuden.co.jp/TYCOMPAS", [
        ("FBMH1608HM101-T", "0603", 100, 2.5, 0.035),
        ("FBMH1608HL600-T", "0603", 60, 1.8, 0.045),
        ("FBMH1608HM102-T", "0603", 1000, 0.6, 0.350),
        ("FBMH2012HM221-T", "0805", 220, 2.0, 0.060),
        ("FBMH3216HM501NT", "1206", 500, 2.0, 0.070),
        ("FBMH3225HM601NT", "1210", 600, 3.0, 0.042),
        ("FBMH4532HM681-T", "1812", 680, 4.0, 0.028),
    ]),
    ("Samsung Electro-Mechanics", "CIx", "https://www.samsungsem.com/global/product/passive-component.do", [
        ("CIC10P471NC", "0603", 470, 1.2, 0.150),
        ("CIM10J471NC", "0603", 470, 0.3, 0.350),
        ("CIS10P260AC", "0603", 26, 6.0, 0.007),
        ("CIC10P121NC", "0603", 120, 2.0, 0.050),
    ]),
    ("Yageo", "PBY", "https://www.yageogroup.com/content/datasheet/asset/file/DATASHEET_BBPY00160808_SERIES", [
        ("PBY160808T-601Y-N", "0603", 600, 1.0, 0.200),
        ("PBY321611T-601Y", "1206", 600, 1.8, 0.100),
    ]),
]


def build(mfr, family, url, ref, case, z, irated, dcr):
    length, width = CASE_LW[case]
    return {"magnetic": {"manufacturerInfo": {
        "name": mfr, "reference": ref, "status": "production", "family": family,
        "datasheetUrl": url,
        "datasheetInfo": {
            "part": {"caseCode": case, "material": "Ferrite",
                     "description": f"{mfr} {family} chip ferrite bead, {z} ohm @ 100 MHz, {case}"},
            "electrical": [{
                "subtype": "chipBead",
                "dcResistance": {"maximum": dcr},
                "ratedCurrents": [irated],
                "impedancePoints": [{"impedance": {"magnitude": float(z)},
                                     "frequency": 100000000.0}],
            }],
            "mechanical": {"length": {"nominal": length}, "width": {"nominal": width}},
        },
    }}}


def main():
    existing = set()
    with open(DATA / "magnetics.ndjson") as f:
        for line in f:
            if not line.strip() or line.startswith("version https://git-lfs"):
                break
            r = json.loads(line)
            ref = r["magnetic"]["manufacturerInfo"].get("reference")
            if ref:
                existing.add(ref)

    built, dup, invalid = [], 0, 0
    for mfr, family, url, parts in SERIES:
        for ref, case, z, irated, dcr in parts:
            if ref in existing:
                dup += 1
                continue
            rec = build(mfr, family, url, ref, case, z, irated, dcr)
            v = tas_validator.validate(rec)
            if not v.valid:
                invalid += 1
                print(f"  INVALID {ref}: {[f.message for f in v.findings]}")
                continue
            built.append(rec)

    by_mfr = {}
    for r in built:
        by_mfr[r["magnetic"]["manufacturerInfo"]["name"]] = \
            by_mfr.get(r["magnetic"]["manufacturerInfo"]["name"], 0) + 1
    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — chip-bead population")
    print(f"  built+validated: {len(built)}   skipped(existing ref): {dup}   invalid: {invalid}")
    print(f"  by manufacturer: {by_mfr}")
    print(f"  sample: {json.dumps(built[0]) if built else '(none)'}")

    if not APPLY:
        print("\n(dry run — re-run with --apply to append to magnetics.ndjson)")
        return
    with open(DATA / "magnetics.ndjson", "a") as f:
        for r in built:
            f.write(json.dumps(r) + "\n")
    print(f"\nappended {len(built)} records to magnetics.ndjson")


if __name__ == "__main__":
    main()
