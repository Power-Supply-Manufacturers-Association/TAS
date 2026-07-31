#!/usr/bin/env python3
"""ABT #390: harvest KEMET ESR-vs-frequency curves into esrPoints[].

WHY: the catalogue's scalar `esr` for ceramics is not measurement. 4,144 ceramic ESR
values >=1 Ohm take only 96 DISTINCT values, 56.7% of them whole numbers
(15/21/30/40/45/60/80/120/150/200 Ohm). That is a binned lookup table. A per-part
frequency curve from the manufacturer replaces it.

THE CHART TYPE, AND HOW IT WAS FOUND
------------------------------------
K-SIM 3's CSV export (ksim3.kemet.com/api/csv/) is driven by `plotType`. The DC-bias
campaign (scripts/kemet_bias_harvest.py) used "Vbias". The ESR value was read straight
out of the app's own bundle rather than guessed -- no browser needed:

    curl https://ksim3.kemet.com/js/app.fd78906a.js | grep -o 'plotType:{title:"Chart Type".*'
    -> options:[{label:"Impedance & ESR",       value:"Imp,ESR"},
                {label:"Capacitance & Inductance", value:"Cap,Ind"},
                {label:"Current & Voltage",     value:"I,V"},
                {label:"S11"...},{label:"S21"...},{label:"SPICE Model",value:"Spice"},
                {label:"Capacitance vs. Vbias (DC)", value:"Vbias"}, ...]

So the request is the CAPTURED body in scripts/kemet_ksim_body.json (kept verbatim --
a hand-trimmed version 400s on every part) with THREE substitutions:

    POST https://ksim3.kemet.com/api/csv/     Content-Type: application/json
      plotType        = "Imp,ESR"
      start           = "100"          (100 Hz; the default 10000 truncates the low end)
      stop            = "1000000000"   (1 GHz; the default 1e10 is model extrapolation)
      parts[1..N]     = the template part object with kemetPn/basePn/id replaced

    -> CSV, 352 lines:
       "Frequency (Hz),Combined - Imp,Combined - ESR,<PN1> - Imp,<PN1> - ESR,<PN2> - ..."
       then 351 rows of  <f_Hz>,<ohm>,<ohm>,...   (50 points per decade, fixed)

UNITS WERE READ, NOT ASSUMED. The response has no unit in its header, so the Imp column
was checked against 1/(2*pi*f*C) using the catalogue's own nominal at the lowest
frequency, on parts spanning three decades of capacitance:
    C0201C103K4PACTU (10 nF)  -> 156529 Ohm at 100 Hz  => 10.17 nF
    C0402C104K4RACTU (100 nF) ->  15769 Ohm at 100 Hz  => 100.9 nF
    T491A105K016AT   (1 uF)   ->   1591.8 Ohm at 100 Hz =>  1.000 uF
Absolute OHMS, not the percent-change that the Vbias chart returns. The same check also
proves the server resolves the real part and ignores the template's capValue/dielectric.

BATCHING: up to 10 real parts per request (11 `parts` entries including the "Combined"
pseudo-part). 12+ is a hard HTTP 500, so on any 500 the batch is BISECTED down to the
single offending part rather than the whole batch being lost.

"UNKNOWN PART" IS AN ALL-ZERO COLUMN, not an absent one. Unlike the Vbias chart, the
header echoes every requested part number whether K-SIM knows it or not; a part it has
no model for comes back as 0,0,0,... So the verdict is read from the data, and a part
whose whole column is zero is recorded in nodata.json instead of being invented.

RESOLUTION: the server's grid is a fixed 50 points/decade and cannot be lowered (`line`
is ignored). 351 points/part x 42k parts would add ~300 MB to capacitors.ndjson, so the
staged curve is a log-uniform SUBSET -- every 5th point, i.e. 10 points/decade, the
density of a printed datasheet impedance curve -- plus the endpoints and the exact
ESR-minimum (self-resonance) point, which is the one feature decimation could blur.
Subsetting, never averaging or resampling: every stored number is a number the server
returned.

PROVENANCE says K-SIM explicitly. Like the bias curves, these are the vendor's own
simulation of the part (from its measured S-parameter models), not a lab sweep, and a
consumer must be able to tell that apart from a measured graph.

  kemet_esr_harvest.py fetch [--limit N] [--delay 0.2]  -> staging/kemet/esr.jsonl
  kemet_esr_harvest.py write [--apply]                  -> data/capacitors.ndjson
"""
import argparse
import copy
import json
import math
import os
import sys
import time
from pathlib import Path

import requests

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "kemet"
OUT = STAGE / "esr.jsonl"
NODATA = STAGE / "esr_nodata.json"
AUDIT = STAGE / "esr_replaced_audit.json"
LOG = STAGE / "esr_harvest.log"
STOP = STAGE / "STOP"

CSV_URL = "https://ksim3.kemet.com/api/csv/"
REFERER = "https://ksim3.kemet.com/capacitor-simulation"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")

PLOT_TYPE = "Imp,ESR"       # read out of K-SIM's own bundle -- see the module docstring
F_START = "100"             # Hz
F_STOP = "1000000000"       # Hz
BATCH = 10                  # 11 `parts` entries including "Combined"; 12+ is a hard 500
KEEP_EVERY = 5              # 50 pts/decade -> 10 pts/decade
REF_HZ = 100_000.0          # conventional datasheet reference frequency for the scalar

BODY_FILE = Path(__file__).with_name("kemet_ksim_body.json")
TEMPLATE = json.loads(BODY_FILE.read_text())


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ---------------------------------------------------------------- catalogue

def kemet_parts():
    """[(partNumber, technology, nominal_F)] for KEMET rows with no ESR curve yet."""
    out, seen = [], set()
    with SRC.open(encoding="utf-8") as fh:
        for line in fh:
            if '"KEMET"' not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = o.get("capacitor") or o
            mi = c.get("manufacturerInfo") or {}
            if mi.get("name") != "KEMET":
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


# ---------------------------------------------------------------- fetch

def ask(sess, pns, timeout=120):
    """CSV -> {pn: {"points": [(f, esr)], "z0": ohm, "esr0": ohm, "f0": Hz}}.

    Raises _Http500 so the caller can bisect; anything else is a transport error.
    """
    body = copy.deepcopy(TEMPLATE)
    body["plotType"] = PLOT_TYPE
    body["start"] = F_START
    body["stop"] = F_STOP
    proto = body["parts"][1]
    body["parts"] = [body["parts"][0]] + [
        dict(proto, kemetPn=pn, basePn=pn, id=900000 + i) for i, pn in enumerate(pns)]
    r = sess.post(CSV_URL, json=body, timeout=timeout, headers={"Referer": REFERER})
    if r.status_code == 500:
        raise _Http500()
    if r.status_code != 200:
        raise _HttpOther(r.status_code)
    lines = [l for l in r.text.splitlines() if l.strip()]
    if not lines or not lines[0].lower().startswith("frequency"):
        raise _HttpOther("unexpected response shape")
    hdr = lines[0].split(",")
    rows = [l.split(",") for l in lines[1:]]
    try:
        freqs = [float(row[0]) for row in rows]
    except (ValueError, IndexError):
        raise _HttpOther("unparseable frequency column")
    out = {}
    for pn in pns:
        try:
            ci = hdr.index(f"{pn} - ESR")
            zi = hdr.index(f"{pn} - Imp")
        except ValueError:
            out[pn] = None          # the server did not echo it back at all
            continue
        esr, imp = [], []
        ok = True
        for row in rows:
            try:
                esr.append(float(row[ci]))
                imp.append(float(row[zi]))
            except (ValueError, IndexError):
                ok = False
                break
        if not ok or len(esr) != len(freqs):
            out[pn] = None
            continue
        if not any(x > 0 for x in esr):
            out[pn] = None          # K-SIM has no model for this part: all-zero column
            continue
        pts = [(f, e) for f, e in zip(freqs, esr) if f > 0 and e > 0]
        if len(pts) < 8:
            out[pn] = None
            continue
        # keep every KEEP_EVERY-th point plus the endpoints and the ESR minimum (SRF)
        keep = set(range(0, len(pts), KEEP_EVERY))
        keep.add(len(pts) - 1)
        keep.add(min(range(len(pts)), key=lambda i: pts[i][1]))
        out[pn] = {
            "points": [pts[i] for i in sorted(keep)],
            "f0": freqs[0], "z0": imp[0], "esr0": esr[0],
            "nRaw": len(pts),
        }
    return out


class _Http500(Exception):
    pass


class _HttpOther(Exception):
    pass


def ask_bisect(sess, pns, results, nodata):
    """ask() with a batch that 500s split in half until the culprit is a single part."""
    try:
        got = ask(sess, pns)
    except _Http500:
        if len(pns) == 1:
            nodata[pns[0]] = "HTTP 500 (K-SIM cannot model this part)"
            return
        mid = len(pns) // 2
        ask_bisect(sess, pns[:mid], results, nodata)
        ask_bisect(sess, pns[mid:], results, nodata)
        return
    for pn, v in got.items():
        if v is None:
            nodata[pn] = "no model (all-zero series)"
        else:
            results[pn] = v


def staged_part_numbers():
    seen = set()
    if OUT.exists():
        with OUT.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    seen.add(json.loads(line)["partNumber"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return seen


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

    done = staged_part_numbers()
    nodata = json.loads(NODATA.read_text()) if NODATA.exists() else {}
    work = [t for t in kemet_parts() if t[0] not in done and t[0] not in nodata]
    if a.limit:
        work = work[:a.limit]
    log(f"FETCH start: {len(work)} KEMET part numbers to ask "
        f"({len(done)} already staged, {len(nodata)} known to K-SIM as no-model)")
    if not work:
        return 0

    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    ok = miss = 0
    errs = {}
    pending = []
    t_start = time.time()
    for i in range(0, len(work), BATCH):
        if STOP.exists():
            log("STOP file -- exiting cleanly")
            break
        chunk = work[i:i + BATCH]
        pns = [t[0] for t in chunk]
        results, nd = {}, {}
        try:
            ask_bisect(sess, pns, results, nd)
        except (_HttpOther, requests.RequestException) as e:
            key = f"{type(e).__name__}:{str(e)[:40]}"
            errs[key] = errs.get(key, 0) + 1
            time.sleep(2.0)
            continue                     # transport faults stay retryable: no verdict
        for pn, tech, nom in chunk:
            if pn in results:
                r = results[pn]
                pending.append(json.dumps({
                    "partNumber": pn, "technology": tech, "nominal": nom,
                    "f0": r["f0"], "z0": r["z0"], "esr0": r["esr0"], "nRaw": r["nRaw"],
                    "points": r["points"],
                }, ensure_ascii=False))
                ok += 1
        for pn, why in nd.items():
            nodata[pn] = why
            miss += 1
        if len(pending) >= 200 or i + BATCH >= len(work):
            with OUT.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(pending) + "\n")
            pending = []
            NODATA.write_text(json.dumps(nodata, indent=0, sort_keys=True))
        if (i // BATCH) % 100 == 0 and i:
            rate = (i + BATCH) / max(time.time() - t_start, 1e-9)
            log(f"  {i + len(chunk)}/{len(work)}  curves={ok} nomodel={miss} "
                f"{rate:.1f} parts/s  eta={(len(work) - i) / max(rate, 1e-9) / 60:.0f} min "
                f"{dict(list(errs.items())[:2])}")
        time.sleep(a.delay)
    if pending:
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(pending) + "\n")
    NODATA.write_text(json.dumps(nodata, indent=0, sort_keys=True))
    log(f"FETCH end: curves={ok} nomodel={miss} transport_errors={errs}")
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
    """ESR at f by log-log interpolation; None outside the measured range.

    Never extrapolates: ESR outside the swept band is exactly what must not be guessed.
    """
    if f < points[0][0] or f > points[-1][0]:
        return None
    for (f0, e0), (f1, e1) in zip(points, points[1:]):
        if f0 <= f <= f1:
            if f1 == f0 or e0 <= 0 or e1 <= 0:
                return e0
            t = (math.log10(f) - math.log10(f0)) / (math.log10(f1) - math.log10(f0))
            return 10 ** (math.log10(e0) + t * (math.log10(e1) - math.log10(e0)))
    return None


def load_curves():
    curves = {}
    if not OUT.exists():
        return curves
    with OUT.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                curves[r["partNumber"]] = r
    return curves


def cmd_write(a):
    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    gate = BladeGate("capacitor")

    curves = load_curves()
    log(f"WRITE: {len(curves)} staged KEMET ESR curves")
    if not curves:
        return 0
    v = build_validator()

    for attempt in range(3):
        stat = SRC.stat()
        before = (stat.st_mtime_ns, stat.st_size)
        res = _write_pass(a, curves, v, gate)
        if not a.apply:
            return 0
        stat = SRC.stat()
        if (stat.st_mtime_ns, stat.st_size) == before:
            _commit(res)
            return 0
        log(f"  {SRC.name} changed underneath us (another vendor's ESR agent) -- "
            f"redoing the pass (attempt {attempt + 2}/3)")
        gate.__init__("capacitor")
    log("ABORT: could not get a clean read-modify-write window; nothing written")
    return 1


def _commit(res):
    out_lines, replaced, tandist = res
    AUDIT.write_text(json.dumps(replaced, indent=1))
    tmp = SRC.with_suffix(".ndjson.kemet_esr_tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for line in out_lines:
            fh.write(line + "\n")
    os.replace(tmp, SRC)
    log(f"atomically replaced {SRC}; {len(replaced)} old scalars recorded in {AUDIT}")


def _write_pass(a, curves, v, gate):
    patched = rejected = 0
    replaced, bad, skipped = [], [], []
    tandist = []
    out_lines = []
    with SRC.open(encoding="utf-8") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            if '"KEMET"' not in s:
                out_lines.append(s)
                continue
            obj = json.loads(s)
            c = obj.get("capacitor") or obj
            mi = c.get("manufacturerInfo") or {}
            ds = mi.get("datasheetInfo") or {}
            p = ds.get("part") or {}
            pn = p.get("partNumber") or mi.get("reference")
            entry = curves.get(pn)
            if mi.get("name") != "KEMET" or entry is None:
                out_lines.append(s)
                continue
            e = ds.setdefault("electrical", {})
            if e.get("esrPoints"):
                out_lines.append(s)
                continue
            pts = [(float(f), float(x)) for f, x in entry["points"]]

            # IDENTITY GATE. At the bottom of the sweep a capacitor's |Z| IS its Xc, so
            # 1/(2*pi*f0*|Z|0) must reproduce the record's own nominal. This proves the
            # server resolved OUR part and that the columns are ohms -- if it disagrees,
            # the curve belongs to something else and must not be written. Only applied
            # where |Z| is actually capacitive there (ESR a small fraction of |Z|);
            # for a large low-ESR-dominated part it says nothing and is not forced.
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
            ds.setdefault("provenance", []).append({
                "source": "manufacturerParametric",
                "sourceName": ("KEMET K-SIM 3 simulation, Impedance & ESR chart "
                               f"(plotType {PLOT_TYPE}, {F_START} Hz to {F_STOP} Hz)"),
                "sourceUrl": CSV_URL,
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
    """tan(delta) = ESR / Xc at 100 kHz, per technology. Above ~0.5 on a CERAMIC means
    the units or the chart are wrong; on an electrolytic well past its SRF it is real."""
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
        worst = max(by[tech])
        log(f"    {tech or '(none)':32s} n={n:6d} min={vals[0]:.4g} p50={q(0.5):.4g} "
            f"p95={q(0.95):.4g} max={vals[-1]:.4g} ({worst[1]})")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--limit", type=int)
    f.add_argument("--delay", type=float, default=0.2)
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return cmd_fetch(a) if a.cmd == "fetch" else cmd_write(a)


if __name__ == "__main__":
    sys.exit(main())
