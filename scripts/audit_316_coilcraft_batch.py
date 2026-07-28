#!/usr/bin/env python3
"""ABT #316 follow-up: audit the WHOLE 2026-06-22 Coilcraft scrape for fabrication.

Blade Runner caught the 1,232 transformer rows only because their description noun
disagreed with their subtype. A fabricated INDUCTOR row says 'inductor' and matches
its subtype, so it sails through silently -- the blind spot documented in
BLADE_RUNNER_AUDIT.md. This audits the remaining batch directly, using the
signatures that convicted the transformer rows.

Read-only: reports, quarantines nothing.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

SRC = Path.home() / "PSMA" / "TAS" / "data" / "magnetics.ndjson"


def main():
    rows = []
    with SRC.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line or "Coilcraft" not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            mag = o.get("magnetic") or {}
            mi = mag.get("manufacturerInfo") or {}
            if mi.get("name") != "Coilcraft":
                continue
            ds = mi.get("datasheetInfo") or {}
            prov = ds.get("provenance") or []
            rows.append({
                "line": n, "mag": mag, "mi": mi, "ds": ds,
                "desc": (ds.get("part") or {}).get("description"),
                "ref": mi.get("reference"),
                "el": ds.get("electrical") or [],
                "prov": prov,
            })

    print(f"Coilcraft rows remaining in magnetics.ndjson: {len(rows)}")
    if not rows:
        return 0

    # --- provenance batches -------------------------------------------------
    batches = Counter()
    for r in rows:
        for p in r["prov"]:
            if isinstance(p, dict):
                batches[f"{p.get('retrievedDate')} | {str(p.get('sourceName'))[:55]}"] += 1
    print("\n--- provenance batches ---")
    for k, v in batches.most_common(10):
        print(f"  {v:6d}  {k}")

    june = [r for r in rows
            if any(isinstance(p, dict) and p.get("retrievedDate") == "2026-06-22"
                   for p in r["prov"])]
    print(f"\nrows from the 2026-06-22 batch: {len(june)}")

    # --- signature 1: missing MPN -------------------------------------------
    no_ref = [r for r in june if not r["ref"]]
    print(f"\n[SIG 1] no manufacturerInfo.reference : {len(no_ref)} / {len(june)}")

    # --- signature 2: perfect rectangle -------------------------------------
    desc_counts = Counter(r["desc"] for r in june)
    print(f"[SIG 2] distinct descriptions          : {len(desc_counts)}")
    if desc_counts:
        vals = Counter(desc_counts.values())
        print(f"         rows-per-description histogram: {dict(list(vals.items())[:8])}")
        if len(vals) == 1:
            print("         *** PERFECT RECTANGLE — every description has an identical "
                  "row count (synthesis signature) ***")

    # --- signature 3: description contradicts own inductance ----------------
    match = mismatch = nocheck = 0
    examples = []
    for r in june:
        m = re.search(r"([\d.]+)\s*µH", r["desc"] or "")
        L = None
        for e in r["el"]:
            if isinstance(e, dict) and e.get("inductance") is not None:
                v = e["inductance"]
                L = v.get("nominal") if isinstance(v, dict) else v
                break
        if not m or L is None:
            nocheck += 1
            continue
        if abs(float(m.group(1)) * 1e-6 - L) / max(float(m.group(1)) * 1e-6, 1e-15) < 0.02:
            match += 1
        else:
            mismatch += 1
            if len(examples) < 6:
                examples.append(f"desc={m.group(1)}uH vs electrical={L*1e6:.0f}uH")
    print(f"[SIG 3] description uH matches data    : {match}")
    print(f"         description uH CONTRADICTS data: {mismatch}   (not checkable: {nocheck})")
    for e in examples:
        print(f"           {e}")

    # --- signature 4: repeated-value grids ----------------------------------
    print("\n[SIG 4] value repetition (synthesis leaves identical values in blocks)")
    for field in ("inductance", "dcResistance", "saturationCurrentPeak"):
        c = Counter()
        for r in june:
            for e in r["el"]:
                if not isinstance(e, dict):
                    continue
                v = e.get(field)
                if isinstance(v, dict):
                    v = v.get("nominal")
                if v is not None:
                    c[v] += 1
        if not c:
            print(f"  {field:22} : absent on all rows")
            continue
        top = c.most_common(3)
        print(f"  {field:22} : {len(c)} distinct, top repeats {top}")

    # --- per-family verdict -------------------------------------------------
    print("\n--- 2026-06-22 Coilcraft rows by subtype ---")
    st = Counter()
    for r in june:
        for e in r["el"]:
            if isinstance(e, dict) and e.get("subtype"):
                st[e["subtype"]] += 1
    for k, v in st.most_common():
        print(f"  {v:6d}  {k}")

    print("\n" + "=" * 70)
    suspicious = len(no_ref) == len(june) and len(june) > 0
    print("VERDICT INPUT: every row in this batch lacks an MPN"
          if suspicious else
          "VERDICT INPUT: batch has real MPNs on at least some rows")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
