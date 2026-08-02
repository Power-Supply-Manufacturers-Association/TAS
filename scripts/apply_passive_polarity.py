#!/usr/bin/env python3
"""Apply the staged #472 passive polarity pinout + derivable through-hole landPattern.

Part A (capacitors): mechanical.pinout = [anode, cathode] for polarized parts where
the footprint convention fixes the pad -- molded tantalum (the #471 TANT_* set, IPC
CAPMP anode-band convention) and radial aluminium-electrolytic THT (radial-can
convention). source='derived'.
Part B (capacitors + varistors): mechanical.landPattern for the through-hole subset
whose case string ENCODES the lead geometry (film 'PCM x', radial-electrolytic 'ØD',
MOV disc 'TH-NN'), where #471 left landPattern absent. source='derived'.

Only writes a field when it is ABSENT. Safe-write per file (refuse on active writer,
byte-identical untouched lines, per-record schema validation before atomic swap).

Usage: apply_passive_polarity.py [--dry-run]
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO.parent
TODAY = "2026-08-02"
SL = Path("/tmp/claude-1000/-home-alf/e0566217-bb16-4d03-9e3f-f35b93581bf0/scratchpad/passive_landpattern")
SP = Path("/tmp/claude-1000/-home-alf/e0566217-bb16-4d03-9e3f-f35b93581bf0/scratchpad/passive_polarity")

sys.path.insert(0, str(SL))
sys.path.insert(0, str(SP))
from normalizer import normalize_case  # noqa: E402  (#471, for TANT_* detection)
from normalizer_th import normalize_th_case  # noqa: E402  (#472 through-hole)

POLARITY = json.load(open(SP / "polarity_by_key.json"))
THLIB = json.load(open(SP / "through_hole_landpattern.json"))
RADIAL_TECH = {"aluminum-electrolytic-wet", "aluminum-electrolytic-polymer", "aluminum-hybrid-polymer"}

FAMILIES = {
    "capacitor": ("capacitors.ndjson", "capacitor", "https://psma.com/cas/capacitor.json"),
    "varistor": ("varistors.ndjson", "varistor", "https://psma.com/ras/varistor.json"),
}


def build_registry():
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    res = []
    for repo in ("PEAS", "CIAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS", "TAS", "TDAS", "COAS"):
        sdir = WORKSPACE / repo / "schemas"
        if sdir.is_dir():
            for p in sdir.rglob("*.json"):
                d = json.loads(p.read_text())
                if "$id" in d:
                    res.append((d["$id"], Resource.from_contents(d, default_specification=DRAFT202012)))
    return Registry().with_resources(res)


def polarity_for(di, part):
    """Return (pinout, key) for a polarized capacitor, or None."""
    case = part.get("case")
    tech = part.get("technology")
    code = normalize_case(case, tech, "capacitor")
    if code and str(code).startswith("TANT_"):
        return POLARITY["TANT_MOLDED"]["pinout"], "TANT_MOLDED"
    assembly = ((di.get("mechanical") or {}).get("shape") or {}).get("assembly")
    if tech in RADIAL_TECH and assembly == "THT":
        return POLARITY["ELEC_RADIAL_THT"]["pinout"], "ELEC_RADIAL_THT"
    return None


def apply_family(family, registry, dry_run):
    from jsonschema import Draft202012Validator
    fname, disc, schema_id = FAMILIES[family]
    path = REPO / "data" / fname
    validator = Draft202012Validator(registry.get_or_retrieve(schema_id).value.contents, registry=registry)
    lsof = subprocess.run(["lsof", "-F", "a", str(path)], capture_output=True, text=True)
    if [l for l in lsof.stdout.splitlines() if l.startswith("a") and ("w" in l or "u" in l)]:
        sys.exit(f"REFUSING: {fname} is open for writing by another process")

    stats = Counter()
    tmp = path.with_suffix(".ndjson.pol_tmp")
    size0 = path.stat().st_size
    with open(path, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            stats["lines"] += 1
            try:
                doc = json.loads(raw)
                comp = doc[disc]
                di = comp["manufacturerInfo"]["datasheetInfo"]
            except Exception:
                out.write(raw)
                continue
            mech = di.get("mechanical")
            part = di.get("part", {}) or {}
            if not isinstance(mech, dict):
                out.write(raw)
                continue
            modified = False

            # Part A -- polarity pinout (capacitors only)
            if family == "capacitor" and "pinout" not in mech:
                pol = polarity_for(di, part)
                if pol:
                    pinout, key = pol
                    mech["pinout"] = pinout
                    di.setdefault("provenance", []).append({
                        "source": "derived",
                        "sourceName": f"OpenConverters passive polarity 2026-08 (#472, {key})",
                        "retrievedDate": TODAY,
                        "fields": ["mechanical.pinout"],
                        "derivation": (f"polarity (anode/cathode) from CAS technology "
                                       f"'{part.get('technology')}' (polarized dielectric); pad->role "
                                       f"per {POLARITY[key]['source'][:90]}"),
                    })
                    stats["polarity"] += 1
                    modified = True

            # Part B -- derivable through-hole landPattern (where absent)
            if "landPattern" not in mech:
                assembly = ((mech.get("shape") or {}).get("assembly")
                            if family == "capacitor" else mech.get("assemblyType"))
                thkey = normalize_th_case(part.get("case"), part.get("technology"), assembly, family)
                if thkey and thkey in THLIB:
                    mech["landPattern"] = THLIB[thkey]["landPattern"]
                    di.setdefault("provenance", []).append({
                        "source": "derived",
                        "sourceName": f"OpenConverters through-hole landPattern 2026-08 (#472, {thkey})",
                        "retrievedDate": TODAY,
                        "fields": ["mechanical.landPattern"],
                        "derivation": (f"through-hole land pattern from the lead geometry encoded in "
                                       f"case '{part.get('case')}': {THLIB[thkey]['source'][:100]}"),
                    })
                    stats["thlandpattern"] += 1
                    modified = True

            if not modified:
                out.write(raw)
                continue
            errs = list(validator.iter_errors(comp))
            if errs:
                tmp.unlink(missing_ok=True)
                sys.exit(f"ABORT {family}: modified record fails schema at line {stats['lines']}: "
                         f"{errs[0].message[:300]}")
            out.write((json.dumps(doc, ensure_ascii=False) + "\n").encode())
            stats["modified"] += 1
        src.seek(0, os.SEEK_END)
        if src.tell() > size0:
            src.seek(size0)
            for raw in src:
                out.write(raw)
                stats["tail"] += 1
    n_out = sum(1 for _ in open(tmp, "rb"))
    if n_out != stats["lines"] + stats["tail"]:
        tmp.unlink()
        sys.exit(f"ABORT {family}: line count {n_out} != {stats['lines'] + stats['tail']}")
    print(f"{family}: lines={stats['lines']} polarity={stats['polarity']} "
          f"thLandPattern={stats['thlandpattern']} modified={stats['modified']} tail={stats['tail']}")
    if dry_run:
        tmp.unlink()
    else:
        os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    registry = build_registry()
    for f in ("capacitor", "varistor"):
        apply_family(f, registry, args.dry_run)
    if args.dry_run:
        print("dry-run: no swap")


if __name__ == "__main__":
    main()
