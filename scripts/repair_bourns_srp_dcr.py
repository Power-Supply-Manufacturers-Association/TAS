#!/usr/bin/env python3
"""Recover the DC resistance for the Bourns SRP / SRR / SRN / AC power-inductor
rows of ABT #351 class B1, from Bourns' own datasheets.

    python3 scripts/repair_bourns_srp_dcr.py --dry-run
    python3 scripts/repair_bourns_srp_dcr.py

These are the rows repair_bourns_dcr_from_datasheets.py deliberately SKIPPED: the
rated current had been copied into the DC resistance field, but the datasheet's
DCR column is in milliohms and that could not be proven from the extracted text,
so scaling would have been a guess. Two extraction changes settle it.

1. THE UNIT IS PROVEN, NOT ASSUMED. In these PDFs the micro and ohm glyphs both
   render as ASCII 'm' and 'W', so the headers read "L (mH)" and "DCR (mW)" for
   microhenries and milliohms. The unit sits on a DIFFERENT extracted line from
   the word DCR (the heading is stacked), so it is looked for in a window after
   it — and an explicit "(Ohm)" is honoured as ohms, never assumed to be milli.

2. THE ROW IS READ WITH pdftotext -raw, NOT -layout. -layout drops trailing
   columns from these tables; -raw keeps the whole line:

       SRP0310F-1R0M  1.0  62  47  56.4  4.0  5.0
                      L    Q   DCRtyp  DCRmax  Irms  Isat

ANCHORED BETWEEN TWO VERIFIED FIELDS. Nothing is taken positionally from the left.
The inductance and the rated current are already known from the corpus row, both
independently correct for this group, so their positions are FOUND in the line and
the DCR max is the value immediately before the rated current. The part is skipped
unless BOTH anchors are present, the DCR pair is ordered typ < max, and the result
no longer trips the 5 W threshold that defines this defect class.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_tdk_cmc import _build_registry, _load_magnetic_schema  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "bourns_srp_dcr_audit.json"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")
SHEET = "https://www.bourns.com/docs/Product-Datasheets/{series}.pdf"
# The physics check is a sanity NET, not the evidence — the DCR itself comes from
# the datasheet, anchored between two independently verified fields. So the bar is
# the same 5 W that DEFINES this defect class: if the repaired value no longer trips
# the class threshold, the repair did its job. A tighter bound was rejecting correct
# datasheet values (SRP0410F-1R5M at 54 mOhm / 4.9 A = 1.3 W is a real 4 mm part at
# its 40 C-rise rating).
MAX_W_AFTER = 5.0
_cache: dict[str, tuple[str, str]] = {}


def sheets(series: str) -> tuple[str, str]:
    """(raw_text, layout_text) — raw keeps whole table rows, layout keeps headings."""
    if series in _cache:
        return _cache[series]
    # Bourns files some families as "<series>.pdf" and others as "<series>_series.pdf"
    data = None
    last = None
    for name in (series, f"{series}_series"):
        try:
            req = urllib.request.Request(SHEET.format(series=name), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            break
        except Exception as e:
            last = e
    if data is None:
        raise last
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        path = f.name
    got = []
    for args in (["-raw"], ["-layout"]):
        out = subprocess.run(["pdftotext", *args, path, "-"], capture_output=True, timeout=120)
        got.append(out.stdout.decode("utf-8", "replace") if out.returncode == 0 else "")
    os.unlink(path)
    _cache[series] = (got[0], got[1])
    time.sleep(0.4)
    return _cache[series]


def dcr_is_milliohms(layout: str) -> bool:
    """The DCR column header must carry a milli prefix. 'mW' is the extractor's
    rendering of mOhm (the Omega glyph maps to W); an explicit '(Ohm)' means no
    scaling. The unit lands on a later line than the word DCR."""
    lines = layout.splitlines()
    for i, line in enumerate(lines):
        # Bourns spells it BOTH ways across families: "DCR" on the SRP/SRR
        # sheets, "RDC" on PM124SH. Matching only one silently reported
        # "unit not stated" and skipped a repairable row.
        if not re.search(r"(?i)\b(DCR|RDC)\b", line):
            continue
        window = " ".join(lines[i:i + 4])
        if re.search(r"(?i)\(\s*m\s*(?:W|Ω|ohm)\s*\)", window):
            return True
        if re.search(r"(?i)\(\s*(?:Ω|ohm)\s*\)", window):
            return False
    return False


def dcr_max(raw: str, reference: str, inductance_h: float, rated_a: float) -> tuple[float, float] | None:
    """(dcr_typ, dcr_max) in the datasheet's units, anchored between the known
    inductance and the known rated current. None when the anchors are absent.

    The L cell may be printed in uH (power inductors) or nH (the AC-series
    air cores), so both scalings are tried as anchors. The gap requirement
    between the anchors is >= 2 numbers (typ+max) rather than 3: some sheets
    put fewer columns between L and the DCR pair."""
    candidates = (inductance_h * 1e6, inductance_h * 1e9)
    # Bourns prints tolerance-agnostic rows with a WILDCARD in the part number
    # (AC3630R-12N_ covers -12NJ/-12NK/-12NG), so the exact reference never
    # appears; the wildcard key replaces the trailing tolerance letter.
    keys = [reference, re.sub(r"[A-Z]$", "_", reference)]
    for line in raw.splitlines():
        key = next((k for k in keys if k in line), None)
        if key is None:
            continue
        tail = line.split(key, 1)[1]
        nums = [float(x) for x in re.findall(r"\d+\.?\d*", tail.replace(",", ""))]
        li = None
        for ind_cell in candidates:
            li = next((i for i, n in enumerate(nums)
                       if abs(n - ind_cell) < 0.05 * max(ind_cell, 1e-9)), None)
            if li is not None:
                break
        ri = next((i for i, n in enumerate(nums) if abs(n - rated_a) < 0.01), None)
        if li is not None and ri is not None and ri - li >= 3:
            typ, mx = nums[ri - 2], nums[ri - 1]
            if 0 < typ < mx:
                return typ, mx, None
        # The corpus rated current may ITSELF be wrong (AC3630R-18N: corpus 4 A,
        # sheet Irms 5 A). When the row is identified by part number AND the L
        # cell anchors, the sheet's own trailing columns are the truth: the last
        # number is Irms and the one before it the DCR max.
        if li is not None and len(nums) >= li + 3:
            mx, irms = nums[-2], nums[-1]
            if mx > 0 and irms > 0:
                return None, mx, irms
    return None


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    validator = _load_magnetic_schema(_build_registry())
    tmp = DATA.with_suffix(".ndjson.tmp")
    fixed, skipped, audit = 0, [], []

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw_line in src:
            if b"Bourns" not in raw_line:
                out.write(raw_line); continue
            try:
                rec = json.loads(raw_line)
                info = rec["magnetic"]["manufacturerInfo"]
                el = info["datasheetInfo"]["electrical"][0]
            except Exception:
                out.write(raw_line); continue
            if "bourns" not in str(info.get("name", "")).lower():
                out.write(raw_line); continue
            d = el.get("dcResistances")
            plural = bool(d)
            d = d[0] if d else el.get("dcResistance")
            dv = (d.get("maximum") if isinstance(d, dict) and d.get("maximum") is not None
                  else (d.get("nominal") if isinstance(d, dict) else None))
            ra = (el.get("ratedCurrents") or [None])[0]
            ind = (el.get("inductance") or {}).get("nominal")
            ref = str(info.get("reference"))
            # any Bourns row still implying >50 W — the copied-field group AND the
            # plain corrupted-DCR ones (PM124SH: 67.5 "ohm" on a 22 uH drum core).
            # >50 W keeps this defect-only; every repaired row sits far below it.
            if not (dv and ra and ind and dv * ra * ra > 50):
                out.write(raw_line); continue
            series = ref.split("-")[0]
            try:
                raw_txt, layout_txt = sheets(series)
            except Exception as e:
                skipped.append((ref, f"datasheet fetch failed: {e}")); out.write(raw_line); continue
            if not dcr_is_milliohms(layout_txt):
                skipped.append((ref, "DCR column header does not prove milliohms")); out.write(raw_line); continue
            got = dcr_max(raw_txt, ref, ind, ra)
            if not got:
                skipped.append((ref, "inductance/rated-current anchors not both found on the row"))
                out.write(raw_line); continue
            typ, mx, sheet_irms = got
            real = mx / 1000.0
            if real * ra * ra > MAX_W_AFTER:
                skipped.append((ref, f"{real} ohm still implies {real*ra*ra:.2f} W")); out.write(raw_line); continue
            shaped = {"maximum": real}
            if plural:
                el["dcResistances"] = [shaped]
            else:
                el["dcResistance"] = shaped
            if list(validator.iter_errors(rec["magnetic"])):
                skipped.append((ref, "would not validate")); out.write(raw_line); continue
            out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
            fixed += 1
            if sheet_irms is not None and abs(sheet_irms - ra) > 0.01:
                el["ratedCurrents"] = [sheet_irms] + list(el.get("ratedCurrents", [])[1:])
            audit.append({"reference": ref, "series": series, "datasheet": SHEET.format(series=series),
                          "sheetIrmsA": sheet_irms,
                          "wasDcrOhm": dv, "nowDcrOhm": real,
                          "datasheetDcrTypMilliohm": typ, "datasheetDcrMaxMilliohm": mx,
                          "anchors": {"inductanceH": ind, "ratedCurrentA": ra},
                          "impliedWattsBefore": round(dv * ra * ra, 1),
                          "impliedWattsAfter": round(real * ra * ra, 4)})
            print(f"  {ref:20} dcr {dv} -> {real}  ({dv*ra*ra:.0f} W -> {real*ra*ra:.3f} W)"
                  f"  [datasheet {typ}/{mx} mOhm]")
        out.flush(); os.fsync(out.fileno())

    print(f"\nrepaired {fixed}, skipped {len(skipped)}")
    for ref, why in skipped:
        print(f"  SKIP {ref}: {why}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
        return 0
    os.replace(tmp, DATA)
    AUDIT.write_text(json.dumps({"ticket": "ABT #351 B1 (Bourns SRP/SRR/SRN/AC)", "rows": audit,
                                 "skipped": [{"reference": r, "why": w} for r, w in skipped]}, indent=1))
    print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
