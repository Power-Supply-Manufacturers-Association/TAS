#!/usr/bin/env python3
"""ABT #500: TO-247 MOSFET records carrying another package's thermal table.

THE DEFECT. A vendor publishes ONE datasheet for a whole package family and gives
it one thermal table PER PACKAGE. Infineon's IPX60R190P6 document is the clearest
case in this catalogue:

    Table 3  Thermal characteristics (Non FullPAK) TO-220, TO-247   RthJC 0.83  RthJA 62
    Table 4  Thermal characteristics (FullPAK)     TO-220FP        RthJC 3.7   RthJA 80

ST does the same with columns (STW13NK60Z: TO-247 0.83 K/W next to TO-220FP
3.6 K/W). Whatever built these records took one thermal row and stamped it on
every package variant, so tabbed TO-247 parts ended up with the ISOLATED FullPAK
figures. IPW80R280P7 is the sample Qarlos caught: stored 3.5 K/W / 36 W where the
Infineon TO-247 datasheet says 1.2 K/W / 101 W.

WHY MOS_POWER_THERMAL DID NOT SEE IT. Ptot and Rth(j-c) came from the SAME wrong
row, so they agree with each other: (150-25)/3.5 = 35.7 W matches the stored 36 W.
The contradiction is with the ELECTRICAL block -- 16 A through 0.28 ohm is 71.7 W
cold, twice what a 3.5 K/W path can remove. That is the new Blade Runner check
MOS_IDC_VS_THERMAL, and it is what decides the split below.

WHAT THIS SCRIPT DOES, per record in the population:

  REPAIR    where the vendor's own datasheet was retrieved and its TO-247 row read.
            thermal.thermalResistanceJunctionCase, thermal.thermalResistanceJunctionAmbient
            and electrical.powerDissipation are replaced together -- they are one
            datum published three ways, and fixing one while leaving the others
            would swap an old contradiction for a new one. A manufacturerDatasheet
            provenance entry naming the exact PDF is appended.

  QUARANTINE (synthetic) for the generated families. They carry
            thermalResistanceJunctionAmbient == 35 * thermalResistanceJunctionCase
            exactly, Rds(on) == constant/Id exactly across a family, Ciss of 50-90 uF,
            Qg of 0.8-1.6 uC and Vth(min) 9 V above a Vgs(max) of 18 V, on MPNs that
            match no vendor scheme (ROHM SCT<3-digit-V><3-digit-R>, Infineon
            IMW<4-digit-V>R<4-digit-R>) and resolve to nothing at rohm.com or
            infineon.com. Nothing in them is a vendor value, so nothing is repairable.

  QUARANTINE (thermal conflict) for the rest: real-looking parts whose thermal block
            is provably not theirs but whose datasheet could not be retrieved to
            replace it (onsemi's document API was returning 502, and several MPNs
            resolve to no datasheet at the vendor at all -- Infineon publishes
            IPA/IPB/IPD/IPP for the 60R280P7 and 60R360P7 dies but no IPW). Records
            move intact so the stored values stay auditable for a re-sourcing pass.

Repairs are gated on Blade Runner AND JSON Schema before they are accepted; a
record that fails either is left untouched and reported, never written.

  fix_to247_thermal_package_conflict.py            # dry run
  fix_to247_thermal_package_conflict.py --apply
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blade_gate import BladeGate  # noqa: E402

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "mosfets.ndjson"
Q_CONFLICT = TAS / "data" / "mosfets.quarantine_thermal_package_conflict.ndjson"
Q_SYNTHETIC = TAS / "data" / "mosfets.quarantine_synthetic.ndjson"

# The population Qarlos filed: a direct-copper-tab package holding a thermal
# resistance that belongs to an isolated one.
CASE_PREFIX = "TO-247"
RTHJC_TRIGGER = 2.0

# Generated-family signature. The generator tied ambient to case by a fixed factor;
# no vendor's two independently measured thermal paths land on an exact ratio.
FABRICATED_RTHJA_OVER_RTHJC = 35.0

# TO-247 thermal rows read from the vendor datasheet retrieved at the URL given.
# ptot is the datasheet's own Ptot for that package at the case temperature its
# thermal row implies, so ptot == (tjmax - 25)/rthjc holds to the datasheet's own
# rounding. Anything not in this table was NOT sourced and is not repaired here.
VERIFIED = {
    "IPW80R280P7": dict(
        rthjc=1.2, rthja=62.0, ptot=101.0,
        url="https://www.infineon.com/assets/row/public/documents/24/49/"
            "infineon-ipw80r280p7-datasheet-en.pdf",
        note="Rev 2.1 2018-02-12, PG-TO247-3; Table 2 Ptot 101 W, Table 3 RthJC 1.2, RthJA 62 leaded"),
    "IPW60R180P7": dict(
        rthjc=1.74, rthja=62.0, ptot=72.0,
        url="https://www.infineon.com/assets/row/public/documents/24/49/"
            "infineon-ipw60r180p7-datasheet-en.pdf",
        note="PG-TO247-3; Ptot 72 W, RthJC 1.74, RthJA 62 leaded"),
    "IPW80R360P7": dict(
        rthjc=1.5, rthja=62.0, ptot=84.0,
        url="https://www.infineon.com/assets/row/public/documents/24/49/"
            "infineon-ipw80r360p7-datasheet-en.pdf",
        note="PG-TO247-3; Ptot 84 W, RthJC 1.5, RthJA 62 leaded"),
    "IPW60R190P6": dict(
        rthjc=0.83, rthja=62.0, ptot=151.0,
        url="https://www.infineon.com/assets/row/public/documents/24/49/"
            "infineon-ipx60r190p6-datasheet-en.pdf",
        note="family datasheet IPW/IPB/IPP/IPA; Table 3 (Non FullPAK) TO-220, TO-247 "
             "RthJC 0.83, RthJA 62; Ptot (Non FullPAK) 151 W. The stored 2.5 K/W / 50 W "
             "is neither that row nor the FullPAK row (3.7 / 34)"),
    "STW13NK60Z": dict(
        rthjc=0.83, rthja=50.0, ptot=150.0,
        url="https://www.st.com/resource/en/datasheet/stw13nk60z.pdf",
        note="Table 2 Thermal data, TO-247 column: RthJC 0.83, RthJA 50; PTOT (TO-220, TO-247) 150 W. "
             "The stored 2.08 K/W / 60 W matches no column (TO-220FP is 3.6 / 35)"),
    "STW9NK90Z": dict(
        rthjc=0.78, rthja=50.0, ptot=160.0,
        url="https://www.st.com/resource/en/datasheet/stw9nk90z.pdf",
        note="Table 2 Thermal data, TO-247 column: RthJC 0.78, RthJA 50; "
             "PTOT (D2PAK, TO-220, TO-247) 160 W"),
    "C3M0280090D": dict(
        rthjc=2.8, rthja=40.0, ptot=45.0,
        url="https://assets.wolfspeed.com/uploads/2020/12/C3M0280090D.pdf",
        note="Thermal Characteristics: RthetaJC 2.8 max, RthetaJA 40; PD 45 W at TC=25 C, TJ=150 C. "
             "A 900 V/280 mohm SiC die in TO-247 genuinely sits above 2 K/W"),
    "C2M1000170D": dict(
        rthjc=1.8, rthja=40.0, ptot=69.0,
        url="https://assets.wolfspeed.com/uploads/2024/01/Wolfspeed_C2M1000170D_data_sheet.pdf",
        note="Thermal Characteristics: RthetaJC 1.7 typ / 1.8 max, RthetaJA 40; "
             "PD 69 W at TC=25 C, TJ=150 C"),
    "IMW120R220M1H": dict(
        rthjc=2.0, rthja=62.0, ptot=75.0,
        url="https://www.infineon.com/assets/row/public/documents/60/49/"
            "infineon-imw120r220m1h-datasheet-en.pdf",
        note="Rev 2.2 2020-12-11, PG-TO247-3; Table 2 Ptot 75 W at TC=25 C, "
             "Table 3 Rth(j-c) 1.5 typ / 2 max, Rth(j-a) 62 leaded"),
    "SCT2450KE": dict(
        rthjc=1.77, rthja=50.0, ptot=85.0, tjmax=175.0,
        url="https://fscdn.rohm.com/en/products/databook/datasheet/discrete/sic/mosfet/sct2450ke-e.pdf",
        note="ROHM TO-247N: RthJC 1.36 typ / 1.77 max, RthJA 50 max, PD 85 W at Tc=25 C, "
             "Tj 175 C. The stored Tj 200 C is also the wrong figure and is corrected with "
             "the row, since 200 C against 1.77 K/W would not reproduce the vendor's 85 W"),
}

# MPNs in the population that resolve to no product and no datasheet at the
# manufacturer the record names, probed 2026-08-02:
#   * Infineon publishes IPA/IPB/IPD/IPP datasheets for the 60R280P7, 60R360P7 and
#     80R450P7 dies but no IPW (TO-247) variant, and no document resolves for any
#     of these under infineon.com/assets/row/public/documents/**/infineon-<mpn>-datasheet-en.pdf
#     (the path that does resolve for every IPW/IMW part confirmed real here).
#   * SCT2xxx/SCT3xxx is ROHM's SiC series, not ST's; these records name
#     STMicroelectronics, their st.com datasheet URL 404s, and rohm.com has no
#     such product either.
# A record whose part cannot be found at its own manufacturer cannot have its
# thermal row re-sourced, and its stored row cannot be trusted.
UNRESOLVED_MPNS = {
    "IPW60R280P7", "IPW60R360P7", "IPW60R450P7", "IPW80R450P7", "IPW80R650P7",
    "IPW60R200G7", "IPW60R280G7", "IPW65R200CFD7", "IPW65R280CFD7",
    "IMW170R450M1H", "IMW170R1K0M1H", "IMW120R300M1H", "IMW65R160M1H", "IMW65R230M1H",
    "SCT3045AL", "SCT3050AL", "SCT3090AL", "SCT3160AL", "SCT2160NY",
}

# Blade Runner's MOS_IDC_VS_THERMAL factor, restated so the split and the gate
# cannot drift, and MOS_POWER_THERMAL's, which catches the records whose stored
# Ptot contradicts their own stored Rth(j-c).
IDC_THERMAL_RATIO_IMP = 2.0
PTHERMAL_RATIO_SUS = 3.0

REASON_CONFLICT = "to247-thermal-block-belongs-to-another-package-variant"
REASON_SYNTHETIC = "generated-family-thermal-and-electrical-values-are-not-vendor-data"


def datasheet_of(rec):
    m = ((rec.get("semiconductor") or {}).get("mosfet")) or {}
    return m, ((m.get("manufacturerInfo") or {}).get("datasheetInfo")) or {}


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def in_population(rec):
    _, di = datasheet_of(rec)
    case = (di.get("part") or {}).get("case")
    rthjc = num((di.get("thermal") or {}).get("thermalResistanceJunctionCase"))
    return (isinstance(case, str) and case.startswith(CASE_PREFIX)
            and rthjc is not None and rthjc > RTHJC_TRIGGER)


def is_generated(rec):
    _, di = datasheet_of(rec)
    th = di.get("thermal") or {}
    rthjc, rthja = num(th.get("thermalResistanceJunctionCase")), num(th.get("thermalResistanceJunctionAmbient"))
    if rthjc is None or rthja is None:
        return False
    return abs(rthja - FABRICATED_RTHJA_OVER_RTHJC * rthjc) < 1e-9


def conflict_evidence(rec):
    """Why this record's thermal row is provably not its own, or None.

    Only positive evidence quarantines a record. 'TO-247 above 2 K/W' by itself is
    not evidence: a small SiC die genuinely sits there (Wolfspeed C3M0280090D is
    2.8 K/W in TO-247-3, from its own datasheet), and withdrawing a correct part to
    make a predicate return zero would be the worse error."""
    _, di = datasheet_of(rec)
    el, th = di.get("electrical") or {}, di.get("thermal") or {}
    pn = (di.get("part") or {}).get("partNumber")
    rthjc = num(th.get("thermalResistanceJunctionCase"))
    tjmax = num(th.get("junctionTemperatureMax"))
    idc, ron = num(el.get("continuousDrainCurrent")), num(el.get("onResistance"))
    pdiss = num(el.get("powerDissipation"))
    pmax = (tjmax - 25.0) / rthjc if (rthjc and tjmax and rthjc > 0 and tjmax > 25) else None

    if pmax and idc and ron and ron > 0:
        pcond = idc * idc * ron
        if pcond > pmax * IDC_THERMAL_RATIO_IMP:
            return (f"conduction loss at the rated {idc} A through {ron} ohm is {pcond:.1f} W, "
                    f"{pcond / pmax:.1f}x what this record's own {rthjc} K/W path can remove "
                    f"({pmax:.1f} W) — Blade Runner MOS_IDC_VS_THERMAL")
    if pmax and pdiss and pdiss > pmax * PTHERMAL_RATIO_SUS:
        return (f"stored powerDissipation {pdiss} W is {pdiss / pmax:.1f}x the "
                f"(Tjmax-25)/{rthjc} K/W ceiling of {pmax:.1f} W the same record states — "
                f"the two figures come from different packages")
    if pn in UNRESOLVED_MPNS:
        return ("no product page and no datasheet resolve for this part number at the "
                "manufacturer the record names, so the thermal row cannot be re-sourced "
                "and the stored one has no vendor behind it")
    return None


def repair(rec, spec):
    """Replace the mis-sourced thermal row and its Ptot with the datasheet's own."""
    _, di = datasheet_of(rec)
    fields = ["thermal.thermalResistanceJunctionCase",
              "thermal.thermalResistanceJunctionAmbient",
              "electrical.powerDissipation"]
    di.setdefault("thermal", {})["thermalResistanceJunctionCase"] = spec["rthjc"]
    di["thermal"]["thermalResistanceJunctionAmbient"] = spec["rthja"]
    di.setdefault("electrical", {})["powerDissipation"] = spec["ptot"]
    if "tjmax" in spec:
        di["thermal"]["junctionTemperatureMax"] = spec["tjmax"]
        fields.append("thermal.junctionTemperatureMax")
    di.setdefault("provenance", []).append({
        "source": "manufacturerDatasheet",
        "sourceName": "ABT #500 re-source of the TO-247 thermal row: " + spec["note"],
        "sourceUrl": spec["url"],
        "fields": fields,
    })
    return rec


def build_schema_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    repos = ["PEAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS", "CIAS"]
    resources = []
    for repo in repos:
        d = TAS.parent / repo / "schemas"
        if not d.is_dir():
            continue
        for p in d.rglob("*.json"):
            s = json.loads(p.read_text())
            if isinstance(s, dict) and "$id" in s:
                resources.append((s["$id"], Resource(contents=s, specification=DRAFT202012)))
    registry = Registry().with_resources(resources)
    schema = json.loads((TAS.parent / "SAS" / "schemas" / "mosfet.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def main():
    apply = "--apply" in sys.argv
    if not SRC.exists():
        print(f"no catalogue at {SRC}", file=sys.stderr)
        return 2

    gate = BladeGate(("semiconductor", "mosfet"))
    schema_validator = build_schema_validator()

    tmp = SRC.with_suffix(".ndjson.abt500.tmp")
    kept = repaired = q_conflict = q_synth = rejected = 0
    repaired_pns, rejected_pns, unproven = [], [], []
    conflict_lines, synth_lines = [], []

    with SRC.open(encoding="utf-8") as fin, tmp.open("w", encoding="utf-8") as fkeep:
        for line in fin:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # Not ours to drop: keep it byte-identical and let the gate report it.
                fkeep.write(line if line.endswith("\n") else line + "\n")
                kept += 1
                continue
            if not in_population(rec):
                fkeep.write(line if line.endswith("\n") else line + "\n")
                kept += 1
                continue

            mos, di = datasheet_of(rec)
            pn = (di.get("part") or {}).get("partNumber")
            spec = VERIFIED.get(pn)

            if spec is not None:
                patched = repair(json.loads(line), spec)
                comp = patched["semiconductor"]["mosfet"]
                ok, why = gate.check(comp)
                # SAS/mosfet.json validates the UNWRAPPED body, exactly as
                # tests/test_data.py peels ["semiconductor", "mosfet"] before validating.
                errs = sorted(schema_validator.iter_errors(comp), key=lambda e: e.path)
                if not ok or errs:
                    # A repair that cannot pass both gates is not a repair. Leave the
                    # record exactly as found and say so.
                    rejected += 1
                    rejected_pns.append((pn, why or errs[0].message))
                    fkeep.write(line if line.endswith("\n") else line + "\n")
                    kept += 1
                    continue
                repaired += 1
                repaired_pns.append(pn)
                fkeep.write(json.dumps(patched, ensure_ascii=False) + "\n")
                continue

            if is_generated(rec):
                rec["quarantineReason"] = REASON_SYNTHETIC
                q_synth += 1
                synth_lines.append(json.dumps(rec, ensure_ascii=False) + "\n")
                continue

            why = conflict_evidence(rec)
            if why is None:
                # In the predicate but nothing proves the row wrong and no datasheet
                # was reachable to confirm it. Withholding judgement is the honest
                # outcome; it is reported, not silently kept.
                unproven.append(pn)
                fkeep.write(line if line.endswith("\n") else line + "\n")
                kept += 1
                continue
            rec["quarantineReason"] = REASON_CONFLICT + ": " + why
            q_conflict += 1
            conflict_lines.append(json.dumps(rec, ensure_ascii=False) + "\n")

    if apply:
        # Append: a quarantine file is the destination of record for its defect, and
        # a later pass must add to it, never replace what an earlier pass put there.
        for path, lines in ((Q_CONFLICT, conflict_lines), (Q_SYNTHETIC, synth_lines)):
            if lines:
                with path.open("a", encoding="utf-8") as fq:
                    fq.writelines(lines)
        os.replace(tmp, SRC)
    else:
        os.remove(tmp)

    print(f"{'APPLIED' if apply else 'DRY RUN'} — ABT #500 TO-247 thermal package conflict")
    print(f"  kept {kept}   repaired {repaired}   "
          f"quarantined {q_conflict} -> {Q_CONFLICT.name}   {q_synth} -> {Q_SYNTHETIC.name}")
    if repaired_pns:
        print("  repaired: " + ", ".join(repaired_pns))
    if rejected:
        print(f"  REPAIR REJECTED BY GATE ({rejected}) — left untouched:")
        for pn, why in rejected_pns:
            print(f"    {pn}: {why}")
    if unproven:
        print(f"  LEFT IN PLACE ({len(unproven)}) — in the predicate, but nothing in the "
              f"record proves the row wrong and no datasheet was reachable: "
              + ", ".join(unproven))
    print("  " + gate.summary())
    if not apply:
        print("\n(dry run — re-run with --apply to write)")
    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main())
