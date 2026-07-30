#!/usr/bin/env python3
"""ABT #390: harvest Taiyo Yuden MEASURED ESR-vs-frequency curves into esrPoints[].

Second vendor of the six-vendor ESR re-harvest; see scripts/we_esr_harvest.py for the
reference implementation and the reason this exists (the catalogue's scalar `esr` for
ceramics is a binned lookup table, not measurement — 4,144 values >=1 Ω taking only 96
distinct values, 56.7% of them whole numbers).

THE API, captured rather than guessed:
  the "Z ESR(log.)" tab on TY-COMPAS's characteristic graph is gtype=CIMP — a COMBINED
  impedance+ESR chart, the same shape as Würth's chart 31.

    POST /TYCOMPAS/eu/graphRest   (form-encoded)
    cid=C&productNoList=<CURRENT PN>&gtype=CIMP
    &xaxisunit=MHz&yaxixunit=ohm     ("yaxixunit" is their typo — keep it)
    -> graphChartData with FOUR keys per point:
         <PN>#<id>#R#x , <PN>#<id>#R#y    <- ESR   (x MHz, y ohm)
         <PN>#<id>#Z#x , <PN>#<id>#Z#y    <- |Z|
    202 points, 10 kHz … 3 GHz.

Guessing gtype names does not work here (CESR/CRS/CZ/CTAND all return an empty
axisUnit list) — the tab had to be clicked and the request captured. graphUnit is a
cheap oracle for confirming a chart's units without fetching data:
  GET /TYCOMPAS/eu/graphUnit?cid=C&gtype=CIMP&axistype=Y -> {"axisUnit":[mohm, ohm]}

SERIES IDENTIFICATION VERIFIED: for MCAST168SB7104KTNA01 (100 nF) at 10 kHz the #Z#
series reads 166.7 Ω against a computed Xc of 159 Ω, and #R# reads 0.986 Ω, i.e. a loss
tangent of 0.006 — right for an X7R. So #R# is ESR and #Z# is impedance, not the
reverse.

Part numbers need the same resolve step as the bias campaign: our catalogue holds
Taiyo's OLD numbers (TMK107B7104KA) and the graph API only answers to current ones
(MCAST168SB7104KTNA01).

  taiyo_esr_harvest.py fetch [--limit N] [--delay 0.3]  -> staging/taiyo/esr.jsonl
  taiyo_esr_harvest.py write [--apply]                  -> data/capacitors.ndjson
"""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import requests

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "taiyo"
OUT = STAGE / "esr.jsonl"
DONE = STAGE / "esr_done.json"
NODATA = STAGE / "esr_nodata.json"
LOG = STAGE / "esr_harvest.log"
STOP = STAGE / "STOP"

BASE = "https://ds.yuden.co.jp/TYCOMPAS/eu"
GTYPE = "CIMP"
REF_HZ = 100_000.0
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
CLASS2 = {"ceramic-class-2", "ceramic-class-3"}

sys.path.insert(0, str(Path(__file__).parent))


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def taiyo_parts():
    """[(our_pn, nominal_F)] for Taiyo ceramics that have no ESR curve yet."""
    out, seen = [], set()
    with SRC.open(encoding="utf-8") as fh:
        for line in fh:
            if "Taiyo" not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = o.get("capacitor") or o
            mi = c.get("manufacturerInfo") or {}
            if "Taiyo" not in (mi.get("name") or ""):
                continue
            ds = mi.get("datasheetInfo") or {}
            e = ds.get("electrical") or {}
            if ((ds.get("part") or {}).get("technology")) not in CLASS2:
                continue
            if e.get("esrPoints"):
                continue
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            cap = e.get("capacitance")
            nom = cap.get("nominal") if isinstance(cap, dict) else cap
            if pn and pn not in seen:
                seen.add(pn)
                out.append((pn, nom))
    return out


def resolve(sess, pn, timeout=30):
    r = sess.get(f"{BASE}/search", params={"cid": "C", "u": "M", "pn": pn}, timeout=timeout)
    if r.status_code != 200:
        return None, f"search HTTP {r.status_code}"
    try:
        recs = (r.json() or {}).get("records") or []
    except ValueError:
        return None, "search not JSON"
    if not recs:
        return None, "not in TY-COMPAS"
    for rec in recs:
        if pn in (rec.get("PartNumber"), rec.get("Pre_PN")):
            return rec.get("PartNumber"), None
    if len(recs) == 1:
        return recs[0].get("PartNumber"), None
    return None, f"ambiguous ({len(recs)} records)"


def fetch_curve(sess, current_pn, timeout=60):
    body = {"cid": "C", "productNoList": current_pn, "tabidx": "0", "gtype": GTYPE,
            "xaxisminauto": "true", "xaxismaxauto": "true",
            "yaxisminauto": "true", "yaxismaxauto": "true",
            "xaxismin": "NaN", "xaxismax": "NaN", "yaxismin": "NaN", "yaxismax": "NaN",
            "xaxisunit": "MHz", "yaxixunit": "ohm",          # their typo, not ours
            "xaxistypelog": "true", "yaxistypelog": "true"}
    r = sess.post(f"{BASE}/graphRest", data=body, timeout=timeout,
                  headers={"X-Requested-With": "XMLHttpRequest",
                           "Referer": f"{BASE}/charactericticGraph"})
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        data = json.loads(r.json().get("graphChartData") or "[]")
    except (ValueError, AttributeError):
        return None, "unparseable graphChartData"
    pts = []
    for row in data:
        # #R# is the ESR series; #Z# is impedance. Take R only, and never by position.
        xk = next((k for k in row if k.endswith("#R#x")), None)
        yk = next((k for k in row if k.endswith("#R#y")), None)
        if not xk or not yk:
            continue
        try:
            f_mhz, esr = float(row[xk]), float(row[yk])
        except (TypeError, ValueError):
            continue
        if f_mhz > 0 and esr > 0:
            pts.append((f_mhz * 1e6, esr))          # MHz -> Hz
    if len(pts) < 8:
        return None, "empty ESR series"
    pts.sort()
    return pts, None


def cmd_fetch(a):
    import fcntl
    STAGE.mkdir(parents=True, exist_ok=True)
    lock = (STAGE / ".lock_esr").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another ESR fetch holds the lock -- exiting")
        return 0
    if STOP.exists():
        STOP.unlink()

    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    nodata = json.loads(NODATA.read_text()) if NODATA.exists() else {}
    work = [(pn, nom) for pn, nom in taiyo_parts() if pn not in done and pn not in nodata]
    if a.limit:
        work = work[:a.limit]
    log(f"ESR FETCH start: {len(work)} parts ({len(done)} done, {len(nodata)} unavailable)")
    if not work:
        return 0

    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    ok = fail = 0
    errs = {}
    for i, (pn, nom) in enumerate(work, 1):
        if STOP.exists():
            log("STOP file -- exiting cleanly")
            break
        try:
            cur, err = resolve(sess, pn)
            pts = None
            if cur:
                pts, err = fetch_curve(sess, cur)
        except requests.RequestException as e:
            pts, err = None, f"transport: {type(e).__name__}"
        if err or not pts:
            fail += 1
            key = err or "no points"
            errs[key] = errs.get(key, 0) + 1
            if key.startswith(("not in TY-COMPAS", "ambiguous", "empty ESR")):
                nodata[pn] = key
                NODATA.write_text(json.dumps(nodata, indent=0, sort_keys=True))
        else:
            ok += 1
            with OUT.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"partNumber": pn, "currentPartNumber": cur,
                                     "nominal": nom, "points": pts}, ensure_ascii=False) + "\n")
            done.add(pn)
            DONE.write_text(json.dumps(sorted(done)))
        if i % 100 == 0:
            log(f"  {i}/{len(work)}  ok={ok} fail={fail} {dict(list(errs.items())[:3])}")
        time.sleep(a.delay)
    log(f"ESR FETCH end: ok={ok} fail={fail} errors={errs}")
    return 0


def interp(points, f):
    """Log-log interpolation; None outside the measured band — never extrapolate."""
    if f < points[0][0] or f > points[-1][0]:
        return None
    for (f0, e0), (f1, e1) in zip(points, points[1:]):
        if f0 <= f <= f1:
            if f1 == f0 or e0 <= 0 or e1 <= 0:
                return e0
            t = (math.log10(f) - math.log10(f0)) / (math.log10(f1) - math.log10(f0))
            return 10 ** (math.log10(e0) + t * (math.log10(e1) - math.log10(e0)))
    return None


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
    from blade_gate import BladeGate
    gate = BladeGate("capacitor")
    curves = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                curves[r["partNumber"]] = r
    log(f"WRITE: {len(curves)} ESR curves available")
    if not curves:
        return 0

    v = build_validator()
    patched = rejected = 0
    bad, replaced, tand = [], [], []
    out_lines = []
    with SRC.open(encoding="utf-8") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            if "Taiyo" not in s:
                out_lines.append(s)
                continue
            obj = json.loads(s)
            c = obj.get("capacitor") or obj
            mi = c.get("manufacturerInfo") or {}
            ds = mi.get("datasheetInfo") or {}
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            entry = curves.get(pn)
            if "Taiyo" not in (mi.get("name") or "") or entry is None:
                out_lines.append(s)
                continue
            e = ds.setdefault("electrical", {})
            if e.get("esrPoints"):
                out_lines.append(s)
                continue
            pts = [(float(f), float(x)) for f, x in entry["points"]]
            e["esrPoints"] = {"xData": [f for f, _ in pts], "yData": [x for _, x in pts]}
            at_ref = interp(pts, REF_HZ)
            cap = e.get("capacitance")
            nominal = cap.get("nominal") if isinstance(cap, dict) else cap
            if at_ref is not None:
                if isinstance(e.get("esr"), (int, float)):
                    replaced.append({"partNumber": pn, "oldEsr": e["esr"],
                                     "oldEsrFrequency": e.get("esrFrequency"),
                                     "newEsr": at_ref})
                e["esr"] = at_ref
                e["esrFrequency"] = REF_HZ
                if isinstance(nominal, (int, float)) and nominal > 0:
                    tand.append(at_ref / (1.0 / (2 * math.pi * REF_HZ * nominal)))
            e.pop("_esrWarning", None)
            ds.setdefault("provenance", []).append({
                "source": "manufacturerParametric",
                "sourceName": ("TAIYO YUDEN TY-COMPAS measured ESR-vs-frequency curve "
                               "(gtype CIMP, series R)"),
                "sourceUrl": f"{BASE}/graphRest",
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
        f"(scalar replaced on {len(replaced)})")
    log(gate.summary())
    if tand:
        tand.sort()
        log(f"  implied tan-delta at {REF_HZ:g} Hz: median={tand[len(tand)//2]:.4g} "
            f"max={tand[-1]:.4g}  (>0.5 would mean wrong units or wrong series)")
    for b in bad:
        log(f"  rejected: {b}")
    if a.apply and patched:
        (STAGE / "esr_replaced_audit.json").write_text(json.dumps(replaced, indent=1))
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
    f = sub.add_parser("fetch")
    f.add_argument("--limit", type=int)
    f.add_argument("--delay", type=float, default=0.3)
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return cmd_fetch(a) if a.cmd == "fetch" else cmd_write(a)


if __name__ == "__main__":
    sys.exit(main())
