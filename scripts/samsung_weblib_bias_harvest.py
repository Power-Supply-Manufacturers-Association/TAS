#!/usr/bin/env python3
"""ABT #304: harvest Samsung DC-bias curves from weblib — the numbers, not a picture.

    python3 scripts/samsung_weblib_bias_harvest.py fetch [--limit N] [--delay S]
    python3 scripts/samsung_weblib_bias_harvest.py write [--apply]

STATUS: WORKS, BUT ITS TARGET SET IS CURRENTLY EMPTY. Read this before running it.

weblib is a different surface from the SEMCO library that scripts/samsung_bias_harvest.py
uses, so it was worth testing whether it carries the 841 parts that one records as
"not in the SEMCO library". It does not. A diversified sample of 10 uppercase part
numbers spanning 10 different series prefixes returned HTTP 500 on every one, while
the control CL05Y105KP6VPN returned 200 with its payload in the same minute — so this
is the parts, not throttling and not transport. A further 682 of the 841 are not
orderable codes at all (see is_orderable).

The extractor itself is verified end-to-end against that control: 81 points at 25 C /
1 kHz, converting to 1.000 uF at 0 V (the anchor, exact) and 0.312 uF at 10 V — a
-68.8 % derating, which is precisely the risk ABT #304 exists to surface.

So this is kept as a working tool for any Samsung part weblib DOES carry — newly
added parts, or a future catalogue refresh — not as a way to close the present gap.
That gap is not closable from Samsung.

WHY NOT DIGITISE THE DATASHEET GRAPH. That was the original plan for this gap and it
was wrong twice over. KEMET, the biggest gap at 2,095 parts, does not publish bias
curves at ALL — KEM_C1050_GOLDMAX_X7R is 19 pages and 86,350 characters of text with
zero occurrences of "DC Bias", which is also why K-SIM has no curve for those parts.
Vishay's 452 rows have no datasheetUrl to open. Meanwhile Samsung, which DOES have
reachable data, ships the data itself:

    "graphType" : "DCBias", "dsDcFre" : 1000, "degC" : 25.0,
    "chartList" : [ { "asixYOption" : "DeltaC",
      "data" : [ {"x":"0","y":"0"}, {"x":"0.1","y":"-0.07078663724660873"}, ... ] } ]

That is pretty-printed JSON sitting in the HTML — no browser, no axis calibration, no
polyline tracing, and no error to characterise. Extracting numbers from a rendering
of numbers would have been strictly worse. Check whether a vendor ships the data
before deciding to read it off a graph.

WHAT IS CHECKED RATHER THAN ASSUMED
  * asixYOption must be "DeltaC". The y values are a PERCENT CHANGE, and converting
    them as if they were farads would be silently wrong by a factor of ~1e6. If
    Samsung ever serves absolute capacitance on this chart, this refuses instead of
    mis-converting.
  * The 0 V point must come back AT the nominal after conversion (to_farads enforces
    it). That is the anchor: it fails loudly if the record's nominal and the vendor's
    curve are not describing the same part.
  * A class-2 curve must not RISE with bias. A rising curve means the wrong chart was
    matched — DCBias, TCC and AC-voltage charts sit in the same payload.

Each row is gated on the CAS schema and Blade Runner before it is written, and
provenance is APPENDED, never replaced, naming weblib and the measurement conditions
the page states (1 kHz, 25 C).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "data" / "capacitors.ndjson"
OUT = REPO / "staging" / "samsung_weblib_bias.jsonl"
PAGE = "https://weblib.samsungsem.com/mlcc/mlcc-ec-data-sheet.do?partNumber="
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
CLASS2 = ("ceramic-class-2", "ceramic-class-3")


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


def samsung_parts_without_curve():
    """[(part_number, nominal_F)] for Samsung class-2/3 rows that have no curve."""
    out, seen = [], set()
    with SRC.open(encoding="utf-8") as fh:
        for line in fh:
            if "amsung" not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = o.get("capacitor") or o
            mi = c.get("manufacturerInfo") or {}
            if "amsung" not in str(mi.get("name")):
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
                out.append((str(pn), nom))
    return out


def is_orderable(pn):
    """A real Samsung MLCC code is all upper-case.

    682 of the 841 uncovered rows carry a lower-case letter (CL05B100K0jFNNE), and
    weblib does not 404 on those — it returns HTTP 500, i.e. the lookup CRASHES on
    the input. Verified against a control in the same minute: CL05Y105KP6VPN returned
    200 with the DCBias payload while CL05B100K0jFNNE returned 500, so this is the
    part number, not rate limiting.

    Those codes are skipped rather than requested. Sending 682 requests that are known
    to crash someone else's application is not a reasonable way to discover they are
    malformed, and the 500s would be indistinguishable from a real outage in the log.
    The malformed references are a catalogue defect, reported separately.
    """
    return pn.isupper() or not any(c.islower() for c in pn)


# The payload is pretty-printed JSON inside the HTML. Rather than regex the numbers
# out — which would silently accept a TCC or AC-voltage chart — find the DCBias block
# and parse it as JSON from its opening brace.
def _block_at(html, idx):
    """Parse the JSON object containing the marker at `idx`, or None."""
    start = html.rfind("{", 0, idx)
    if start < 0:
        return None
    depth = 0
    for i in range(start, min(len(html), start + 400_000)):
        ch = html[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def extract_dcbias(html):
    """The DCBias curve points, or (None, reason).

    The page mentions DCBias more than once: one occurrence is a menu/option entry
    with no chartList, the other is the payload. Parsing the FIRST match returned
    "carries no chartList" — so every occurrence is tried and the first one that
    actually holds data wins. Matching on position alone was the bug.
    """
    marks = [m.start() for m in re.finditer(r'"graphType"\s*:\s*"DCBias"', html)]
    if not marks:
        return None, "no DCBias chart on the page"
    last_why = "DCBias block carries no chartList"
    for idx in marks:
        blk = _block_at(html, idx)
        if not blk:
            continue
        charts = blk.get("chartList") or []
        if not charts:
            continue
        ch0 = charts[0]
        axis = ch0.get("asixYOption")
        if axis != "DeltaC":
            # y is not a percent change — converting it as one would be wrong by ~1e6.
            last_why = f"unexpected y axis {axis!r} (expected DeltaC)"
            continue
        pts = []
        bad = False
        for pt in ch0.get("data") or []:
            try:
                pts.append((float(pt["x"]), float(pt["y"])))
            except (KeyError, TypeError, ValueError):
                bad = True
                break
        if bad:
            last_why = "non-numeric point in the DCBias data"
            continue
        if len(pts) < 5:
            last_why = f"only {len(pts)} points"
            continue
        ys = [y for _, y in pts]
        if any(b - a > 1.0 for a, b in zip(ys, ys[1:])):
            # a class-2 bias curve falls; a rise means the wrong chart was matched
            last_why = "curve rises with bias — wrong chart matched"
            continue
        return {"points": pts,
                "degC": blk.get("degC"),
                "acFreqHz": blk.get("dsDcFre"),
                "acVolts": blk.get("dsDcSign")}, None
    return None, last_why


def cmd_fetch(a):
    todo = samsung_parts_without_curve()
    done = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["partNumber"])
    todo = [t for t in todo if t[0] not in done]
    malformed = [t for t in todo if not is_orderable(t[0])]
    todo = [t for t in todo if is_orderable(t[0])]
    if malformed:
        log(f"skipping {len(malformed)} part numbers that are not orderable codes "
            f"(lower-case letter present, e.g. {malformed[0][0]}) — weblib 500s on these")
    if a.limit:
        todo = todo[: a.limit]
    log(f"FETCH start: {len(todo)} Samsung class-2/3 parts without a curve "
        f"({len(done)} already fetched)")
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    ok = miss = err = 0
    with OUT.open("a", encoding="utf-8") as fh:
        for i, (pn, nom) in enumerate(todo, 1):
            # A transport failure or a 5xx is the SERVER not answering, not an answer
            # about the part. Retried, and if it still fails the part is recorded as
            # inconclusive and left for a later run — never written off as "no curve".
            data = why = None
            last = None
            for attempt in range(4):
                try:
                    r = sess.get(PAGE + pn, timeout=45)
                    if r.status_code >= 500:
                        last = f"HTTP {r.status_code}"
                        time.sleep(2.0 * (attempt + 1))
                        continue
                    if r.status_code != 200:
                        last = f"HTTP {r.status_code}"
                        break
                    data, why = extract_dcbias(r.text)
                    last = None
                    break
                except Exception as e:                            # noqa: BLE001
                    last = type(e).__name__
                    time.sleep(2.0 * (attempt + 1))
            if last and data is None:
                err += 1
                fh.write(json.dumps({"partNumber": pn, "error": last,
                                     "inconclusive": True}) + "\n")
                fh.flush()
                continue
            if data is None:
                miss += 1
                fh.write(json.dumps({"partNumber": pn, "error": why}) + "\n")
            else:
                ok += 1
                fh.write(json.dumps({"partNumber": pn, "nominal": nom, **data}) + "\n")
            fh.flush()
            if i % 50 == 0:
                log(f"  {i}/{len(todo)}  ok={ok} no-curve={miss} err={err}")
            time.sleep(a.delay)
    log(f"FETCH done: ok={ok} no-curve={miss} err={err} -> {OUT}")
    return 0


def to_farads(pts, nominal):
    """percent-change points -> capacitanceBiasPoints[] in SI (house convention).

    C(V) = nominal * (1 + pct/100). The 0 V point must land ON the nominal by
    construction, so a mismatch means the curve and the record are not the same part.
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


def build_validator():
    sys.path.insert(0, str(Path(__file__).parent))
    from extract_tdk_cmc import _build_registry  # noqa: E402
    from jsonschema import Draft202012Validator
    from referencing import Registry
    reg: Registry = _build_registry()
    schema = json.loads((REPO.parent / "CAS" / "schemas" / "capacitor.json").read_text())
    return Draft202012Validator(schema, registry=reg)


def cmd_write(a):
    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    gate = BladeGate("capacitor")

    curves = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("points"):
                curves[r["partNumber"]] = r
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
            if "amsung" not in s:
                out_lines.append(s)
                continue
            obj = json.loads(s)
            c = obj.get("capacitor") or obj
            mi = c.get("manufacturerInfo") or {}
            ds = mi.get("datasheetInfo") or {}
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            if "amsung" not in str(mi.get("name")) or pn not in curves:
                out_lines.append(s)
                continue
            e = ds.setdefault("electrical", {})
            if e.get("capacitanceBiasPoints"):
                out_lines.append(s)
                continue
            cap = e.get("capacitance")
            nominal = cap.get("nominal") if isinstance(cap, dict) else cap
            if not isinstance(nominal, (int, float)) or nominal <= 0:
                skipped.append((pn, "no usable nominal capacitance"))
                out_lines.append(s)
                continue
            rec = curves[pn]
            pts, why = to_farads(rec["points"], nominal)
            if pts is None:
                skipped.append((pn, why))
                out_lines.append(s)
                continue
            degc = rec.get("degC")
            if isinstance(degc, (int, float)):
                for p in pts:
                    p["temperature"] = float(degc)

            e["capacitanceBiasPoints"] = pts
            ds.setdefault("provenance", []).append({
                "source": "manufacturerParametric",
                "sourceName": (
                    "Samsung Electro-Mechanics weblib component library, DC Bias "
                    f"Characteristics chart data ({rec.get('acFreqHz')} Hz, "
                    f"{degc} C) — percent change applied to the record's nominal"),
                "sourceUrl": PAGE + pn,
                "retrievedDate": time.strftime("%Y-%m-%d"),
                "fields": ["capacitanceBiasPoints"],
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
            out_lines.append(json.dumps(obj, separators=(",", ":")))

    log(f"WRITE: {patched} rows patched, {rejected} rejected, {len(skipped)} skipped")
    for b in bad:
        log(f"  REJECTED {b}")
    for pn, why in skipped[:5]:
        log(f"  SKIPPED  {pn}: {why}")
    if not a.apply:
        log("dry run — pass --apply to write")
        return 0
    import os
    tmp = SRC.with_suffix(".ndjson.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(out_lines) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, SRC)
    log(f"replaced {SRC}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--limit", type=int)
    f.add_argument("--delay", type=float, default=1.0)
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return cmd_fetch(a) if a.cmd == "fetch" else cmd_write(a)


if __name__ == "__main__":
    sys.exit(main())
