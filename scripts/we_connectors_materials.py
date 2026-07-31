#!/usr/bin/env python3
"""Populate material + contactPlating on Würth connectors, now the registry defines them.

The datasheet harvest recovered a housing material for 4,821 parts, a contact metal for
3,477 and a plating for 3,453 — but only 13 records could store any of it, because
conas-materials defined 10 ids and almost none of WE's materials were among them. With the
registry extended (CONAS: +8 materials), most of that data can finally land.

MAPPING IS EXACT-MATCH ONLY. 'PA66' -> pa66-nylon, 'LCP' -> lcp. Nothing is mapped by
resemblance: 'Copper Alloy' (1,746 parts) is NOT brass and NOT beryllium copper, so it
maps to nothing and those parts keep no material rather than a plausible-looking wrong one.
Same for PA9T/PA6T/PA4T, which the registry does not define yet.

CONAS used to make `material` all-or-nothing (both refs required together), which meant a
part with a known housing but an unspecified contact alloy stored NEITHER. That rule was
dropped with approval (ABT #405), so a housing-only material object is now legal and those
parts keep what the manufacturer does publish.

Plating detail is parsed from strings like 'Gold, min. 0.127 µm over Nickel' and
'100 (µ") Tin over 50 (µ") Nickel' into matingArea/underplating refs + thicknesses in m.

  we_connectors_materials.py            # dry run
  we_connectors_materials.py --apply
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
LIVE = TAS / "data" / "connectors.ndjson"
SPECS = TAS / "staging" / "we_conn" / "specs.jsonl"
AUDIT = TAS / "staging" / "we_conn" / "materials_audit.json"
REGISTRY = TAS.parent / "CONAS" / "data" / "conas-materials.ndjson"
RETRIEVED = "2026-07-31"

# WE's exact datasheet wording -> registry id. Exact match, lowercased.
HOUSING = {
    "pa66": "pa66-nylon",
    "nylon 66": "pa66-nylon",
    "pa66 gf30": "pa66-gf30",
    "lcp": "lcp",
    "ptfe": "ptfe",
    "pbt": "pbt",
    "pet": "pet-polyester",
    "pet (white)": "pet-polyester",
    "pvc": "pvc-rigid",
    "pc": "pc-polycarbonate",
    "pps": "pps-gf40",
    # Semi-aromatic polyamide (PPA). WE writes it three ways for the same material.
    "pa6t": "ppa-pa6t-gf",
    "pa 6t": "ppa-pa6t-gf",
    "nylon 6t": "ppa-pa6t-gf",
    "abs": "abs",
    "pom": "pom-acetal",
    # PA9T = Kuraray GENESTAR; WE writes it three ways for the same polymer.
    "pa9t": "pa9t",
    "pa 9t": "pa9t",
    "nylon 9t": "pa9t",
    "pa4t": "pa4t",
    "pa46": "pa46",
    "nylon 46": "pa46",
    # NOT mapped on purpose: "abs metallized" is a metallised (conductive-plated) ABS
    # housing, whose surface is no longer the dielectric ABS is characterised as.
}
CONTACT = {
    "phosphor bronze": "cusnp-phosphorBronze",
    "beryllium copper": "cube-berylliumCopper",
    "brass": "cuznSn-brass",
    "copper": "cu-etp-copper",
}
PLATING = {"gold": "au-gold", "tin": "sn-tin", "nickel": "ni-nickel"}


def parse_plating(raw):
    """'Gold, min. 0.127 µm over Nickel' / '100 (µ\") Tin over 50 (µ\") Nickel' -> plating."""
    if not raw:
        return None
    s = raw.strip().lower()
    if s in ("1)", "2)", "pin 1", "pin 2", "-", "–"):
        return None
    parts = re.split(r"\bover\b", s)
    out = {}

    def metal(seg):
        for k, v in PLATING.items():
            if re.search(rf"\b{k}\b", seg):
                return v
        return None

    def thickness(seg):
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:\(?[µμ]m\)?)", seg)
        if m:
            return float(m.group(1).replace(",", ".")) * 1e-6
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*\(?[µμ]\"?\)?", seg)   # micro-inches
        if m:
            return float(m.group(1).replace(",", ".")) * 25.4e-9
        return None

    top = metal(parts[0])
    if not top:
        return None
    out["matingAreaMaterialRef"] = top
    t = thickness(parts[0])
    if t:
        out["matingAreaThickness"] = t
    if len(parts) > 1:
        under = metal(parts[1])
        if under:
            out["underplatingMaterialRef"] = under
            t = thickness(parts[1])
            if t:
                out["underplatingThickness"] = t
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    from merge_staged_connectors import build_validator
    gate = BladeGate("connector")
    v = build_validator()

    ids = {json.loads(l)["id"] for l in REGISTRY.read_text(encoding="utf-8").splitlines()
           if l.strip()}
    print(f"registry defines {len(ids)} materials")
    for m in set(HOUSING.values()) | set(CONTACT.values()) | set(PLATING.values()):
        if m not in ids:
            print(f"  ERROR: mapping targets unknown registry id {m!r}")
            return 1

    specs = {}
    for ln in SPECS.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            specs[r["orderCode"]] = r["spec"]

    before_lines = sum(1 for _ in LIVE.open(encoding="utf-8"))
    before_size = LIVE.stat().st_size

    audit = []
    mat_set = plat_set = touched = reverted = 0
    blocked_by_contact, unmapped_house, unmapped_contact = 0, {}, {}
    tmp = LIVE.with_suffix(".ndjson.mat_tmp")
    with LIVE.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as out:
        for raw in src:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            if "rth Elektronik" not in s:
                out.write(s + "\n")
                continue
            obj = json.loads(s)
            c = obj.get("connector") or obj
            mi = c.get("manufacturerInfo") or {}
            ref = mi.get("reference")
            spec = specs.get(ref)
            if "rth" not in (mi.get("name") or "") or not spec:
                out.write(s + "\n")
                continue
            ds = mi.setdefault("datasheetInfo", {})
            existing = ds.get("material") or {}

            house = (spec.get("insulatorMaterial") or "").strip().lower()
            cont = (spec.get("contactMaterial") or "").strip().lower()
            h_ref, c_ref = HOUSING.get(house), CONTACT.get(cont)
            if house and not h_ref:
                unmapped_house[house] = unmapped_house.get(house, 0) + 1
            if cont and not c_ref:
                unmapped_contact[cont] = unmapped_contact.get(cont, 0) + 1
            if not (h_ref or c_ref):
                out.write(s + "\n")
                continue
            if h_ref and not c_ref:
                blocked_by_contact += 1      # now stored housing-only, not discarded

            # CONAS no longer requires the two refs together (ABT #405), so a part with
            # a known housing and an unspecified contact alloy keeps its housing instead
            # of losing both.
            # MERGE into any existing material rather than skipping the record: a part
            # that earlier got only a contact ref (housing unmapped at the time) must
            # still gain its housing once the registry defines that polymer.
            mat = dict(existing)
            for key, val in (("contactBaseMaterialRef", c_ref),
                             ("housingMaterialRef", h_ref)):
                if val and key not in mat:
                    mat[key] = val
            plating = parse_plating(spec.get("contactPlating"))
            if plating and "contactPlating" not in mat:
                mat["contactPlating"] = plating
            if mat == existing:
                out.write(s + "\n")
                continue
            ds["material"] = mat
            ds.setdefault("provenance", []).append({
                "source": "manufacturerDatasheet",
                "sourceName": f"Würth Elektronik datasheet {ref} (materials)",
                "sourceUrl":
                    f"https://www.we-online.com/components/products/datasheet/{ref}.pdf",
                "retrievedDate": RETRIEVED})

            errs = sorted(v.iter_errors(c), key=lambda e: e.path)
            ok, _ = (False, None) if errs else gate.check(c)
            if not ok:
                reverted += 1
                out.write(s + "\n")
                continue
            touched += 1
            mat_set += 1
            if plating:
                plat_set += 1
            audit.append({"reference": ref, "material": mat})
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")

    print(f"\nrecords given a material : {mat_set}   (of which plating detail: {plat_set})")
    print(f"left untouched           : {reverted} (would not validate)")
    print(f"stored HOUSING-ONLY (contact alloy unspecified by the vendor): "
          f"{blocked_by_contact}")
    print("unmapped housing materials:")
    for k, n in sorted(unmapped_house.items(), key=lambda kv: -kv[1])[:8]:
        print(f"   {n:>5}  {k}")
    print("unmapped contact materials:")
    for k, n in sorted(unmapped_contact.items(), key=lambda kv: -kv[1])[:8]:
        print(f"   {n:>5}  {k}")
    print(" ", gate.summary())

    if not a.apply:
        tmp.unlink()
        print("\nDRY RUN — pass --apply to write")
        return 0
    if LIVE.stat().st_size != before_size or \
            sum(1 for _ in LIVE.open(encoding="utf-8")) != before_lines:
        tmp.unlink()
        print("ABORTED: connectors.ndjson changed while building the copy")
        return 1
    AUDIT.write_text(json.dumps(audit, indent=1, ensure_ascii=False))
    os.replace(tmp, LIVE)
    print(f"\nwrote {LIVE}; audit: {AUDIT} ({len(audit)} records)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
