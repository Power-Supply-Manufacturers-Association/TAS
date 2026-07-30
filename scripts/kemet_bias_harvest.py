#!/usr/bin/env python3
"""ABT #304: harvest KEMET DC-bias curves into capacitanceBiasPoints[].

KEMET (now YAGEO) publishes its per-part bias behaviour through K-SIM 3 / "Y-SIM"
(ksim3.kemet.com). The mechanism, captured from a real UI interaction and then
verified against plain curl:

  POST https://ksim3.kemet.com/api/csv/
  Content-Type: application/json
  body: the app's whole simulation state, with
          plotType: "Vbias"
          parts[1].kemetPn = <PART NUMBER>
  -> CSV, 101 lines: "Frequency (Hz),Combined - Change %,<PN> - Change %"
     then 100 rows of  <bias_volts>,<pct>,<pct>

*** NO BROWSER NEEDED. *** curl gets 200s. That is worth stating because the app
LOOKS like it needs one: the curve is computed with no network traffic at all while
you click around, so the obvious conclusion is "client-side, unharvestable". It is
the CSV EXPORT that goes to the server, and it happily answers a replayed body.

HOW THE PAYLOAD WAS PINNED DOWN (the app hides all of this):
  - the app shell at / renders nav only; the real form lives on /capacitor-simulation
  - parts are added by clicking `label[for="capacitance-N"]` -- the checkbox itself is
    an invisible sibling input, so clicking the row or the input does nothing
  - only then does the chart exist, and only then does Export CSV fire

WHAT THE SERVER ACTUALLY KEYS ON -- tested, not assumed:
  - `kemetPn` IS the lookup: a fabricated part number returns an EMPTY series
  - `dielectric` / `voltageRating` / `id` in the payload are IGNORED (changing X7R to
    X5R, 50 V to 16 V, or the internal id to 0 returns byte-identical curves), so no
    id map has to be built and no property has to be correct beyond the part number
  - the bias sweep in the payload is IGNORED too: the server always sweeps 0..rated
    in 100 points. Higher resolution than TDK's 5-22.

UNITS: the response is *percentage change from the 0 V capacitance*, not farads. So
  C(V) = C_nominal * (1 + pct/100)
using the record's own nominal. This is the ONE vendor here whose numbers are a
vendor SIMULATION rather than a measured graph, so the provenance entry says K-SIM
explicitly -- a consumer must be able to tell it apart from the Murata/TDK measured
curves. (User decision, 2026-07-30: store it, with the provenance naming K-SIM.)

Sanity gates on write are the same as the other two vendors: the curve must start at
~nominal, must be monotonically non-increasing in magnitude within tolerance, and every
patched record is validated against CAS and run through the Blade Runner gate.

Usage:
  kemet_bias_harvest.py fetch [--limit N] [--delay 0.25]  -> staging/kemet/bias.jsonl
  kemet_bias_harvest.py write [--apply]                   -> data/capacitors.ndjson
"""
import argparse
import copy
import json
import sys
import time
from pathlib import Path

import requests

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "kemet"
OUT = STAGE / "bias.jsonl"
DONE = STAGE / "bias_done.json"
NODATA = STAGE / "nodata.json"
TRIES = STAGE / "attempts.json"
ATTEMPT_CAP = 3
LOG = STAGE / "bias_harvest.log"
STOP = STAGE / "STOP"

CSV_URL = "https://ksim3.kemet.com/api/csv/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
CLASS2 = {"ceramic-class-2", "ceramic-class-3"}

# The request template is the CAPTURED body, kept verbatim in kemet_ksim_body.json
# (minus the telemetry session id). Do NOT hand-trim it: a version with the seemingly
# decorative parts dropped -- tolerance option lists, the param min/max/step metadata --
# got HTTP 400 on every single part. The server validates more of this state than it
# uses. Only kemetPn/basePn are substituted per part.
BODY_FILE = Path(__file__).with_name("kemet_ksim_body.json")


TEMPLATE = json.loads(BODY_FILE.read_text())


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def kemet_class2_parts():
    """[(part_number, nominal_F)] for KEMET class-2/3 rows with no curve yet."""
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


def fetch_curve(sess, pn, nominal, timeout=45):
    """[(bias_V, pct_change)] from K-SIM, or (None, reason)."""
    body = copy.deepcopy(TEMPLATE)
    body["parts"][1]["kemetPn"] = pn
    body["parts"][1]["basePn"] = pn
    r = sess.post(CSV_URL, json=body, timeout=timeout,
                  headers={"Referer": "https://ksim3.kemet.com/capacitor-simulation"})
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    lines = [l for l in r.text.splitlines() if l.strip()]
    if not lines or not lines[0].lower().startswith("frequency"):
        return None, "unexpected response shape"
    if pn not in lines[0]:
        # the header names the part the server actually resolved; a mismatch means our
        # part number is not the one being plotted, and the curve is not ours to keep
        return None, "part not in response header"
    pts = []
    for line in lines[1:]:
        f = line.split(",")
        if len(f) < 3:
            continue
        try:
            v, pct = float(f[0]), float(f[2])
        except ValueError:
            break                      # trailing markup, not data
        pts.append((v, pct))
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
    tries = json.loads(TRIES.read_text()) if TRIES.exists() else {}
    work = [(pn, nom) for pn, nom in kemet_class2_parts()
            if pn not in done and pn not in nodata]
    if a.limit:
        work = work[:a.limit]
    log(f"FETCH start: {len(work)} KEMET class-2/3 part numbers "
        f"({len(done)} done, {len(nodata)} unknown to K-SIM)")
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
            pts, err = fetch_curve(sess, pn, nom)
        except requests.RequestException as e:
            pts, err = None, f"transport: {type(e).__name__}"
        if err:
            fail += 1
            errs[err] = errs.get(err, 0) + 1
            # "not in response header" / "empty series" is K-SIM's definitive answer
            # that it does not know this part -- record it instead of re-asking forever.
            # Transport and HTTP errors stay retryable.
            if err in ("part not in response header", "empty series"):
                nodata[pn] = err
                NODATA.write_text(json.dumps(nodata, indent=0, sort_keys=True))
            else:
                # Transport/HTTP faults are retryable -- but not forever. K-SIM's server
                # 500s consistently on its specialty families (05HV high-voltage, CK/CCR
                # military, ArcShield), so an uncapped retry means cron re-asks the same
                # 73 parts every 5 minutes for good. After ATTEMPT_CAP tries the part is
                # parked WITH the error, which is a claim about the server, not the part.
                tries[pn] = tries.get(pn, 0) + 1
                if tries[pn] >= ATTEMPT_CAP:
                    nodata[pn] = f"{err} after {tries[pn]} attempts"
                    NODATA.write_text(json.dumps(nodata, indent=0, sort_keys=True))
                TRIES.write_text(json.dumps(tries, indent=0, sort_keys=True))
        else:
            ok += 1
            with OUT.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"partNumber": pn, "nominal": nom,
                                     "percentPoints": pts}, ensure_ascii=False) + "\n")
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


def to_farads(pts, nominal):
    """percent-change points -> capacitanceBiasPoints[] in SI.

    C(V) = nominal * (1 + pct/100). The 0 V point must come out AT the nominal by
    construction (pct is 0 there); anything else means the response was not what we
    think it is, so it is checked rather than trusted.
    """
    out = []
    for v, pct in pts:
        c = nominal * (1.0 + pct / 100.0)
        if c <= 0:
            return None, f"non-physical capacitance at {v} V ({pct}%)"
        out.append({"voltage": v, "capacitance": c})
    if abs(out[0]["capacitance"] / nominal - 1.0) > 0.01:
        return None, "0 V point is not the nominal"
    return out, None


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
                curves[r["partNumber"]] = r["percentPoints"]
    log(f"WRITE: {len(curves)} harvested curves available")
    if not curves:
        return 0

    v = build_validator()
    patched = rejected = 0
    bad, skipped = [], []
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
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            if mi.get("name") != "KEMET" or pn not in curves:
                out_lines.append(s)
                continue
            e = ds.setdefault("electrical", {})
            if e.get("capacitanceBiasPoints"):
                out_lines.append(s)
                continue
            cap = e.get("capacitance")
            nominal = cap.get("nominal") if isinstance(cap, dict) else cap
            if not isinstance(nominal, (int, float)) or nominal <= 0:
                # percent change is meaningless without a nominal to apply it to
                skipped.append((pn, "no usable nominal capacitance"))
                out_lines.append(s)
                continue
            pts, why = to_farads(curves[pn], nominal)
            if pts is None:
                skipped.append((pn, why))
                out_lines.append(s)
                continue

            e["capacitanceBiasPoints"] = pts
            ds.setdefault("provenance", []).append({
                "source": "manufacturerParametric",
                # K-SIM is named explicitly: unlike the Murata/TDK curves these are the
                # vendor's SIMULATION of the part, converted from its percent-change
                # output using the record's own nominal.
                "sourceName": ("KEMET K-SIM 3 simulation, Capacitance vs. Vbias (DC) "
                               "(percent change applied to the nominal)"),
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
            ok_bl, why_bl = gate.check(c)
            if not ok_bl:
                rejected += 1
                if len(bad) < 5:
                    bad.append(f"{pn}: BLADE {why_bl}")
                out_lines.append(s)
                continue
            patched += 1
            out_lines.append(json.dumps(obj, ensure_ascii=False))

    log(f"WRITE {'APPLIED' if a.apply else 'DRY RUN'}: patched={patched} rejected={rejected}")
    log(gate.summary())
    if skipped:
        log(f"  SKIPPED {len(skipped)} that could not be converted honestly:")
        for pn, why in skipped[:6]:
            log(f"    {pn}: {why}")
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
    f.add_argument("--delay", type=float, default=0.25)
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return cmd_fetch(a) if a.cmd == "fetch" else cmd_write(a)


if __name__ == "__main__":
    sys.exit(main())
