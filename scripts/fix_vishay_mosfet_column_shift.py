#!/usr/bin/env python3
"""Undo the Vishay MOSFET parametric column shift (ABT #482).

    python3 scripts/fix_vishay_mosfet_column_shift.py [--dry-run]

WHAT WENT WRONG. Vishay publishes its MOSFET grid as a Next.js `webtableResults`
array keyed by opaque field ids. The relevant four, read back from the gateway's
own `tableColumns` map on 2026-08-01:

    P7002  Drain-to-source voltage   (V)
    P7012  Gate-to-source voltage    (V)   <- V_GS(max), the +-20 / +-30 rating
    P7006  Drain current (max.)      (A)   <- the real I_D
    P7008  Power dissipation (max.)  (W)

The import that seeded these records (May 2026, script not in the tree) took
P7012 for `continuousDrainCurrent` and P7008 for `gateSourceVoltageMax`. Every
affected part therefore claims a 20 A or 30 A drain rating -- Vishay's gate
rating -- and a gate rating of 62.5 V, 190 V, 375 V ... -- Vishay's watts.

SiHD2N80E is the sample the auditor caught: catalogued at 30 A against its own
2.75 ohm, i.e. 2475 W at the rated current in a DPAK. Vishay's grid gives
V_GS = 30 V, I_D = 2.8 A, P_D = 62.5 W for that row -- all three of the
record's numbers, one column to the left.

HOW THE REPAIR IS PROVEN. Not by inference: the same parametric layer is read
again and the record is tied to its vendor row before anything is written.

  * staging/vishay_mosfet_parametric_2026-08-01.json -- 2,021 series pulled from
    the 84 leaf gateways under /en/mosfets/.
  * A record is tied to its row by its OWN `onResistance` matching one of the
    row's r_DS(on)-at-V_GS columns within 1 % (or, when the row publishes no
    r_DS(on) at all, by drain-source voltage). No tie, no automatic write.
  * DATASHEET_VERIFIED below carries the parts the live grid cannot settle --
    discontinued rows, and multi-die parts where the record holds one channel
    and the grid shows the other. Each was read off the Vishay datasheet's
    PRODUCT SUMMARY and pinned by the same exact r_DS(on) match; the pin is
    quoted per entry so it can be re-checked.

Two fields are written, both straight from the vendor's correct column:
`continuousDrainCurrent` and `gateSourceVoltageMax`. `powerDissipation` is left
alone -- 900 of these records already carry one from a later DigiKey backfill,
and reconciling those is not this ticket.

Every rewritten record is validated against SAS/mosfet.json before it is
written; one failure aborts the whole run and leaves the file untouched.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO = Path(__file__).resolve().parents[1]
PSMA = REPO.parent
DATA = REPO / "data" / "mosfets.ndjson"
VENDOR = REPO / "staging" / "vishay_mosfet_parametric_2026-08-01.json"
AUDIT = REPO / "staging" / "vishay_mosfet_column_shift_audit.json"

PROV = {"source": "manufacturerParametric",
        "sourceName": "Vishay parametric grid re-read column-correct (__NEXT_DATA__ "
                      "webtableResults; P7006=Drain current, P7012=V_GS) [ABT #482]",
        "sourceUrl": "https://www.vishay.com/en/mosfets/",
        "retrievedDate": "2026-08-01",
        "fields": ["electrical.continuousDrainCurrent", "electrical.gateSourceVoltageMax"]}

RTOL = 0.01                     # r_DS(on) agreement that ties a record to a vendor row
RDS_KEYS = ("rds10", "rds75", "rds6", "rds45", "rds25", "rds18", "rds12")
VGS_CEILING = 30.0              # no Si MOSFET in this catalogue gates above 30 V;
                                # a larger gateSourceVoltageMax is a watt figure

# ref -> (continuousDrainCurrent | None, gateSourceVoltageMax, datasheet, pin)
# None for the current means the record's own value is already the vendor's and only
# the gate rating is repaired. `pin` is the r_DS(on) that identifies the row/channel.
DATASHEET_VERIFIED = {
    # Discontinued -- no longer carried in the live parametric grid.
    "SQ4282EY":        (8,     20, "docs/63582", "r_DS(on) 0.0123 at 10 V"),
    "SQ4284EY":        (8,     20, "docs/67334", "r_DS(on) 0.0135 at 10 V"),
    "SQ4920EY":        (8,     20, "docs/66724", "r_DS(on) 0.0145 at 10 V, per leg"),
    "SQ4937EY":        (-5,    20, "docs/67043", "r_DS(on) 0.075 at -10 V, per leg"),
    "SQ4949EY":        (-7.5,  20, "docs/67035", "r_DS(on) 0.035 at -10 V, per leg"),
    "SQS482EN":        (16,    20, "docs/67074", "r_DS(on) 0.0085 at 10 V"),
    "Si7116BDN":       (65,    20, "docs/78973", "r_DS(on) 0.0074 at 10 V"),
    "Si7738DP":        (30,    20, "docs/69982", "r_DS(on) 0.038 at 10 V"),
    "Si4425DDY":       (-19.7, 20, "docs/64732", "r_DS(on) 0.0098 at 10 V"),
    "SQ3419AEEV":      (-6.9,  12, "docs/65332", "r_DS(on) 0.061 at -10 V"),
    "SQ1464EEH":       (0.44,   8, "docs/75570", "r_DS(on) 1.41 at 1.5 V"),
    # Multi-die parts: the record carries one channel, the grid row shows the other.
    "SQ4532CEY":       (-5.3,  20, "docs/61727", "p-channel r_DS(on) 0.070 at -10 V"),
    "SQJ200EP":        (60,    20, "docs/67774", "n-channel-2 r_DS(on) 0.0037 at 10 V"),
    "SQJ204EP":        (60,    12, "docs/76441", "n-channel-2 r_DS(on) 0.0030 at 10 V"),
    "SQJ208EP":        (60,    20, "docs/77836", "n-channel-2 r_DS(on) 0.00390 at 10 V"),
    "SQJ260EP":        (54,    20, "docs/75486", "n-channel-2 r_DS(on) 0.0085 at 10 V"),
    "SQJ264EP":        (54,    20, "docs/77239", "n-channel-2 r_DS(on) 0.0086 at 10 V"),
    "SQJ262EP":        (40,    20, "docs/75504", "n-channel-2 r_DS(on) 0.0155 at 10 V"),
    "SQJ500AEP":       (-30,   20, "docs/62878", "p-channel r_DS(on) 0.0270 at -10 V"),
    "SQJ940EP":        (18,    20, "docs/62767", "n-channel-2 r_DS(on) 0.0064 at 10 V"),
    "SQJ942EP":        (45,    20, "docs/62669", "n-channel-2 r_DS(on) 0.011 at 10 V"),
    "SQJ990EP":        (34,    20, "docs/77789", "n-channel-2 r_DS(on) 0.0190 at 10 V"),
    "Si8916EDB":       (2.5,   12, "docs/61672", "R_S1S2(on) 0.075 at 4.5 V"),
    "SiSF12EDN":       (109,    8, "docs/61700", "R_S1S2(on) 0.0036 at 4.5 V"),
    "SiZ240DT":        (47,    20, "docs/77182", "channel-2 r_DS(on) 0.00841 at 10 V"),
    "SiZ270DT":        (19.1,  20, "docs/77670", "channel-2 r_DS(on) 0.0394 at 10 V"),
    "SiZ988DT":        (60,    20, "docs/66937", "channel-2 r_DS(on) 0.0041 at 10 V"),
    "SiZ200DT":        (60,    20, "docs/75033", "channel-2 r_DS(on) 0.0058 at 10 V"),
    "SiZ340DDT":       (63,    20, "docs/61551", "channel-2 r_DS(on) 0.005 at 10 V"),
    "SiZ710DT":        (16,    20, "docs/65733", "channel-1 r_DS(on) 0.0068 at 10 V"),
    "SiZ998BDT":       (94.6,  20, "docs/77875", "channel-2 r_DS(on) 0.0024 at 10 V"),
    "SiZ998DT":        (60,    20, "docs/62979", "channel-2 r_DS(on) 0.0028 at 10 V; V_GS +20, -16"),
    "SiZ980BDT":       (197,   20, "docs/77251", "channel-2 r_DS(on) 0.00106 at 10 V"),
    "SiZF300DT":       (141,   16, "docs/76288", "channel-2 r_DS(on) 0.00184 at 10 V; V_GS +16, -12"),
    "SiZF360DT":       (143,   16, "docs/77233", "channel-2 r_DS(on) 0.00190 at 10 V; V_GS +16, -12"),
    "SiZF914DT":       (40,    20, "docs/75978", "channel-1 r_DS(on) 0.00380 at 10 V; V_GS +20, -16"),
    "SiZF918BDT":      (158,   16, "docs/62448", "channel-2 r_DS(on) 0.0014 at 10 V; V_GS +16, -12"),
    "SiZF920DT":       (197,   16, "docs/79595", "channel-2 r_DS(on) 0.00105 at 10 V; V_GS +16, -12"),
    "SiZF928DT":       (248,   16, "docs/63037", "channel-2 r_DS(on) 0.00075 at 10 V; V_GS +16, -12"),
    "SiZF906DDT":      (257,   20, "docs/61545", "channel-2 r_DS(on) 0.00090 at 10 V; V_GS +20, -16"),
    "SiZF906BDT":      (257,   20, "docs/77619", "channel-2 r_DS(on) 0.00068 at 10 V; V_GS +20, -16"),
    "Si4288DY":        (9.2,   20, "docs/67078", "r_DS(on) 0.020 at 10 V"),
    "Si4202DY":        (12.1,  20, "docs/67092", "r_DS(on) 0.014 at 10 V"),
    "Si4214DDY-T1-E3": (8.5,   20, "docs/67907", "r_DS(on) 0.0195 at 10 V"),
    "Si4532CDY":       (-4.3,  20, "docs/64805", "p-channel r_DS(on) 0.089 at -10 V"),
    "Si7540ADP":       (9,     12, "docs/62951", "p-channel r_DS(on) 0.0280 at -4.5 V"),
    "Si1539CDL":       (-0.5,  20, "docs/67469", "p-channel r_DS(on) 0.890 at -10 V"),
    "Si8821EDB":       (-2.3,  12, "docs/63268", "r_DS(on) 0.135 at -4.5 V"),
    "SIR5208DP":       (165,    8, "docs/62488", "r_DS(on) 0.0013 at 8 V; V_GS +8 / -7"),
    "Si8806DB":        (3.9,    8, "docs/62652", "12 V part, I_D 3.9 A"),
    "Si8819EDB":       (-2.9,   8, "docs/62963", "r_DS(on) 0.080 at -3.7 V"),
    "Si7157DP":        (-60,   12, "docs/62860", "r_DS(on) 0.0016 at -10 V"),
    "Si7119DN":        (-3.8,  20, "docs/74251", "r_DS(on) 1.05 at -10 V"),
    "Si2303CDS":       (-2.7,  20, "docs/69991", "r_DS(on) 0.190 at -10 V"),
    # Current already agrees with the vendor; only the gate rating held watts.
    "SiA445EDJT":      (None,  12, "docs/67437", "r_DS(on) 0.0167 at -4.5 V; I_D -12 A on every row"),
    "Si7956DP":        (None,  20, "docs/72960", "r_DS(on) 0.105 at 10 V; I_D 2.6 A already vendor-correct"),
    "Si1553CDL":       (None,  12, "docs/67693", "n-channel r_DS(on) 0.390; I_D 0.7 A already vendor-correct"),
}


def load_validator():
    reg = Registry()
    for repo in ("PEAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS", "CIAS"):
        root = PSMA / repo / "schemas"
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            doc = json.loads(path.read_text())
            if "$id" in doc:
                reg = reg.with_resource(doc["$id"], Resource.from_contents(doc))
    schema = json.loads((PSMA / "SAS" / "schemas" / "mosfet.json").read_text())
    return Draft202012Validator(schema, registry=reg)


def vendor_index(series):
    idx = {}
    for name, row in series.items():
        for tok in (t.strip().upper() for t in name.split(",")):
            if tok:
                idx.setdefault(tok, row)
    return idx


def vendor_row(idx, ref):
    key = (ref or "").strip().upper()
    if key in idx:
        return idx[key]
    for tok in (t.strip() for t in key.split(",")):
        if tok in idx:
            return idx[tok]
    for suffix in ("-T1-GE3", "-T1-E3", "-T1R", "-GE3", "-T1", "-E3"):
        if key.endswith(suffix) and key[: -len(suffix)] in idx:
            return idx[key[: -len(suffix)]]
    return None


def same(a, b):
    return a is not None and b is not None and abs(abs(a) - abs(b)) <= 1e-6


def ties_to_row(electrical, row):
    """The record is this vendor row: its r_DS(on) is one the row publishes."""
    rds = [row[k] for k in RDS_KEYS if isinstance(row.get(k), (int, float))]
    own = electrical.get("onResistance")
    if rds:
        return any(own is not None and abs(own - v) <= RTOL * abs(v) for v in rds)
    return same(electrical.get("drainSourceVoltage"), row.get("vds"))


def plan_record(ref, electrical, row):
    """-> (new_current, new_gate, reason) or None when the record is not shifted."""
    override = DATASHEET_VERIFIED.get(ref)
    if override is not None:
        current, gate, doc, pin = override
        return current, gate, f"Vishay {doc} PRODUCT SUMMARY, pinned by {pin}"

    if row is None:
        return None
    cur, gate = electrical.get("continuousDrainCurrent"), electrical.get("gateSourceVoltageMax")
    v_id, v_gs, v_pd = row.get("id"), row.get("vgs"), row.get("pd")

    current_is_gate = same(cur, v_gs) and not same(cur, v_id)
    gate_is_watts = gate is not None and same(gate, v_pd) and not same(gate, v_gs)
    gate_impossible = (gate is not None and v_gs is not None
                       and abs(gate) > VGS_CEILING and not same(gate, v_gs))
    if not (current_is_gate or gate_is_watts or gate_impossible):
        return None
    if not ties_to_row(electrical, row):
        return None

    why = []
    if current_is_gate:
        why.append(f"continuousDrainCurrent {cur} is the row's V_GS ({v_gs} V), not its I_D ({v_id} A)")
    if gate_is_watts:
        why.append(f"gateSourceVoltageMax {gate} is the row's P_D ({v_pd} W)")
    elif gate_impossible:
        why.append(f"gateSourceVoltageMax {gate} exceeds any gate rating; vendor V_GS is {v_gs} V")
    return (v_id if current_is_gate else None), v_gs, "; ".join(why)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    idx = vendor_index(json.loads(VENDOR.read_text())["series"])
    validator = load_validator()

    audit = {"ticket": "ABT #482", "repaired": [], "unresolved": []}
    seen_override, skipped_override = set(), set()
    tmp = DATA.with_suffix(".ndjson.tmp")
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            wrote = False
            if b'"Vishay"' in raw:
                try:
                    rec = json.loads(raw)
                    info = rec["semiconductor"]["mosfet"]["manufacturerInfo"]
                    sheet = info["datasheetInfo"]
                    el = sheet["electrical"]
                    ref = str(info.get("reference") or "").strip()
                except Exception:
                    ref, info = "", None
                if info is not None and info.get("name") == "Vishay":
                    row = vendor_row(idx, ref)
                    plan = plan_record(ref, el, row)
                    if plan is not None and validator.is_valid(rec["semiconductor"]["mosfet"]):
                        current, gate, why = plan
                        if ref in DATASHEET_VERIFIED:
                            seen_override.add(ref)
                        changed = {}
                        if current is not None and not same(current, el.get("continuousDrainCurrent")):
                            changed["continuousDrainCurrent"] = {
                                "was": el.get("continuousDrainCurrent"), "now": current}
                            el["continuousDrainCurrent"] = current
                        if gate is not None and el.get("gateSourceVoltageMax") != gate:
                            changed["gateSourceVoltageMax"] = {
                                "was": el.get("gateSourceVoltageMax"), "now": gate}
                            el["gateSourceVoltageMax"] = gate
                        if changed:
                            prov = sheet.get("provenance") or []
                            if PROV not in prov:
                                sheet["provenance"] = prov + [PROV]
                            errors = list(validator.iter_errors(rec["semiconductor"]["mosfet"]))
                            if errors:
                                print(f"ABORT {ref}: {errors[0].message[:160]}")
                                out.close()
                                tmp.unlink(missing_ok=True)
                                return 1
                            out.write(json.dumps(rec, ensure_ascii=False).encode() + b"\n")
                            wrote = True
                            audit["repaired"].append({"reference": ref, "changed": changed,
                                                      "evidence": why})
                    elif plan is not None:
                        # Already schema-invalid before this run for an unrelated reason.
                        # Left byte-for-byte alone: repairing it would smuggle a second,
                        # unreviewed fix into this ticket's diff.
                        skipped_override.add(ref)
                        audit["unresolved"].append(
                            {"reference": ref, "reason": "record was schema-invalid before "
                             "this run; not touched",
                             "firstError": next(validator.iter_errors(
                                 rec["semiconductor"]["mosfet"])).message[:200],
                             "wouldHaveBeen": {"continuousDrainCurrent": plan[0],
                                               "gateSourceVoltageMax": plan[1]}})
            if not wrote:
                out.write(raw)
        out.flush()
        os.fsync(out.fileno())

    missing = sorted(set(DATASHEET_VERIFIED) - seen_override - skipped_override)
    if missing:
        print(f"ABORT: datasheet-verified parts never seen in {DATA.name}: {missing}")
        tmp.unlink(missing_ok=True)
        return 1

    print(f"repaired {len(audit['repaired'])} records")
    if args.dry_run:
        tmp.unlink(missing_ok=True)
        print("--dry-run: nothing replaced")
        return 0
    os.replace(tmp, DATA)
    AUDIT.write_text(json.dumps(audit, indent=1))
    print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
