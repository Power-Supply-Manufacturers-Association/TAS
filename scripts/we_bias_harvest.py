#!/usr/bin/env python3
"""ABT #304: harvest Würth Elektronik MLCC DC-bias curves into capacitanceBiasPoints[].

The cheapest vendor on this ticket by a wide margin: TWO plain GETs harvest the whole
catalogue -- no per-part requests, no browser, no session, no rate limit.

  GET /redexpert/product/list/13     -> every WE MLCC with its Order_Code, capacitance,
                                        rated voltage, dielectric (module 13 = Multilayer
                                        Capacitors)
  GET /redexpert/tc/values/74/13     -> chart 74 for ALL 1,242 parts at once:
                                        {"ID": <ProductId>, "Values": [[volts, pF], ...]}

WHICH CHART IS DC BIAS -- identified by reading the numbers, not by name. For the 1 uF
50 V part 885012209047R, chart 74 starts at [0, 1000000] pF (= 1 uF, its nominal) and
ends at [50, 528000] pF (= 0.528 uF at its rated voltage). Chart 33 is the SAME sweep
expressed as percent change and ends at -47.2%, and 528000/1000000 = 0.528 -> -47.2%
exactly, so the two corroborate each other. For completeness: chart 160 is the
temperature characteristic (-55..125 degC), 47 is the AC-voltage dependence, and 323 is
impedance vs frequency (its "measurements" are 100 kHz..1 MHz, which is what makes it
obvious it is not a bias curve).

*** THE CHART ID IS 74, AND IT IS NOT DISCOVERABLE BY SCANNING SMALL NUMBERS. *** An
earlier pass scanned chart types 1-70 against /redexpert/tc/measurements/<chart>/13 and
concluded WE published no MLCC curves. Two things were wrong with that: the id space
goes far higher (the module's own page requests 323), and a bias chart has no
"measurements" list at all -- that endpoint is for charts parameterised by a measurement
condition (frequency, temperature). The values live under /tc/values/<chart>/13.

UNITS: picofarads on the y axis (a 1 uF part reads 1000000). Verified against each
record's own nominal on write, as with every other vendor here.

JOIN: our rows carry the bare order code (885012104001); REDEXPERT's carries a trailing
packaging letter (885012209047R). Matching is on the digits only, and only when exactly
one REDEXPERT part matches -- an ambiguous code is skipped rather than guessed.

Usage:
  we_bias_harvest.py fetch            -> staging/we/bias.jsonl
  we_bias_harvest.py write [--apply]  -> data/capacitors.ndjson
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "we"
OUT = STAGE / "bias.jsonl"
LOG = STAGE / "bias_harvest.log"

BASE = "https://redexpert.we-online.com/redexpert"
MODULE = 13                     # Multilayer Capacitors
CHART_DCBIAS = 74               # see the module docstring
PF = 1e-12
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
CLASS2 = {"ceramic-class-2", "ceramic-class-3"}


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def get_json(sess, path, timeout=120):
    r = sess.get(f"{BASE}/{path}", timeout=timeout)
    r.raise_for_status()
    # REDEXPERT responses carry raw control characters that json.loads rejects
    return json.loads(re.sub(r"[\x00-\x1f]", "", r.text))


def digits(code):
    return re.sub(r"\D", "", code or "")


def cmd_fetch(a):
    STAGE.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept": "application/json"})

    cat = {}
    for rec in get_json(sess, f"product/list/{MODULE}")["Data"]:
        cat[rec["ID"]] = rec
    log(f"catalogue: {len(cat)} WE MLCC parts")

    curves = get_json(sess, f"tc/values/{CHART_DCBIAS}/{MODULE}")
    log(f"chart {CHART_DCBIAS}: {len(curves)} parts carry a DC-bias curve")

    # one order code -> one product; a code claimed by more than one part is dropped
    by_code = {}
    for pid, rec in cat.items():
        by_code.setdefault(digits(rec.get("Order_Code")), []).append(pid)

    written = skipped = 0
    with OUT.open("w", encoding="utf-8") as fh:
        for entry in curves:
            rec = cat.get(entry["ID"])
            if not rec:
                skipped += 1
                continue
            pts = []
            for row in entry.get("Values") or []:
                try:
                    v, cap_pf = float(row[0]), float(row[1])
                except (TypeError, ValueError, IndexError):
                    continue
                pts.append({"voltage": v, "capacitance": cap_pf * PF})
            if not pts:
                skipped += 1
                continue
            fh.write(json.dumps({"orderCode": rec.get("Order_Code"),
                                 "codeDigits": digits(rec.get("Order_Code")),
                                 "productId": entry["ID"],
                                 "nominal": rec.get("Capacitance"),
                                 "ratedVoltage": rec.get("Rated_Voltage"),
                                 "dielectric": rec.get("Type"),
                                 "points": pts}, ensure_ascii=False) + "\n")
            written += 1
    ambiguous = sum(1 for k, v in by_code.items() if len(v) > 1)
    log(f"FETCH end: {written} curves staged, {skipped} without a catalogue match or points, "
        f"{ambiguous} order codes claimed by more than one product")
    return 0


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    psma = TAS.parent
    by = {}
    for repo in ("PEAS", "CAS"):
        for p in (psma / repo / "schemas").rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by[s["$id"]] = s
    reg = Registry().with_resources(
        [(s["$id"], Resource(contents=s, specification=DRAFT202012)) for s in by.values()])
    return Draft202012Validator(
        json.loads((psma / "CAS" / "schemas" / "capacitor.json").read_text()), registry=reg)


def cmd_write(a):
    import os
    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    gate = BladeGate("capacitor")

    curves, dup = {}, set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            key = r["codeDigits"]
            if key in curves:
                dup.add(key)            # two REDEXPERT parts share this code
            curves[key] = r["points"]
    for k in dup:
        curves.pop(k, None)             # ambiguous: never guess which part is ours
    log(f"WRITE: {len(curves)} usable curves ({len(dup)} dropped as ambiguous order codes)")
    if not curves:
        return 0

    v = build_validator()
    patched = rejected = 0
    bad, mismatched = [], []
    out_lines = []
    with SRC.open(encoding="utf-8") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            if "rth Elektronik" not in s:
                out_lines.append(s)
                continue
            obj = json.loads(s)
            c = obj.get("capacitor") or obj
            mi = c.get("manufacturerInfo") or {}
            ds = mi.get("datasheetInfo") or {}
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            key = digits(pn)
            if "rth" not in (mi.get("name") or "") or key not in curves:
                out_lines.append(s)
                continue
            # Stay inside the ticket's scope: class-1 (C0G/NP0) is excluded from #304
            # because its bias derating is negligible. REDEXPERT publishes curves for
            # those parts too -- they are simply not what this campaign is filling in,
            # and adding them here would make WE the only vendor with class-1 curves.
            if ((ds.get("part") or {}).get("technology")) not in CLASS2:
                out_lines.append(s)
                continue
            e = ds.setdefault("electrical", {})
            if e.get("capacitanceBiasPoints"):
                out_lines.append(s)
                continue
            pts = curves[key]
            cap = e.get("capacitance")
            nominal = cap.get("nominal") if isinstance(cap, dict) else cap
            c0 = (pts[0] or {}).get("capacitance")
            if (isinstance(nominal, (int, float)) and nominal > 0
                    and isinstance(c0, (int, float)) and c0 > 0
                    and not (0.5 <= c0 / nominal <= 2.0)):
                mismatched.append((pn, nominal, c0, c0 / nominal))
                out_lines.append(s)
                continue

            e["capacitanceBiasPoints"] = pts
            ds.setdefault("provenance", []).append({
                "source": "manufacturerParametric",
                "sourceName": ("Würth Elektronik REDEXPERT measured DC-bias curve "
                               f"(module {MODULE}, chart {CHART_DCBIAS})"),
                "sourceUrl": f"{BASE}/tc/values/{CHART_DCBIAS}/{MODULE}",
                "retrievedDate": time.strftime("%Y-%m-%d"),
            })
            errs = sorted(v.iter_errors(c), key=lambda x: x.path)
            if errs:
                rejected += 1
                if len(bad) < 5:
                    bad.append(f"{pn}: {errs[0].message[:140]}")
                out_lines.append(s)
                continue
            ok_bl, why = gate.check(c)
            if not ok_bl:
                rejected += 1
                if len(bad) < 5:
                    bad.append(f"{pn}: BLADE {why}")
                out_lines.append(s)
                continue
            patched += 1
            out_lines.append(json.dumps(obj, ensure_ascii=False))

    log(f"WRITE {'APPLIED' if a.apply else 'DRY RUN'}: patched={patched} rejected={rejected}")
    log(gate.summary())
    if mismatched:
        log(f"  SKIPPED {len(mismatched)} whose curve disagrees with the record's own nominal:")
        for pn, nom, c0, r in mismatched[:6]:
            log(f"    {pn}: nominal={nom:.3e} F vs curve0={c0:.3e} F (x{r:.3g})")
    for b in bad:
        log(f"  rejected: {b}")
    if a.apply and patched:
        tmp = SRC.with_suffix(".ndjson.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for line in out_lines:
                fh.write(line + "\n")
        os.replace(tmp, SRC)
        log(f"atomically replaced {SRC}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return cmd_fetch(a) if a.cmd == "fetch" else cmd_write(a)


if __name__ == "__main__":
    sys.exit(main())
