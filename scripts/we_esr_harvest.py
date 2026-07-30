#!/usr/bin/env python3
"""ABT #390: harvest Würth MEASURED ESR-vs-frequency curves into esrPoints[].

Reference implementation for the six-vendor ESR re-harvest. Würth is the cheapest of
the six — the whole vendor is two plain GETs, no browser, no session, no rate limit:

  GET /redexpert/product/list/13   every WE MLCC (module 13 = Multilayer Capacitors)
  GET /redexpert/tc/values/31/13   chart 31 for ALL 1,225 parts at once:
                                   {"ID": <ProductId>, "Values": [[f_Hz, |Z|_Ω, ESR_Ω], …]}
                                   187 points per part, 1 kHz … ~1 GHz

WHY THIS EXISTS: the catalogue's scalar `esr` for ceramics is not measurement. Blade
Runner's GEN_COHORT_CEILING found values pinned at round ceilings across four
independent manufacturers, and removing the top rung exposed another beneath it —
4,144 ceramic ESR values >=1 Ω take only 96 DISTINCT values, 56.7% of them whole
numbers. That is a binned lookup table. A measured curve replaces it.

COLUMN MAPPING WAS VERIFIED, NOT ASSUMED. Chart 31 has three columns and nothing names
them. Evidence that col3 is ESR: its MINIMUM lands at 0.77-48 MHz — i.e. at each part's
self-resonance, which is exactly where ESR bottoms out — and that minimum agrees with
the part's own equivalent-circuit Rs from product/list/13 within ~2x on every part
sampled. Col2 tracks 1/(2*pi*f*C) at low frequency, so it is |Z|.

WHAT GETS WRITTEN:
  - esrPoints  — the full measured curve (xData Hz, yData Ω), which the CAS schema
    itself calls the preferred form for selection logic.
  - esr + esrFrequency — REPLACED from the curve at 100 kHz, the conventional datasheet
    reference. The old scalar is recorded in the audit file first. Replacing is the
    point of the exercise: a curve-derived value at a stated frequency strictly
    dominates an unsourced number off a lookup ladder.
  - _esrWarning — CLEARED when set, since the value is no longer estimated.

GATES: every patched record is validated against CAS and run through Blade Runner, and
a curve whose minimum disagrees with the record's own equivalent-circuit Rs by more
than 20x is SKIPPED for review rather than written.

  we_esr_harvest.py fetch            -> staging/we/esr.jsonl
  we_esr_harvest.py write [--apply]  -> data/capacitors.ndjson
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "we"
OUT = STAGE / "esr.jsonl"
AUDIT = STAGE / "esr_replaced_audit.json"
LOG = STAGE / "esr_harvest.log"

BASE = "https://redexpert.we-online.com/redexpert"
MODULE = 13
CHART_ESR = 31              # [f_Hz, |Z|_Ω, ESR_Ω] — see the module docstring
REF_HZ = 100_000.0          # conventional datasheet reference frequency for the scalar
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def get_json(sess, path, timeout=180):
    r = sess.get(f"{BASE}/{path}", timeout=timeout)
    r.raise_for_status()
    return json.loads(re.sub(r"[\x00-\x1f]", "", r.text))   # raw control chars in feed


def digits(code):
    return re.sub(r"\D", "", code or "")


def interp(points, f):
    """ESR at frequency f by log-log interpolation; None outside the measured range.

    Never extrapolates: ESR outside the swept band is exactly what must not be guessed
    (below the band it rises steeply, above it the part is inductive).
    """
    import math
    if f < points[0][0] or f > points[-1][0]:
        return None
    for (f0, e0), (f1, e1) in zip(points, points[1:]):
        if f0 <= f <= f1:
            if f1 == f0 or e0 <= 0 or e1 <= 0:
                return e0
            t = (math.log10(f) - math.log10(f0)) / (math.log10(f1) - math.log10(f0))
            return 10 ** (math.log10(e0) + t * (math.log10(e1) - math.log10(e0)))
    return None


def cmd_fetch(a):
    STAGE.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept": "application/json"})
    cat = {r["ID"]: r for r in get_json(sess, f"product/list/{MODULE}")["Data"]}
    log(f"catalogue: {len(cat)} WE MLCC parts")
    curves = get_json(sess, f"tc/values/{CHART_ESR}/{MODULE}")
    log(f"chart {CHART_ESR}: {len(curves)} parts carry an ESR curve")

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
                    f, esr = float(row[0]), float(row[2])
                except (TypeError, ValueError, IndexError):
                    continue
                if f > 0 and esr > 0:
                    pts.append((f, esr))
            if len(pts) < 8:
                skipped += 1
                continue
            pts.sort()
            fh.write(json.dumps({
                "orderCode": rec.get("Order_Code"),
                "codeDigits": digits(rec.get("Order_Code")),
                "rsEquivalent": rec.get("Rs"),
                "points": pts,
            }, ensure_ascii=False) + "\n")
            written += 1
    log(f"FETCH end: {written} ESR curves staged, {skipped} skipped")
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
    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    gate = BladeGate("capacitor")

    curves, dup = {}, set()
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        k = r["codeDigits"]
        if k in curves:
            dup.add(k)
        curves[k] = r
    for k in dup:
        curves.pop(k, None)      # ambiguous order code: never guess which part is ours
    log(f"WRITE: {len(curves)} usable ESR curves ({len(dup)} dropped as ambiguous)")

    v = build_validator()
    patched = rejected = 0
    replaced, bad, mismatch = [], [], []
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
            entry = curves.get(digits(pn))
            if "rth" not in (mi.get("name") or "") or entry is None:
                out_lines.append(s)
                continue
            e = ds.setdefault("electrical", {})
            if e.get("esrPoints"):
                out_lines.append(s)
                continue
            pts = [(float(f), float(x)) for f, x in entry["points"]]

            # PHYSICS GATE: the curve's minimum is the part's high-frequency ESR and must
            # agree with its own equivalent-circuit Rs. A gross disagreement means the
            # columns are not what we think, and a wrong ESR is worse than none.
            rs = entry.get("rsEquivalent")
            emin = min(x for _, x in pts)
            if isinstance(rs, (int, float)) and rs > 0 and not (0.05 <= emin / rs <= 20):
                mismatch.append((pn, rs, emin, emin / rs))
                out_lines.append(s)
                continue

            e["esrPoints"] = {"xData": [f for f, _ in pts], "yData": [x for _, x in pts]}
            at_ref = interp(pts, REF_HZ)
            if at_ref is not None:
                if isinstance(e.get("esr"), (int, float)):
                    replaced.append({"partNumber": pn, "oldEsr": e.get("esr"),
                                     "oldEsrFrequency": e.get("esrFrequency"),
                                     "newEsr": at_ref, "newEsrFrequency": REF_HZ})
                e["esr"] = at_ref
                e["esrFrequency"] = REF_HZ
            e.pop("_esrWarning", None)      # no longer estimated
            ds.setdefault("provenance", []).append({
                "source": "manufacturerParametric",
                "sourceName": (f"Würth Elektronik REDEXPERT measured ESR-vs-frequency curve "
                               f"(module {MODULE}, chart {CHART_ESR})"),
                "sourceUrl": f"{BASE}/tc/values/{CHART_ESR}/{MODULE}",
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

    log(f"WRITE {'APPLIED' if a.apply else 'DRY RUN'}: patched={patched} rejected={rejected} "
        f"(scalar esr replaced on {len(replaced)})")
    log(gate.summary())
    for pn, rs, emin, r in mismatch[:5]:
        log(f"  SKIPPED {pn}: curve min {emin:.4g} Ω vs equivalent-circuit Rs {rs:.4g} Ω (x{r:.3g})")
    for b in bad:
        log(f"  rejected: {b}")
    if a.apply and patched:
        AUDIT.write_text(json.dumps(replaced, indent=1))
        tmp = SRC.with_suffix(".ndjson.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for line in out_lines:
                fh.write(line + "\n")
        os.replace(tmp, SRC)
        log(f"atomically replaced {SRC}; {len(replaced)} old scalars recorded in {AUDIT}")
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
