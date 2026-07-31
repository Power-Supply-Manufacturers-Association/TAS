#!/usr/bin/env python3
"""ABT #403: make the pre-existing Würth connector records agree with their datasheets.

The 4,205 WE connectors imported from the Access-DB snapshot disagree with WE's own
per-part datasheets in 2,220 places. Some are unit errors (insulationResistance stored in
MΩ/GΩ into an ohms field — only three distinct values exist corpus-wide: 1.0, 100.0,
1000.0); some are stale or swapped values (61000813321 stored 105 °C where its datasheet
says 125, and its neighbour 61000821121 the exact reverse). The datasheet is the
controlled document, so it wins.

THE GATE THAT MAKES THIS SAFE: a datasheet is only allowed to correct a record if the
PDF's own text contains that order code. WE serves a per-order-code PDF, so this proves
the document describes the part being corrected rather than a family sheet. Without it,
one mis-served PDF would silently rewrite a good record.

Every overwrite is recorded — order code, field, old value, new value — in
staging/we_conn/reconcile_audit.json, so any correction can be reviewed or undone. Records
are re-validated against CONAS and re-checked by Blade Runner after correction; a record
that would become invalid is left exactly as it was.

  we_connectors_reconcile.py            # dry run
  we_connectors_reconcile.py --apply
"""
import argparse
import gzip
import json
import os
import sys
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
LIVE = TAS / "data" / "connectors.ndjson"
STAGE = TAS / "staging" / "we_conn"
SPECS = STAGE / "specs.jsonl"
DS = STAGE / "ds"
AUDIT = STAGE / "reconcile_audit.json"
RETRIEVED = "2026-07-31"
DS_URL = "https://www.we-online.com/components/products/datasheet/{}.pdf"

FIELDS = [
    ("ratedCurrentPerContact", "electrical", "ratedCurrentPerContact"),
    ("ratedVoltage", "electrical", "ratedVoltage"),
    ("dielectricWithstandingVoltage", "electrical", "dielectricWithstandingVoltage"),
    ("insulationResistance", "electrical", "insulationResistance"),
    ("matingCycles", "mechanical", "matingCycles"),
    ("operatingTemperature", "environmental", "operatingTemperature"),
]
REL_TOL = 0.02


def differs(a, b):
    if isinstance(a, dict) or isinstance(b, dict):
        return a != b
    try:
        return abs(float(a) - float(b)) > REL_TOL * max(abs(float(a)), abs(float(b)), 1e-12)
    except (TypeError, ValueError):
        return a != b


def names_itself(code):
    """Does the cached datasheet text actually contain this order code?"""
    f = DS / f"{code}.txt.gz"
    if not f.exists():
        return False
    with gzip.open(f, "rt", encoding="utf-8", errors="replace") as fh:
        return code in fh.read().replace(" ", "")


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

    verified = {c: names_itself(c) for c in specs}
    print(f"datasheets that name their own order code: {sum(verified.values())} "
          f"({len(verified) - sum(verified.values())} rejected as unverifiable)")

    before_lines = sum(1 for _ in LIVE.open(encoding="utf-8"))
    before_size = LIVE.stat().st_size

    audit = []
    corrected = {}
    added = {}
    touched = reverted = 0
    tmp = LIVE.with_suffix(".ndjson.reconcile_tmp")
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
            if "rth" not in (mi.get("name") or "") or not spec or not verified.get(ref):
                out.write(s + "\n")
                continue

            ds = mi.setdefault("datasheetInfo", {})
            changes = []
            for key, sec, field in FIELDS:
                if key not in spec:
                    continue
                tgt = ds.setdefault(sec, {})
                cur = tgt.get(field)
                if cur is None:
                    tgt[field] = spec[key]
                    added[f"{sec}.{field}"] = added.get(f"{sec}.{field}", 0) + 1
                    changes.append((f"{sec}.{field}", None, spec[key]))
                elif differs(cur, spec[key]):
                    tgt[field] = spec[key]
                    corrected[f"{sec}.{field}"] = corrected.get(f"{sec}.{field}", 0) + 1
                    changes.append((f"{sec}.{field}", cur, spec[key]))
            if "contactResistance" in spec:
                el = ds.setdefault("electrical", {})
                new = ({"maximum": spec["contactResistance"]}
                       if spec.get("contactResistanceIsMax", True)
                       else {"nominal": spec["contactResistance"]})
                cur = el.get("contactResistance")
                cur_v = cur.get("maximum", cur.get("nominal")) if isinstance(cur, dict) else cur
                if cur is None:
                    el["contactResistance"] = new
                    added["electrical.contactResistance"] = \
                        added.get("electrical.contactResistance", 0) + 1
                    changes.append(("electrical.contactResistance", None, new))
                elif cur_v is None or differs(cur_v, spec["contactResistance"]):
                    el["contactResistance"] = new
                    corrected["electrical.contactResistance"] = \
                        corrected.get("electrical.contactResistance", 0) + 1
                    changes.append(("electrical.contactResistance", cur, new))

            if not changes:
                out.write(s + "\n")
                continue

            ds.setdefault("provenance", []).append({
                "source": "manufacturerDatasheet",
                "sourceName": f"Würth Elektronik datasheet {ref} (reconciled against source)",
                "sourceUrl": DS_URL.format(ref),
                "retrievedDate": RETRIEVED})

            errs = sorted(v.iter_errors(c), key=lambda e: e.path)
            ok, why = (False, errs[0].message[:120]) if errs else gate.check(c)
            if not ok:
                reverted += 1
                out.write(s + "\n")           # original, untouched
                continue
            touched += 1
            for field, old, new in changes:
                audit.append({"reference": ref, "field": field, "old": old, "new": new})
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")

    print(f"\nrecords changed  : {touched}")
    print(f"left untouched   : {reverted} (would not validate after correction)")
    print("values CORRECTED (stored disagreed with datasheet):")
    for k, n in sorted(corrected.items(), key=lambda kv: -kv[1]):
        print(f"   {n:>6}  {k}")
    print("values ADDED (stored had none):")
    for k, n in sorted(added.items(), key=lambda kv: -kv[1]):
        print(f"   {n:>6}  {k}")
    print(" ", gate.summary())

    if not a.apply:
        tmp.unlink()
        print("\nDRY RUN — pass --apply to write")
        return 0
    if LIVE.stat().st_size != before_size or \
            sum(1 for _ in LIVE.open(encoding="utf-8")) != before_lines:
        tmp.unlink()
        print("ABORTED: connectors.ndjson changed while building the corrected copy")
        return 1
    AUDIT.write_text(json.dumps(audit, indent=1, ensure_ascii=False))
    os.replace(tmp, LIVE)
    after = sum(1 for _ in LIVE.open(encoding="utf-8"))
    print(f"\nwrote {LIVE} ({before_lines} -> {after} lines)")
    print(f"audit trail: {AUDIT} ({len(audit)} changes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
