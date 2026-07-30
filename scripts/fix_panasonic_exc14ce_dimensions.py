#!/usr/bin/env python3
"""Fix the 17 Panasonic EXC14C* rows whose package is 100x too big (ABT #386).

    python3 scripts/fix_panasonic_exc14ce_dimensions.py [--dry-run]

Six series are affected — EXC14CE / CG / CH / CP / CT / CX — and all six datasheets
were fetched and read individually rather than one being assumed from its siblings.
Each states the 0302 package outright:

    "Small and thin (L 0.85 mm x W 0.65 mm x H 0.45 mm)"
    part table: 0.65 +-0.05 | 0.85 +-0.05 | 0.45 +-0.05

The corpus held length 0, width 85.0 mm, height 0.45 mm. Two faults in one field
set: the length was written into WIDTH, and it lost a factor of 100 on the way
(0.85 -> 85.0), leaving the length itself empty. The result claims a chip common-mode
filter 85 mm wide and 0.45 mm tall — an aspect ratio of 190:1 that no check based on
electrical values could ever notice.

This is the same root fault as the 633 Abracon rows repaired alongside it: an
importer with a value it could not place, writing 0 rather than leaving the field
out. A zero dimension is not a harmless gap — every check that divides by surface
area silently SKIPS a row whose area is zero, so these rows have never been tested
by the physics that would have caught the 85 mm width immediately.

Panasonic times out for a plain client; the datasheet was retrieved through the
browser session that clears the gate.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validator" / "build-ninja"))
from extract_tdk_cmc import _build_registry, _load_magnetic_schema  # noqa: E402
import tas_validator  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "abt386_panasonic_dimension_audit.json"
TODAY = "2026-07-31"

DIMS = {"length": 0.00085, "width": 0.00065, "height": 0.00045}

# Every EXC14C* series in the corpus is the same 0302 package, and each sheet was
# fetched and read separately rather than inferred from a sibling — all six state
# "L 0.85 mm x W 0.65 mm x H 0.45 mm" and carry the same +-0.05 package table.
SHEETS = {
    "EXC14CE": "AWE0000C147.pdf",
    "EXC14CG": "ast-ind-288838.pdf",
    "EXC14CH": "AWE0000C264.pdf",
    "EXC14CP": "AWE0000C300.pdf",
    "EXC14CT": "AWE0000C267.pdf",
    "EXC14CX": "AWE0000C270.pdf",
}
BASE = "https://industrial.panasonic.com/cdbs/www-data/pdf/AWE0000/"


def provenance_for(ref):
    for series, sheet in SHEETS.items():
        if ref.startswith(series):
            return {"source": "manufacturerDatasheet",
                    "sourceName": f"Panasonic {series} datasheet {sheet.replace('.pdf','')}, "
                                  f"package table (fetched and read)",
                    "sourceUrl": BASE + sheet,
                    "retrievedDate": TODAY}
    return None


def nom(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("nominal", "maximum", "minimum"):
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
    return None


def main(argv):
    dry = "--dry-run" in argv
    validator = _load_magnetic_schema(_build_registry())
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #386 (Panasonic EXC14C*)", "date": TODAY,
             "datasheets": {s: BASE + f for s, f in SHEETS.items()},
             "repaired": [], "skipped": []}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            wrote = False
            if b"EXC14C" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["magnetic"]["manufacturerInfo"]
                    ref = str(mi.get("reference"))
                    di = mi["datasheetInfo"]
                    mech = di.get("mechanical") or {}
                except Exception:
                    ref = None
                prov = provenance_for(ref) if ref else None
                if prov:
                    was = {k: nom(mech.get(k)) for k in ("length", "width", "height")}
                    if all(was.get(k) is not None and abs(was[k] - v) < 1e-9
                           for k, v in DIMS.items()):
                        audit["skipped"].append({"reference": ref, "why": "already correct"})
                    else:
                        for k, v in DIMS.items():
                            mech[k] = {"nominal": v}
                        di["mechanical"] = mech
                        di["provenance"] = [prov]
                        if list(validator.iter_errors(rec["magnetic"])):
                            audit["skipped"].append({"reference": ref, "why": "schema-invalid"})
                        else:
                            vd = tas_validator.validate(json.dumps(rec))
                            bad = [str(f.code) for f in vd.findings
                                   if str(f.severity).upper() == "IMPOSSIBLE"]
                            if bad:
                                audit["skipped"].append({"reference": ref, "why": f"IMPOSSIBLE {bad}"})
                            else:
                                out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                                wrote = True
                                audit["repaired"].append(
                                    {"reference": ref,
                                     "wasMm": {k: (v * 1000 if v is not None else None)
                                               for k, v in was.items()},
                                     "nowMm": {k: v * 1000 for k, v in DIMS.items()}})
                                print(f"  {ref:16} {[round(v*1000,2) if v is not None else None for v in was.values()]}"
                                      f" -> [0.85, 0.65, 0.45] mm")
            if not wrote:
                out.write(raw)
        out.flush()
        os.fsync(out.fileno())

    print(f"\nrepaired {len(audit['repaired'])}, skipped {len(audit['skipped'])}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("--dry-run: nothing replaced")
    else:
        os.replace(tmp, DATA)
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
