#!/usr/bin/env python3
"""ABT #390: harvest Samsung Electro-Mechanics ESR-vs-frequency curves into esrPoints[].

WHY: the catalogue's scalar `esr` for ceramics is a binned lookup table, not measurement
(4,144 ceramic values >=1 Ohm take only 96 distinct values, 56.7% whole numbers). A
per-part frequency curve from the manufacturer replaces it.

THE CHART TYPE, AND HOW IT WAS FOUND
------------------------------------
The DC-bias campaign (scripts/samsung_bias_harvest.py) posts graphType "DCBias" to
/graph/mlccEcMakeGraphJson.do. The ESR chart needs TWO changes, and the second one is
the reason a naive attempt returns HTTP 200 with an EMPTY chartList: `simulationType`
is not a constant, it is a per-chart-type lookup. Both were read out of the site's own
JS rather than guessed:

  https://weblib.samsungsem.com/mlcc/mlcc-ec.do   -> #graphTypeBtn buttons carry
      data-chart-type: |Z|, R, |X|, |Z|_R, C, L, Q, DF, DCBias, ACVolt, RippleCurr, TCC
  https://weblib.samsungsem.com/resources/js/common_chart.js  -> SEM.chart.getInfoData
      var simulationType = {'|Z|':'TEMPDC', 'R':'TEMPDC', '|Z|_R':'TEMPDC', ...,
                            'DCBias':'CHAR', 'ACVolt':'CHAR', 'TCC':'CHAR', ...}

There is no button literally labelled "ESR": for a capacitor the ESR IS R, the real part
of the impedance. "|Z|_R" is taken rather than "R" alone because it returns BOTH series
in one request, and |Z| at the bottom of the sweep is what proves the response is our
part (see the identity gate below).

THE EXACT REQUEST:

  POST https://weblib.samsungsem.com/graph/mlccEcMakeGraphJson.do
  X-CSRF-TOKEN: <from <meta name="_csrf"> on the page>   (a bare POST is 403)
  {"graphType":"|Z|_R","simulationType":"TEMPDC",
   "paramList":[{"partsName":"<PN>","vdc":"0","degC":"25"}, ... up to 10],
   "modelTypeList":["P"]}

  -> {"selectPartsList":[{"partsName":..., "vdc":0.0, "degC":25.0,
        "chartList":[{"graphSubType":"|Z|","modelType":"Precise","data":[{x,y},…300]},
                     {"graphSubType":"R",  "modelType":"Precise","data":[{x,y},…300]}]}]}

UNITS WERE READ, NOT ASSUMED. Nothing in the response names a unit. x runs 0.0001 …
6000, which is MEGAHERTZ, not Hz -- proven three ways on CL21B105KAFNNN (1 uF 0805 X7R):
  * |Z| at x=0.0001 is 1587.5 Ohm; 1/(2*pi*100 Hz*1587.5) = 1.0025 uF, the part's own
    nominal. Reading x as Hz would make it 1.0 FARAD.
  * |Z| bottoms out at x=6.90, i.e. 6.9 MHz -- the textbook self-resonance of a 1 uF
    0805. As "Hz" that would be a 6.9 Hz self-resonance.
  * R bottoms out at 4.4 milliohm, the expected HF ESR of a 1 uF MLCC.
y is in OHMS on both series. So x is multiplied by 1e6 on the way in; y is taken as-is.

BATCHING: up to 10 parts per request. Responses are matched BY partsName, never by
position -- a part SEMCO cannot model is silently dropped from selectPartsList, so
position would silently shift every curve after it onto the wrong part.

PART NUMBERS: every one of our 1,592 Samsung rows is 15 characters where SEMCO's are 14
(ours carry one extra packaging character, CL31B106KAHNNNE vs CL31B106KAHNNN). The
already-harvested bias run recorded the exact SEMCO name it resolved for each part, so
that map is reused when available; otherwise one trailing character is stripped, and a
result is accepted ONLY if the response's own partsName equals what was asked.

SOCKET DROPS: SEMCO closes the connection for a part it does not have, and the session
does not survive it. In a batch that costs the whole batch, so a drop triggers a bisect
down to the offending part. A drop is only recorded as a verdict about a PART if the
CONTROL part still answers -- SEMCO also rate-limits, and under a limit everything drops.

RESOLUTION: the server returns 300 points over 100 Hz .. 6 GHz (~39/decade). The staged
curve keeps every 4th point (~10/decade, the density of a printed datasheet impedance
curve) plus the endpoints and the exact ESR-minimum (self-resonance) point. Subsetting
only -- every stored number is a number the server returned.

  samsung_esr_harvest.py fetch [--limit N] [--delay 0.3] -> staging/samsung/esr.jsonl
  samsung_esr_harvest.py write [--apply]                 -> data/capacitors.ndjson
"""
import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import requests

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "samsung"
OUT = STAGE / "esr.jsonl"
BIAS = STAGE / "bias.jsonl"          # reused only for its our-PN -> SEMCO-PN map
NODATA = STAGE / "esr_nodata.json"
AUDIT = STAGE / "esr_replaced_audit.json"
LOG = STAGE / "esr_harvest.log"
STOP = STAGE / "STOP"

PAGE = "https://weblib.samsungsem.com/mlcc/mlcc-ec.do"
GRAPH = "https://weblib.samsungsem.com/graph/mlccEcMakeGraphJson.do"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")

GRAPH_TYPE = "|Z|_R"        # read out of the page's #graphTypeBtn buttons
SIM_TYPE = "TEMPDC"         # read out of common_chart.js's getInfoData lookup
X_TO_HZ = 1e6               # the x axis is MEGAHERTZ -- see the module docstring
BATCH = 10
KEEP_EVERY = 4              # ~39 pts/decade -> ~10 pts/decade
REF_HZ = 100_000.0
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
    m = re.search(r'name="_csrf"[^>]*(?:content|value)="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("no _csrf token on the page -- the page layout changed")
    s.headers.update({"X-CSRF-TOKEN": m.group(1), "Referer": PAGE,
                      "Content-Type": "application/json"})
    return s


# ---------------------------------------------------------------- catalogue

def samsung_parts():
    """[(ourPn, technology, nominal_F)] for Samsung rows with no ESR curve yet."""
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
            p = ds.get("part") or {}
            e = ds.get("electrical") or {}
            if e.get("esrPoints"):
                continue
            pn = p.get("partNumber") or mi.get("reference")
            cap = e.get("capacitance")
            nom = cap.get("nominal") if isinstance(cap, dict) else cap
            if pn and pn not in seen:
                seen.add(pn)
                out.append((pn, p.get("technology"), nom))
    return out


def semco_name_map():
    """ourPn -> the SEMCO part name the DC-bias run actually resolved, when known."""
    m = {}
    if BIAS.exists():
        with BIAS.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("semcoPartNumber"):
                    m[r["partNumber"]] = r["semcoPartNumber"]
    return m


# ---------------------------------------------------------------- fetch

class _Dropped(Exception):
    """SEMCO closed the socket -- at least one part in the batch is unknown to it."""


def ask(sess, names, timeout=120):
    """{semcoName: {"points": [(f_Hz, esr_ohm)], "f0","z0","esr0","nRaw","vdc","degC"}}.

    Only names the server itself echoed back are present; matching is by partsName.
    """
    body = {"graphType": GRAPH_TYPE, "simulationType": SIM_TYPE,
            "paramList": [{"partsName": n, "vdc": "0", "degC": "25"} for n in names],
            "modelTypeList": ["P"]}
    try:
        r = sess.post(GRAPH, data=json.dumps(body), timeout=timeout)
    except requests.exceptions.ConnectionError:
        raise _Dropped()
    if r.status_code != 200:
        raise _HttpError(r.status_code)
    try:
        j = r.json()
    except ValueError:
        raise _HttpError("not JSON")
    out = {}
    for rec in j.get("selectPartsList") or []:
        name = rec.get("partsName")
        if not name:
            continue
        charts = {c.get("graphSubType"): c for c in (rec.get("chartList") or [])}
        zc, rc = charts.get("|Z|"), charts.get("R")
        if not zc or not rc:
            continue
        z, e = [], []
        for row in zc.get("data") or []:
            try:
                z.append((float(row["x"]) * X_TO_HZ, float(row["y"])))
            except (KeyError, TypeError, ValueError):
                continue
        for row in rc.get("data") or []:
            try:
                e.append((float(row["x"]) * X_TO_HZ, float(row["y"])))
            except (KeyError, TypeError, ValueError):
                continue
        if len(z) != len(e) or len(e) < 8:
            continue
        pts = [(round(f, 6), y) for (f, _), (_, y) in zip(z, e) if f > 0 and y > 0]
        if len(pts) < 8:
            continue
        keep = set(range(0, len(pts), KEEP_EVERY))
        keep.add(len(pts) - 1)
        keep.add(min(range(len(pts)), key=lambda i: pts[i][1]))
        out[name] = {
            "points": [pts[i] for i in sorted(keep)],
            "f0": round(z[0][0], 6), "z0": z[0][1], "esr0": e[0][1],
            "nRaw": len(pts),
            "vdc": rec.get("vdc"), "degC": rec.get("degC"),
        }
    return out


class _HttpError(Exception):
    pass


def _rebuild(sess):
    try:
        return new_session()
    except Exception:
        time.sleep(5)
        return new_session()


def control_alive(sess):
    try:
        return bool(ask(sess, [CONTROL_PN]))
    except Exception:
        return False


def cmd_fetch(a):
    import fcntl
    STAGE.mkdir(parents=True, exist_ok=True)
    lockfh = (STAGE / ".lock_esr_fetch").open("a+")
    try:
        fcntl.flock(lockfh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another ESR fetch holds the lock -- exiting")
        return 0
    if STOP.exists():
        STOP.unlink()

    done = set()
    if OUT.exists():
        with OUT.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    done.add(json.loads(line)["partNumber"])
    nodata = json.loads(NODATA.read_text()) if NODATA.exists() else {}
    known = semco_name_map()
    work = [t for t in samsung_parts() if t[0] not in done and t[0] not in nodata]
    if a.limit:
        work = work[:a.limit]
    log(f"FETCH start: {len(work)} Samsung part numbers "
        f"({len(done)} staged, {len(nodata)} known to SEMCO as no-model, "
        f"{len(known)} names already resolved by the DC-bias run)")
    if not work:
        return 0

    sess = new_session()
    ok = miss = 0
    pending = []

    def candidates(pn):
        """SEMCO names to try, best first. The bias run's resolved name wins; otherwise
        one trailing packaging character is stripped (ours are 15 chars, SEMCO's 14)."""
        cands = []
        if pn in known:
            cands.append(known[pn])
        for k in (1, 0, 2):
            c = pn if k == 0 else pn[:-k]
            if len(c) >= 8 and c not in cands:
                cands.append(c)
        return cands

    def run_batch(sess, chunk, depth=0):
        """-> (sess, {ourPn: entry}, {ourPn: reason}). Bisects around a dropped socket."""
        names = [candidates(pn)[0] for pn, _, _ in chunk]
        try:
            got = ask(sess, names)
        except _Dropped:
            sess = _rebuild(sess)
            if len(chunk) == 1:
                return sess, {}, {chunk[0][0]: "connection closed (part unknown to SEMCO)"}
            mid = len(chunk) // 2
            sess, g1, n1 = run_batch(sess, chunk[:mid], depth + 1)
            sess, g2, n2 = run_batch(sess, chunk[mid:], depth + 1)
            g1.update(g2)
            n1.update(n2)
            return sess, g1, n1
        except _HttpError as e:
            if len(chunk) == 1:
                return sess, {}, {chunk[0][0]: f"HTTP {e} (recognised but no model)"}
            mid = len(chunk) // 2
            sess, g1, n1 = run_batch(sess, chunk[:mid], depth + 1)
            sess, g2, n2 = run_batch(sess, chunk[mid:], depth + 1)
            g1.update(g2)
            n1.update(n2)
            return sess, g1, n1
        good, bad = {}, {}
        for (pn, _, _), name in zip(chunk, names):
            if name in got:
                good[pn] = (name, got[name])
            else:
                bad[pn] = "not in selectPartsList"
        return sess, good, bad

    drops = 0
    for i in range(0, len(work), BATCH):
        if STOP.exists():
            log("STOP file -- exiting cleanly")
            break
        chunk = work[i:i + BATCH]
        sess, good, bad = run_batch(sess, chunk)
        for pn, tech, nom in chunk:
            if pn in good:
                name, r = good[pn]
                pending.append(json.dumps({
                    "partNumber": pn, "semcoPartNumber": name, "technology": tech,
                    "nominal": nom, "f0": r["f0"], "z0": r["z0"], "esr0": r["esr0"],
                    "nRaw": r["nRaw"], "vdc": r["vdc"], "degC": r["degC"],
                    "points": r["points"],
                }, ensure_ascii=False))
                ok += 1
        if bad:
            # A drop/500 is a verdict about the PART only if the control still answers;
            # under a rate limit everything drops and recording those would write false
            # verdicts that nothing ever revisits.
            drops += len(bad)
            if not control_alive(sess):
                log("  control part also fails -- rate-limited; stopping cleanly")
                sess = _rebuild(sess)
                break
            for pn, why in bad.items():
                nodata[pn] = why
                miss += 1
        if pending:
            with OUT.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(pending) + "\n")
            pending = []
        NODATA.write_text(json.dumps(nodata, indent=0, sort_keys=True))
        if (i // BATCH) % 20 == 0 and i:
            log(f"  {i + len(chunk)}/{len(work)}  curves={ok} nomodel={miss}")
        time.sleep(a.delay)
    if pending:
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(pending) + "\n")
    NODATA.write_text(json.dumps(nodata, indent=0, sort_keys=True))
    log(f"FETCH end: curves={ok} nomodel={miss}")
    return 0


# ---------------------------------------------------------------- write

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


def interp(points, f):
    """ESR at f by log-log interpolation; None outside the measured range. Never
    extrapolates: ESR outside the swept band is exactly what must not be guessed."""
    if f < points[0][0] or f > points[-1][0]:
        return None
    for (f0, e0), (f1, e1) in zip(points, points[1:]):
        if f0 <= f <= f1:
            if f1 == f0 or e0 <= 0 or e1 <= 0:
                return e0
            t = (math.log10(f) - math.log10(f0)) / (math.log10(f1) - math.log10(f0))
            return 10 ** (math.log10(e0) + t * (math.log10(e1) - math.log10(e0)))
    return None


def cmd_write(a):
    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    gate = BladeGate("capacitor")

    curves = {}
    if OUT.exists():
        with OUT.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    curves[r["partNumber"]] = r
    log(f"WRITE: {len(curves)} staged Samsung ESR curves")
    if not curves:
        return 0
    v = build_validator()

    for attempt in range(3):
        st = SRC.stat()
        before = (st.st_mtime_ns, st.st_size)
        res = _write_pass(a, curves, v, gate)
        if not a.apply:
            return 0
        st = SRC.stat()
        if (st.st_mtime_ns, st.st_size) == before:
            out_lines, replaced, _ = res
            AUDIT.write_text(json.dumps(replaced, indent=1))
            tmp = SRC.with_suffix(".ndjson.samsung_esr_tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for line in out_lines:
                    fh.write(line + "\n")
            os.replace(tmp, SRC)
            log(f"atomically replaced {SRC}; {len(replaced)} old scalars in {AUDIT}")
            return 0
        log(f"  {SRC.name} changed underneath us (another vendor's ESR agent) -- "
            f"redoing the pass (attempt {attempt + 2}/3)")
        gate.__init__("capacitor")
    log("ABORT: could not get a clean read-modify-write window; nothing written")
    return 1


def _write_pass(a, curves, v, gate):
    patched = rejected = 0
    replaced, bad, skipped, tandist = [], [], [], []
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
            p = ds.get("part") or {}
            pn = p.get("partNumber") or mi.get("reference")
            entry = curves.get(pn)
            if "Samsung" not in (mi.get("name") or "") or entry is None:
                out_lines.append(s)
                continue
            e = ds.setdefault("electrical", {})
            if e.get("esrPoints"):
                out_lines.append(s)
                continue
            pts = [(float(f), float(x)) for f, x in entry["points"]]

            # IDENTITY GATE. At the bottom of the sweep a capacitor's |Z| IS its Xc, so
            # 1/(2*pi*f0*|Z|0) must reproduce the record's own nominal. This is what
            # proves the response is OUR part (part numbers had a trailing character
            # stripped to reach SEMCO) and that both axes are the units we read them as.
            nom = entry.get("nominal")
            z0, esr0, f0 = entry.get("z0"), entry.get("esr0"), entry.get("f0")
            if (isinstance(nom, (int, float)) and nom > 0 and z0 and f0
                    and esr0 is not None and esr0 < 0.3 * z0):
                c_implied = 1.0 / (2 * math.pi * f0 * z0)
                if not (0.5 <= c_implied / nom <= 2.0):
                    skipped.append((pn, f"implied C {c_implied:.3e} F vs nominal "
                                        f"{nom:.3e} F (x{c_implied / nom:.3g})"))
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
                if isinstance(nom, (int, float)) and nom > 0:
                    xc = 1.0 / (2 * math.pi * REF_HZ * nom)
                    tandist.append((p.get("technology"), at_ref / xc, pn))
            e.pop("_esrWarning", None)      # no longer estimated
            used = entry.get("semcoPartNumber")
            ds.setdefault("provenance", []).append({
                "source": "manufacturerParametric",
                "sourceName": ("SAMSUNG ELECTRO-MECHANICS Component Library, "
                               f"|Z| & R (ESR) vs frequency chart (graphType {GRAPH_TYPE}, "
                               f"simulationType {SIM_TYPE}, {entry.get('vdc')} Vdc, "
                               f"{entry.get('degC')} degC)"
                               + (f" as {used}" if used and used != pn else "")),
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

    log(f"WRITE {'APPLIED' if a.apply else 'DRY RUN'}: patched={patched} "
        f"rejected={rejected} (scalar esr replaced on {len(replaced)})")
    log(gate.summary())
    if skipped:
        log(f"  SKIPPED {len(skipped)} whose curve disagrees with the record's own nominal:")
        for pn, why in skipped[:6]:
            log(f"    {pn}: {why}")
    for b in bad:
        log(f"  rejected: {b}")
    _report_tan(tandist)
    return out_lines, replaced, tandist


def _report_tan(tandist):
    if not tandist:
        return
    import collections
    by = collections.defaultdict(list)
    for tech, td, pn in tandist:
        by[tech].append((td, pn))
    log(f"  tan(delta) = esr/Xc at {REF_HZ:.0f} Hz over {len(tandist)} curve-derived scalars:")
    for tech in sorted(by):
        vals = sorted(x[0] for x in by[tech])
        n = len(vals)
        q = lambda p: vals[min(n - 1, int(p * n))]
        log(f"    {tech or '(none)':22s} n={n:6d} min={vals[0]:.4g} p50={q(0.5):.4g} "
            f"p95={q(0.95):.4g} max={vals[-1]:.4g} ({max(by[tech])[1]})")


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
