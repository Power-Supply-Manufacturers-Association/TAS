#!/usr/bin/env python3
"""ABT #400: extract every Würth connector's DATASHEET properties, not just the table.

The product-line table gives ~10 useful columns. The per-part datasheet PDF gives the full
property block, and WE uses one consistent layout across every connector family:

  Rated Current                IR                       3        A
  Working Voltage                                     250     V (AC)
  Withstanding Voltage         1 min                  500     V (AC)
  Contact Resistance           R                       20       mΩ     max.
  Insulation Resistance        RISO                  1000       MΩ     min.
  Impedance                    Z    DC~18 GHz          50        Ω
  Operating Temperature                     -40 up to +105 °C
  Durability                                 25 Mating cycles
  Insulator Material                                 PA6T
  Contact Material                           Copper Alloy
  Contact Plating                                    Gold

So insulationResistance, dielectricWithstandingVoltage, matingCycles, contactResistance,
materials and plating — none of which appear in the table for most parts — are all
recoverable per part.

  we_connectors_datasheets.py fetch [--limit N]   -> staging/we_conn/ds/<code>.txt.gz
  we_connectors_datasheets.py parse              -> staging/we_conn/specs.jsonl

The extracted TEXT is cached, not the PDF (a tenth of the size), so re-parsing after a
rule change costs nothing. Only extraction happens here; nothing is written to data/.
"""
import argparse
import gzip
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
STAGE = TAS / "staging" / "we_conn"
ROWS = STAGE / "rows.jsonl"
DS = STAGE / "ds"
MISSING = STAGE / "ds_missing.json"
SPECS = STAGE / "specs.jsonl"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
URL = "https://www.we-online.com/components/products/datasheet/{}.pdf"

# Unit -> SI multiplier. Everything CONAS stores is SI base units.
UNITS = {
    "mΩ": 1e-3, "mOhm": 1e-3, "Ω": 1.0, "kΩ": 1e3, "MΩ": 1e6, "GΩ": 1e9,
    "V": 1.0, "mV": 1e-3, "kV": 1e3,
    "A": 1.0, "mA": 1e-3,
    "GHz": 1e9, "MHz": 1e6, "kHz": 1e3, "Hz": 1.0,
    "mm": 1e-3, "µm": 1e-6, "μm": 1e-6, "cm": 1e-2, "m": 1.0,
    "N": 1.0,
}
NUM = r"(-?\d+(?:[  ]\d{3})*(?:[.,]\d+)?)"


def num(s):
    """'5 000' -> 5000.0, '1.35' -> 1.35, '0,127' -> 0.127."""
    return float(s.replace(" ", "").replace(" ", "").replace(",", "."))


def val_unit(rest, units):
    """First number in `rest` followed by one of `units`, converted to SI."""
    alt = "|".join(re.escape(u) for u in sorted(units, key=len, reverse=True))
    m = re.search(NUM + r"\s*(" + alt + r")\b", rest)
    if not m:
        return None
    return num(m.group(1)) * UNITS[m.group(2)]


# A cell that is nothing but a unit — this is the datasheet's "Unit" column.
# The unit column may carry the qualifier too: 'V (RMS) min.', 'mΩ max.'
UNIT_CELL = re.compile(r"^(mΩ|MΩ|kΩ|GΩ|Ω|kV|mV|V|mA|A|GHz|MHz|kHz|Hz|[µμ]m|mm|cm|N|°C)"
                       r"(?:\s*\((?:AC|DC|RMS)\))?(?:\s*(?:min|max|typ)\.?)?$")
NUM_CELL = re.compile(r"^" + NUM + r"$")


def prop_line(text, *names):
    """The remainder of the first line starting with any of `names`."""
    for ln in text.splitlines():
        s = ln.strip()
        for n in names:
            if s.startswith(n):
                return s[len(n):].strip()
    return None


def prop_cells(text, *names):
    """Column cells of the first property row whose FIRST cell matches one of `names`.

    `pdftotext -layout` preserves columns as runs of 2+ spaces, but it also merges
    SIDE-BY-SIDE TABLES into one physical line:

      Withstanding Voltage | 1 min | 1000 | V (AC) | Reverse Voltage | VREV | 5 | V

    (a modular jack with an integrated LED). Reading "the last number with a unit" off
    that line yields the LED's 5 V reverse voltage as the connector's withstanding
    voltage — which is exactly what happened, and what Blade Runner's CONN_DWV_VS_RATED
    caught on a 125 V part. Columns disambiguate it; first/last heuristics cannot.
    """
    for ln in text.splitlines():
        cells = [c.strip() for c in re.split(r"\s{2,}", ln.strip()) if c.strip()]
        if not cells:
            continue
        for n in names:
            if cells[0] == n or (cells[0].startswith(n) and "After Test" not in cells[0]):
                return cells
    return None


def col_value(cells, allowed):
    """The value in the cell immediately preceding the first matching Unit cell."""
    if not cells:
        return None
    for i, c in enumerate(cells):
        m = UNIT_CELL.match(c)
        if not m or m.group(1) not in allowed:
            continue
        for j in range(i - 1, 0, -1):
            if NUM_CELL.match(cells[j]):
                return num(cells[j]) * UNITS[m.group(1)]
            if UNIT_CELL.match(cells[j]):
                break          # ran into the previous table's unit column
    return None


def extract(text):
    """Datasheet text -> {field: SI value}. Absent fields are simply absent."""
    out = {}

    c = prop_cells(text, "Rated Current")
    if c and "[" not in c[0]:                             # skip 'Rated Current [VDE]'
        v = col_value(c, ["A", "mA"])
        if v is not None:
            out["ratedCurrentPerContact"] = v

    c = prop_cells(text, "Working Voltage")
    if c and "[" not in c[0]:
        v = col_value(c, ["V", "kV"])
        if v is not None:
            out["ratedVoltage"] = v

    c = prop_cells(text, "Withstanding Voltage", "Dielectric Withstanding Voltage")
    if c and "Shielding" not in c[0]:
        v = col_value(c, ["V", "kV"])
        if v is not None:
            out["dielectricWithstandingVoltage"] = v

    c = prop_cells(text, "Contact Resistance")
    if c:
        v = col_value(c, ["mΩ", "Ω", "kΩ"])
        if v is not None:
            out["contactResistance"] = v
            out["contactResistanceIsMax"] = "max" in " ".join(c).lower()

    c = prop_cells(text, "Insulation Resistance")
    if c:
        v = col_value(c, ["MΩ", "GΩ", "kΩ", "Ω"])
        if v is not None:
            out["insulationResistance"] = v

    c = prop_cells(text, "Impedance")
    if c:
        v = col_value(c, ["Ω"])
        if v is not None:
            out["characteristicImpedance"] = v

    r = prop_line(text, "VSWR")
    if r:
        # 'VSWR   DC~18 GHz   1.35   max.' — the first number belongs to the test
        # condition, not the rating, so strip frequency tokens before reading it.
        # Taken literally this yielded maxVswr = 18 on every RF part.
        m = re.search(NUM, re.sub(NUM + r"\s*[GMk]?Hz", " ", r))
        if m:
            out["maxVswr"] = num(m.group(1))

    r = prop_line(text, "Frequency Range")
    if r:
        # 'Frequency Range   f   DC~18 GHz' — DC means 0 Hz.
        m = re.search(r"(DC|" + NUM + r"\s*([GMk]?Hz))\s*[~\-–]\s*" + NUM + r"\s*([GMk]?Hz)", r)
        if m:
            lo = 0.0 if m.group(1) == "DC" else num(m.group(2)) * UNITS[m.group(3)]
            out["frequencyRange"] = {"minimum": lo, "maximum": num(m.group(4)) * UNITS[m.group(5)]}

    r = prop_line(text, "Operating Temperature")
    if r:
        m = re.search(NUM + r"\s*(?:°C)?\s*up to\s*\+?" + NUM + r"\s*°C", r)
        if m:
            out["operatingTemperature"] = {"minimum": num(m.group(1)),
                                           "maximum": num(m.group(2))}

    r = prop_line(text, "Durability")
    if r:
        m = re.search(NUM + r"\s*Mating cycles", r)
        if m:
            out["matingCycles"] = int(num(m.group(1)))

    for field, names in (("insulatorMaterial", ("Insulator Material",)),
                         ("contactMaterial", ("Contact Material", "Center Contact Material")),
                         ("contactPlating", ("Contact Plating", "Center Contact Plating")),
                         ("bodyMaterial", ("Body Material",))):
        r = prop_line(text, *names)
        if r:
            v = re.sub(r"\s{2,}.*$", "", r).strip()
            if v and v not in ("-", "–"):
                out[field] = v
    return out


def codes():
    """Every live WE connector order code, from the staged product-line tables."""
    seen = {}
    with ROWS.open(encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            r = json.loads(ln)
            for row in r["rows"]:
                seen.setdefault(row["orderCode"], r["productLine"])
    return seen


def cmd_fetch(a):
    DS.mkdir(parents=True, exist_ok=True)
    missing = set(json.loads(MISSING.read_text())) if MISSING.exists() else set()
    all_codes = codes()
    todo = [c for c in sorted(all_codes)
            if not (DS / f"{c}.txt.gz").exists() and c not in missing]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(all_codes)} order codes; fetching {len(todo)} datasheets "
          f"({len(all_codes) - len(todo) - len(missing)} cached, {len(missing)} absent)")

    def one(code):
        try:
            req = urllib.request.Request(URL.format(code), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                pdf = r.read()
        except Exception as e:
            return code, None, str(e)[:60]
        if not pdf.startswith(b"%PDF"):
            return code, None, "not a pdf"
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tf:
            tf.write(pdf)
            tf.flush()
            try:
                txt = subprocess.run(["pdftotext", "-layout", tf.name, "-"],
                                     capture_output=True, timeout=120).stdout
            except subprocess.TimeoutExpired:
                return code, None, "pdftotext timeout"
        t = txt.decode("utf-8", "replace")
        with gzip.open(DS / f"{code}.txt.gz", "wt", encoding="utf-8") as fh:
            fh.write(t)
        return code, len(t), None

    ok = fail = 0
    errs = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, (code, size, err) in enumerate(ex.map(one, todo), 1):
            if err:
                fail += 1
                errs[err] = errs.get(err, 0) + 1
                missing.add(code)
            else:
                ok += 1
            if i % 250 == 0:
                MISSING.write_text(json.dumps(sorted(missing)))
                rate = i / max(time.time() - t0, 1e-9)
                print(f"  [{i}/{len(todo)}] ok={ok} fail={fail} {rate:.1f}/s "
                      f"eta {int((len(todo)-i)/max(rate,1e-9)/60)}m")
    MISSING.write_text(json.dumps(sorted(missing)))
    print(f"fetch done: ok={ok} fail={fail} {errs}")
    return 0


def cmd_parse(a):
    # Walk every CACHED datasheet, not just the live catalogue's codes: TAS also holds
    # EOL Würth parts that the live product-line tables no longer list, and WE still
    # serves their datasheets. Those records need checking against source too.
    all_codes = codes()
    n = 0
    fields = {}
    with SPECS.open("w", encoding="utf-8") as out:
        for f in sorted(DS.glob("*.txt.gz")):
            code = f.name[:-7]
            pl = all_codes.get(code, "(not in live catalogue)")
            with gzip.open(f, "rt", encoding="utf-8", errors="replace") as fh:
                spec = extract(fh.read())
            if not spec:
                continue
            n += 1
            for k in spec:
                fields[k] = fields.get(k, 0) + 1
            out.write(json.dumps({"orderCode": code, "productLine": pl, "spec": spec},
                                 ensure_ascii=False) + "\n")
    print(f"parsed {n} datasheets -> {SPECS}")
    print("field coverage:")
    for k, c in sorted(fields.items(), key=lambda kv: -kv[1]):
        print(f"   {c:>6}  {k}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--limit", type=int)
    f.add_argument("--workers", type=int, default=6)
    sub.add_parser("parse")
    a = ap.parse_args()
    return cmd_fetch(a) if a.cmd == "fetch" else cmd_parse(a)


if __name__ == "__main__":
    sys.exit(main())
