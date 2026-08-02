#!/usr/bin/env python3
"""Undo the Vishay MOSFET powerDissipation column shift (ABT #494).

    python3 scripts/fix_vishay_mosfet_pd_column.py [--dry-run]

WHAT WENT WRONG. The same May-2026 import that read `continuousDrainCurrent` and
`gateSourceVoltageMax` one column to the left (ABT #482) shifted a third field.
Vishay's `webtableResults` field ids, read back from the gateway's own
`tableColumns` map on 2026-08-01:

    P7008  Power dissipation (max.)   (W)   <- what powerDissipation means
    P7016  On-resistance at 4.5 V     (ohm) <- what the import actually wrote

So 917 records carry an OHM figure in a WATT field, and the shape is loud:
SQ1421EDH stored 0.395 (its own r_DS(on) at -4.5 V) against a real P_D of 2.7 W;
SiRS4300DP, a 680 A part, stored 0.00068 W. `powerDissipation` is defined by
SAS/mosfet.json as "P_D max at Tc=25C in Watts", which is exactly Vishay's P7008.

HOW THE REPAIR IS PROVEN. Nothing is inferred from the record alone.

  * staging/vishay_mosfet_parametric_2026-08-01.json -- the parametric layer
    re-read for #482 (2,021 series from the 84 leaf gateways under /en/mosfets/).
  * A record is repaired from the grid only when its stored powerDissipation is,
    to the last digit, one of the r_DS(on)-at-V_GS columns that row publishes.
    That equality IS the tie: it says the number came from that row, so the same
    row's `pd` is the P_D the import should have written. 905 repaired that way.
  * DATASHEET_VERIFIED below carries the 56 the grid cannot settle: the 12 whose
    row publishes no P_D at all, and the 44 multi-die / discontinued parts where
    the record holds one channel and the grid shows the other (there the row's own
    `pd` is the WRONG channel's -- SiZ988DT's grid row says 20.2 W, which is
    channel-1; the record is channel-2 and its P_D is 40 W). Each was read off the
    Vishay datasheet's ABSOLUTE MAXIMUM RATINGS and is quoted with the r_DS(on)
    that pins the row/channel, so it can be re-checked.

One field is written: `electrical.powerDissipation`. Nothing else is touched.

Every rewritten record must pass BOTH gates before it is accepted -- JSON Schema
(SAS/mosfet.json) and Blade Runner (tas_validator). A schema failure aborts the
whole run and leaves the file untouched; a Blade Runner block leaves that one
record byte-for-byte alone and is reported in the audit.

data/mosfets.quarantine_incomplete.ndjson holds 11 more of the same import's
records and is repaired too -- leaving a known ohms-in-watts value in a record a
later promotion pass will pull into the live catalogue is a landmine, not a
containment. Those records are quarantined for MISSING REQUIRED FIELDS, so they
cannot satisfy SAS/mosfet.json by construction; there the schema rule is "no new
error" (the error set must be identical before and after) rather than "must
pass", and Blade Runner must still pass outright. They stay quarantined.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blade_gate import BladeGate  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PSMA = REPO.parent
DATA = REPO / "data" / "mosfets.ndjson"
QUARANTINE = REPO / "data" / "mosfets.quarantine_incomplete.ndjson"
VENDOR = REPO / "staging" / "vishay_mosfet_parametric_2026-08-01.json"
AUDIT = REPO / "staging" / "vishay_mosfet_pd_column_audit.json"

GRID_PROV = {"source": "manufacturerParametric",
             "sourceName": "Vishay parametric grid re-read column-correct (__NEXT_DATA__ "
                           "webtableResults; P7008=Power dissipation, NOT P7016=On-resistance "
                           "at 4.5 V) [ABT #494]",
             "sourceUrl": "https://www.vishay.com/en/mosfets/",
             "retrievedDate": "2026-08-01",
             "fields": ["electrical.powerDissipation"]}

RDS_KEYS = ("rds10", "rds75", "rds6", "rds45", "rds25", "rds18", "rds12")

# ref -> (P_D in W, Vishay doc id, the reading that pins the row/channel)
# Read off the datasheet's ABSOLUTE MAXIMUM RATINGS block, T_C = 25 C wherever the
# package publishes a case rating (T_F for SOT-23/SOT-363, which quote foot
# temperature; T_A only where the package publishes nothing else).
DATASHEET_VERIFIED = {
    # --- The grid row matches the record but publishes no P_D column. ---------
    "SQ4483EY":   (7,     "74794", "T_C = 25 C; single p-channel, r_DS(on) 0.0085 at -10 V / 0.0200 at -4.5 V"),
    "Si4943CDY":  (3.1,   "69985", "T_C = 25 C; dual p-channel, r_DS(on) 0.0192 at -10 V / 0.0330 at -4.5 V"),
    "Si7998DP":   (22,    "68970", "channel-1 (I_D 25 A at T_C = 25 C, r_DS(on) 0.0093 at 10 V); P_D 22 W at T_C = 25 C"),
    "Si2399BDS":  (1.8,   "61604", "T_F = 25 C (SOT-23 quotes foot temperature); r_DS(on) 0.028"),
    "Si2324BDS":  (1.7,   "61734", "T_C = 25 C; r_DS(on) 0.21"),
    "Si4459BDY":  (5.6,   "76759", "T_C = 25 C; r_DS(on) 0.0049 at -10 V"),
    "Si1480BDH":  (2.6,   "62196", "T_C = 25 C; r_DS(on) 0.212"),
    "Si3425DV":   (3.0,   "61605", "T_F = 25 C; r_DS(on) 0.028"),
    "Si1425DH":   (2.8,   "61598", "T_F = 25 C; r_DS(on) 0.035"),
    "SiSA40DN":   (52,    "76681", "T_C = 25 C; r_DS(on) 0.0011 at 10 V"),
    "Si3424CDV":  (3.6,   "67443", "T_C = 25 C; r_DS(on) 0.026"),
    "SiSA01DN":   (52,    "76198", "T_C = 25 C; r_DS(on) 0.0049 at -10 V"),
    "TN2404K, TN2404KL, BS107KL":
                  (0.36,  "72225", "T_A = 25 C in the SOT-23 (TO-236) column the grid row is "
                                   "drawn from, R_thJA 350 C/W (the TO-92 column is 0.8 W); the "
                                   "part publishes no case rating. r_DS(on) 4 ohm at both 10 V "
                                   "and 4.5 V -- which is the stored figure"),
    # --- Discontinued: the live grid no longer carries the row. ---------------
    "SQ4282EY":   (3.9,   "63582", "T_C = 25 C; r_DS(on) 0.0123 at 10 V / 0.0135 at 4.5 V (the stored figure)"),
    "SQ4284EY":   (3.9,   "67334", "T_C = 25 C; r_DS(on) 0.0135 at 10 V / 0.0148 at 4.5 V (the stored figure)"),
    "SQ4920EY":   (4.4,   "66724", "T_C = 25 C; r_DS(on) 0.0145 at 10 V / 0.0175 at 4.5 V (the stored figure)"),
    "SQ4937EY":   (3.3,   "67043", "T_C = 25 C; r_DS(on) 0.075 at -10 V / 0.145 at -4.5 V (the stored figure)"),
    "SQ4949EY":   (3.3,   "67035", "T_C = 25 C; r_DS(on) 0.035 at -10 V / 0.065 at -4.5 V (the stored figure)"),
    "SQS482EN":   (62,    "67074", "T_C = 25 C; r_DS(on) 0.0085 at 10 V / 0.0100 at 4.5 V (the stored figure)"),
    "SQ3419AEEV": (5,     "65332", "T_C = 25 C; r_DS(on) 0.061 at -10 V / 0.093 at -4.5 V (the stored figure)"),
    "Si7116BDN":  (62.5,  "78973", "T_C = 25 C; r_DS(on) 0.0074 at 10 V / 0.0096 at 4.5 V (the stored figure)"),
    "Si4425DDY":  (5.7,   "64732", "T_C = 25 C; r_DS(on) 0.0098 at 10 V / 0.0165 at 4.5 V (the stored figure)"),
    # --- Multi-die: the record holds one channel, the grid row shows the other,
    #     so the row's own `pd` belongs to the channel this record is NOT. ------
    "SQJ200EP":   (48,    "67774", "channel-2, T_C = 25 C; r_DS(on) 0.0037 at 10 V / 0.0050 at 4.5 V (grid `pd` 27 W is channel-1)"),
    "SQJ204EP":   (48,    "76441", "channel-2, T_C = 25 C; r_DS(on) 0.0030 at 10 V / 0.0035 at 4.5 V (grid `pd` 27 W is channel-1)"),
    "SQJ208EP":   (48,    "77836", "channel-2, T_C = 25 C; r_DS(on) 0.00390 at 10 V / 0.00480 at 4.5 V (grid `pd` 27 W is channel-1)"),
    "SQJ260EP":   (48,    "75486", "channel-2, T_C = 25 C; r_DS(on) 0.0085 at 10 V / 0.0115 at 4.5 V (grid `pd` 27 W is channel-1)"),
    "SQJ262EP":   (48,    "75504", "channel-2, T_C = 25 C; r_DS(on) 0.0155 at 10 V / 0.0200 at 4.5 V (grid `pd` 27 W is channel-1)"),
    "SQJ500AEP":  (48,    "62878", "p-channel; both channels 48 W at T_C = 25 C; r_DS(on) 0.0270 at -10 V / 0.0435 at -4.5 V"),
    "SQJ940EP":   (43,    "62767", "channel-2, T_C = 25 C; r_DS(on) 0.0064 at 10 V / 0.0076 at 4.5 V (grid `pd` 48 W is channel-1)"),
    "SQJ942EP":   (48,    "62669", "channel-2, T_C = 25 C; r_DS(on) 0.011 at 10 V / 0.013 at 4.5 V (grid `pd` 17 W is channel-1)"),
    "SQJ990EP":   (48,    "77789", "channel-2, T_C = 25 C; r_DS(on) 0.0190 at 10 V / 0.0235 at 4.5 V (grid `pd` 27 W is channel-1)"),
    "SiZ200DT":   (33,    "75033", "channel-2 (both channels 33 W), T_C = 25 C; r_DS(on) 0.0058 at 10 V / 0.0077 at 4.5 V"),
    "SiZ240DT":   (33,    "77182", "channel-2 (both channels 33 W), T_C = 25 C; r_DS(on) 0.00841 at 10 V / 0.0133 at 4.5 V"),
    "SiZ270DT":   (33,    "77670", "channel-2 (both channels 33 W), T_C = 25 C; r_DS(on) 0.0394 at 10 V / 0.0541 at 4.5 V"),
    "SiZ340DDT":  (31,    "61551", "channel-2, T_C = 25 C; r_DS(on) 0.005 at 10 V / 0.0075 at 4.5 V (grid `pd` 16.7 W is channel-1)"),
    "SiZ988DT":   (40,    "66937", "channel-2, T_C = 25 C; r_DS(on) 0.0041 at 10 V / 0.0052 at 4.5 V (grid `pd` 20.2 W is channel-1)"),
    "SiZ998DT":   (32.9,  "62979", "channel-2, T_C = 25 C; r_DS(on) 0.0028 at 10 V / 0.0038 at 4.5 V (grid `pd` 20.2 W is channel-1)"),
    "SiZ998BDT":  (32.9,  "77875", "channel-2, T_C = 25 C; r_DS(on) 0.0024 at 10 V / 0.0038 at 4.5 V (grid `pd` 20 W is channel-1)"),
    "SiZ980BDT":  (66,    "77251", "channel-2, T_C = 25 C; r_DS(on) 0.00106 at 10 V / 0.00172 at 4.5 V (grid `pd` 20 W is channel-1)"),
    "SiZF300DT":  (74,    "76288", "channel-2, T_C = 25 C; r_DS(on) 0.00184 at 10 V / 0.00257 at 4.5 V (grid `pd` 48 W is channel-1)"),
    "SiZF360DT":  (78,    "77233", "channel-2, T_C = 25 C; r_DS(on) 0.00190 at 10 V / 0.0026 at 4.5 V (grid `pd` 52 W is channel-1)"),
    "SiZF906BDT": (83,    "77619", "channel-2, T_C = 25 C; r_DS(on) 0.00068 at 10 V / 0.0013 at 4.5 V (grid `pd` 38 W is channel-1)"),
    "SiZF906DDT": (83,    "61545", "channel-2, T_C = 25 C; r_DS(on) 0.00090 at 10 V / 0.0013 at 4.5 V (grid `pd` 38 W is channel-1)"),
    "SiZF914DT":  (26.6,  "75978", "channel-1, T_C = 25 C; r_DS(on) 0.00380 at 10 V / 0.00620 at 4.5 V (grid `pd` 60 W is channel-2)"),
    "SiZF918BDT": (60,    "62448", "channel-2, T_C = 25 C; r_DS(on) 0.0014 at 10 V / 0.0023 at 4.5 V (grid `pd` 26.6 W is channel-1)"),
    "SiZF920DT":  (74,    "79595", "channel-2, T_C = 25 C; r_DS(on) 0.00105 at 10 V / 0.00145 at 4.5 V (grid `pd` 28 W is channel-1)"),
    "SiZF928DT":  (74,    "63037", "channel-2, T_C = 25 C; r_DS(on) 0.00075 at 10 V / 0.0012 at 4.5 V (grid `pd` 28 W is channel-1)"),
    # --- Complementary pairs: the import took the P-channel r_DS(on) column
    #     while the record is the N-channel (or the reverse). ------------------
    "Si4532CDY":  (2.78,  "64805", "p-channel (both channels 2.78 W), T_C = 25 C; r_DS(on) 0.089 at -10 V / 0.14 at -4.5 V"),
    "Si3585CDV":  (1.4,   "67470", "n-channel (1.4 W; the p-channel is 1.3 W), T_C = 25 C; r_DS(on) 0.058 at 4.5 V, I_D 3.9 A; stored 0.195 is the p-channel"),
    "SQ3585CEV":  (1.67,  "61721", "n-channel (both channels 1.67 W), T_C = 25 C; r_DS(on) 0.077 at +-4.5 V, I_D 3.57 A; stored 0.166 is the p-channel"),
    "SQ4532CEY":  (3.3,   "61727", "p-channel (both channels 3.3 W), T_C = 25 C; r_DS(on) 0.070 at -10 V / 0.190 at -4.5 V"),
    "Si1016CX":   (0.22,  "67535", "T_A = 25 C, the only P_D the SC89-6 datasheet publishes; n-channel r_DS(on) 0.396 at 4.5 V; stored 0.756 is the p-channel at -4.5 V"),
    "Si1539CDL":  (0.34,  "67469", "channel-2 (both channels 0.34 W), T_C = 25 C; r_DS(on) 0.890 at +-10 V / 1.700 at +-4.5 V"),
    "Si1553CDL":  (0.34,  "67693", "channel-1 (both channels 0.34 W), T_C = 25 C; r_DS(on) 0.390 at +-4.5 V; stored 0.850 is channel-2"),
    "SiA517DJ":   (6.5,   "64832", "both channels 6.5 W at T_C = 25 C; n-channel r_DS(on) 0.029 at 4.5 V; stored 0.061 is the p-channel at -4.5 V"),
    "SiA533EDJ":  (7.8,   "65706", "both channels 7.8 W at T_C = 25 C; n-channel r_DS(on) 0.034 at +-4.5 V; stored 0.059 is the p-channel"),
    "SiA537EDJ":  (7.8,   "62934", "both channels 7.8 W at T_C = 25 C; n-channel r_DS(on) 0.028 at +-4.5 V; stored 0.054 is the p-channel"),
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


def plan_record(ref, electrical, row):
    """-> (P_D in W, evidence, provenance) or None when the record is not shifted."""
    override = DATASHEET_VERIFIED.get(ref)
    if override is not None:
        watts, doc, pin = override
        url = f"https://www.vishay.com/doc?{doc}"
        return watts, (f"powerDissipation held an r_DS(on) figure; Vishay docs/{doc} "
                       f"ABSOLUTE MAXIMUM RATINGS gives P_D = {watts} W ({pin})"), {
            "source": "manufacturerDatasheet",
            "sourceName": f"Vishay datasheet docs/{doc} ABSOLUTE MAXIMUM RATINGS [ABT #494]",
            "sourceUrl": url,
            "retrievedDate": "2026-08-02",
            "fields": ["electrical.powerDissipation"]}

    if row is None:
        return None
    stored = electrical.get("powerDissipation")
    if stored is None:
        return None
    ohms = [(k, row[k]) for k in RDS_KEYS if isinstance(row.get(k), (int, float))]
    hit = next((k for k, v in ohms if stored == v), None)
    if hit is None:
        return None                      # not this import's shape -- leave it alone
    watts = row.get("pd")
    if not isinstance(watts, (int, float)):
        return None                      # row publishes no P_D; DATASHEET_VERIFIED covers these
    return watts, (f"powerDissipation {stored} is this row's {hit} (r_DS(on) in ohms, "
                   f"Vishay field P7016); the row's P_D (P7008) is {watts} W"), GRID_PROV


class Abort(Exception):
    pass


def schema_errors(validator, component):
    return sorted(e.message for e in validator.iter_errors(component))


def repair_file(path, idx, validator, gate, audit, seen_override, quarantined):
    """Rewrite `path` in place. Returns the temp file to swap in, or raises Abort."""
    tmp = path.with_suffix(".ndjson.tmp")
    # Other processes append to these files. Read only as far as the file reached
    # when we opened it, then splice whatever arrived while we ran back onto the
    # tail -- so a concurrent append is never dropped by the replace.
    size_at_open = path.stat().st_size
    with open(path, "rb") as src, open(tmp, "wb") as out:
        while src.tell() < size_at_open:
            raw = src.readline()
            if not raw:
                break
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
                    plan = plan_record(ref, el, vendor_row(idx, ref))
                    if plan is not None:
                        watts, why, prov = plan
                        if ref in DATASHEET_VERIFIED:
                            seen_override.add(ref)
                        was = el.get("powerDissipation")
                        if watts <= 0:
                            raise Abort(f"{ref}: refusing to write P_D = {watts} W")
                        if was != watts:
                            before = schema_errors(validator, rec["semiconductor"]["mosfet"])
                            el["powerDissipation"] = watts
                            trail = sheet.get("provenance") or []
                            if prov not in trail:
                                sheet["provenance"] = trail + [prov]
                            after = schema_errors(validator, rec["semiconductor"]["mosfet"])
                            # Live catalogue: must validate. Quarantined-incomplete: must
                            # not gain an error it did not already have.
                            if after and (not quarantined or after != before):
                                raise Abort(f"{ref}: {after[0][:160]}")
                            ok, blocked = gate.check(rec["semiconductor"]["mosfet"])
                            if not ok:
                                # Leave the line byte-for-byte alone rather than write a
                                # record Blade Runner calls physically impossible.
                                audit["unresolved"].append(
                                    {"file": path.name, "reference": ref,
                                     "reason": "blocked by Blade Runner", "finding": blocked,
                                     "wouldHaveBeen": {"powerDissipation": watts}})
                            else:
                                out.write(json.dumps(rec, ensure_ascii=False).encode() + b"\n")
                                wrote = True
                                audit["repaired"].append(
                                    {"file": path.name, "reference": ref,
                                     "changed": {"powerDissipation": {"was": was, "now": watts}},
                                     "evidence": why,
                                     **({"stillQuarantined": before[0][:120]}
                                        if quarantined and before else {})})
            if not wrote:
                out.write(raw)
        src.seek(size_at_open)
        shutil.copyfileobj(src, out)
        out.flush()
        os.fsync(out.fileno())
    return tmp


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    idx = vendor_index(json.loads(VENDOR.read_text())["series"])
    validator = load_validator()
    gate = BladeGate(("semiconductor", "mosfet"))

    audit = {"ticket": "ABT #494", "repaired": [], "unresolved": []}
    seen_override = set()
    staged = []
    try:
        for path, quarantined in ((DATA, False), (QUARANTINE, True)):
            staged.append((path, repair_file(path, idx, validator, gate, audit,
                                             seen_override, quarantined)))
        missing = sorted(set(DATASHEET_VERIFIED) - seen_override)
        if missing:
            raise Abort(f"datasheet-verified parts never seen in {DATA.name}: {missing}")
    except Abort as e:
        for _, tmp in staged:
            tmp.unlink(missing_ok=True)
        print(f"ABORT {e}")
        return 1

    for path, _ in staged:
        n = sum(1 for r in audit["repaired"] if r["file"] == path.name)
        print(f"{path.name}: repaired {n} records")
    print(f"{len(audit['unresolved'])} left unresolved")
    print(gate.summary())
    if args.dry_run:
        for _, tmp in staged:
            tmp.unlink(missing_ok=True)
        print("--dry-run: nothing replaced")
        return 0
    for path, tmp in staged:
        os.replace(tmp, path)
        print(f"replaced {path}")
    AUDIT.write_text(json.dumps(audit, indent=1))
    print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
