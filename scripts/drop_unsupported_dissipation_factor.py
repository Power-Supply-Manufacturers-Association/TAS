#!/usr/bin/env python3
"""Remove dissipation factors that provably did not come from a datasheet (ABT #434).

    python3 scripts/drop_unsupported_dissipation_factor.py [--dry-run]

8,860 of 253,830 capacitor rows carry a dissipationFactor, and not one provenance entry
in the corpus names dissipationFactor among the fields it supplied. That alone proves
nothing - `fields` is optional, and a blanket provenance implicitly covers everything -
so this removes only what can be shown to be wrong, and leaves alone what might be right.

WHAT A SINGLE REPEATED VALUE DOES AND DOES NOT PROVE. A whole series sharing one tan-delta
is NORMAL: datasheets routinely specify "tan-delta <= 0.025" once for an entire X7R series,
so every part in it legitimately carries the same number. A degenerate column is therefore
NOT evidence of fabrication on its own, and treating it as such would delete thousands of
real datasheet limits. What IS evidence is one value spanning dielectrics whose real
limits differ by a factor of 25 to 50 - no datasheet says that.

THE THREE RULES, each with its proof.

1. A Class 1 ceramic cannot have a Class 2 dissipation factor. C0G/NP0/U2J/X8G are
   temperature-compensating dielectrics whose tan-delta limit is 0.1 % (0.001); 0.025 is
   the X7R figure, 25x higher. The corpus's own majority agrees with the physics rather
   than with these rows: TDK stores 0.001 for 720 of 720 class-1 rows, YAGEO 355 of 355,
   Murata 10 of 10, and even inside the two offending manufacturers the majority is
   correct - Samsung 440 right against 182 wrong, KEMET 400 against 141. 326 rows.

2. Samsung's CL import used one constant for every dielectric it holds. All 466 rows carry
   0.025 across C0G, X7R, X5R, X6S, Y5V, X7S and X7T - dielectrics whose limits span
   0.001 to 0.05. It is not a series limit, because these are not one series. And it is
   contradicted directly: Samsung's own specification sheet for CL10F104ZB8NNNC, one of
   these rows, states "Tan delta (DF) 0.05 max", double what is stored. 466 rows.

3. KYOCERA AVX's column is one value across two different capacitor classes - 0.025 for
   all 206 rows, including 3 filed as ceramic-class-1. A number that does not change when
   the dielectric class changes is not a measurement of either. 206 rows.

WHAT IS DELIBERATELY LEFT. TDK, Murata and most of KEMET map dissipation factor to
dielectric class CORRECTLY (C0G 0.001, X7R/X5R 0.025), which is what a series limit looks
like. Panasonic (17 distinct values), Nichicon (7), Chemi-Con (6) and Rubycon (6) show a
real per-part spread across electrolytic technologies. Knowles, Walsin and WIMA hold a
single value each, but confined to ONE technology, which a series datasheet can honestly
produce. None of those can be shown to be wrong, so none is touched - the standing gap in
DF provenance stays open in ABT #434 rather than being resolved by deletion.

NOTHING IS SUBSTITUTED. No class-typical figure is written in place of a removed one; that
would replace a wrong number with a plausible one and lose the fact that we do not know.
An absent dissipationFactor is the honest state, and consumers already handle it - 96.5 %
of the catalogue has never had one.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "capacitors.ndjson"
AUDIT = REPO / "staging" / "unsupported_dissipation_factor_audit.json"
TODAY = "2026-08-01"

CLASS1_CODES = {"C0G", "NP0", "COG", "U2J", "X8G", "CH", "CG", "SL"}
CLASS1_LIMIT = 0.002          # Class 1 tan-delta is 0.001; 0.002 leaves room for rounding

REASONS = {
    "class1-impossible":
        "Class 1 dielectric (tan-delta limit 0.001) carrying a Class 2 dissipation factor",
    "samsung-blanket":
        "Samsung CL import stored one value across dielectrics whose limits differ by 50x, "
        "and Samsung's own sheet for CL10F104ZB8NNNC states 0.05 where 0.025 was stored",
    "kyocera-blanket":
        "one value across two capacitor classes - unchanged by the dielectric it describes",
}


def main(argv):
    dry = "--dry-run" in argv
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #434", "date": TODAY, "reasons": REASONS,
             "removed": [], "byReason": Counter(), "byManufacturer": Counter()}

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"dissipationFactor" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["capacitor"]["manufacturerInfo"]
                    di = mi.get("datasheetInfo") or {}
                    el = di.get("electrical") or {}
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                df = el.get("dissipationFactor")
                if isinstance(df, (int, float)) and not isinstance(df, bool):
                    man = str(mi.get("name") or "")
                    ref = str(mi.get("reference") or "")
                    part = di.get("part") or {}
                    tech = str(part.get("technology") or "")
                    code = str(part.get("dielectricCode") or "").upper()
                    reason = None
                    if (tech == "ceramic-class-1" or code in CLASS1_CODES) \
                            and df > CLASS1_LIMIT:
                        reason = "class1-impossible"
                    elif "Samsung" in man and ref.startswith("CL"):
                        reason = "samsung-blanket"
                    elif man.startswith("KYOCERA AVX"):
                        reason = "kyocera-blanket"
                    if reason:
                        el.pop("dissipationFactor")
                        audit["removed"].append(
                            {"reference": ref, "manufacturer": man, "was": df,
                             "technology": tech, "dielectricCode": code or None,
                             "reason": reason})
                        audit["byReason"][reason] += 1
                        audit["byManufacturer"][man] += 1
                        line = json.dumps(rec, separators=(",", ":"),
                                          ensure_ascii=False).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    print(f"dissipation factors removed: {len(audit['removed'])}")
    for k, v in audit["byReason"].most_common():
        print(f"     {v:5}  {k}  — {REASONS[k][:70]}")
    print("   by manufacturer:")
    for k, v in audit["byManufacturer"].most_common(6):
        print(f"     {v:5}  {k}")
    for r in audit["removed"][:3]:
        print(f"       e.g. {r['reference'][:24]:24} {r['dielectricCode'] or r['technology']:18} "
              f"was {r['was']}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        audit["byReason"] = dict(audit["byReason"])
        audit["byManufacturer"] = dict(audit["byManufacturer"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
