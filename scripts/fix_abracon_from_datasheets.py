#!/usr/bin/env python3
"""Repair the Abracon rows of the ABT #351 density-impossible queue from the
datasheet PDFs the rows themselves cite.

    python3 scripts/fix_abracon_from_datasheets.py queue288.json [--dry-run]

abracon.com serves the PDFs to plain curl, and each family sheet carries a parts
table with an explicit Units header line:

       Part Number      L        -     Freq.   DCR     Ir      S.R.F
       Units            nH       -     MHz     Ω       mA      MHz
       AISC-0603F-100   10000  J,K,M   2.5     5.00    100     30

Adjudication of the first part: corpus AISC-0603F-100J-T has L 10 uH (matches)
and DCR 5.00 ohm (MATCHES THE VENDOR — the DCR is correct), while its rated
current says 1.2 A against the vendor's 100 mA. The CURRENT is the broken field,
and not by a unit factor. Same non-unit corruption family as WE 7427921.

METHOD:
  * The table row is found by part-number prefix (the corpus reference minus its
    tolerance/packaging suffix: AISC-0603F-100J-T -> AISC-0603F-100).
  * Column units come from the sheet's own Units line, not from assumptions.
  * The row is ANCHORED on the corpus inductance (must equal the L cell within
    2% after unit conversion) — a row that does not anchor is skipped.
  * ratedCurrents <- Ir cell; dcResistance <- DCR cell where it differs.
  * Every repaired row must pass MAS and the areal-density gate afterwards.
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
AUDIT = REPO / "staging" / "abracon_datasheet_repair_audit.json"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")
# NOTE both mu codepoints: Abracon sheets use GREEK SMALL MU (U+03BC), not the
# micro sign (U+00B5) — a lookup keyed on one silently misses the other.
UNIT = {"nH": 1e-9, "µH": 1e-6, "μH": 1e-6, "uH": 1e-6, "mH": 1e-3, "H": 1.0,
        "Ω": 1.0, "mΩ": 1e-3, "ohm": 1.0, "mohm": 1e-3,
        "A": 1.0, "mA": 1e-3, "MHz": 1e6, "GHz": 1e9, "kHz": 1e3}
_cache: dict[str, tuple[str, str]] = {}


def sheet(url: str):
    if url in _cache:
        return _cache[url]
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        path = f.name
    texts = []
    for args in (["-layout"], ["-raw"]):
        out = subprocess.run(["pdftotext", *args, path, "-"], capture_output=True, timeout=120)
        texts.append(out.stdout.decode("utf-8", "replace") if out.returncode == 0 else "")
    os.unlink(path)
    _cache[url] = (texts[0], texts[1])
    time.sleep(0.4)
    return _cache[url]


def units_line(layout: str):
    """Ordered unit tokens from the sheet's 'Units' header row."""
    for line in layout.splitlines():
        if re.match(r"\s*Units\b", line):
            toks = [t for t in line.split() if t != "Units"]
            return [t for t in toks]
    return None


def table_key(ref: str) -> str:
    """AISC-0603F-100J-T -> AISC-0603F-100 ; ASPI-4030S-1R0N-T -> ASPI-4030S-1R0 ;
    AMDLH4020S-1R0MT -> AMDLH4020S-1R0. Strip tolerance letter(s) + packaging."""
    r = re.sub(r"-T\d*$", "", ref)
    r = re.sub(r"([0-9RN])[JKMN]+T?$", r"\1", r)
    return r


def row_numbers(raw: str, key: str):
    for line in raw.splitlines():
        if not line.startswith(key):
            continue
        tail = line[len(key):]
        # a longer part number (key + extra digit) is a different part
        if tail[:1].isdigit():
            continue
        nums = [float(x) for x in re.findall(r"\d+\.?\d*", tail.replace(",", ""))]
        if len(nums) >= 3:
            return nums
    return None


def nominal(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("nominal", "maximum", "minimum"):
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
    return None


def density_ok(dcr, rated, dims_m):
    if len(dims_m) < 2:
        return True
    l, w = dims_m[0], dims_m[1]
    h = dims_m[2] if len(dims_m) > 2 else min(l, w)
    return dcr * rated * rated / (2 * (l * w + l * h + w * h) * 1e4) <= 2.5


# AMDLH4020S: the sheet's DCR Max and Typ are TWO numeric columns under ONE
# "mΩ" unit token, which breaks the unit-zip (it misread DCR-typ as the rated
# current and would have overwritten a CORRECT Irms of 24 A with 1.6). The table
# below is transcribed directly from the datasheet
# (https://abracon.com/datasheets/AMDLH4020S.pdf, Electrical table):
#   part suffix : (DCR max mOhm, Irms typ A)   — corpus Irms already matches.
AMDLH_TABLE = {
    "R10": (2.0, 24.0), "R15": (3.5, 18.0), "R22": (4.9, 16.0), "R33": (5.8, 15.0),
    "R47": (7.0, 13.0), "R68": (8.8, 11.0), "1R0": (11.0, 10.0), "1R5": (16.0, 8.5),
    "2R2": (23.0, 7.5), "3R3": (45.6, 5.6), "4R7": (64.8, 4.5),
}


def amdlh_lookup(ref: str):
    m = __import__("re").search(r"AMDLH4020S-([0-9R]+)M?T?$", ref)
    if not m:
        return None
    row = AMDLH_TABLE.get(m.group(1))
    if not row:
        return None
    return {"dcr": row[0] / 1000.0, "rated": row[1]}


def main(argv):
    dry = "--dry-run" in argv
    queue = [r for r in json.loads(Path(argv[0]).read_text()) if r["mk"].startswith("Abracon")]
    plan = {r["ref"]: r for r in queue}
    print(f"Abracon rows in the density-impossible queue: {len(plan)}")

    validator = _load_magnetic_schema(_build_registry())
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"repaired": [], "skipped": []}
    seen = set()
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw_line in src:
            wrote = False
            if b"Abracon" in raw_line:
                try:
                    rec = json.loads(raw_line)
                    info = rec["magnetic"]["manufacturerInfo"]
                    di = info["datasheetInfo"]
                    el = di["electrical"][0]
                    ref = str(info.get("reference"))
                except Exception:
                    ref = None
                if ref in plan and ref not in seen:
                    seen.add(ref)
                    url = info.get("datasheetUrl")
                    why = None
                    L = nominal(el.get("inductance"))
                    try:
                        layout, rawtxt = sheet(url)
                    except Exception as e:
                        why = f"datasheet fetch failed: {e}"
                        layout = rawtxt = ""
                    units = units_line(layout) if layout else None
                    nums = row_numbers(rawtxt, table_key(ref)) if rawtxt else None
                    parsed = amdlh_lookup(ref)
                    if parsed is not None:
                        why = None
                        units = nums = ()      # transcribed table, no generic parse
                    elif why is None and (not units or not nums):
                        why = "units header or part row not found"
                    if why is None and parsed is None:
                        # anchor: the L cell (first numeric column) must equal the
                        # corpus inductance under the sheet's own L unit
                        lu = UNIT.get(units[0])
                        if not lu or L is None or abs(nums[0] * lu - L) > 0.02 * max(L, 1e-12):
                            why = f"L anchor failed (sheet {nums[0]}{units[0]} vs corpus {L})"
                        else:
                            # remaining numeric cells map onto the remaining units.
                            # Columns whose unit is "-" (the tolerance letters) carry
                            # NO number in the extracted row — dropping them from the
                            # unit list keeps the zip aligned; keeping them shifted
                            # every later column by one and misread DCR as Ir.
                            rest_units = [u for u in units[1:] if u != "-"]
                            rest_nums = nums[1:]
                            vals = {}
                            # the zip is only trustworthy when every unit token
                            # owns exactly one numeric cell — merged Max/Typ
                            # double-columns (AMDLH) silently shift everything
                            if len(rest_units) != len(rest_nums):
                                rest_units = []
                            for u, n in zip(rest_units, rest_nums):
                                if u in ("Ω", "mΩ", "ohm", "mohm") and "dcr" not in vals:
                                    vals["dcr"] = n * UNIT[u]
                                elif u in ("A", "mA") and "rated" not in vals:
                                    vals["rated"] = n * UNIT[u]
                            if "dcr" not in vals or "rated" not in vals:
                                why = f"DCR/Ir columns not identified in units {units}"
                            else:
                                parsed = vals
                    if why is None:
                        d = el.get("dcResistances")
                        plural = bool(d)
                        d = d[0] if d else el.get("dcResistance")
                        dcr = (d.get("maximum") if isinstance(d, dict) and d.get("maximum") is not None
                               else (d.get("nominal") if isinstance(d, dict) else None))
                        rated = (el.get("ratedCurrents") or [None])[0]
                        changed = {}
                        if dcr and abs(dcr - parsed["dcr"]) > 0.05 * parsed["dcr"]:
                            changed["dcResistance"] = {"was": dcr, "now": parsed["dcr"]}
                            shaped = {"maximum": parsed["dcr"]}
                            if plural:
                                el["dcResistances"] = [shaped]
                            else:
                                el["dcResistance"] = shaped
                            dcr = parsed["dcr"]
                        if rated and abs(rated - parsed["rated"]) > 0.05 * parsed["rated"]:
                            changed["ratedCurrents"] = {"was": rated, "now": parsed["rated"]}
                            el["ratedCurrents"] = [parsed["rated"]] + list(el.get("ratedCurrents", [])[1:])
                            rated = parsed["rated"]
                        mech = di.get("mechanical") or {}
                        dims = [nominal(mech.get(k)) for k in ("length", "width", "height")]
                        dims = [x for x in dims if x and x > 0]
                        if not changed:
                            why = "sheet agrees with corpus — row remains unexplained"
                        elif not density_ok(dcr, rated, dims):
                            why = "still density-impossible after the sheet's values"
                        else:
                            prov = di.get("provenance") or []
                            entry = {"source": "manufacturerDatasheet",
                                     "sourceName": "Abracon family datasheet (parts table)",
                                     "sourceUrl": url}
                            if entry not in prov:
                                di["provenance"] = prov + [entry]
                            if list(validator.iter_errors(rec["magnetic"])):
                                why = "schema-invalid after repair"
                            else:
                                out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                                wrote = True
                                audit["repaired"].append({"reference": ref, "changed": changed,
                                                          "datasheet": url})
                                print(f"  {ref:22} {json.dumps(changed)[:100]}")
                    if why:
                        audit["skipped"].append({"reference": ref, "why": why})
            if not wrote:
                out.write(raw_line)
        out.flush()
        os.fsync(out.fileno())

    print(f"\nrepaired {len(audit['repaired'])}, skipped {len(audit['skipped'])}")
    for s in audit["skipped"]:
        print(f"  SKIP {s['reference']}: {s['why'][:90]}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
    else:
        os.replace(tmp, DATA)
        AUDIT.write_text(json.dumps({"ticket": "ABT #351 (Abracon)", **audit}, indent=1))
        print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
