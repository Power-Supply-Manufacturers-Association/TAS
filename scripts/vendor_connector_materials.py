#!/usr/bin/env python3
"""Populate connector material refs from vendor parametric data ALREADY on disk.

The materials gap on connectors (4,331 of 392,346 records, all Würth) was going to be
handed to the librarian to grind out of distributor pages with an LLM. It does not need
that for two of the biggest vendors: Sullins publishes Insulator Material / Contact
Material / Plating as columns of its own parametric grid, and WAGO publishes them as
classification features on its product API. Both pulls are already staged locally, so
this is a mapping job, not a sourcing job — deterministic, from the manufacturer's own
table, with no model in the loop and no new network access.

  Sullins  /tmp/sullins/rows.jsonl        attrs: Insulator Material, Contact Material,
                                          Plating [Contact Surface] / [Termination]
  WAGO     staging/wago/specs.ndjson      f: 'Material data || Insulation material
                                          (main housing)', '... || Contact material',
                                          '... || Contact Plating'

MAPPING IS EXACT-MATCH ONLY, and the vocabulary is shared with we_connectors_materials.py
so the three vendors cannot drift apart. Nothing is mapped by resemblance:

  * Sullins "Nylon" (22,490 parts) is NOT mapped. Bare 'Nylon' does not say which
    polyamide — PA6, PA66 and PA46 are different materials with different temperature
    ratings, and the registry distinguishes them. 'Nylon 66' would map; 'Nylon' does not.
  * WAGO "Copper alloy" / "Copper or copper alloy; surface-treated" (9,934) is NOT
    mapped, for the same reason Würth's "Copper Alloy" is not: it is not brass and not
    beryllium copper, and guessing which would put a wrong modulus and conductivity on
    ten thousand parts.
  * WAGO "PA66 GF" (258) is NOT mapped: the registry's pa66-gf30 is specifically 30%
    glass-filled and WAGO does not state the fill fraction.
  * Sullins "PEEK" / "Spinodal" / "Beryllium Nickel" and WAGO "Polyolefin" / silver
    plating are NOT mapped: the registry does not define them.

Every unmapped string is counted and written to the audit file, so the tail is a list to
work through rather than an invisible shortfall.

  vendor_connector_materials.py sullins            # dry run
  vendor_connector_materials.py wago --apply
  vendor_connector_materials.py all --apply
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
PSMA = TAS.parent
LIVE = TAS / "data" / "connectors.ndjson"
REGISTRY = PSMA / "CONAS" / "data" / "conas-materials.ndjson"
AUDIT = TAS / "staging" / "vendor_materials_audit.json"

SULLINS_ROWS = Path("/tmp/sullins/rows.jsonl")
WAGO_SPECS = TAS / "staging" / "wago" / "specs.ndjson"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from we_connectors_materials import CONTACT, HOUSING, parse_plating  # noqa: E402

# Vendor wording that the shared Würth vocabulary does not already cover, still exact.
HOUSING_EXTRA = {
    # Sullins
    "pa9t": "pa9t",
    "peek": "peek",
    # WAGO writes the polymer name and the code together.
    "polyamide (pa66)": "pa66-nylon",
    "polyamide 66 (pa 66)": "pa66-nylon",
    "polyamide (pa46)": "pa46",
    "polycarbonate (pc)": "pc-polycarbonate",
    "polybutylene terephthalate (pbt)": "pbt",
    "polyphthalamide (ppa gf)": "ppa-pa6t-gf",
}
CONTACT_EXTRA = {
    # E-Cu is electrolytic tough pitch copper, exactly what cu-etp-copper names.
    "electrolytic copper (e<sub>cu</sub>)": "cu-etp-copper",
    "electrolytic copper (ecu)": "cu-etp-copper",
    # Sullins' "Spinodal" is the Cu-15Ni-8Sn connector alloy; the registry record says so
    # and records only the temper-independent properties, since Sullins states no temper.
    "spinodal": "cuniSn-spinodal",
}

HOUSING_MAP = {**HOUSING, **HOUSING_EXTRA}
CONTACT_MAP = {**CONTACT, **CONTACT_EXTRA}


def load_sullins():
    """{partNumber: {housing, contact, platingContact, platingTermination}}"""
    if not SULLINS_ROWS.exists():
        sys.exit(f"missing {SULLINS_ROWS} — re-run scripts/sullins_fetch.py")
    out = {}
    with SULLINS_ROWS.open(encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            r = json.loads(ln)
            a = r.get("attrs") or {}
            out[r["partNumber"]] = {
                "housing": a.get("Insulator Material"),
                "contact": a.get("Contact Material"),
                "plating": a.get("Plating [Contact Surface]"),
            }
    return out


def load_wago():
    if not WAGO_SPECS.exists():
        sys.exit(f"missing {WAGO_SPECS} — re-run staging/wago/wago_specs.py")
    out = {}
    with WAGO_SPECS.open(encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            r = json.loads(ln)
            f = r.get("f") or {}

            def first(key):
                x = f.get(key)
                v = (x or {}).get("v") or []
                return v[0] if v else None

            out[r["code"]] = {
                "housing": first("Material data || Insulation material (main housing)"),
                "contact": first("Material data || Contact material"),
                "plating": first("Material data || Contact Plating"),
            }
    return out


VENDORS = {
    "sullins": ("Sullins Connector Solutions", load_sullins),
    "wago": ("WAGO", load_wago),
}


def run(vendor, specs_by_ref, mfr, validator, gate, ids, apply_):
    stats = Counter()
    unmapped_h, unmapped_c, unmapped_p = Counter(), Counter(), Counter()
    rejected = []

    tmp = LIVE.with_suffix(".ndjson.mat_tmp")
    with LIVE.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as out:
        for raw in src:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            stats["total"] += 1
            if mfr not in s:
                out.write(s + "\n")
                continue
            obj = json.loads(s)
            c = obj.get("connector") or obj
            mi = c.get("manufacturerInfo") or {}
            if mi.get("name") != mfr:
                out.write(s + "\n")
                continue
            stats["vendor_records"] += 1
            spec = specs_by_ref.get(mi.get("reference"))
            if not spec:
                stats["no_staged_spec"] += 1
                out.write(s + "\n")
                continue

            house = (spec.get("housing") or "").strip().lower()
            cont = (spec.get("contact") or "").strip().lower()
            h_ref, c_ref = HOUSING_MAP.get(house), CONTACT_MAP.get(cont)
            if house and not h_ref:
                unmapped_h[house] += 1
            if cont and not c_ref:
                unmapped_c[cont] += 1
            plating = parse_plating(spec.get("plating"))
            if spec.get("plating") and not plating:
                unmapped_p[spec["plating"].strip().lower()] += 1
            if not (h_ref or c_ref or plating):
                out.write(s + "\n")
                continue

            ds = mi.setdefault("datasheetInfo", {})
            mat = dict(ds.get("material") or {})
            before = json.dumps(mat, sort_keys=True)
            if h_ref:
                mat["housingMaterialRef"] = h_ref
            if c_ref:
                mat["contactBaseMaterialRef"] = c_ref
            if plating:
                mat["contactPlating"] = {**(mat.get("contactPlating") or {}), **plating}
            if json.dumps(mat, sort_keys=True) == before:
                stats["unchanged"] += 1
                out.write(s + "\n")
                continue
            ds["material"] = mat

            errs = sorted(validator.iter_errors(c), key=lambda e: e.path)
            if errs:
                stats["rejected_invalid"] += 1
                if len(rejected) < 5:
                    rejected.append(f"{mi.get('reference')}: {errs[0].message[:150]}")
                out.write(s + "\n")          # ORIGINAL line, untouched
                continue
            ok, why = gate.check(c)
            if not ok:
                stats["rejected_blade"] += 1
                if len(rejected) < 5:
                    rejected.append(f"{mi.get('reference')}: BLADE {why}")
                out.write(s + "\n")
                continue

            stats["patched"] += 1
            stats["housing_set"] += bool(h_ref)
            stats["contact_set"] += bool(c_ref)
            stats["plating_set"] += bool(plating)
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")

    if apply_:
        os.replace(tmp, LIVE)
    else:
        tmp.unlink()

    print(f"\n=== {vendor} ({mfr}) — {'APPLIED' if apply_ else 'DRY RUN'} ===")
    for k in ("vendor_records", "no_staged_spec", "patched", "unchanged",
              "housing_set", "contact_set", "plating_set",
              "rejected_invalid", "rejected_blade"):
        print(f"  {k:20} {stats[k]}")
    for label, ctr in (("housing", unmapped_h), ("contact", unmapped_c),
                       ("plating", unmapped_p)):
        if ctr:
            print(f"  -- unmapped {label} strings (kept EMPTY, not guessed) --")
            for k, n in ctr.most_common(10):
                print(f"     {n:7d}  {k}")
    if rejected:
        print("  -- left unpatched (failed a gate) --")
        for r in rejected:
            print(f"     {r}")
    return {"stats": dict(stats),
            "unmapped_housing": dict(unmapped_h),
            "unmapped_contact": dict(unmapped_c),
            "unmapped_plating": dict(unmapped_p),
            "rejected": rejected}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("vendor", choices=[*VENDORS, "all"])
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    from blade_gate import BladeGate
    from merge_staged_connectors import build_validator
    gate = BladeGate("connector")
    validator = build_validator()

    ids = {json.loads(l)["id"] for l in REGISTRY.read_text(encoding="utf-8").splitlines()
           if l.strip()}
    bad = {m for m in set(HOUSING_MAP.values()) | set(CONTACT_MAP.values())
           if m not in ids}
    if bad:
        sys.exit(f"mapping targets unknown registry ids: {sorted(bad)}")
    print(f"registry defines {len(ids)} materials; all mapping targets exist")

    audit = {}
    for name in ([*VENDORS] if a.vendor == "all" else [a.vendor]):
        mfr, loader = VENDORS[name]
        specs = loader()
        print(f"{name}: {len(specs)} staged parametric rows")
        audit[name] = run(name, specs, mfr, validator, gate, ids, a.apply)

    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=1))
    print(f"\naudit -> {AUDIT}")
    if not a.apply:
        print("Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
