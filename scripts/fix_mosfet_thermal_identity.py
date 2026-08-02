#!/usr/bin/env python3
"""Repair or quarantine the 40 mosfets.ndjson records failing MOS_IDC_VS_THERMAL (ABT #500).

Blade Runner's check: conduction loss at the rated continuous drain current,
Id^2 * Rds(on)@25C, must not exceed the package's own budget (Tjmax-25)/Rth_jc.
Forty records fail it, and they block the changed-records gate for EVERY commit
touching mosfets.ndjson (ABT #500/#501/#507/#523 all stalled on it).

Every value written here was read from the manufacturer's datasheet by a
research pass whose output is the --plan JSON; nothing is computed to "make the
check pass". Three actions, decided per part by what the research found:

  repair       datasheet disagrees with the record -> corrected fields +
               a provenance entry naming the datasheet and the fields changed
  reattribute  the part is real but belongs to ANOTHER manufacturer
               (SCT3xxxxLGC11 are ROHM parts wearing ST's name) -> manufacturer,
               package and all electrical/thermal set from the real datasheet
  quarantine   the part number does not exist in its manufacturer's catalogue
               -> moved to mosfets.quarantine_not_in_manufacturer_catalogue.ndjson
               with _validatorQuarantine {date, reason, ticket, evidence}

Untouched lines are copied byte-identical. Repaired records must (a) validate
against SAS mosfet.json and (b) clear Blade Runner with zero IMPOSSIBLE
findings, or the whole run aborts and replaces nothing.

    python3 scripts/fix_mosfet_thermal_identity.py --plan PLAN.json --dry-run
    python3 scripts/fix_mosfet_thermal_identity.py --plan PLAN.json
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PSMA = REPO.parent
DATA = REPO / "data" / "mosfets.ndjson"
QUARANTINE = REPO / "data" / "mosfets.quarantine_not_in_manufacturer_catalogue.ndjson"
AUDIT = REPO / "staging" / "mosfet_thermal_identity_audit.json"
TODAY = date.today().isoformat()
TICKET = "ABT #500"

sys.path.insert(0, str(REPO / "validator" / "build"))
import tas_validator  # noqa: E402


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    resources = []
    for repo in ("PEAS", "SAS", "CAS", "RAS", "MAS", "CIAS", "CTAS", "AAS", "CONAS"):
        root = PSMA / repo / "schemas"
        if not root.is_dir():
            continue
        for p in root.rglob("*.json"):
            doc = json.loads(p.read_text())
            if "$id" in doc:
                resources.append((doc["$id"], Resource.from_contents(doc)))
    registry = Registry().with_resources(resources)
    schema = json.loads((PSMA / "SAS" / "schemas" / "mosfet.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def blade_impossible(rec):
    r = tas_validator.validate(json.dumps(rec))
    findings = r.findings if hasattr(r, "findings") else r
    return [str(f) for f in findings if "IMPOSSIBLE" in str(f).upper()]


def set_path(obj, dotted, value):
    parts = dotted.split(".")
    for k in parts[:-1]:
        obj = obj.setdefault(k, {})
    old = obj.get(parts[-1])
    obj[parts[-1]] = value
    return old


def apply_values(rec, plan):
    """Write the datasheet values into the record; returns {field: [old, new]}."""
    info = rec["semiconductor"]["mosfet"]["manufacturerInfo"]
    sheet = info["datasheetInfo"]
    changed = {}

    def put(dotted, value):
        if value is None:
            return
        old = set_path(sheet, dotted, value)
        if old != value:
            changed[dotted] = [old, value]

    # Fields the datasheet does NOT define for this part are REMOVED, never
    # zeroed or invented (a chip-scale LGA has no junction-to-case path).
    for dotted in plan.get("remove", []):
        obj = sheet
        parts = dotted.split(".")
        for k in parts[:-1]:
            obj = obj.get(k) or {}
        if parts[-1] in obj:
            changed[dotted] = [obj.pop(parts[-1]), None]

    put("electrical.continuousDrainCurrent", plan.get("id_25c_A"))
    put("electrical.onResistance", plan.get("rdson_max_ohm"))
    put("electrical.onResistanceVgs", plan.get("rdson_vgs_V"))
    put("electrical.totalGateCharge", plan.get("qg_typ_C"))
    put("electrical.powerDissipation", plan.get("ptot_25c_W"))
    put("electrical.drainSourceVoltage", plan.get("vds_V"))
    put("electrical.gateSourceCharge", plan.get("qgs_typ_C"))
    put("electrical.gateDrainCharge", plan.get("qgd_typ_C"))
    put("thermal.thermalResistanceJunctionCase", plan.get("rth_jc_max_KperW"))
    put("thermal.thermalResistanceJunctionAmbient", plan.get("rth_ja_KperW"))
    put("thermal.junctionTemperatureMax", plan.get("tj_max_C"))
    put("part.case", plan.get("package"))
    put("part.technology", plan.get("technology"))

    if plan.get("actualManufacturer") and plan["actualManufacturer"] != info.get("name"):
        changed["manufacturerInfo.name"] = [info.get("name"), plan["actualManufacturer"]]
        info["name"] = plan["actualManufacturer"]
    if plan.get("datasheetUrl") and plan["datasheetUrl"] != info.get("datasheetUrl"):
        changed["manufacturerInfo.datasheetUrl"] = [info.get("datasheetUrl"),
                                                    plan["datasheetUrl"]]
        info["datasheetUrl"] = plan["datasheetUrl"]

    if changed:
        sheet.setdefault("provenance", []).append({
            "source": "manufacturerDatasheet",
            "sourceName": (f"thermal-identity re-read from the manufacturer datasheet "
                           f"[{TICKET}]" + (f"; {plan['provenance_note']}"
                                            if plan.get("provenance_note") else "")),
            "sourceUrl": plan.get("datasheetUrl"),
            "retrievedDate": TODAY,
            "fields": sorted(k for k in changed if not k.startswith("manufacturerInfo")),
        })
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    plans = {p["partNumber"]: p for p in json.loads(Path(a.plan).read_text())}
    validator = build_validator()

    audit = {"ticket": TICKET, "date": TODAY, "repaired": [], "quarantined": [],
             "untouched_reason": []}
    quarantined_lines = []
    seen = set()
    tmp = DATA.with_suffix(".ndjson.tmp")

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            wrote = False
            probe = next((pn for pn in plans if pn.encode() in raw), None)
            if probe is not None:
                try:
                    rec = json.loads(raw)
                    info = rec["semiconductor"]["mosfet"]["manufacturerInfo"]
                except Exception:
                    info = None
                ref = (info or {}).get("reference", "")
                if info is not None and ref in plans and ref not in seen:
                    seen.add(ref)
                    plan = plans[ref]
                    if plan["action"] == "quarantine":
                        rec["_validatorQuarantine"] = {
                            "date": TODAY,
                            "reason": ("part number does not exist in its own "
                                       "manufacturer's catalogue; thermal data is "
                                       "templated and physically impossible "
                                       f"(MOS_IDC_VS_THERMAL)"),
                            "ticket": TICKET,
                            "evidence": plan.get("evidence", ""),
                        }
                        quarantined_lines.append(
                            json.dumps(rec, ensure_ascii=False) + "\n")
                        audit["quarantined"].append(
                            {"reference": ref, "evidence": plan.get("evidence", "")})
                        wrote = True  # dropped from the main file
                    else:
                        changed = apply_values(rec, plan)
                        errors = [e.message[:160] for e in
                                  validator.iter_errors(rec["semiconductor"]["mosfet"])]
                        blade = blade_impossible(rec)
                        if errors or blade:
                            # In dry-run, collect every such record — a repair that
                            # exposes MORE impossible fields means the record is
                            # wrong beyond the re-read set and needs a full
                            # re-source; the caller decides. Real runs still abort.
                            print(f"{'DRY-FAIL' if a.dry_run else 'ABORT'} on {ref}: "
                                  f"schema={errors[:2]} blade={blade[:2]}")
                            if a.dry_run:
                                audit["untouched_reason"].append(
                                    {"reference": ref, "blade": blade, "schema": errors})
                                out.write(raw)
                                continue
                            tmp.unlink(missing_ok=True)
                            return 1
                        out.write(json.dumps(rec, ensure_ascii=False).encode() + b"\n")
                        audit["repaired"].append({"reference": ref, "action": plan["action"],
                                                  "changed": changed,
                                                  "datasheet": plan.get("datasheetUrl")})
                        wrote = True
            if not wrote:
                out.write(raw)
        out.flush()
        os.fsync(out.fileno())

    missing = sorted(set(plans) - seen)
    if missing:
        print(f"ABORT: planned parts never seen in {DATA.name}: {missing}")
        tmp.unlink(missing_ok=True)
        return 1

    print(f"repaired {len(audit['repaired'])}, quarantined {len(audit['quarantined'])}")
    if a.dry_run:
        tmp.unlink(missing_ok=True)
        print("--dry-run: nothing replaced")
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
