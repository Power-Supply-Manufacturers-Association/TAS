#!/usr/bin/env python3
"""ABT #304: harvest Taiyo Yuden DC-bias curves into capacitanceBiasPoints[].

Source is TY-COMPAS (ds.yuden.co.jp). Captured from a real UI interaction, then
verified to answer plain curl -- no browser in the harvest path:

  1. RESOLVE. Our catalogue holds Taiyo Yuden's OLD part numbers (TMK107B7104KA) as
     well as current ones. Their search API maps one to the other:
       GET /TYCOMPAS/eu/search?cid=C&u=M&pn=<PN>
       -> {"records":[{"PartNumber":"MCAST168SB7104KTNA01",
                       "Pre_PN":"TMK107B7104KAHT", ...}]}
     `PartNumber` is the current number the graph API needs; `Pre_PN` is the legacy
     one. Feeding the legacy number straight to the graph API returns nothing, so the
     resolve step is not optional.

  2. CURVE.
       POST /TYCOMPAS/eu/graphRest      (form-encoded)
       cid=C&productNoList=<CURRENT PN>&gtype=CDCBB
       &xaxisunit=V&yaxixunit=uF&...    (yes, "yaxixunit" -- their typo, keep it)
       -> {"graphChartData": "[{\"<PN>#3188#DC2#x\":\"0.0\",
                                \"<PN>#3188#DC2#y\":\"0.0938975\"}, ...]"}
     ~106 points, x = DC volts, y = capacitance in the unit we asked for.

  gtype=CDCBB is DC Bias (capacitance). The tab list also offers CIMP (impedance),
  Cap, ESL, "DC Bias(%)" and TCC; the gtypes were read off the page's own graphUnit
  calls rather than guessed -- graphUnit?cid=C&gtype=CDCBB&axistype=Y answers
  {"axisUnit":[{"pF"},{"uF"}]}, which is what confirms the axis is capacitance.

UNITS: we REQUEST uF and the response honours it, but the curve is still checked
against each record's own nominal before writing (>2x disagreement is skipped for
review, never rescaled) -- the same gate that caught the 1e6 Murata error.

Unlike KEMET's K-SIM these are measured characteristic curves, so provenance names
the TY-COMPAS characteristic graph directly.

Usage:
  taiyo_bias_harvest.py fetch [--limit N] [--delay 0.3]  -> staging/taiyo/bias.jsonl
  taiyo_bias_harvest.py write [--apply]                  -> data/capacitors.ndjson
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "taiyo"
OUT = STAGE / "bias.jsonl"
DONE = STAGE / "bias_done.json"
NODATA = STAGE / "nodata.json"
LOG = STAGE / "bias_harvest.log"
STOP = STAGE / "STOP"

BASE = "https://ds.yuden.co.jp/TYCOMPAS/eu"
SEARCH = f"{BASE}/search"
GRAPH = f"{BASE}/graphRest"
GTYPE = "CDCBB"                       # DC Bias (capacitance). See the docstring.
Y_UNIT = "uF"
Y_SCALE = 1e-6
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
CLASS2 = {"ceramic-class-2", "ceramic-class-3"}


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def taiyo_class2_parts():
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
            if e.get("capacitanceBiasPoints"):
                continue
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            cap = e.get("capacitance")
            nom = cap.get("nominal") if isinstance(cap, dict) else cap
            if pn and pn not in seen:
                seen.add(pn)
                out.append((pn, nom))
    return out


def resolve(sess, pn, timeout=30):
    """our part number -> Taiyo Yuden's CURRENT part number, or (None, reason)."""
    r = sess.get(SEARCH, params={"cid": "C", "u": "M", "pn": pn}, timeout=timeout)
    if r.status_code != 200:
        return None, f"search HTTP {r.status_code}"
    try:
        j = r.json()
    except ValueError:
        return None, "search not JSON"
    recs = j.get("records") or []
    if not recs:
        return None, "not in TY-COMPAS"
    # Exact match on either the current or the legacy number wins; otherwise the
    # single record a unique part-number search returns is the answer. Never pick
    # from a multi-record result on position -- that would attach another part's curve.
    for rec in recs:
        if pn in (rec.get("PartNumber"), rec.get("Pre_PN")):
            return rec.get("PartNumber"), None
    if len(recs) == 1:
        return recs[0].get("PartNumber"), None
    return None, f"ambiguous ({len(recs)} records)"


def fetch_curve(sess, current_pn, timeout=45):
    body = {
        "cid": "C", "productNoList": current_pn, "tabidx": "3", "gtype": GTYPE,
        "xaxisminauto": "false", "xaxismaxauto": "true",
        "yaxisminauto": "true", "yaxismaxauto": "true",
        "xaxismin": "0", "xaxismax": "NaN", "yaxismin": "NaN", "yaxismax": "NaN",
        "xaxisunit": "V", "yaxixunit": Y_UNIT,        # their typo, not ours
        "xaxistypelog": "false", "yaxistypelog": "false",
    }
    r = sess.post(GRAPH, data=body, timeout=timeout,
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
        xk = next((k for k in row if k.endswith("#x")), None)
        yk = next((k for k in row if k.endswith("#y")), None)
        if not xk or not yk:
            continue
        try:
            v, cap = float(row[xk]), float(row[yk])
        except (TypeError, ValueError):
            continue
        pts.append({"voltage": v, "capacitance": cap * Y_SCALE})
    if not pts:
        return None, "empty series"
    return pts, None


def cmd_fetch(a):
    import fcntl
    STAGE.mkdir(parents=True, exist_ok=True)
    lockfh = (STAGE / ".lock_fetch").open("a+")
    try:
        fcntl.flock(lockfh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another fetch holds the lock -- exiting")
        return 0
    if STOP.exists():
        STOP.unlink()

    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    nodata = json.loads(NODATA.read_text()) if NODATA.exists() else {}
    work = [(pn, nom) for pn, nom in taiyo_class2_parts()
            if pn not in done and pn not in nodata]
    if a.limit:
        work = work[:a.limit]
    log(f"FETCH start: {len(work)} Taiyo Yuden class-2/3 part numbers "
        f"({len(done)} done, {len(nodata)} unresolvable)")
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
            if cur:
                pts, err = fetch_curve(sess, cur)
            else:
                pts = None
        except requests.RequestException as e:
            pts, err = None, f"transport: {type(e).__name__}"
        if err or not pts:
            fail += 1
            key = err or "no points"
            errs[key] = errs.get(key, 0) + 1
            # "not in TY-COMPAS" / "ambiguous" / "empty series" are definitive answers
            # about the part; HTTP and transport faults stay retryable.
            if key.startswith(("not in TY-COMPAS", "ambiguous", "empty series")):
                nodata[pn] = key
                NODATA.write_text(json.dumps(nodata, indent=0, sort_keys=True))
        else:
            ok += 1
            with OUT.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"partNumber": pn, "currentPartNumber": cur,
                                     "nominal": nom, "points": pts},
                                    ensure_ascii=False) + "\n")
            done.add(pn)
            DONE.write_text(json.dumps(sorted(done)))
        if i % 100 == 0:
            log(f"  {i}/{len(work)}  ok={ok} fail={fail} {dict(list(errs.items())[:3])}")
        time.sleep(a.delay)
    log(f"FETCH end: ok={ok} fail={fail} errors={errs}")
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

    curves = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                curves[r["partNumber"]] = (r["points"], r.get("currentPartNumber"))
    log(f"WRITE: {len(curves)} harvested curves available")
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
            if "Taiyo" not in s:
                out_lines.append(s)
                continue
            obj = json.loads(s)
            c = obj.get("capacitor") or obj
            mi = c.get("manufacturerInfo") or {}
            ds = mi.get("datasheetInfo") or {}
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            if "Taiyo" not in (mi.get("name") or "") or pn not in curves:
                out_lines.append(s)
                continue
            e = ds.setdefault("electrical", {})
            if e.get("capacitanceBiasPoints"):
                out_lines.append(s)
                continue
            pts, cur = curves[pn]
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
                "sourceName": (f"TAIYO YUDEN TY-COMPAS characteristic graph "
                               f"(gtype CDCBB, DC bias){f' as {cur}' if cur and cur != pn else ''}"),
                "sourceUrl": GRAPH,
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
    f = sub.add_parser("fetch")
    f.add_argument("--limit", type=int)
    f.add_argument("--delay", type=float, default=0.3)
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return cmd_fetch(a) if a.cmd == "fetch" else cmd_write(a)


if __name__ == "__main__":
    sys.exit(main())
