#!/usr/bin/env python3
"""Un-swap capacitance bounds stored the wrong way round (ABT #356).

    python3 scripts/fix_swapped_capacitance_bounds.py [--dry-run]

150 Vishay Y5V rows store their tolerance band inverted:

    A103Z15Y5VF5TAA   nominal 10 nF   minimum 12 nF   maximum 8 nF

min/nom is 1.20 and max/nom is 0.80 — the +20 % figure landed in `minimum` and the
-20 % figure in `maximum`. Blade Runner flags both directions (CAP_TOLERANCE:
"capacitance minimum > nominal", "capacitance maximum < nominal"), 300 IMPOSSIBLE
findings across the 150 rows.

THE FIX IS A SWAP, AND DELIBERATELY NOTHING MORE. A minimum above its nominal is
impossible whatever the tolerance is, so exchanging the two values is provably an
improvement and invents no number — both figures are already in the record.

WHAT THIS DOES NOT DO, AND WHY. These part numbers carry a `Z` tolerance code
(A103**Z**15Y5V...), and in the EIA scheme Z means +80 %/-20 %, which would make the
correct maximum 1.8x nominal rather than 1.2x. It is tempting to write that. But the
catalogue's own evidence does not support it: of the Z-coded rows here, 151 are
stored at +-20 %, 18 at +-10 %, 54 at +-5 % and 4 at +-2 %, and NOT ONE is stored as
+80/-20. Meanwhile J, K and M are encoded exactly as the standard says (+-5, +-10,
+-20 across ~48,000 rows), so the encoder is not simply ignorant of EIA.

So "Z should be +80/-20" is a real question about 377 Z-coded rows and their source,
not something to settle silently inside a swap fix. Deciding it here would replace a
provable correction with a plausible guess — the exact substitution this whole
campaign exists to stop. Filed separately; this script restores a valid ordering and
leaves the magnitude alone.

Applies to any capacitor row where minimum > nominal or maximum < nominal, not only
the Y5V ones, so the same defect elsewhere is caught rather than left.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "capacitors.ndjson"
AUDIT = REPO / "staging" / "swapped_capacitance_bounds_audit.json"
TODAY = "2026-08-01"

NOTE = ("capacitance minimum/maximum were stored inverted (minimum above nominal, "
        "maximum below it) — the two values were exchanged, no value was recomputed "
        "(ABT #356)")


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #356", "date": TODAY, "note": NOTE, "fixed": [],
             "byManufacturer": Counter()}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b'"minimum"' in raw and b'"capacitance"' in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["capacitor"]["manufacturerInfo"]
                    di = mi["datasheetInfo"]
                    cap = (di.get("electrical") or {}).get("capacitance") or {}
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                nom, mn, mx = cap.get("nominal"), cap.get("minimum"), cap.get("maximum")
                ok = all(isinstance(v, (int, float)) and not isinstance(v, bool)
                         for v in (nom, mn, mx))
                if ok and (mn > nom or mx < nom):
                    # Only swap when swapping actually RESOLVES it. If the pair is
                    # inconsistent in some other way, leave it for a human.
                    if mx <= nom <= mn:
                        cap["minimum"], cap["maximum"] = mx, mn
                        part = di.get("part") or {}
                        ref = mi.get("reference") or part.get("partNumber")
                        audit["fixed"].append({
                            "reference": str(ref),
                            "dielectric": part.get("dielectricCode"),
                            "nominal": nom, "wasMinimum": mn, "wasMaximum": mx,
                            "nowMinimum": mx, "nowMaximum": mn})
                        audit["byManufacturer"][str(mi.get("name"))] += 1
                        line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"rows with swapped bounds fixed: {len(audit['fixed'])}")
    for k, v in audit["byManufacturer"].most_common():
        print(f"  {v:5}  {k}")
    for f in audit["fixed"][:3]:
        print(f"    {f['reference'][:24]:24} nom={f['nominal']:.3g} "
              f"min {f['wasMinimum']:.3g}->{f['nowMinimum']:.3g}  "
              f"max {f['wasMaximum']:.3g}->{f['nowMaximum']:.3g}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        audit["byManufacturer"] = dict(audit["byManufacturer"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
