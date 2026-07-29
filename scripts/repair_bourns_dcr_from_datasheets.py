#!/usr/bin/env python3
"""Recover the real DC resistance for the Bourns rows of ABT #351 class B1 by
reading each part's own datasheet.

    python3 scripts/repair_bourns_dcr_from_datasheets.py --dry-run
    python3 scripts/repair_bourns_dcr_from_datasheets.py

45 Bourns rows imply more than 50 W at their own rated current. They are not one
defect but three, and only the first is repaired here:

  dcr == ratedCurrent exactly (19 rows) — the RATED CURRENT WAS COPIED INTO THE
    DC RESISTANCE FIELD. Bourns' own 1110-series datasheet settles it:

        Part Number     L (uH) +-20%   DCR Ohm Max.   I, dc (A)
        1110-270K-RC    27             0.030          4.5

    the corpus row carries L 27 uH (correct), I 4.5 A (correct) and DCR 4.5
    (the current, duplicated). The real DCR is on the datasheet line.

  dcr == the MPN's EIA impedance code (13 rows) — the SRF pattern, e.g.
    SRF1206A-172Y with "DCR" 1700 ohm, where 172 encodes 1700. Same defect
    already fixed for TDK/Laird; NOT handled here because the correct value is an
    impedance point at a stated frequency and that frequency is not on hand for
    every part.

  neither (13 rows) — no single explanation; left for per-part work.

HOW THE DCR IS IDENTIFIED, WITHOUT GUESSING

Each part's datasheet is fetched from the URL the record already carries, the
table line for that exact part number is located, and its numbers are read. The
inductance and rated current are ALREADY KNOWN from the corpus row (both verified
correct for this group), so the DCR is the remaining number — and it must also be
a plausible DC resistance and must make the part physical (under 1 W at its rated
current). A part whose line cannot be found, or where no candidate satisfies all
of that, is SKIPPED and counted. Nothing is inferred from a series-typical value.
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
AUDIT = REPO / "staging" / "bourns_dcr_repair_audit.json"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")
MAX_W_AFTER = 1.0
_cache: dict[str, str] = {}


def datasheet_text(url: str) -> str:
    if url in _cache:
        return _cache[url]
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        path = f.name
    text = ""
    try:
        out = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, timeout=120)
        if out.returncode == 0:
            text = out.stdout.decode("utf-8", "replace")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    os.unlink(path)
    _cache[url] = text
    time.sleep(0.4)
    return text


def dcr_for(text: str, reference: str, inductance_h: float | None, rated_a: float) -> float | None:
    """The DC resistance on this part's own datasheet line, or None.

    Identified by elimination, never by assumption: the line's numbers are read,
    the known inductance and rated current are removed, and what remains must be
    a plausible DC resistance that also makes the part physical."""
    for line in text.splitlines():
        if reference not in line:
            continue
        nums = [float(x) for x in re.findall(r"\d+\.?\d*", line.replace(",", ""))]
        ind_uh = inductance_h * 1e6 if inductance_h else None
        cands = []
        for n in nums:
            if n <= 0:
                continue
            if ind_uh is not None and abs(n - ind_uh) < 0.01 * max(ind_uh, 1):
                continue                       # that is the inductance
            if abs(n - rated_a) < 1e-9:
                continue                       # that is the rated current
            if n * rated_a * rated_a > MAX_W_AFTER:
                continue                       # would not make the part physical
            cands.append(n)
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1:
            return min(cands)                  # DCR is the smallest such number
    return None


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    validator = _load_magnetic_schema(_build_registry())
    tmp = DATA.with_suffix(".ndjson.tmp")
    fixed, skipped, audit = 0, [], []

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            if b"Bourns" not in raw:
                out.write(raw); continue
            try:
                rec = json.loads(raw)
                info = rec["magnetic"]["manufacturerInfo"]
                el = info["datasheetInfo"]["electrical"][0]
            except Exception:
                out.write(raw); continue
            if "bourns" not in str(info.get("name", "")).lower():
                out.write(raw); continue
            d = el.get("dcResistances")
            plural = bool(d)
            d = d[0] if d else el.get("dcResistance")
            dv = (d.get("maximum") if isinstance(d, dict) and d.get("maximum") is not None
                  else (d.get("nominal") if isinstance(d, dict) else None))
            ra = (el.get("ratedCurrents") or [None])[0]
            ref = str(info.get("reference"))
            url = info.get("datasheetUrl")
            # only the "current copied into the DCR field" group
            if not (dv and ra and abs(dv - ra) < 1e-9 and dv * ra * ra > 50 and url):
                out.write(raw); continue
            try:
                real = dcr_for(datasheet_text(url), ref, (el.get("inductance") or {}).get("nominal"), ra)
            except Exception as e:
                skipped.append((ref, f"datasheet fetch/parse failed: {e}")); out.write(raw); continue
            if real is None:
                skipped.append((ref, "no DCR identifiable on the datasheet line")); out.write(raw); continue
            shaped = {"maximum": real}
            if plural:
                el["dcResistances"] = [shaped]
            else:
                el["dcResistance"] = shaped
            if list(validator.iter_errors(rec["magnetic"])):
                skipped.append((ref, "would not validate")); out.write(raw); continue
            out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
            fixed += 1
            audit.append({"reference": ref, "datasheet": url, "wasDcrOhm": dv, "nowDcrOhm": real,
                          "ratedCurrentA": ra, "impliedWattsBefore": round(dv * ra * ra, 1),
                          "impliedWattsAfter": round(real * ra * ra, 4),
                          "reason": "ratedCurrent had been copied into the DC resistance field"})
            print(f"  {ref:22} dcr {dv} -> {real}  ({dv*ra*ra:.0f} W -> {real*ra*ra:.3f} W)")
        out.flush(); os.fsync(out.fileno())

    print(f"\nrepaired {fixed}, skipped {len(skipped)}")
    for ref, why in skipped:
        print(f"  SKIP {ref}: {why}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
        return 0
    os.replace(tmp, DATA)
    AUDIT.write_text(json.dumps({"ticket": "ABT #351 B1 (Bourns)", "rows": audit,
                                 "skipped": [{"reference": r, "why": w} for r, w in skipped]}, indent=1))
    print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
