#!/usr/bin/env python3
"""ABT #304: harvest Samsung Electro-Mechanics DC-bias curves into capacitanceBiasPoints[].

Source is SEMCO's Component Library (weblib.samsungsem.com). Captured from the UI, then
verified against plain HTTP:

  POST https://weblib.samsungsem.com/graph/mlccEcMakeGraphJson.do
  {"graphType":"DCBias","simulationType":"CHAR",
   "paramList":[{"partsName":"<PN>","vdc":"0","degC":"25"}],"modelTypeList":["P"]}
  -> {"selectPartsList":[{"partsName":..., "dsDcFre":1000, "dsDcSign":1.0,
        "chartList":[{"asixYOption":"Capacitance","data":[{"x":volts,"y":uF},...]},
                     {"asixYOption":"DeltaC",     "data":[...percent...]}]}]}

*** CSRF IS REQUIRED. *** A bare POST gets 403. The token is in the page as
<meta name="_csrf" content="..."> and must come back as the X-CSRF-TOKEN header with
the session cookie from that same page load. That is the only reason a browser looked
necessary; with the token, plain requests works.

TWO SERIES COME BACK -- we take "Capacitance", which is ABSOLUTE (microfarads), not
the "DeltaC" percent series. So no nominal is needed to reconstruct the curve, unlike
KEMET. The response also states the measurement conditions (dsDcFre 1000 Hz,
dsDcSign 1.0 Vrms, degC 25), so temperature and acVoltage are stored FROM THE RESPONSE
rather than assumed -- and omitted if the response does not state them.

PART NUMBERS: our rows often carry one extra trailing packaging character
(CL31B106KAHNNNE vs SEMCO's CL31B106KAHNNN). The harvester tries the exact number
first, then progressively strips up to two trailing characters, and ACCEPTS a result
only if the response's own partsName matches what was asked -- never by position.

Usage:
  samsung_bias_harvest.py fetch [--limit N] [--delay 0.3] -> staging/samsung/bias.jsonl
  samsung_bias_harvest.py write [--apply]                 -> data/capacitors.ndjson
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
STAGE = TAS / "staging" / "samsung"
OUT = STAGE / "bias.jsonl"
DONE = STAGE / "bias_done.json"
NODATA = STAGE / "nodata.json"
LOG = STAGE / "bias_harvest.log"
STOP = STAGE / "STOP"

PAGE = "https://weblib.samsungsem.com/mlcc/mlcc-ec.do"
GRAPH = "https://weblib.samsungsem.com/graph/mlccEcMakeGraphJson.do"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
CLASS2 = {"ceramic-class-2", "ceramic-class-3"}

# Which trailing-character trim resolved a part last time (see fetch_curve). Starts at
# 1 because EVERY one of our 1,152 Samsung part numbers is 15 characters and SEMCO's are
# 14 -- ours carry one extra packaging character. Starting at 0 meant the first request
# for every part was one SEMCO drops the socket on.
PREFERRED_STRIP = 1
STRIP_CONFIRMED = False

# A part number known to have a DC-bias curve, used as a CONTROL. A dropped socket means
# "this part is unknown" only if the control still answers; if the control drops too we
# are rate-limited, and recording the part as unknown would write a false verdict into
# nodata.json that nothing would ever revisit.
CONTROL_PN = "CL21B105KAFNNN"


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def new_session():
    """A session carrying the CSRF token and cookie the graph endpoint demands."""
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.get(PAGE, timeout=60)
    r.raise_for_status()
    m = re.search(r'name="_csrf"[^>]*content="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("no _csrf token on the page -- the page layout changed")
    s.headers.update({"X-CSRF-TOKEN": m.group(1), "Referer": PAGE,
                      "Content-Type": "application/json"})
    return s


def samsung_class2_parts():
    out, seen = [], set()
    with SRC.open(encoding="utf-8") as fh:
        for line in fh:
            if "Samsung" not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = o.get("capacitor") or o
            mi = c.get("manufacturerInfo") or {}
            if "Samsung" not in (mi.get("name") or ""):
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


def ask(sess, parts_name, timeout=60):
    body = {"graphType": "DCBias", "simulationType": "CHAR",
            "paramList": [{"partsName": parts_name, "vdc": "0", "degC": "25"}],
            "modelTypeList": ["P"]}
    r = sess.post(GRAPH, data=json.dumps(body), timeout=timeout)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        j = r.json()
    except ValueError:
        return None, "not JSON"
    sel = (j.get("selectPartsList") or [])
    if not sel:
        return None, "no series"
    rec = sel[0]
    if (rec.get("partsName") or "").upper() != parts_name.upper():
        # the endpoint answered about a DIFFERENT part -- that curve is not ours
        return None, f"answered for {rec.get('partsName')}"
    chart = next((c for c in (rec.get("chartList") or [])
                  if c.get("asixYOption") == "Capacitance"), None)
    if not chart or not chart.get("data"):
        return None, "no capacitance series"
    pts = []
    for row in chart["data"]:
        try:
            v, cap_uf = float(row["x"]), float(row["y"])
        except (KeyError, TypeError, ValueError):
            continue
        p = {"voltage": v, "capacitance": cap_uf * 1e-6}
        # conditions come from the response, never from an assumption
        if isinstance(rec.get("degC"), (int, float)):
            p["temperature"] = float(rec["degC"])
        if isinstance(rec.get("dsDcSign"), (int, float)):
            p["acVoltage"] = float(rec["dsDcSign"])
        pts.append(p)
    return (pts, None) if pts else (None, "empty capacitance series")


def fetch_curve(sess, pn):
    """Try the exact part number, then up to two trailing characters stripped.

    *** AN UNKNOWN PART NUMBER CLOSES THE CONNECTION. *** SEMCO does not answer with an
    error for a part it does not have -- it drops the socket
    ("RemoteDisconnected: Remote end closed connection without response"). Left
    uncaught that aborts the whole candidate loop on the FIRST try, which is exactly
    the try that fails for us: our rows carry one extra packaging character
    (CL31B106KAHNNNE vs CL31B106KAHNNN). So a dropped connection is treated as this
    candidate being wrong, and the next one is tried on a fresh connection.
    """
    global PREFERRED_STRIP, STRIP_CONFIRMED
    last = None
    # Try the trim that worked last time FIRST. Our Samsung rows follow one convention
    # (a single extra packaging character), so after the first success this hits on the
    # first request and no connection ever gets dropped -- which matters because a drop
    # also poisons the session and costs a full page fetch to rebuild.
    order = ([PREFERRED_STRIP] if STRIP_CONFIRMED
             else [PREFERRED_STRIP] + [k for k in (0, 1, 2) if k != PREFERRED_STRIP])
    for k in order:
        cand = pn if k == 0 else pn[:-k]
        if len(cand) < 8:
            continue
        try:
            pts, err = ask(sess, cand)
        except requests.exceptions.ConnectionError:
            # SEMCO drops the socket for a part it does not have, and the session does
            # not survive it -- rebuild before trying the next candidate.
            last = "connection closed (part unknown)"
            try:
                sess = new_session()
            except Exception as e:
                return None, None, f"session rebuild failed: {str(e)[:60]}"
            continue
        if pts:
            PREFERRED_STRIP = k
            STRIP_CONFIRMED = True      # every part follows one convention; stop probing
            return pts, cand, None
        last = err
    return None, None, last or "not found"


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
    work = [(pn, nom) for pn, nom in samsung_class2_parts()
            if pn not in done and pn not in nodata]
    if a.limit:
        work = work[:a.limit]
    log(f"FETCH start: {len(work)} Samsung class-2/3 part numbers "
        f"({len(done)} done, {len(nodata)} not in the SEMCO library)")
    if not work:
        return 0

    sess = new_session()
    ok = fail = 0
    errs = {}
    for i, (pn, nom) in enumerate(work, 1):
        if STOP.exists():
            log("STOP file -- exiting cleanly")
            break
        try:
            pts, used, err = fetch_curve(sess, pn)
        except requests.RequestException as e:
            pts, used, err = None, None, f"transport: {type(e).__name__}"
        if err and "HTTP 403" in err:
            # the CSRF token/session expired mid-run; renew once and retry this part
            log("  403 -- renewing the CSRF session")
            try:
                sess = new_session()
                pts, used, err = fetch_curve(sess, pn)
            except Exception as e:
                err = f"session renew failed: {str(e)[:60]}"
        if err or not pts:
            fail += 1
            key = err or "no points"
            errs[key] = errs.get(key, 0) + 1
            # A drop or a 500 is only a verdict ABOUT THE PART if the control part still
            # answers. SEMCO rate-limits, and under a limit everything drops -- recording
            # those as "unknown" would write false verdicts nothing ever revisits.
            if key.startswith(("connection closed", "HTTP 5")):
                try:
                    sess = new_session()
                    ctrl, _ = ask(sess, CONTROL_PN)
                except Exception:
                    ctrl = None
                if not ctrl:
                    log("  control part also fails -- rate-limited; stopping cleanly, "
                        "cron will resume")
                    break
            # HTTP 500 is SEMCO's answer for a part it cannot model (verified: it is
            # returned consistently for the same parts, e.g. ones whose part number
            # carries a stray lowercase character in our own data). Only transport and
            # session faults stay retryable.
            if not key.startswith(("transport", "session")):
                nodata[pn] = key
                NODATA.write_text(json.dumps(nodata, indent=0, sort_keys=True))
        else:
            ok += 1
            with OUT.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"partNumber": pn, "semcoPartNumber": used,
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
                curves[r["partNumber"]] = (r["points"], r.get("semcoPartNumber"))
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
            if "Samsung" not in s:
                out_lines.append(s)
                continue
            obj = json.loads(s)
            c = obj.get("capacitor") or obj
            mi = c.get("manufacturerInfo") or {}
            ds = mi.get("datasheetInfo") or {}
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            if "Samsung" not in (mi.get("name") or "") or pn not in curves:
                out_lines.append(s)
                continue
            e = ds.setdefault("electrical", {})
            if e.get("capacitanceBiasPoints"):
                out_lines.append(s)
                continue
            pts, used = curves[pn]
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
                "sourceName": ("SAMSUNG ELECTRO-MECHANICS Component Library, DC Bias "
                               "characteristic" + (f" as {used}" if used and used != pn else "")),
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
