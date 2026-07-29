#!/usr/bin/env python3
"""Rescale the Vanguard chip-inductor rated currents that are milliamps stored as
amps (ABT #351 class B2).

    python3 scripts/fix_vanguard_ma_currents.py --dry-run
    python3 scripts/fix_vanguard_ma_currents.py

THE DEFECT IS THE VENDOR'S, NOT THE SCRAPE'S. ve1.com publishes the attribute as
"DC Current Max (A)" — R50029-NT-J carries 170. That part is 1.5 uH with a 1.7 ohm
max DCR; at 170 A it would dissipate 49 kW. Vanguard's own datasheet for the
series states "Current Rating (mA): 85 to 630". The value is right, the website's
unit label is wrong, and the corpus reproduced it faithfully. Re-importing from
the vendor API would therefore reintroduce the error — which is why this is a
rescale rather than a re-source.

ONLY SERIES WHOSE OWN DATASHEET SAYS mA ARE TOUCHED. scripts/check_vanguard_current_units.py
fetches each affected series' datasheet and reads the unit off it; four came back
mA (C50000, C26000, C27000, XT30000). Two came back A, and the rest were
undetermined — none of those are touched.

TWO GROUPS DELIBERATELY LEFT ALONE, both of which a blanket fix would have damaged:

  * CURRENT-SENSE TRANSFORMERS (136 rows: CS/SCS/CSN/SCSN series). These are FALSE
    POSITIVES of the "DCR x I^2 > 5 W" signature that found them. On a current
    transformer the rated current is the PRIMARY current and the DC resistance is
    a WINDING resistance — multiplying them is not a physical quantity, so the
    test says nothing. Their datasheets say "A", and that is correct.
  * AC1 / AC2 / AC3 air-core inductors (127 rows). The unit could not be read off
    their datasheets, so nothing is assumed.
  * XT30000 (196 rows). Its datasheet scan returned mA, but its corpus values are
    already amps (0.015-0.75, matching the vendor attribute). Excluded — see below.

GUARD: a row is only rewritten if the rescale actually makes it physical — DCR at
the new current must dissipate under 1 W. A rescale that does not fix the physics
is not the right explanation for that row, and it aborts instead.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_tdk_cmc import _build_registry, _load_magnetic_schema  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "vanguard_ma_rescale_audit.json"

# Proven by each series' own datasheet (see check_vanguard_current_units.py) AND
# confirmed against the corpus values themselves.
#
# XT30000 is deliberately NOT here even though its datasheet scan also returned
# "mA": all 196 of its corpus rows already hold 0.015-0.75, and the vendor
# attribute agrees (XT30048 -> 0.028). Those are already AMPS — 28 mA correctly
# stored. Rescaling them would turn 28 mA into 28 uA, i.e. break correct data. The
# datasheet hit was a stray "(mA)" elsewhere in that PDF.
MA_SERIES_PREFIXES = ("C50000", "C26000", "C27000")

# Second, independent guard against the XT30000 trap: only a value of at least
# 1 A is the mislabel signature. A chip inductor genuinely rated under 1 A is
# already stored correctly and must never be divided again. Belt and braces —
# the series list alone should be enough, but a wrong series entry would silently
# destroy correct data, and this makes that impossible.
MIN_A_TO_RESCALE = 1.0

MAX_W_AFTER = 1.0


def series_of(sku: str, catalog: dict) -> str | None:
    p = catalog.get(sku)
    if not p:
        return None
    s = p["attributes"].get("Series")
    return s if isinstance(s, str) else (",".join(s) if s else None)


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    cat_path = Path(argv[argv.index("--catalog") + 1]) if "--catalog" in argv else None
    if not cat_path or not cat_path.exists():
        print("need --catalog <vanguard.json> from scripts/pull_vanguard.py")
        return 2
    catalog = {p["sku"]: p for p in json.loads(cat_path.read_text())["products"]}
    validator = _load_magnetic_schema(_build_registry())

    tmp = DATA.with_suffix(".ndjson.tmp")
    audit, rescaled, skipped = [], 0, 0
    total = 0
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            total += 1
            if b"Vanguard" not in raw:
                out.write(raw)
                continue
            try:
                rec = json.loads(raw)
                info = rec["magnetic"]["manufacturerInfo"]
                el = info["datasheetInfo"]["electrical"][0]
            except Exception:
                out.write(raw)
                continue
            if "vanguard" not in str(info.get("name", "")).lower():
                out.write(raw)
                continue
            ref = str(info.get("reference"))
            series = series_of(ref, catalog)
            rated = (el.get("ratedCurrents") or [None])[0]
            if (not series or not series.startswith(MA_SERIES_PREFIXES)
                    or rated is None or rated < MIN_A_TO_RESCALE):
                out.write(raw)
                continue
            d = el.get("dcResistances")
            d = d[0] if d else el.get("dcResistance")
            dv = (d.get("maximum") if isinstance(d, dict) and d.get("maximum") is not None
                  else (d.get("nominal") if isinstance(d, dict) else None))
            new_rated = rated / 1000.0
            if dv is not None and dv * new_rated * new_rated > MAX_W_AFTER:
                print(f"ABORT: {ref} still implies {dv * new_rated * new_rated:.2f} W after rescale")
                out.close(); tmp.unlink(missing_ok=True)
                return 1
            el["ratedCurrents"] = [new_rated] + list(el["ratedCurrents"][1:])
            if list(validator.iter_errors(rec["magnetic"])):
                print(f"ABORT: {ref} would not validate after rescale")
                out.close(); tmp.unlink(missing_ok=True)
                return 1
            out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
            rescaled += 1
            audit.append({"reference": ref, "series": series, "wasA": rated, "nowA": new_rated,
                          "dcResistanceOhm": dv,
                          "impliedWattsBefore": round(dv * rated * rated, 1) if dv else None,
                          "impliedWattsAfter": round(dv * new_rated * new_rated, 4) if dv else None})
        out.flush()
        os.fsync(out.fileno())

    print(f"lines read : {total}")
    print(f"rescaled   : {rescaled}  (series {', '.join(MA_SERIES_PREFIXES)})")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
        return 0
    os.replace(tmp, DATA)
    AUDIT.write_text(json.dumps({"ticket": "ABT #351 class B2", "file": str(DATA),
                                 "evidence": "per-series datasheet states Current Rating (mA)",
                                 "rows": audit}, indent=1))
    print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
