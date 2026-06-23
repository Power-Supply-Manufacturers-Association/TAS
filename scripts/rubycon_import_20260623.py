#!/usr/bin/env python3
"""Import Rubycon ProductList CSV exports (2026-06-23) into TAS/data/capacitors.ndjson.

Two source files:
  * ProductList_20260623.csv        -> Aluminum Electrolytic + Hybrid (cylindrical)
  * ProductList_20260623 (1).csv    -> Film (PP/PET/PPS) + PMLCAP (acrylic) (box/chip)

Encoding is cp932 (Shift-JIS, Japanese OEM). All electrical values are emitted in
SI base units (F, V, A, Ohm, m, Hz). Nothing is fabricated: a field is written
only when the CSV provides it (or it is a real geometric derivation of provided
dimensions). Records that fail CAS/capacitor.json validation are written to a
quarantine file instead of the main library.

Appends only (never rewrites the live file). Dedupes against existing part
numbers already present under manufacturer "Rubycon".
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
PROTEUS = REPO.parent
DATA = REPO / "data"
CAP_FILE = DATA / "capacitors.ndjson"
QUARANTINE = DATA / "capacitors.quarantine_rubycon_20260623.ndjson"

SRC_DIR = Path("/mnt/c/Users/Alfonso/Downloads")
FILE_ELEC = SRC_DIR / "ProductList_20260623.csv"
FILE_FILM = SRC_DIR / "ProductList_20260623 (1).csv"

MANUFACTURER = "Rubycon"

# EIA tolerance letter codes -> fractional tolerance
TOL = {"J": 0.05, "K": 0.10, "M": 0.20}

# CSV category/dielectric -> CAS technology enum
TECH_ELEC = {
    "Aluminum Electrolytic Capacitors": "aluminum-electrolytic-wet",
    "Hybrid type": "aluminum-hybrid-polymer",
}
TECH_FILM_DIELECTRIC = {
    "PP": "film-polypropylene",
    "PET": "film-polyester",
    "PPS": "film-polyphenylene-sulfide",
    "Acrylic resin": "film-acrylic",
}
POLARIZED_TECH = {"aluminum-electrolytic-wet", "aluminum-hybrid-polymer"}

ASSEMBLY = {
    "RADIAL LEAD TYPE": "THT",
    "SNAP-IN TYPE": "Snap-In",
    "CHIP TYPE": "SMT",
}


# --------------------------------------------------------------------------- #
# Registry / validator (mirrors tests/test_data.py::_build_full_registry)
# --------------------------------------------------------------------------- #
def build_validator() -> Draft202012Validator:
    def _walk(d: Path):
        for p in d.rglob("*.json"):
            try:
                yield p, json.loads(p.read_text())
            except json.JSONDecodeError:
                continue

    by_id: dict[str, dict] = {}
    by_path: dict[Path, dict] = {}
    for repo_name in ("PEAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "CONAS", "AAS"):
        repo_dir = PROTEUS / repo_name / "schemas"
        if not repo_dir.is_dir():
            continue
        for path, schema in _walk(repo_dir):
            path = path.resolve()
            by_path[path] = schema
            sid = schema.get("$id")
            if sid:
                by_id[sid] = schema

    META_KEYS = {"$schema", "$id", "title", "description", "$comment"}
    for sid, schema in list(by_id.items()):
        if set(schema.keys()) - META_KEYS != {"$ref"}:
            continue
        path = next((p for p, s in by_path.items() if s is schema), None)
        if path is None:
            continue
        target = by_path.get((path.parent / schema["$ref"]).resolve())
        if target is None:
            continue
        inlined = {k: v for k, v in target.items() if k not in ("$id", "$schema")}
        inlined["$id"] = sid
        inlined["$schema"] = schema.get(
            "$schema", "https://json-schema.org/draft/2020-12/schema"
        )
        by_id[sid] = inlined

    reg = Registry().with_resources(
        [(sid, Resource(contents=s, specification=DRAFT202012)) for sid, s in by_id.items()]
    )
    schema = json.loads((PROTEUS / "CAS" / "schemas" / "capacitor.json").read_text())
    return Draft202012Validator(schema, registry=reg)


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def fnum(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


_FREQ = re.compile(r"([\d.]+)\s*(k?)Hz", re.IGNORECASE)
_TEMP = re.compile(r"(-?[\d.]+)\s*℃")  # ℃ = ℃


def parse_freq(cond: str):
    if not cond:
        return None
    m = _FREQ.search(cond)
    if not m:
        return None
    val = float(m.group(1))
    if m.group(2):  # 'k'
        val *= 1000.0
    return val


def parse_temp(cond: str):
    if not cond:
        return None
    m = _TEMP.search(cond)
    return float(m.group(1)) if m else None


def capacitance_block(cap_uf, tol_code):
    nominal = cap_uf * 1e-6
    block = {"nominal": nominal}
    tol = TOL.get(tol_code)
    if tol is not None:
        block["minimum"] = nominal * (1 - tol)
        block["maximum"] = nominal * (1 + tol)
    return block


# --------------------------------------------------------------------------- #
# Row -> record
# --------------------------------------------------------------------------- #
def load_csv(path: Path):
    with open(path, encoding="cp932", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("Part Number") or "").strip()]


def build_electrolytic(row):
    """File 1: Aluminum Electrolytic + Hybrid, cylindrical."""
    cat = row["Category"]
    tech = TECH_ELEC[cat]
    pn = row["Part Number"].strip()

    d_mm = fnum(row.get("φD (mm)"))   # φD
    l_mm = fnum(row.get("L (mm)"))

    elec = {}
    cap = fnum(row.get("Capacitance (μF)"))
    if cap is None:
        raise ValueError("missing capacitance")
    elec["capacitance"] = capacitance_block(cap, row.get("Capacitance Tolerance"))
    rv = fnum(row.get("Rated Voltage (Vdc)"))
    if rv is None:
        raise ValueError("missing rated voltage")
    elec["ratedVoltage"] = rv
    elec["voltageRatedDcMax"] = rv  # file 1 is entirely DC-rated
    elec["polarized"] = tech in POLARIZED_TECH

    # ESR / impedance (mOhm -> Ohm)
    esr = fnum(row.get("ESR or Z (mΩ)"))
    if esr is not None:
        elec["esr"] = esr * 1e-3
        f = parse_freq(row.get("ESR or Z Condition", ""))
        if f is not None:
            elec["esrFrequency"] = f

    # Ripple current: prefer the rated (typically 100 kHz) column, else the
    # secondary (typically 120 Hz) column, else the permissible column. Each
    # carries its own condition string.
    for val_col, cond_col in (
        ("Rated Ripple Current (mArms)", "Rated Ripple Current Condition"),
        ("Rated Ripple Current 2 (mArms)", "Rated Ripple Current 2 Condition"),
        ("Permissible Ripple Current", "Permissible Ripple Current Condition"),
    ):
        ir = fnum(row.get(val_col))
        if ir is not None:
            elec["rippleCurrent"] = ir * 1e-3
            cond = row.get(cond_col, "")
            f, t = parse_freq(cond), parse_temp(cond)
            if f is not None:
                elec["rippleCurrentFrequency"] = f
            if t is not None:
                elec["rippleCurrentTemperature"] = t
            break

    part = {"partNumber": pn, "technology": tech}
    if row.get("Series", "").strip():
        part["series"] = row["Series"].strip()
    if d_mm is not None and l_mm is not None:
        part["case"] = f"{row['φD (mm)'].strip()}x{row['L (mm)'].strip()}"

    dims = {}
    if d_mm is not None:
        dims["diameter"] = {"nominal": d_mm * 1e-3}
    if l_mm is not None:
        dims["length"] = {"nominal": l_mm * 1e-3}

    shape = {"assembly": ASSEMBLY[row["Package"]], "shapeType": "Radial Cylindrical"}
    if d_mm is not None and l_mm is not None:
        r = d_mm * 1e-3 / 2.0
        shape["volume"] = {"nominal": math.pi * r * r * (l_mm * 1e-3)}
        shape["footprint"] = {"nominal": math.pi * r * r}

    mech = {"shape": shape}
    if dims:
        mech["dimensions"] = dims

    thermal = temperature_block(row)
    lifetime = endurance_block(row)
    model = {"cs": cap * 1e-6}
    if "esr" in elec:
        model["rs"] = elec["esr"]

    return assemble(pn, part, elec, mech, thermal, lifetime, model, row.get("Datasheet"))


def build_film(row):
    """File 2: Film (PP/PET/PPS) + PMLCAP (acrylic), box/chip."""
    diel = row.get("Dielectric", "").strip()
    tech = TECH_FILM_DIELECTRIC.get(diel)
    if tech is None:
        raise ValueError(f"unmapped dielectric {diel!r}")
    pn = row["Part Number"].strip()

    elec = {}
    cap = fnum(row.get("Capacitance (μF)"))
    if cap is None:
        raise ValueError("missing capacitance")
    elec["capacitance"] = capacitance_block(cap, row.get("Capacitance Tolerance"))
    rv = fnum(row.get("Rated Voltage (V)"))
    if rv is None:
        raise ValueError("missing rated voltage")
    elec["ratedVoltage"] = rv
    if row.get("Voltage Type", "").strip() == "DC":
        elec["voltageRatedDcMax"] = rv
    elec["polarized"] = False  # film + PMLCAP are non-polar

    l_mm = fnum(row.get("A or L (mm)"))
    w_mm = fnum(row.get("B or W (mm)"))
    h_mm = fnum(row.get("C or H (mm)"))
    p_mm = fnum(row.get("Lead Pitch (mm)"))

    part = {"partNumber": pn, "technology": tech}
    if row.get("Series", "").strip():
        part["series"] = row["Series"].strip()
    if None not in (l_mm, w_mm, h_mm):
        part["case"] = f"{row['A or L (mm)'].strip()}x{row['B or W (mm)'].strip()}x{row['C or H (mm)'].strip()}"

    dims = {}
    if l_mm is not None:
        dims["length"] = {"nominal": l_mm * 1e-3}
    if w_mm is not None:
        dims["width"] = {"nominal": w_mm * 1e-3}
    if h_mm is not None:
        dims["height"] = {"nominal": h_mm * 1e-3}
    if p_mm:  # 0 means no leads (SMD)
        dims["pitch"] = {"nominal": p_mm * 1e-3}

    pkg = row["Package"]
    shape = {
        "assembly": ASSEMBLY[pkg],
        "shapeType": "SMD Chip" if pkg == "CHIP TYPE" else "Box type",
    }
    if None not in (l_mm, w_mm, h_mm):
        shape["volume"] = {"nominal": l_mm * w_mm * h_mm * 1e-9}
        shape["footprint"] = {"nominal": l_mm * w_mm * 1e-6}

    mech = {"shape": shape}
    if dims:
        mech["dimensions"] = dims

    thermal = temperature_block(row)
    model = {"cs": cap * 1e-6}

    return assemble(pn, part, elec, mech, thermal, None, model, row.get("Datasheet"))


def temperature_block(row):
    lo = fnum(row.get("Lower Category Temperature (℃)"))
    hi = fnum(row.get("Upper Category Temperature (℃)"))
    temp = {}
    if lo is not None:
        temp["minimum"] = lo
    if hi is not None:
        temp["maximum"] = hi
    return {"temperature": temp} if temp else None


def endurance_block(row):
    e = fnum(row.get("Endurance (h)"))
    return {"lifetimeEndurance": e} if e is not None else None


def assemble(pn, part, elec, mech, thermal, lifetime, model, datasheet):
    di = {"part": part, "electrical": elec, "mechanical": mech}
    if thermal:
        di["thermal"] = thermal
    if lifetime:
        di["lifetime"] = lifetime
    if model:
        di["modelParams"] = model
    mi = {"name": MANUFACTURER, "reference": pn, "datasheetInfo": di}
    ds = (datasheet or "").strip()
    if ds:
        mi["datasheetUrl"] = ds
    return {"capacitor": {"manufacturerInfo": mi}}


# --------------------------------------------------------------------------- #
def existing_rubycon_part_numbers() -> set[str]:
    seen = set()
    if not CAP_FILE.exists():
        return seen
    with CAP_FILE.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or '"Rubycon"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            mi = rec.get("capacitor", {}).get("manufacturerInfo", {})
            if mi.get("name") != MANUFACTURER:
                continue
            pn = mi.get("datasheetInfo", {}).get("part", {}).get("partNumber")
            if pn:
                seen.add(pn)
            if mi.get("reference"):
                seen.add(mi["reference"])
    return seen


def main():
    validator = build_validator()
    existing = existing_rubycon_part_numbers()
    print(f"existing Rubycon part numbers in library: {len(existing)}")

    valid, quarantined, skipped_dup, errors = [], [], 0, 0
    seen_this_run = set()

    for path, builder in ((FILE_ELEC, build_electrolytic), (FILE_FILM, build_film)):
        rows = load_csv(path)
        print(f"{path.name}: {len(rows)} rows")
        for row in rows:
            pn = row["Part Number"].strip()
            if pn in existing or pn in seen_this_run:
                skipped_dup += 1
                continue
            seen_this_run.add(pn)
            try:
                rec = builder(row)
            except Exception as e:  # noqa: BLE001 - capture row-level mapping errors
                errors += 1
                quarantined.append({"_error": f"build: {e}", "_partNumber": pn, "_row": row})
                continue
            errs = list(validator.iter_errors(rec["capacitor"]))
            if errs:
                rec["_quarantineReason"] = f"{errs[0].message} @ {list(errs[0].absolute_path)}"
                quarantined.append(rec)
            else:
                valid.append(rec)

    print(f"valid: {len(valid)}  quarantined: {len(quarantined)}  "
          f"dup-skipped: {skipped_dup}  build-errors: {errors}")

    if "--write" in sys.argv:
        with CAP_FILE.open("a") as fh:
            for rec in valid:
                fh.write(json.dumps(rec) + "\n")
        print(f"appended {len(valid)} records to {CAP_FILE}")
        if quarantined:
            with QUARANTINE.open("w") as fh:
                for rec in quarantined:
                    fh.write(json.dumps(rec) + "\n")
            print(f"wrote {len(quarantined)} records to {QUARANTINE}")
    else:
        print("dry run (pass --write to append). Sample valid record:")
        if valid:
            print(json.dumps(valid[0], indent=1))
        if quarantined:
            print("Sample quarantine reason:", quarantined[0].get("_quarantineReason")
                  or quarantined[0].get("_error"))


if __name__ == "__main__":
    main()
