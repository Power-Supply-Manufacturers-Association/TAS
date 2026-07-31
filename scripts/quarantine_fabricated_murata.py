#!/usr/bin/env python3
"""Quarantine the fifth fabrication batch: Murata parts that do not exist (ABT #391).

    python3 scripts/quarantine_fabricated_murata.py PROBE.jsonl REIDENT.json [--dry-run]

WHAT THESE ARE. 3,782 records whose Murata part number cannot be found anywhere:

  * unknown to Murata's own resolver (cross-categories returns {} for them, and the
    same call returns a category hierarchy for every real part)
  * absent from Murata's COMPLETE 41,683-part ceramicCapacitorSMD catalogue, pulled
    in full for this check
  * 0 results at DigiKey, which does index discontinued parts
  * no real Murata part within one character that shares their capacitance, rated
    voltage, case size, dielectric and tolerance

3,049 of them are not structurally valid Murata codes at all — 14-15 characters
ending "AE"/"AB" where a real MLCC code is 17-18 (GRM188R71H104KA93D). Every
completion tried (adding the missing thickness digit, the packaging suffix) also
returns nothing.

THEY WERE COMPUTED, NOT MEASURED. The electrical data settles it. Across all 3,384
AE/AB rows:

    leakageCurrent      1 distinct value: 0        (no real capacitor leaks nothing)
    rippleCurrent       1 distinct value: 0
    dissipationFactor   2 distinct values: 0.001 and 0.025 — the textbook class-1 and
                        class-2 defaults, not measurements
    insulationResistance 8 distinct values
    thermalResistance   absent entirely

and ESR is a FORMULA, not a measurement. For the class-1 rows: 100 pF -> 60 ohm,
220 pF -> 27.27 ohm, 470 pF -> 12.77 ohm. Those products are 6000, 5999, 6002 — ESR
was computed as DF/(2*pi*f*C) and then clamped at exactly 60 ohm for class 1 and
160 ohm for class 2. The confirmed-real Murata rows in the same catalogue show
39-47 distinct values in each of these fields and a leakage current that is not zero.

HOW THEY PASSED FOR REAL. backfill_provenance.py stamped them
"Murata parametric (SimSurfing export)" — byte-identical to the provenance on
genuinely sourced Murata rows — because it infers provenance from a record's own URL
host and never fetches anything. Provenance was the one field that should have
distinguished them, and it was manufactured. That is the same laundering step
documented for the Coilcraft batch, and it is why this batch survived four previous
fabrication audits.

No committed script produces these rows; like the Coilcraft batch the generator was
written ad hoc and never reviewed. This is the FIFTH fabricated batch found in these
catalogues, after ABT #247, the phase2-5 generators of #256, and the Coilcraft
families of #351.

Quarantine, never delete: rows move to <catalogue>.quarantine_fabricated.ndjson with
a _validatorQuarantine block carrying this evidence.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "murata_fabricated_quarantine_audit.json"
DATE = "2026-07-31"

REASON = (
    "fabricated part - the part number exists nowhere. Unknown to Murata's own "
    "cross-categories resolver (which resolves every real part), absent from Murata's "
    "complete 41,683-part ceramicCapacitorSMD catalogue pulled in full for this check, "
    "0 results at DigiKey (which does index discontinued parts), and no real Murata "
    "part within edit distance 1 sharing its capacitance, rated voltage, case, "
    "dielectric and tolerance. 3,049 of the batch are not valid Murata codes at all "
    "(14-15 chars ending AE/AB where a real MLCC code is 17-18). The electrical data "
    "was COMPUTED, not measured: across all 3,384 AE/AB rows leakageCurrent is 0 and "
    "rippleCurrent is 0 with one distinct value each, dissipationFactor has just two "
    "values (0.001/0.025, the textbook class-1/class-2 defaults), thermalResistance is "
    "absent, and ESR follows DF/(2*pi*f*C) clamped at exactly 60 ohm (class 1) and "
    "160 ohm (class 2) — 100pF->60ohm, 220pF->27.27ohm, 470pF->12.77ohm, products of "
    "6000. Confirmed-real Murata rows in the same catalogue show 39-47 distinct values "
    "per field and non-zero leakage. They passed for real because "
    "backfill_provenance.py stamped them 'Murata parametric (SimSurfing export)', "
    "identical to genuinely sourced rows, inferring provenance from the record's own "
    "URL host without ever fetching it. Fifth fabrication batch, after ABT #247, the "
    "phase2-5 generators of #256, and the Coilcraft families of #351."
)
CODES = ["GEN_FABRICATED_MPN", "GEN_FABRICATED_COMPUTED_ELECTRICALS"]

PATHS = {"capacitors": ("capacitor",), "magnetics": ("magnetic",)}


def ref_of(mi):
    r = mi.get("reference")
    if r:
        return str(r)
    part = (mi.get("datasheetInfo") or {}).get("part") or {}
    p = part.get("partNumber")
    return str(p) if p else None


def main(argv):
    dry = "--dry-run" in argv
    probe = [json.loads(l) for l in Path(argv[0]).open(encoding="utf-8")]
    reident = json.loads(Path(argv[1]).read_text())
    rescued = set(reident.get("identified", {}))

    # unknown-to-vendor, excluding the ones re-identified from the real catalogue
    targets = {}
    for r in probe:
        if r.get("exists") or r.get("error"):
            continue
        if r["reference"] in rescued:
            continue
        targets[r["reference"]] = r["catalogue"]
    print(f"{len(targets)} references to quarantine "
          f"({len(rescued)} re-identified rows are kept)")

    audit = {"ticket": "ABT #391 (fifth fabrication batch — Murata)", "date": DATE,
             "reason": REASON, "codes": CODES, "moved": Counter(), "rows": []}

    for cat, keys in PATHS.items():
        path = DATA / f"{cat}.ndjson"
        if not path.exists():
            continue
        want = {k for k, v in targets.items() if v == cat}
        if not want:
            continue
        quar = DATA / f"{cat}.quarantine_fabricated.ndjson"
        tmp = path.with_suffix(".ndjson.tmp")
        taken = []
        with open(path, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                keep = True
                if b"Murata" in raw:
                    try:
                        rec = json.loads(raw)
                        o = rec
                        for k in keys:
                            o = o[k]
                        mi = o["manufacturerInfo"]
                        ref = ref_of(mi)
                    except Exception:                             # noqa: BLE001
                        ref = None
                    if ref in want:
                        rec["_validatorQuarantine"] = {
                            "date": DATE, "reason": REASON, "codes": CODES,
                            "messages": [f"part number {ref} is unknown to Murata's resolver, "
                                         f"absent from their full catalogue, and 0 results at "
                                         f"DigiKey"]}
                        taken.append((ref, json.dumps(rec, separators=(",", ":"))))
                        keep = False
                if keep:
                    out.write(raw)
            out.flush()
            os.fsync(out.fileno())

        print(f"  {cat:12} {len(taken)} rows -> {quar.name}")
        audit["moved"][cat] = len(taken)
        audit["rows"].extend(r for r, _ in taken[:600])
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            with open(quar, "a", encoding="utf-8") as q:
                for _, line in taken:
                    q.write(line + "\n")
                q.flush()
                os.fsync(q.fileno())
            os.replace(tmp, path)

    total = sum(audit["moved"].values())
    print(f"\n{total} rows quarantined")
    if dry:
        print("--dry-run: nothing moved")
    else:
        audit["moved"] = dict(audit["moved"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
