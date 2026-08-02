#!/usr/bin/env python3
"""ABT #500 residue: the last two TO-247 records whose thermal row is not theirs.

WHERE THIS PICKS UP. The #500 population query is

    part.case startswith "TO-247"  AND  thermal.thermalResistanceJunctionCase > 2.0

which stood at 98 records. The main campaign (fix_mosfet_thermal_identity.py,
commit 719db53) re-read the vendors' TO-247 rows and took it to 2. Those two are
a different stratum -- neither carries a FullPAK table -- and each needed its own
document before it could be judged.

  Wolfspeed C3M0280090D  REPAIR. Its 2.8 K/W is REAL: the datasheet the record
        itself cites (assets.wolfspeed.com/uploads/2020/12/C3M0280090D.pdf,
        Rev. 05 Sept 2024) prints "Thermal Resistance from Junction to Case
        RthetaJC 2.8 max" for the TO-247-3 part -- a 900 V / 280 mohm SiC die is
        small enough that a direct copper tab still sits above 2 K/W. What is NOT
        its own is junctionTemperatureMax 175: that is the hardcoded family
        default of the (now disabled) *_sourcing.py generators, and it is what
        blinded the check. The same page says TJ,Tstg -55 to +150, and the whole
        thermal block only closes at 150 -- (150-25)/2.8 = 44.6 W, matching the
        stored PD 45 W, where 175 would claim 53.6 W of headroom the part has
        not got. The inflated ceiling came with an inflated current rating: the
        record stores 11.5 A / 7.5 A / 36 A against the document's 10.2 A / 6.8 A
        / 22 A, and 11.5 A had been copied onto the body diode as well (IS max 9).
        Every continuous and pulsed rating the thermal path governs is re-read
        together -- they are one datum published five ways, and fixing the
        ceiling while leaving the currents would swap one contradiction for
        another.

  Transphorm TP65H150G4  QUARANTINE. There is no TO-247 variant of this die to
        re-source a thermal row from. The 650 V / 150 mohm Gen-4 SuperGaN part is
        sold as TP65H150G4PS (TO-220, RthJC 1.5 K/W, PD 83 W), TP65H150G4LSG and
        TP65H150G4LSGBE (PQFN88, RthJC 2.4 and 2.0 K/W, PD 52 W) and
        TP65H150BG4JSG (PQFN56) -- four SKUs, no TO-247 among them, and every
        real one carries a package suffix the stored bare MPN lacks. The stored
        3.5 K/W / 25 W matches none of them, and the record's own datasheetUrl
        now redirects to a Renesas category landing page. Nothing here is
        re-sourceable, so the record moves out intact and stays auditable.

WHAT DOES NOT CHANGE. C3M0280090D's onResistance stays 0.28. Rev. 05 gives 320
mohm typ / 360 mohm max, but all 24 C3M records in the catalogue store the part
number's nominal, and picking a different convention for one of them is a
family-wide decision, not a thermal-identity repair. Reported, not rewritten --
along with gateSourceVoltageMax 25 (sheet: +19 transient) and
bodyDiodeForwardVoltage 3.3 (sheet: 4.8 typ) on the same record.

THE IMPORTER. junctionTemperatureMax 175 is written unconditionally by
manufacturer_sourcing.py, bulk_manufacturer_sourcing.py, extended_sourcing.py and
parametric_sourcing.py ("tj_max: float = 175"). All four were disabled in July
2026 for fabricating parts and now sys.exit on import, so nothing live can
reintroduce the default; no further importer change is needed for this ticket.

Every written record is gated on Blade Runner AND JSON Schema before it is
accepted; a record that fails either aborts the run rather than landing.

    fix_500_to247_thermal_residue.py            # dry run
    fix_500_to247_thermal_residue.py --apply
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blade_gate import BladeGate  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "mosfets.ndjson"
QUARANTINE = REPO / "data" / "mosfets.quarantine_thermal_package_conflict.ndjson"
AUDIT = REPO / "staging" / "abt500_to247_thermal_residue.json"
TICKET = "ABT #500"
TODAY = "2026-08-02"

DATASHEET = "https://assets.wolfspeed.com/uploads/2020/12/C3M0280090D.pdf"

# Wolfspeed C3M0280090D, Rev. 05 September 2024 -- the document the record cites.
# Key Parameters: ID 10.2 A (VGS=15 V, TC=25 C, TJ<=150 C), 6.8 A (TC=100 C);
# IDM 22 A; PD 45 W (TC=25 C, TJ=150 C); TJ,Tstg -55 to +150 C.
# Reverse Diode Characteristics: IS 9 A max (VGS=-4 V).
REPAIR = {
    "C3M0280090D": {
        "electrical": {
            "continuousDrainCurrent": 10.2,
            "continuousDrainCurrentAt100C": 6.8,
            "pulsedDrainCurrent": 22,
            "bodyDiodeContinuousCurrent": 9,
        },
        "thermal": {
            "junctionTemperatureMax": 150,
        },
        "note": ("re-read of the Absolute Maximum / Key Parameters table of the "
                 "datasheet this record already cites, Rev. 05 Sept 2024: "
                 "TJ,Tstg -55 to +150 C (not 175 -- the disabled *_sourcing.py "
                 "family default), ID 10.2 A at TC=25 C and 6.8 A at TC=100 C, "
                 "IDM 22 A, IS 9 A max. RthJC 2.8 K/W and PD 45 W are the part's "
                 "own and are unchanged; at TJmax=150 they close exactly "
                 "((150-25)/2.8 = 44.6 W)"),
    },
}

QUARANTINE_PLAN = {
    "TP65H150G4": (
        "to247-thermal-block-belongs-to-another-package-variant: no TO-247 "
        "variant of this die exists to re-source the row from. Renesas/Transphorm "
        "sell the 650 V / 150 mOhm Gen-4 SuperGaN die as TP65H150G4PS (TO-220, "
        "RthJC 1.5 K/W, PD 83 W), TP65H150G4LSG and TP65H150G4LSGBE (PQFN88, "
        "RthJC 2.4 and 2.0 K/W, PD 52 W) and TP65H150BG4JSG (PQFN56) -- four "
        "SKUs, none in TO-247, and every one carries a package suffix that this "
        "record's bare 'TP65H150G4' lacks. The stored TO-247-4 / RthJC 3.5 K/W / "
        "PD 25 W matches no published variant, and the record's own datasheetUrl "
        "(transphormusa.com/document/tp65h150g4-datasheet/) now redirects to the "
        f"Renesas GaN category landing page. {TICKET}, checked {TODAY}"
    ),
}


def build_schema_validator():
    """SAS mosfet schema with the sibling registry, per tests/test_data.py."""
    import jsonschema
    from referencing import Registry, Resource

    registry = Registry()
    for repo in ("PEAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS", "CIAS"):
        root = REPO.parent / repo / "schemas"
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            doc = json.loads(path.read_text())
            if "$id" in doc:
                registry = registry.with_resource(
                    doc["$id"], Resource.from_contents(doc))
    schema = json.loads((REPO.parent / "SAS" / "schemas" / "mosfet.json").read_text())
    return jsonschema.Draft202012Validator(schema, registry=registry)


def apply_repair(rec, plan):
    sheet = rec["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"]
    changed = {}
    for block in ("electrical", "thermal"):
        for key, new in plan.get(block, {}).items():
            old = sheet[block].get(key)
            if old != new:
                changed[f"{block}.{key}"] = [old, new]
                sheet[block][key] = new
    if changed:
        sheet.setdefault("provenance", []).append({
            "source": "manufacturerDatasheet",
            "sourceName": f"{TICKET} {plan['note']}",
            "sourceUrl": DATASHEET,
            "retrievedDate": TODAY,
            "fields": sorted(changed),
        })
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    schema = build_schema_validator()
    blade = BladeGate(("semiconductor", "mosfet"))
    wanted = set(REPAIR) | set(QUARANTINE_PLAN)

    audit = {"ticket": TICKET, "date": TODAY, "repaired": [], "quarantined": []}
    quarantined_lines = []
    seen = set()
    tmp = DATA.with_suffix(".ndjson.tmp")

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            probe = next((pn for pn in wanted if pn.encode() in raw), None)
            if probe is None:
                out.write(raw)
                continue
            rec = json.loads(raw)
            ref = rec["semiconductor"]["mosfet"]["manufacturerInfo"]["reference"]
            if ref not in wanted or ref in seen:
                out.write(raw)
                continue
            seen.add(ref)

            if ref in QUARANTINE_PLAN:
                rec["quarantineReason"] = QUARANTINE_PLAN[ref]
                quarantined_lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
                audit["quarantined"].append(
                    {"reference": ref, "reason": QUARANTINE_PLAN[ref]})
                continue  # dropped from the main file

            changed = apply_repair(rec, REPAIR[ref])
            component = rec["semiconductor"]["mosfet"]
            errors = [e.message[:200] for e in schema.iter_errors(component)]
            ok, why = blade.check(component)
            if errors or not ok:
                print(f"ABORT on {ref}: schema={errors[:2]} blade={why}")
                tmp.unlink(missing_ok=True)
                return 1
            out.write(json.dumps(rec, ensure_ascii=False).encode() + b"\n")
            audit["repaired"].append({"reference": ref, "changed": changed,
                                      "datasheet": DATASHEET})
        out.flush()
        os.fsync(out.fileno())

    missing = sorted(wanted - seen)
    if missing:
        print(f"ABORT: planned parts never seen in {DATA.name}: {missing}")
        tmp.unlink(missing_ok=True)
        return 1

    print(f"repaired {len(audit['repaired'])}, quarantined {len(audit['quarantined'])}")
    for r in audit["repaired"]:
        print(f"  REPAIR {r['reference']}: {json.dumps(r['changed'])}")
    for q in audit["quarantined"]:
        print(f"  QUARANTINE {q['reference']}")
    print(blade.summary())

    if not a.apply:
        tmp.unlink(missing_ok=True)
        print("dry run: nothing replaced (pass --apply)")
        return 0

    os.replace(tmp, DATA)
    if quarantined_lines:
        with open(QUARANTINE, "a", encoding="utf-8") as q:
            q.writelines(quarantined_lines)
    AUDIT.write_text(json.dumps(audit, indent=1))
    print(f"replaced {DATA}\nquarantine -> {QUARANTINE}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
