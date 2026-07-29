#!/usr/bin/env python3
"""ABT #304: harvest Murata DC-bias curves into capacitanceBiasPoints[].

Plain HTTP, no browser, no auth — the SimSurfing characteristics service answers
curl directly. The request format was CAPTURED from a real UI interaction (see the
ticket); the critical field is `chara_type` (not "chara") with conditions nested in
`parameter{tc,ac}`.

  GET https://ds.murata.co.jp/simserve/characteristics
      ?ReqType=Characteristics&callback=cb
      &ReqChara=[{"partnumber":PN,"chara_type":"c_dcbias_capacitance",
                  "parameter":{"tc":"25","ac":"0.5"}}]
  -> JsonCharaData[0].charadata[0].data = 201 x [[V],[uF]] from 0 V to rated V.

UNITS: the service returns MICROFARADS. CAS stores SI farads, so every value is
scaled by 1e-6. Verified against the 1,311 Murata parts that already carry curves —
the API reproduces them exactly, so this backfill is consistent with existing data.

Writes to a staging JSONL first; a separate --write pass line-patches
capacitors.ndjson with per-record CAS validation. Nothing touches the catalogue
until the data has been fetched and inspected.

Usage:
  murata_bias_harvest.py fetch [--limit N] [--delay 0.4]   -> staging/murata/bias.jsonl
  murata_bias_harvest.py write [--apply]                   -> capacitors.ndjson
"""
import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path

import requests

TAS = Path.home() / "PSMA" / "TAS"
SRC = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "murata"
OUT = STAGE / "bias.jsonl"
DONE = STAGE / "bias_done.json"
LOG = STAGE / "bias_harvest.log"
STOP = STAGE / "STOP"

SERVICE = "https://ds.murata.co.jp/simserve/characteristics"
CHARA = "c_dcbias_capacitance"
TC = "25"          # degC, matches the existing records
AC = "0.5"         # Vrms, matches the existing records
CLASS2 = {"ceramic-class-2", "ceramic-class-3"}


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def catalog_index():
    """{part_number: ac_vrms} from the bulk mlcc.csv.

    The AC measurement voltage is NOT a free choice: the service rejects anything but
    the part's own condition ("Specified AC is not matched with the options"). The
    catalogue states it per part in `Condition` (e.g. "1MHz / 1Vrms", "120Hz /
    0.5Vrms"), so read it rather than assuming 0.5.
    """
    import csv
    import re
    p = STAGE / "mlcc.csv"
    if not p.exists():
        return {}
    out = {}
    with p.open(encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            pn = (r.get("part_number") or "").strip()
            if not pn:
                continue
            m = re.search(r"([\d.]+)\s*Vrms", r.get("Condition") or "")
            out[pn] = m.group(1) if m else AC
    return out


def resolve(pn, cat):
    """Map our part number onto one the service knows.

    Our rows often carry a trailing packaging letter the catalogue omits
    (GRM31CR61H106KA12L -> GRM31CR61H106KA12). Resolving LOCALLY against the bulk
    catalogue avoids burning a request per part just to be told it does not exist.
    """
    if not cat or pn in cat:
        return pn
    for k in range(1, 4):                 # strip up to 3 trailing chars
        if len(pn) - k >= 12 and pn[:-k] in cat:
            return pn[:-k]
    return None


def murata_class2_parts():
    """Every Murata class-2/3 part that does not already carry a curve."""
    out = []
    with SRC.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or "Murata" not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = o.get("capacitor") or o
            mi = c.get("manufacturerInfo") or {}
            if mi.get("name") != "Murata":
                continue
            ds = mi.get("datasheetInfo") or {}
            e = ds.get("electrical") or {}
            # technology lives under datasheetInfo.PART, not electrical -- reading it
            # from electrical silently matches nothing (all None).
            tech = (ds.get("part") or {}).get("technology") or e.get("technology")
            if tech not in CLASS2:
                continue
            if e.get("capacitanceBiasPoints"):
                continue                      # already has one
            # manufacturerInfo.reference is None on most un-curved Murata rows; the
            # orderable number lives in datasheetInfo.part.partNumber. Prefer that.
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            if pn:
                out.append(pn)
    return out


def worklist():
    """(our_pn, murata_pn, ac_vrms) triples the service should recognise."""
    idx = catalog_index()
    cat = set(idx)
    triples, unresolved = [], 0
    for pn in murata_class2_parts():
        r = resolve(pn, cat)
        if r:
            triples.append((pn, r, idx.get(r, AC)))
        else:
            unresolved += 1
    return triples, unresolved


def fetch_curve(sess, pn, ac=AC, timeout=60):
    req = [{"partnumber": pn, "chara_type": CHARA,
            "parameter": {"tc": TC, "ac": ac}}]
    params = {"callback": "cb", "ReqType": "Characteristics",
              "ReqChara": json.dumps(req, separators=(",", ":"))}
    r = sess.get(SERVICE, params=params, timeout=timeout)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    t = r.text
    try:
        j = json.loads(t[t.index("(") + 1:t.rindex(")")])
    except Exception as e:
        return None, f"unparseable: {str(e)[:60]}"
    arr = j.get("JsonCharaData") or []
    if not arr:
        return None, "empty JsonCharaData"
    cd = (arr[0].get("charadata") or [{}])[0]
    if cd.get("error"):
        return None, cd["error"]
    data = cd.get("data")
    if not isinstance(data, list) or not data:
        return None, "no data array"
    pts = []
    for row in data:
        try:
            v = float(row[0][0])
            cap_uf = float(row[1][0])
        except Exception:
            continue
        pts.append({"voltage": v,
                    "capacitance": cap_uf * 1e-6,      # uF -> F (SI)
                    "temperature": float(TC),
                    "acVoltage": float(ac)})
    if not pts:
        return None, "no parseable points"
    return pts, None


def cmd_fetch(a):
    STAGE.mkdir(parents=True, exist_ok=True)
    if STOP.exists():
        STOP.unlink()
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    pairs, unresolved = worklist()
    pairs = [t for t in pairs if t[0] not in done]
    if a.limit:
        pairs = pairs[:a.limit]
    log(f"FETCH start: {len(pairs)} resolvable Murata class-2/3 parts without a curve "
        f"({len(done)} done, {unresolved} unresolvable against the bulk catalogue)")

    sess = requests.Session()
    ok = fail = 0
    errs = {}
    for i, (pn, murata_pn, ac) in enumerate(pairs, 1):
        if STOP.exists():
            log("STOP file -- exiting cleanly")
            break
        pts, err = fetch_curve(sess, murata_pn, ac)
        if err:
            fail += 1
            errs[err] = errs.get(err, 0) + 1
        else:
            ok += 1
            with OUT.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"partNumber": pn, "points": pts},
                                    ensure_ascii=False) + "\n")
            done.add(pn)
            DONE.write_text(json.dumps(sorted(done)))
        if i % 50 == 0:
            log(f"  {i}/{len(parts)}  ok={ok} fail={fail}  {dict(list(errs.items())[:3])}")
        time.sleep(a.delay)
    log(f"FETCH end: ok={ok} fail={fail} errors={errs}")
    return 0


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    PSMA = Path.home() / "PSMA"
    by = {}
    for repo in ("PEAS", "CAS"):
        for p in (PSMA / repo / "schemas").rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by[s["$id"]] = s
    res = [Resource(contents=s, specification=DRAFT202012) for s in by.values()]
    reg = Registry().with_resources([(r.contents["$id"], r) for r in res])
    return Draft202012Validator(
        json.loads((PSMA / "CAS" / "schemas" / "capacitor.json").read_text()), registry=reg)


def cmd_write(a):
    import os
    curves = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                curves[r["partNumber"]] = r["points"]
    log(f"WRITE: {len(curves)} harvested curves available")
    if not curves:
        return 0

    v = build_validator()
    patched = rejected = 0
    bad = []
    out_lines = []
    with SRC.open(encoding="utf-8") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            if "Murata" not in s:
                out_lines.append(s)
                continue
            obj = json.loads(s)
            c = obj.get("capacitor") or obj
            mi = c.get("manufacturerInfo") or {}
            ref = mi.get("reference")
            if mi.get("name") != "Murata" or ref not in curves:
                out_lines.append(s)
                continue
            ds = mi.setdefault("datasheetInfo", {})
            e = ds.setdefault("electrical", {})
            if e.get("capacitanceBiasPoints"):
                out_lines.append(s)
                continue
            e["capacitanceBiasPoints"] = curves[ref]
            ds.setdefault("provenance", []).append({
                "source": "manufacturerParametric",
                "sourceName": "Murata SimSurfing characteristics service (c_dcbias_capacitance)",
                "sourceUrl": SERVICE,
                "retrievedDate": time.strftime("%Y-%m-%d"),
            })
            errs = sorted(v.iter_errors(c), key=lambda x: x.path)
            if errs:
                rejected += 1
                if len(bad) < 5:
                    bad.append(f"{ref}: {errs[0].message[:140]}")
                out_lines.append(s)          # leave original untouched
                continue
            patched += 1
            out_lines.append(json.dumps(obj, ensure_ascii=False))

    log(f"WRITE {'APPLIED' if a.apply else 'DRY RUN'}: patched={patched} rejected={rejected}")
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
    f = sub.add_parser("fetch"); f.add_argument("--limit", type=int); f.add_argument("--delay", type=float, default=0.4)
    w = sub.add_parser("write"); w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return cmd_fetch(a) if a.cmd == "fetch" else cmd_write(a)


if __name__ == "__main__":
    sys.exit(main())
