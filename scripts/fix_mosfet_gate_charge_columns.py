#!/usr/bin/env python3
"""Repair the MOSFET records whose totalGateCharge is another column (ABT #512).

    python3 scripts/fix_mosfet_gate_charge_columns.py [--dry-run]

WHAT WENT WRONG. Qarlos found SiZF680LDT carrying totalGateCharge = 2 nC beside
onResistance = 5.5 mOhm, drainSourceVoltage = 80 and continuousDrainCurrent = 72.
Vishay's own parametric grid publishes 55 nC for that row. It is the same family
of defect as ABT #482 (drain current / V_GS shifted) and #494 (r_DS(on) in the
watt field): a never-committed May-2026 importer reading a vendor grid by column
position, plus -- new here -- vendor exports whose column HEADINGS lie.

The ticket's population query is

    totalGateCharge < 5 nC AND onResistance <= 10 mOhm AND drainSourceVoltage >= 30 V

which is 42 records over five vendors, and they are not one defect but three:

  * the charge field holds another quantity (Vishay 11, Infineon 2, EPC 1, TI 5);
  * the RESISTANCE field is wrong and drags an honest Q_g into the net -- onsemi's
    export writes multi-channel r_DS(on) as the string "Q1=Q2=95", and
    onsemi_csv_import.py's `[-+]?\\d*\\.?\\d+` regex takes the "1" out of "Q1"
    and stores 1 mOhm for 95 mOhm (9 records, fixed in the importer too);
  * the record describes a different device than the part number it carries
    (EPC2115 is a dual 150 V gate-driver IC, not a 40 V discrete) or the vendor
    publishes no total gate charge at all for it (CSD87588N is a power block).

EVERY REPAIR COMES FROM THE SOURCE THE RECORD ALREADY CITES, re-read
column-correct; nothing here is inferred from the die:

  Vishay   staging/abt512_vishay_mosfet_grid.json   P7023/P7022/P7024 (Q_g at
           10 V, Q_gs, Q_gd). Duals publish one row per CH_ID, so the row is tied
           to the record by its OWN onResistance matching that row's r_DS(on)
           column -- #482's proven tie.
  TI       staging/abt512_ti_mosfet_parametric.json  QGtyp/QGStyp/QGDtyp
           (parameter ids 1730/1729/1728), already in coulombs.
  EPC      staging/abt512_epc_gan_selector.json      QG/QGS/QGD/QOSS typ columns.
  Infineon Downloads/MOSFET Finder.xlsx              "QG (typ @10V)", else
           "QG (typ @4.5V)" -- the finder carries no 10 V figure for OptiMOS 5,
           and SAS's totalGateCharge has no V_GS qualifier, so the drive
           condition is recorded in the provenance entry instead.
  onsemi   Downloads/parametrics*.csv                "RDS(on) Max @ VGS = 10 V",
           parsed channel-aware.

A VALUE IS ONLY WRITTEN IF THE SOURCE CONTRADICTS ITSELF NOWHERE. onsemi's
"Qg Typ @ VGS = 10 V (nC)" column is NOT total gate charge on every row -- for
NTTFS4C05NTAG it reads 3 nC beside a Q_gd of 5.5 nC and a Q_g at 4.5 V of 8.4 nC,
i.e. it is the gate-SOURCE charge under a total-gate-charge heading. Two checks
against the row's own neighbours decide it: Q_gd < Q_g, and Q_g >= 0.4*C_iss*10 V
(Q_g = integral of C_iss dV_GS, and C_iss is quoted where it is near its minimum,
so 0.4 leaves a 2.5x allowance). A row that fails either gives no trustworthy
gate charge, and the record is QUARANTINED rather than patched from a guess.

Quarantined records keep both the stored value and the vendor's, so they can be
re-sourced from a datasheet later. They go to
data/mosfets.quarantine_gate_charge_column.ndjson.

Every rewritten record must pass BOTH gates -- JSON Schema (SAS/mosfet.json) and
Blade Runner (tas_validator). A schema failure aborts the whole run and leaves
the file untouched.

The standing guard against reintroduction is Blade Runner's MOS_QG_VS_RON
(IMPOSSIBLE below thr::MOS_QG_RON_FOM_{SI,GAN}_IMP), added with this ticket:
r_DS(on)*Q_g is a technology constant independent of die area, so a charge field
holding something else falls orders of magnitude below it whatever the part.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
from pathlib import Path

import openpyxl
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blade_gate import BladeGate  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PSMA = REPO.parent
DATA = REPO / "data" / "mosfets.ndjson"
QUARANTINE = REPO / "data" / "mosfets.quarantine_gate_charge_column.ndjson"
AUDIT = REPO / "staging" / "abt512_gate_charge_column_audit.json"

VISHAY_GRID = REPO / "staging" / "abt512_vishay_mosfet_grid.json"
TI_GRID = REPO / "staging" / "abt512_ti_mosfet_parametric.json"
EPC_GRID = REPO / "staging" / "abt512_epc_gan_selector.json"
INFINEON_XLSX = "/mnt/c/Users/Alfonso/Downloads/MOSFET Finder.xlsx"
ONSEMI_CSV_GLOB = "/mnt/c/Users/Alfonso/Downloads/parametrics*.csv"

TODAY = "2026-08-02"
RTOL = 0.01          # r_DS(on) agreement that ties a record to a vendor row
DIFF_TOL = 0.02      # a stored value this close to the vendor's is left alone
CISS_FLOOR = 0.4     # Q_g >= CISS_FLOOR * C_iss * 10 V for the onsemi Q_g column

# The ticket's population predicate, verbatim.
POP_QG, POP_RON, POP_VDS = 5e-9, 0.01, 30.0


def in_population(el):
    qg, ron, vds = (el.get(k) for k in
                    ("totalGateCharge", "onResistance", "drainSourceVoltage"))
    return (isinstance(qg, (int, float)) and isinstance(ron, (int, float))
            and isinstance(vds, (int, float))
            and qg < POP_QG and ron <= POP_RON and vds >= POP_VDS)


def num(s):
    if s is None:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", str(s).replace(",", ""))
    return float(m.group()) if m else None


def close(a, b, tol=DIFF_TOL):
    return a is not None and b is not None and b != 0 and abs(a - b) / abs(b) <= tol


# ---- vendor sources ---------------------------------------------------------

def load_vishay():
    """Series -> [row]. Duals publish one row per channel; the caller ties on r_DS(on)."""
    d = json.loads(VISHAY_GRID.read_text(encoding="utf-8"))
    by = {}
    for r in d["rows"]:
        by.setdefault(str(r.get("P1001") or "").strip().upper(), []).append(r)
    return by


def vishay_charges(rows, ron):
    """The row whose OWN r_DS(on) is the record's, and its charge triple [C]."""
    for r in rows:
        for key in ("P7013", "P7016", "P7508", "P7014", "P7020", "P7021", "P7436"):
            v = r.get(key)
            if isinstance(v, (int, float)) and close(ron, v, RTOL):
                qg = r.get("P7023")
                if not isinstance(qg, (int, float)):
                    return None, f"grid row has no Q_g (P7023) for r_DS(on)={v}"
                out = {"totalGateCharge": round(qg * 1e-9, 15)}
                for src, dst in (("P7022", "gateSourceCharge"), ("P7024", "gateDrainCharge")):
                    if isinstance(r.get(src), (int, float)):
                        out[dst] = round(r[src] * 1e-9, 15)
                return out, f"grid row r_DS(on)={v} (CH_ID={r.get('P7010')})"
    return None, "no grid row whose r_DS(on) matches the record"


def load_ti():
    d = json.loads(TI_GRID.read_text(encoding="utf-8"))
    by = {}
    for r in d["results"]:
        params = {}
        for p in r["paramList"]:
            base = p["value"].get("base")
            if base:
                params[p["id"]] = base[0]
        by[str(r["genericPartNumber"]).strip().upper()] = params
    return by


def load_epc():
    d = json.loads(EPC_GRID.read_text(encoding="utf-8"))
    hdr = [h.replace("\n", " ") for h in d["header"]]
    idx = {}
    for want, label in (("qg", "QG typ"), ("qgs", "QGS typ"), ("qgd", "QGD typ"),
                        ("qoss", "QOSS typ")):
        for i, h in enumerate(hdr):
            if h.startswith(label):
                idx[want] = i
                break
    by = {}
    for row in d["rows"]:
        by[str(row[0]).strip().upper()] = {k: num(row[i]) for k, i in idx.items()}
    return by


def load_infineon():
    wb = openpyxl.load_workbook(INFINEON_XLSX, read_only=True)
    it = wb.active.iter_rows(values_only=True)
    hdr = [str(c) for c in next(it)]
    H = {h: i for i, h in enumerate(hdr)}
    by = {}
    for r in it:
        pn = re.sub(r"\s+", " ", str(r[0])).strip() if r[0] else None
        if pn:
            by[pn.upper()] = {h: r[i] for h, i in H.items() if i < len(r)}
    return by


def charge_c(s):
    """A finder cell like '45 nC' -> coulombs. No unit, no value: the cell is not
    a charge and must not be guessed at."""
    v = num(s)
    if v is None:
        return None
    u = str(s).lower()
    if "nc" in u:
        return round(v * 1e-9, 15)
    if "pc" in u:
        return round(v * 1e-12, 15)
    if "uc" in u or "µc" in u:
        return round(v * 1e-6, 15)
    return None


def load_onsemi():
    by = {}
    for path in sorted(glob.glob(ONSEMI_CSV_GLOB)):
        with open(path, encoding="utf-8-sig") as fh:
            rd = csv.reader(fh)
            hdr = next(rd)
            for row in rd:
                if not row or not row[0].strip():
                    continue
                pn = row[0].strip().rstrip(",").strip().upper()
                by.setdefault(pn, {h: (row[i] if i < len(row) else None)
                                   for i, h in enumerate(hdr)})
    return by


def onsemi_cell(row, frag):
    for h, v in row.items():
        if frag in re.sub(r"\s+", " ", h).lower():
            v = (str(v).strip().rstrip(",").strip() if v is not None else "")
            return None if v in ("", "-", "~NA~", "NA", "N/A") else v
    return None


def onsemi_ohms(cell):
    """'95' / 'Q1=Q2=95' / 'Q1: 62.0, Q2: 62.0' -> ohms. Channel labels are
    stripped BEFORE the numbers are read (the importer's regex used to return the
    '1' of 'Q1'); channels that disagree give no single r_DS(on) for the record."""
    if cell is None:
        return None
    vals = re.findall(r"[-+]?\d*\.?\d+", re.sub(r"[Qq]\d+\s*[:=]", " ", str(cell)))
    if not vals:
        return None
    nums = {float(v) for v in vals}
    if len(nums) != 1:
        return None
    return nums.pop() * 1e-3


def onsemi_gate_charge(row):
    """The row's own Q_g in coulombs, or (None, why) when its "Qg Typ @ VGS = 10 V"
    column is contradicted by its neighbours and so is not a total gate charge."""
    qg = num(onsemi_cell(row, "qg typ @ vgs = 10"))
    if qg is None:
        return None, "export has no Qg column for this row"
    qgd = num(onsemi_cell(row, "qgd typ"))
    if qgd is not None and qgd >= qg:
        return None, f"export's Qg column ({qg} nC) is <= its own Qgd ({qgd} nC)"
    ciss = num(onsemi_cell(row, "ciss typ"))
    if ciss is not None and qg * 1e-9 < CISS_FLOOR * ciss * 1e-12 * 10.0:
        return None, (f"export's Qg column ({qg} nC) is below {CISS_FLOOR}*Ciss*10 V "
                      f"(Ciss={ciss} pF)")
    return round(qg * 1e-9, 15), None


# ---- gates ------------------------------------------------------------------

def build_validator():
    reg = Registry()
    for repo in ("PEAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS", "CIAS", "TAS"):
        root = PSMA / repo / "schemas"
        for p in root.rglob("*.json"):
            doc = json.loads(p.read_text(encoding="utf-8"))
            if "$id" in doc:
                reg = reg.with_resource(doc["$id"], Resource.from_contents(doc))
    schema = json.loads((PSMA / "SAS" / "schemas" / "mosfet.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=reg)


# ---- main -------------------------------------------------------------------

PROV = {
    "Vishay": ("manufacturerParametric",
               "Vishay parametric grid re-read column-correct (__NEXT_DATA__ "
               "webtableResults; P7023=Qg at 10 V, P7022=Qgs, P7024=Qgd) [ABT #512]",
               "https://www.vishay.com/en/mosfets/"),
    "Texas Instruments": ("manufacturerParametric",
                          "TI selectionmodel parametric API re-read column-correct "
                          "(result-list destinationId=1647; QGtyp/QGStyp/QGDtyp) [ABT #512]",
                          "https://www.ti.com/product-category/power-management/mosfets/products.html"),
    "EPC": ("manufacturerParametric",
            "EPC eGaN FET selector guide re-read column-correct "
            "(QG/QGS/QGD/QOSS typ) [ABT #512]",
            "https://epc-co.com/epc/products/gan-fets-and-ics"),
    "Infineon": ("manufacturerParametric",
                 "Infineon MOSFET Finder (xlsx export), QG typ at V_GS = 4.5 V -- the "
                 "finder publishes no 10 V figure for this part [ABT #512]", None),
    "onsemi": ("manufacturerParametric",
               "onsemi parametric export (CSV) re-read channel-correct: "
               "\"RDS(on) Max @ VGS = 10 V\" is published as \"Q1=Q2=<mOhm>\" for "
               "multi-channel parts [ABT #512]", None),
}


def stamp(path):
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


def stamped(rec):
    """Has this ticket already written to the record? Lets a second run find the
    records whose derived figureOfMerit the first run left behind."""
    prov = rec["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"].get("provenance") or []
    return any("[ABT #512]" in str(p.get("sourceName", "")) for p in prov)


def fom_patch(el, patch):
    """SAS defines figureOfMerit as R_DS(on) x Q_g. A repair that moves either
    factor leaves the field stating a product of two numbers the record no longer
    holds -- on EPC2071 it would have kept 3.52 pOhm*C, the very value the new
    Blade Runner check calls impossible, inside a record whose factors now give
    39.6. Restated, never left stale; 5% absorbs the vendors' own rounding."""
    if "figureOfMerit" not in el:
        return {}
    ron = patch.get("onResistance", el.get("onResistance"))
    qg = patch.get("totalGateCharge", el.get("totalGateCharge"))
    if not (isinstance(ron, (int, float)) and isinstance(qg, (int, float))
            and ron > 0 and qg > 0):
        return {}
    return {} if close(el["figureOfMerit"], ron * qg, 0.05) else {"figureOfMerit": ron * qg}


DERIVED_PROV = {"source": "derived",
                "derivation": "electrical.figureOfMerit = electrical.onResistance * "
                              "electrical.totalGateCharge, restated after the "
                              "column-correct re-read [ABT #512]",
                "retrievedDate": TODAY, "fields": ["electrical.figureOfMerit"]}


def decide(rec, src):
    """(patch, quarantineWhy, note) for one population record. `patch` and `why`
    are mutually exclusive; an empty patch with no `why` means the vendor source
    confirms the record as stored."""
    mi = rec["semiconductor"]["mosfet"]["manufacturerInfo"]
    el = mi["datasheetInfo"]["electrical"]
    vendor = mi.get("name")
    key = str(mi.get("reference") or "").strip().upper()

    if vendor == "Vishay":
        rows = src["vishay"].get(key)
        if not rows:
            return {}, "part absent from the Vishay parametric grid", None
        patch, note = vishay_charges(rows, el["onResistance"])
        return (patch, None, note) if patch else ({}, note, None)

    if vendor == "Texas Instruments":
        p = src["ti"].get(key)
        if not p:
            return {}, "part absent from the TI parametric API", None
        if 1730 not in p:
            return {}, "TI publishes no total gate charge for this part", None
        patch = {"totalGateCharge": float(p[1730])}
        for pid, k in ((1729, "gateSourceCharge"), (1728, "gateDrainCharge")):
            if pid in p:
                patch[k] = float(p[pid])
        # The same API row carries V_DS and r_DS(on). Repair them where the record
        # disagrees, or the part stays mis-described: CSD17507Q5A is stored as
        # 60 V / 2.5 mOhm against TI's own 30 V / 10.8 mOhm.
        if 267 in p and not close(el.get("drainSourceVoltage"), float(p[267])):
            patch["drainSourceVoltage"] = float(p[267])
        rcol = {10: 2749, 4.5: 2748, 2.5: 2747}.get(el.get("onResistanceVgs"))
        if rcol is None or rcol not in p:
            rcol = next((c for c in (2749, 2748, 2747) if c in p), None)
        if rcol is not None and not close(el.get("onResistance"), float(p[rcol])):
            patch["onResistance"] = float(p[rcol])
            patch["onResistanceVgs"] = {2749: 10, 2748: 4.5, 2747: 2.5}[rcol]
        patch = {k: v for k, v in patch.items() if el.get(k) != v}
        return patch, None, "TI parametric row"

    if vendor == "EPC":
        p = src["epc"].get(key)
        if not p or p.get("qg") is None:
            return {}, "part absent from the EPC selector guide (its datasheet URL 404s)", None
        patch = {"totalGateCharge": round(p["qg"] * 1e-9, 15)}
        for k, dst in (("qgs", "gateSourceCharge"), ("qgd", "gateDrainCharge"),
                       ("qoss", "outputCharge")):
            if p.get(k) is not None:
                patch[dst] = round(p[k] * 1e-9, 15)
        patch = {k: v for k, v in patch.items() if el.get(k) != v}
        return patch, None, "EPC selector-guide row"

    if vendor == "Infineon":
        r = src["infineon"].get(key)
        if not r:
            return {}, "part absent from the Infineon MOSFET Finder export", None
        q = charge_c(r.get("QG (typ @10V)"))
        if q is None:
            q = charge_c(r.get("QG (typ @4.5V)"))
        if q is None:
            return {}, "the finder publishes no QG cell carrying a charge unit", None
        return {"totalGateCharge": q}, None, "Infineon MOSFET Finder row"

    if vendor == "onsemi":
        r = src["onsemi"].get(key)
        if not r:
            return {}, "part absent from the onsemi parametric CSV exports", None
        qg, bad = onsemi_gate_charge(r)
        if qg is None:
            return {}, bad, None
        ron = onsemi_ohms(onsemi_cell(r, "rds(on) max @ vgs = 10"))
        if ron is None:
            return {}, "the export's r_DS(on) cell gives no single value for this part", None
        if close(el.get("onResistance"), ron):
            return {}, None, "export confirms both r_DS(on) and Q_g as stored"
        return ({"onResistance": ron, "onResistanceVgs": 10}, None,
                "onsemi export r_DS(on) parsed channel-correct")

    return {}, f"no vendor source is wired up for {vendor!r}", None


def repair(rec, patch, validator, gate):
    """Apply the patch, stamp provenance, and put the result through both gates."""
    out = json.loads(json.dumps(rec))
    mos = out["semiconductor"]["mosfet"]
    mos["manufacturerInfo"]["datasheetInfo"]["electrical"].update(patch)
    sheet = mos["manufacturerInfo"]["datasheetInfo"]
    entries = []
    vendor_fields = sorted(k for k in patch if k != "figureOfMerit")
    if vendor_fields:
        source, name, url = PROV[mos["manufacturerInfo"]["name"]]
        prov = {"source": source, "sourceName": name, "retrievedDate": TODAY,
                "fields": [f"electrical.{k}" for k in vendor_fields]}
        if url:
            prov["sourceUrl"] = url
        entries.append(prov)
    if "figureOfMerit" in patch:
        entries.append(dict(DERIVED_PROV))
    sheet["provenance"] = (sheet.get("provenance") or []) + entries
    errs = sorted(f"{list(e.absolute_path)}: {e.message}"
                  for e in validator.iter_errors(mos))
    if errs:
        return None, f"schema {errs[0][:200]}"
    ok, why = gate.check(mos)
    if not ok:
        return None, f"blade runner {why}"
    return out, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    src = {"vishay": load_vishay(), "ti": load_ti(), "epc": load_epc(),
           "infineon": load_infineon(), "onsemi": load_onsemi()}
    validator = build_validator()
    gate = BladeGate(("semiconductor", "mosfet"))

    audit = {"ticket": "ABT #512", "date": TODAY,
             "population": ("totalGateCharge < 5e-9 AND onResistance <= 0.01 AND "
                            "drainSourceVoltage >= 30"),
             "repaired": [], "quarantined": [], "unchanged": []}
    moved = []

    before = stamp(DATA)
    tmp = DATA.with_suffix(".ndjson.abt512.tmp")
    with open(DATA, "rb") as fsrc, open(tmp, "wb") as out:
        for raw in fsrc:
            if not raw.strip():
                continue
            rec = json.loads(raw)
            mos = rec.get("semiconductor", {}).get("mosfet")
            el = ((mos or {}).get("manufacturerInfo", {}).get("datasheetInfo", {})
                  .get("electrical") or {})
            pop = mos is not None and in_population(el)
            # A record this ticket already repaired is revisited only to restate the
            # figureOfMerit its new factors imply; it is out of the population by
            # then, and must not be re-judged against the vendor sources.
            if not pop and not (mos is not None and stamped(rec) and fom_patch(el, {})):
                out.write(raw)
                continue

            mi = mos["manufacturerInfo"]
            entry = {"manufacturer": mi.get("name"), "reference": mi.get("reference"),
                     "stored": {k: el.get(k) for k in
                                ("drainSourceVoltage", "onResistance", "onResistanceVgs",
                                 "totalGateCharge", "gateSourceCharge", "gateDrainCharge",
                                 "figureOfMerit")}}
            if pop:
                patch, why, note = decide(rec, src)
            else:
                patch, why, note = {}, None, "figureOfMerit restated from the repaired factors"
            if not why:
                patch = {**patch, **fom_patch(el, patch)}

            if why:
                bad = dict(rec)
                bad["quarantineReason"] = (
                    f"ABT #512: totalGateCharge {el['totalGateCharge']} C is not a gate "
                    f"charge for a {el['drainSourceVoltage']} V / {el['onResistance']} ohm "
                    f"die (r_DS(on)*Q_g = {el['onResistance'] * el['totalGateCharge']:.2e} "
                    f"ohm*C, below any real device) and no vendor source resolves it: "
                    f"{why} ({TODAY})")
                entry["quarantineReason"] = bad["quarantineReason"]
                audit["quarantined"].append(entry)
                moved.append(bad)
                continue

            if not patch:
                entry["note"] = note or "vendor source confirms the stored values"
                audit["unchanged"].append(entry)
                out.write(raw)
                continue

            fixed, blocked = repair(rec, patch, validator, gate)
            if fixed is None:
                out.close()
                tmp.unlink(missing_ok=True)
                sys.exit(f"ABORT {entry['reference']}: {blocked}")
            entry.update({"repaired": patch, "source": note})
            audit["repaired"].append(entry)
            out.write(json.dumps(fixed, ensure_ascii=False).encode() + b"\n")
        out.flush()
        os.fsync(out.fileno())

    n = len(audit["repaired"]) + len(audit["quarantined"]) + len(audit["unchanged"])
    print(f"population {n}: repaired {len(audit['repaired'])}, "
          f"quarantined {len(audit['quarantined'])}, unchanged {len(audit['unchanged'])}")
    print(gate.summary())
    for e in audit["unchanged"]:
        print(f"  UNCHANGED {e['manufacturer']}/{e['reference']}: {e['note']}")

    # The script is re-runnable (a second pass restates the derived figureOfMerit),
    # so the audit ACCUMULATES: overwriting it would erase the first pass's record
    # of what moved, which is the only place the pre-repair values survive.
    if AUDIT.exists():
        old = json.loads(AUDIT.read_text(encoding="utf-8"))
        for section in ("repaired", "quarantined", "unchanged"):
            seen = {(e["manufacturer"], e["reference"]) for e in audit[section]}
            audit[section] = [e for e in old.get(section, [])
                              if (e["manufacturer"], e["reference"]) not in seen
                              ] + audit[section]
    AUDIT.write_text(json.dumps(audit, indent=1, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    if a.dry_run:
        tmp.unlink(missing_ok=True)
        print(f"--dry-run: nothing replaced; audit -> {AUDIT}")
        return 0

    # data/*.ndjson is appended to concurrently. Every untouched line above was
    # copied through byte-for-byte, but os.replace would still drop anything that
    # arrived while the temp file was being built -- refuse rather than lose it.
    if stamp(DATA) != before:
        tmp.unlink(missing_ok=True)
        sys.exit(f"ABORT: {DATA.name} was appended to while this run was in flight; re-run")
    os.replace(tmp, DATA)
    with open(QUARANTINE, "a", encoding="utf-8") as fo:
        for rec in moved:
            fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"replaced {DATA}; appended {len(moved)} to {QUARANTINE}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
