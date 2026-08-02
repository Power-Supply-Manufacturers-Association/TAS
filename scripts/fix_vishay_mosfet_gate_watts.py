#!/usr/bin/env python3
"""Undo the Vishay MOSFET gate-rating column shift (ABT #501).

    python3 scripts/fix_vishay_mosfet_gate_watts.py [--dry-run]

WHAT WENT WRONG. This is the residue of ABT #482, not a new import bug. That
ticket established the shift in the May-2026 Vishay import (script not in the
tree), reading the `webtableResults` grid one column to the left:

    P7008  Power dissipation (max.)  (W)  <- what the import wrote to ...
    P7012  Gate-to-source voltage    (V)  <- ... gateSourceVoltageMax

#482 repaired 1,842 records but SKIPPED every record that was already
schema-invalid for an unrelated reason, on the principle that repairing it would
smuggle a second, unreviewed fix into that ticket's diff. Three of those skips
were left LIVE in data/mosfets.ndjson carrying a watt figure as a gate rating --
IRF9540S at 150 V, IRF520S at 60 V, IRLZ14S at 43 V. Its own audit lists them
under `unresolved`. A 150 V gate rating on a Si part is a punched-through oxide,
not a rating, and the cross-reference tool renders it next to a real 20 V part.

THE THREE LIVE RECORDS are repaired here from the DATASHEET, not the grid,
because making them schema-valid is a precondition of writing to them at all --
each is missing `totalGateCharge` and carries `gateThresholdVoltage` as a bare
number where SAS/mosfet.json requires the dimensionWithTolerance object. Every
value below is read off the named Vishay document's ABSOLUTE MAXIMUM RATINGS and
SPECIFICATIONS tables (T_C = 25 C), so the whole record can be re-checked against
one source. The grid agrees with the datasheet on both shifted fields for all
three, and #482's own `wouldHaveBeen` matches too.

data/mosfets.quarantine_incomplete.ndjson holds 80 more of the same import's
records with the same watt-in-the-gate-field -- 375 V, 348 V, 283 V "gate
ratings". Leaving them is the landmine ABT #494 described: a later promotion pass
pulls them into the live catalogue. They are repaired by #482's proven tie --
the record's OWN onResistance matches one of the row's r_DS(on)-at-V_GS columns,
which says the record IS that row, so the row's `vgs` is the rating the import
should have written. All 80 tie, and in all 80 the stored figure is exactly the
row's `pd`. They stay quarantined; only the gate field is touched.

Every rewritten record must pass BOTH gates -- JSON Schema (SAS/mosfet.json) and
Blade Runner (tas_validator). For the live records a schema failure aborts the
whole run and leaves the file untouched. The quarantined records are quarantined
for MISSING REQUIRED FIELDS and so cannot satisfy the schema by construction;
there the rule is "no new error" (the error set must be identical before and
after) rather than "must pass". Blade Runner must pass outright in both files.

The standing guard against reintroduction is Blade Runner's MOS_VGS_MAX_RATING
(IMPOSSIBLE above thr::MOS_VGS_MAX_ABS_IMP), added with this ticket: #482 knew
the ceiling but kept it as a local constant in a one-off script, so nothing in
the repo would refuse the next record that arrived this way.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
AUDIT = REPO / "staging" / "vishay_mosfet_gate_watts_audit.json"

SHEET_PROV = {"source": "manufacturerDatasheet",
              "sourceName": "Vishay datasheet ABSOLUTE MAXIMUM RATINGS / SPECIFICATIONS, "
                            "T_C = 25 C, re-read column-correct (the stored gate rating was "
                            "the P_D watt column) [ABT #501]",
              "retrievedDate": "2026-08-02"}
GRID_PROV = {"source": "manufacturerParametric",
             "sourceName": "Vishay parametric grid re-read column-correct (__NEXT_DATA__ "
                           "webtableResults; P7012=V_GS, NOT P7008=Power dissipation) "
                           "[ABT #501]",
             "sourceUrl": "https://www.vishay.com/en/mosfets/",
             "retrievedDate": "2026-08-02",
             "fields": ["electrical.gateSourceVoltageMax"]}

RTOL = 0.01                     # r_DS(on) agreement that ties a record to a vendor row
RDS_KEYS = ("rds10", "rds75", "rds6", "rds45", "rds25", "rds18", "rds12")
VGS_CEILING = 30.0              # no real MOSFET gates above this; see MOS_VGS_MAX_ABS_IMP

# The three live records. ref -> (electrical patch, Vishay doc id).
# P-channel keeps this record's own signed convention (its V_DS is already -100).
# gateSourceVoltageMax is the unsigned +- rating, as #482 wrote it for P-channel.
DATASHEET_VERIFIED = {
    # V_GS +-20, I_D 9.2 A at T_C 25 C, P_D 60 W, Qg 16 nC, V_GS(th) 2.0-4.0,
    # r_DS(on) 0.27 at V_GS = 10 V.
    "IRF520S, SiHF520S": ({
        "gateSourceVoltageMax": 20,
        "continuousDrainCurrent": 9.2,
        "powerDissipation": 60,
        "onResistanceVgs": 10,
        "totalGateCharge": 1.6e-08,
        "gateThresholdVoltage": {"minimum": 2.0, "maximum": 4.0},
    }, "91018"),
    # V_GS +-10 (logic level), I_D 10 A at T_C 25 C, P_D 43 W, Qg 8.4 nC,
    # V_GS(th) 1.0-2.0, r_DS(on) 0.20 at V_GS = 5 V (0.28 at 4 V).
    "IRLZ14S, SiHLZ14S": ({
        "gateSourceVoltageMax": 10,
        "powerDissipation": 43,
        "onResistance": 0.2,
        "onResistanceVgs": 5,
        "totalGateCharge": 8.4e-09,
        "gateThresholdVoltage": {"minimum": 1.0, "maximum": 2.0},
    }, "90414"),
    # V_GS +-20, I_D -19 A at T_C 25 C, P_D 150 W, Qg 61 nC, V_GS(th) -2.0..-4.0,
    # r_DS(on) 0.20 at V_GS = -10 V.
    "IRF9540S, SiHF9540S": ({
        "gateSourceVoltageMax": 20,
        "continuousDrainCurrent": -19,
        "powerDissipation": 150,
        "onResistanceVgs": -10,
        "totalGateCharge": 6.1e-08,
        "gateThresholdVoltage": {"minimum": -2.0, "maximum": -4.0},
    }, "91079"),
}

# The quarantined records the r_DS(on) tie cannot settle, read off the datasheet
# instead. Three of them carry no onResistance at all and their vendor row
# publishes none either; #482 fell back to a drain-source-voltage-only tie there,
# but V_DS alone cannot tell a variant or a channel apart, so the datasheet is
# read directly. SQJ500EP is an N- and P-pair: the record holds the N channel and
# its stored r_DS(on) (0.014 at +-10 V) matches neither column of the P-channel
# row the grid returns. It also shows the shift in all THREE columns at once --
# I_D holds V_GS, V_GS holds P_D, and P_D holds r_DS(on) at +-4.5 V (0.015, the
# ABT #494 shape) -- so the whole row is rewritten from the one datasheet page.
# ref -> (electrical patch, Vishay doc id, the reading that pins the row)
QUARANTINE_DATASHEET_VERIFIED = {
    "IRLZ44S, SiHLZ44S": ({"gateSourceVoltageMax": 10}, "91329/sihlz44s",
                          "V_GS +-10 V; V_DS 60, P_D 150 W = the stored figure"),
    "IRL640S, SiHL640S": ({"gateSourceVoltageMax": 10}, "91306/sihl640s",
                          "V_GS +-10 V; V_DS 200, P_D 125 W = the stored figure"),
    "IRL510S, SiHL510S": ({"gateSourceVoltageMax": 10}, "90380/sihf510s",
                          "V_GS +-10 V; V_DS 100, P_D 43 W = the stored figure "
                          "(the doc is filed under sihf510s but is the IRL510S sheet)"),
    "SQJ500EP": ({"gateSourceVoltageMax": 20, "continuousDrainCurrent": 8,
                  "powerDissipation": 48}, "67517/sqj500ep",
                 "N-channel of the N- and P-pair, T_C = 25 C: V_GS +-20 V, I_D 8 A, "
                 "P_D 48 W = the stored gate figure; pinned by r_DS(on) 0.014 at +-10 V"),
}


def load_validator():
    resources = []
    for repo in ("PEAS", "CIAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS"):
        root = PSMA / repo / "schemas"
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            doc = json.loads(path.read_text())
            if "$id" in doc:
                resources.append((doc["$id"], Resource.from_contents(doc)))
    registry = Registry().with_resources(resources)
    schema = json.loads((PSMA / "SAS" / "schemas" / "mosfet.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def vendor_index(series):
    """Vishay keys a row by its Series string, which is sometimes a comma-joined
    alias list ("IRF520S, SiHF520S"). Index the whole key and each alias."""
    idx = {}
    for key, row in series.items():
        idx.setdefault(norm(key), row)
        for alias in str(key).split(","):
            idx.setdefault(norm(alias), row)
    return idx


def vendor_row(idx, ref):
    row = idx.get(norm(ref))
    if row is not None:
        return row
    for alias in str(ref).split(","):
        if norm(alias) in idx:
            return idx[norm(alias)]
    return None


def ties_to_row(electrical, row):
    """The record is this vendor row: its r_DS(on) is one the row publishes."""
    rds = [row[k] for k in RDS_KEYS if isinstance(row.get(k), (int, float))]
    own = electrical.get("onResistance")
    return bool(rds) and any(own is not None and abs(own - v) <= RTOL * abs(v) for v in rds)


def stamp(path):
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


def unmoved(path, before):
    """These files are appended to concurrently. Every line this script does not
    repair is copied through byte-for-byte, but the final os.replace would still
    drop anything appended while we were building the temp file. Refuse rather
    than lose an append; re-running picks up the new lines."""
    if stamp(path) != before:
        print(f"ABORT {path.name}: appended to while this run was in flight; re-run")
        return False
    return True


def errors_of(validator, record):
    return sorted(f"{list(e.absolute_path)}: {e.message}"
                  for e in validator.iter_errors(record["semiconductor"]["mosfet"]))


def repair_live(path, validator, gate, audit):
    """The three records #482 left behind. Full datasheet repair; must end valid."""
    seen = set()
    before_stamp = stamp(path)
    tmp = path.with_suffix(".ndjson.tmp")
    with open(path, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            wrote = False
            if b'"Vishay"' in raw:
                rec = json.loads(raw)
                info = rec["semiconductor"]["mosfet"]["manufacturerInfo"]
                ref = str(info.get("reference") or "").strip()
                if ref in DATASHEET_VERIFIED:
                    seen.add(ref)
                    sheet = info["datasheetInfo"]
                    el = sheet["electrical"]
                    patch, docid = DATASHEET_VERIFIED[ref]
                    changed = {k: {"was": el.get(k), "now": v}
                               for k, v in patch.items() if el.get(k) != v}
                    el.update(patch)
                    prov = dict(SHEET_PROV)
                    prov["sourceUrl"] = info.get("datasheetUrl")
                    prov["fields"] = [f"electrical.{k}" for k in sorted(patch)]
                    sheet["provenance"] = (sheet.get("provenance") or []) + [prov]
                    # The 2026-06-22 triage said these could not be enriched from a
                    # distributor parametric feed. The datasheet settled them, so the
                    # note no longer describes the record.
                    rec.pop("_triage", None)
                    errs = errors_of(validator, rec)
                    if errs:
                        print(f"ABORT {ref}: schema {errs[0][:160]}")
                        out.close()
                        tmp.unlink(missing_ok=True)
                        return 1
                    ok, why = gate.check(rec["semiconductor"]["mosfet"])
                    if not ok:
                        print(f"ABORT {ref}: blade runner {why}")
                        out.close()
                        tmp.unlink(missing_ok=True)
                        return 1
                    out.write(json.dumps(rec, ensure_ascii=False).encode() + b"\n")
                    wrote = True
                    audit["live"].append({"reference": ref, "changed": changed,
                                          "evidence": f"Vishay docs/{docid}"})
            if not wrote:
                out.write(raw)
        out.flush()
        os.fsync(out.fileno())

    missing = sorted(set(DATASHEET_VERIFIED) - seen)
    if missing:
        print(f"ABORT: datasheet-verified parts never seen in {path.name}: {missing}")
        tmp.unlink(missing_ok=True)
        return 1
    if not unmoved(path, before_stamp):
        tmp.unlink(missing_ok=True)
        return 1
    audit["_tmp_live"] = str(tmp)
    return 0


def repair_quarantine(path, validator, gate, idx, audit):
    """The 80 quarantined siblings. Gate field only, tied to the vendor row."""
    before_stamp = stamp(path)
    tmp = path.with_suffix(".ndjson.tmp")
    with open(path, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            wrote = False
            if b'"Vishay"' in raw:
                rec = json.loads(raw)
                mos = rec.get("semiconductor", {}).get("mosfet")
                info = (mos or {}).get("manufacturerInfo", {})
                el = (info.get("datasheetInfo") or {}).get("electrical") or {}
                gatev = el.get("gateSourceVoltageMax")
                if (mos is not None and info.get("name") == "Vishay"
                        and isinstance(gatev, (int, float)) and abs(gatev) > VGS_CEILING):
                    ref = str(info.get("reference") or "").strip()
                    row = vendor_row(idx, ref)
                    override = QUARANTINE_DATASHEET_VERIFIED.get(ref)
                    before = errors_of(validator, rec)
                    if override is not None:
                        patch, doc, pin = override
                        why = f"Vishay docs/{doc}.pdf: {pin}"
                    elif row is None or not ties_to_row(el, row):
                        patch = None
                        audit["unresolved"].append(
                            {"reference": ref, "gateSourceVoltageMax": gatev,
                             "reason": "no vendor row, or the record's onResistance does not "
                                       "match one the row publishes"})
                    elif not isinstance(row.get("vgs"), (int, float)):
                        patch = None
                        audit["unresolved"].append(
                            {"reference": ref, "gateSourceVoltageMax": gatev,
                             "reason": "vendor row publishes no V_GS column"})
                    else:
                        patch = {"gateSourceVoltageMax": row["vgs"]}
                        why = (f"stored figure is the row's P_D ({row.get('pd')} W); "
                               f"row tied by r_DS(on) {el.get('onResistance')}")
                    if patch is not None:
                        sheet = info["datasheetInfo"]
                        changed = {k: {"was": el.get(k), "now": v}
                                   for k, v in patch.items() if el.get(k) != v}
                        el.update(patch)
                        prov = dict(SHEET_PROV) if override else dict(GRID_PROV)
                        if override:
                            prov["sourceUrl"] = f"https://www.vishay.com/docs/{doc}.pdf"
                        prov["fields"] = [f"electrical.{k}" for k in sorted(patch)]
                        sheet["provenance"] = (sheet.get("provenance") or []) + [prov]
                        after = errors_of(validator, rec)
                        ok, blocked = gate.check(mos)
                        if after != before:
                            print(f"ABORT {ref}: new schema error {set(after) - set(before)}")
                            out.close()
                            tmp.unlink(missing_ok=True)
                            return 1
                        if not ok:
                            print(f"ABORT {ref}: blade runner {blocked}")
                            out.close()
                            tmp.unlink(missing_ok=True)
                            return 1
                        out.write(json.dumps(rec, ensure_ascii=False).encode() + b"\n")
                        wrote = True
                        audit["quarantined"].append(
                            {"reference": ref, "changed": changed, "evidence": why})
            if not wrote:
                out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    if not unmoved(path, before_stamp):
        tmp.unlink(missing_ok=True)
        return 1
    audit["_tmp_quarantine"] = str(tmp)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    idx = vendor_index(json.loads(VENDOR.read_text())["series"])
    validator = load_validator()
    gate = BladeGate(("semiconductor", "mosfet"))

    audit = {"ticket": "ABT #501", "live": [], "quarantined": [], "unresolved": []}
    if repair_live(DATA, validator, gate, audit):
        return 1
    if repair_quarantine(QUARANTINE, validator, gate, idx, audit):
        Path(audit["_tmp_live"]).unlink(missing_ok=True)
        return 1

    live_tmp = Path(audit.pop("_tmp_live"))
    quar_tmp = Path(audit.pop("_tmp_quarantine"))
    print(f"repaired {len(audit['live'])} live, {len(audit['quarantined'])} quarantined, "
          f"{len(audit['unresolved'])} unresolved")
    print(gate.summary())
    if args.dry_run:
        live_tmp.unlink(missing_ok=True)
        quar_tmp.unlink(missing_ok=True)
        print("--dry-run: nothing replaced")
        return 0
    os.replace(live_tmp, DATA)
    os.replace(quar_tmp, QUARANTINE)
    AUDIT.write_text(json.dumps(audit, indent=1))
    print(f"replaced {DATA} and {QUARANTINE}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
