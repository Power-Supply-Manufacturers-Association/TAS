#!/usr/bin/env python3
"""ABT #400: back-fill the EXISTING Würth connector records from their datasheets.

The 4,205 WE connectors already in TAS came from an Access-DB snapshot that carried
current/voltage/pins/pitch but almost nothing else — 13% had an insulation resistance,
29% a withstanding voltage, 0% mating cycles. The datasheets harvested for ABT #400 have
all of it, so this adds what is missing without disturbing what is there.

ADD-ONLY. A field already present is never overwritten: the stored value came from a
different WE source and silently replacing it would destroy the ability to tell the two
apart. Where both exist and DISAGREE, the disagreement is reported (and written to
staging/we_conn/enrich_conflicts.json) rather than resolved unilaterally.

Every modified record is re-validated against CONAS and re-checked by Blade Runner before
it is allowed into the output; a record that would become invalid is left untouched.

  we_connectors_enrich.py            # dry run
  we_connectors_enrich.py --apply
"""
import argparse
import json
import os
import sys
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
LIVE = TAS / "data" / "connectors.ndjson"
SPECS = TAS / "staging" / "we_conn" / "specs.jsonl"
CONFLICTS = TAS / "staging" / "we_conn" / "enrich_conflicts.json"
RETRIEVED = "2026-07-31"
DS_URL = "https://www.we-online.com/components/products/datasheet/{}.pdf"

# spec key -> (section, field). contactResistance is a dimensionWithTolerance.
PLAIN = [
    ("ratedCurrentPerContact", "electrical", "ratedCurrentPerContact"),
    ("ratedVoltage", "electrical", "ratedVoltage"),
    ("dielectricWithstandingVoltage", "electrical", "dielectricWithstandingVoltage"),
    ("insulationResistance", "electrical", "insulationResistance"),
    ("matingCycles", "mechanical", "matingCycles"),
    ("operatingTemperature", "environmental", "operatingTemperature"),
]
REL_TOL = 0.02          # values within 2% are the same number, not a conflict


def differs(a, b):
    if isinstance(a, dict) or isinstance(b, dict):
        return a != b
    try:
        return abs(float(a) - float(b)) > REL_TOL * max(abs(float(a)), abs(float(b)), 1e-12)
    except (TypeError, ValueError):
        return a != b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    from merge_staged_connectors import build_validator
    gate = BladeGate("connector")
    v = build_validator()

    specs = {}
    for ln in SPECS.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            specs[r["orderCode"]] = r["spec"]
    print(f"{len(specs)} parsed datasheets available")

    before_lines = sum(1 for _ in LIVE.open(encoding="utf-8"))
    before_size = LIVE.stat().st_size

    added = {}
    conflicts = []
    touched = rejected = 0
    tmp = LIVE.with_suffix(".ndjson.enrich_tmp")
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
            new = 0
            for key, sec, field in PLAIN:
                if key not in spec:
                    continue
                tgt = ds.setdefault(sec, {})
                if field in tgt:
                    if differs(tgt[field], spec[key]):
                        conflicts.append({"reference": ref, "field": f"{sec}.{field}",
                                          "stored": tgt[field], "datasheet": spec[key]})
                    continue
                tgt[field] = spec[key]
                added[f"{sec}.{field}"] = added.get(f"{sec}.{field}", 0) + 1
                new += 1
            if "contactResistance" in spec:
                el = ds.setdefault("electrical", {})
                if "contactResistance" in el:
                    cur = el["contactResistance"]
                    cur_v = cur.get("maximum", cur.get("nominal")) if isinstance(cur, dict) else cur
                    if cur_v is not None and differs(cur_v, spec["contactResistance"]):
                        conflicts.append({"reference": ref, "field": "electrical.contactResistance",
                                          "stored": cur_v, "datasheet": spec["contactResistance"]})
                else:
                    el["contactResistance"] = (
                        {"maximum": spec["contactResistance"]}
                        if spec.get("contactResistanceIsMax", True)
                        else {"nominal": spec["contactResistance"]})
                    added["electrical.contactResistance"] = \
                        added.get("electrical.contactResistance", 0) + 1
                    new += 1

            if not new:
                out.write(s + "\n")
                continue

            ds.setdefault("provenance", []).append({
                "source": "manufacturerDatasheet",
                "sourceName": f"Würth Elektronik datasheet {ref}",
                "sourceUrl": DS_URL.format(ref),
                "retrievedDate": RETRIEVED})

            errs = sorted(v.iter_errors(c), key=lambda e: e.path)
            ok, why = (False, errs[0].message[:120]) if errs else gate.check(c)
            if not ok:
                rejected += 1
                out.write(s + "\n")           # leave the original untouched
                continue
            touched += 1
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")

    print(f"records enriched : {touched}")
    print(f"left untouched   : {rejected} (would not validate after enrichment)")
    print("fields added:")
    for k, n in sorted(added.items(), key=lambda kv: -kv[1]):
        print(f"   {n:>6}  {k}")
    print(f"conflicts (stored vs datasheet disagree): {len(conflicts)}")
    for c in conflicts[:8]:
        print(f"   {c['reference']} {c['field']}: stored={c['stored']} "
              f"datasheet={c['datasheet']}")
    print(" ", gate.summary())

    if not a.apply:
        tmp.unlink()
        print("\nDRY RUN — pass --apply to write")
        return 0
    # Optimistic-concurrency guard: this is a whole-file rewrite, and other campaigns
    # APPEND to catalogue files. If the source grew while we were transforming it, the
    # replace would silently drop those lines — so refuse instead.
    if LIVE.stat().st_size != before_size or \
            sum(1 for _ in LIVE.open(encoding="utf-8")) != before_lines:
        tmp.unlink()
        print("ABORTED: connectors.ndjson changed while building the enriched copy")
        return 1
    CONFLICTS.write_text(json.dumps(conflicts, indent=1, ensure_ascii=False))
    os.replace(tmp, LIVE)
    after = sum(1 for _ in LIVE.open(encoding="utf-8"))
    print(f"\nwrote {LIVE} ({before_lines} -> {after} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
