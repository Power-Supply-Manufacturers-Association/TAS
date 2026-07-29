#!/usr/bin/env python3
"""Repair the Bourns SRF common-mode-choke rows from Bourns' own datasheets
(ABT #351 B1 / #286).

    python3 scripts/repair_bourns_srf_cmc.py --dry-run
    python3 scripts/repair_bourns_srf_cmc.py

13 SRF rows carry the ABT #286 defect in its complete form: the part number's
impedance code sitting in dcResistance, AND the 10 uH placeholder inductance.

    corpus  SRF7038A-101Y   "DCR" 100 ohm   L 10 uH (placeholder)   I 14 A
    Bourns  SRF7038A-101Y   Z@100MHz 100 ohm, L 0.83 uH @100 kHz, DCR 5 mOhm, I 14 A

So unlike the Laird rows — where the impedance could only be deleted because its
measurement frequency was unknown for most parts — here the datasheet names the
frequency in the column header, which means the number can be FILED CORRECTLY as
an impedance point instead of merely removed. Four fields are recovered per part:

    impedancePoints  <- Z typ at the stated frequency (100 MHz)
    inductance       <- the real L at its stated test condition, replacing 10 uH
    dcResistances    <- the real DCR
    ratedCurrents    <- verified against the corpus value, not overwritten blindly

UNITS: the DCR column header renders as "DCR (mW)" in the extracted text. That is
a font-encoding artifact for mOhm (Omega -> W), and physics confirms it: 5 at 14 A
is 980 W as ohms and 0.98 W as milliohms. The script REQUIRES the header to carry
the milli prefix before scaling; if a datasheet's header does not, that part is
skipped rather than assumed.

ANCHORING: the row is not parsed positionally. The part's line is located, and the
number equal to the MPN's EIA code (101 -> 100) is found; the three numbers after
it are L, DCR and rated current. The parse is then CHECKED against the corpus —
the rated current must agree — and the part is skipped if it does not. A datasheet
whose layout differs is skipped and counted, never guessed at.
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
AUDIT = REPO / "staging" / "bourns_srf_repair_audit.json"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")
# bourns.com/docs is NOT Cloudflare-gated; the mouser mirrors in the records are.
SHEET = "https://www.bourns.com/docs/Product-Datasheets/{series}.pdf"
_cache: dict[str, str] = {}


def sheet_text(series: str) -> str:
    if series in _cache:
        return _cache[series]
    req = urllib.request.Request(SHEET.format(series=series), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        path = f.name
    out = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, timeout=120)
    os.unlink(path)
    text = out.stdout.decode("utf-8", "replace") if out.returncode == 0 else ""
    _cache[series] = text
    time.sleep(0.4)
    return text


def eia_code(reference: str) -> int | None:
    m = re.search(r"-(\d{3})[A-Z]", reference)
    return int(m.group(1)[:2]) * 10 ** int(m.group(1)[2]) if m else None


def dcr_is_milliohms(text: str) -> bool:
    """The DCR column header must carry a milli prefix. 'mW' is the extractor's
    rendering of mOhm; a bare 'DCR (Ohm)' would mean no scaling.

    The header is a stacked table heading, so 'DCR' and its '(mW)' unit land on
    DIFFERENT extracted lines — the unit is looked for in the two lines after the
    one naming DCR, not adjacent to it."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not re.search(r"(?i)\bDCR\b", line):
            continue
        window = " ".join(lines[i:i + 3])
        if re.search(r"(?i)\(\s*m\s*(?:W|Ω|ohm)\s*\)", window):
            return True
        if re.search(r"(?i)\(\s*(?:Ω|ohm)\s*\)", window):
            return False          # explicitly ohms — do not scale
    return False


def impedance_frequency(text: str) -> float | None:
    m = re.search(r"(?i)Z\s*\([^)]*\)\s*@\s*([\d.]+)\s*(k|M|G)?Hz", text)
    if not m:
        return None
    return float(m.group(1)) * {"k": 1e3, "M": 1e6, "G": 1e9, None: 1.0}[m.group(2)]


def parse_row(text: str, reference: str, code: int) -> dict | None:
    for line in text.splitlines():
        if reference not in line:
            continue
        nums = [float(x) for x in re.findall(r"\d+\.?\d*", line.replace(",", ""))]
        for i, n in enumerate(nums):
            if abs(n - code) < 0.01 and i + 3 < len(nums):
                return {"zTypOhm": n, "inductanceUh": nums[i + 1],
                        "dcrRaw": nums[i + 2], "ratedA": nums[i + 3]}
    return None


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    validator = _load_magnetic_schema(_build_registry())
    tmp = DATA.with_suffix(".ndjson.tmp")
    fixed, skipped, audit = 0, [], []

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            if b"SRF" not in raw or b"Bourns" not in raw:
                out.write(raw); continue
            try:
                rec = json.loads(raw)
                info = rec["magnetic"]["manufacturerInfo"]
                el = info["datasheetInfo"]["electrical"][0]
            except Exception:
                out.write(raw); continue
            ref = str(info.get("reference"))
            d = el.get("dcResistances")
            plural = bool(d)
            d = d[0] if d else el.get("dcResistance")
            dv = (d.get("maximum") if isinstance(d, dict) and d.get("maximum") is not None
                  else (d.get("nominal") if isinstance(d, dict) else None))
            ra = (el.get("ratedCurrents") or [None])[0]
            code = eia_code(ref)
            if not (dv and ra and code and abs(dv - code) < 0.01):
                out.write(raw); continue
            series = ref.split("-")[0]
            try:
                text = sheet_text(series)
            except Exception as e:
                skipped.append((ref, f"datasheet fetch failed: {e}")); out.write(raw); continue
            row = parse_row(text, ref, code)
            if not row:
                skipped.append((ref, "part line not found / layout differs")); out.write(raw); continue
            # The parse must corroborate the corpus rated current, in A or in mA
            # (some SRF sheets print the current column in milliamps). Anything
            # else means the columns were not understood, and the part is skipped.
            if not (abs(row["ratedA"] - ra) < 0.01 or abs(row["ratedA"] - ra * 1000) < 1.0):
                skipped.append((ref, f"parse disagrees with corpus rated current "
                                     f"({row['ratedA']} vs {ra}) — layout not understood"))
                out.write(raw); continue
            if not dcr_is_milliohms(text):
                skipped.append((ref, "DCR column header does not state milliohms")); out.write(raw); continue
            freq = impedance_frequency(text)
            if not freq:
                skipped.append((ref, "impedance measurement frequency not stated")); out.write(raw); continue

            before = {"dcResistance": dv, "inductance": el.get("inductance")}
            shaped = {"maximum": row["dcrRaw"] / 1000.0}
            if plural:
                el["dcResistances"] = [shaped]
            else:
                el["dcResistance"] = shaped
            el["inductance"] = {"nominal": row["inductanceUh"] * 1e-6}
            el["impedancePoints"] = [{"frequency": freq,
                                      "impedance": {"magnitude": row["zTypOhm"]}}]
            if list(validator.iter_errors(rec["magnetic"])):
                skipped.append((ref, "would not validate")); out.write(raw); continue
            out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
            fixed += 1
            audit.append({"reference": ref, "series": series, "datasheet": SHEET.format(series=series),
                          "before": before, "after": {"dcResistanceOhm": shaped["maximum"],
                                                      "inductanceH": row["inductanceUh"] * 1e-6,
                                                      "impedanceOhmAtHz": [row["zTypOhm"], freq]},
                          "ratedCurrentAConfirmed": ra})
            print(f"  {ref:18} Z {row['zTypOhm']:>6} ohm @ {freq/1e6:.0f} MHz | "
                  f"L {row['inductanceUh']} uH | DCR {row['dcrRaw']} mOhm")
        out.flush(); os.fsync(out.fileno())

    print(f"\nrepaired {fixed}, skipped {len(skipped)}")
    for ref, why in skipped:
        print(f"  SKIP {ref}: {why}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
        return 0
    os.replace(tmp, DATA)
    AUDIT.write_text(json.dumps({"ticket": "ABT #351 B1 / #286 (Bourns SRF)", "rows": audit,
                                 "skipped": [{"reference": r, "why": w} for r, w in skipped]}, indent=1))
    print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
