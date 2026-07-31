#!/usr/bin/env python3
"""Harvest WAGO connector mating relations from WAGO's own counterparts endpoint.

WAGO publishes the mating relation as first-class data, and it was never pulled:

    GET https://www.wago.com/wagoapi/v2/global-wago/products/{code}/counterparts
        ?apiVersion=2&fields=FULL&lang=en_GG
    -> {"products": [{"code": "733-202", "name": ..., "url": ...}, ...]}

    (A '/' inside a WAGO item number is written '_' in the path, same as the
     classifications endpoint: 2606-2103/010-000 -> 2606-2103_010-000.)

These are EXACT counterpart item numbers, not series stems — 733-102 (1-conductor female
connector) returns 733-202 (the male connector it plugs into). That is the strongest form
matesWith can carry, so nothing is inferred here at all.

The endpoint answers 200 with an empty object for a part that has no counterpart (a
splicing connector, a ferrule), so an empty result is a real answer and is recorded as
such — the part is marked checked, not retried forever.

  wago_counterparts_harvest.py sample
  wago_counterparts_harvest.py pull            # -> staging/wago/counterparts.ndjson
  wago_counterparts_harvest.py write [--apply]
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
STAGE = TAS / "staging" / "wago"
OUT = STAGE / "counterparts.ndjson"
AUDIT = STAGE / "counterparts_audit.json"
LIVE = TAS / "data" / "connectors.ndjson"
MFR = "WAGO"

BASE = "https://www.wago.com/wagoapi/v2/global-wago/products"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")

_lock = threading.Lock()


def fetch(code, tries=4):
    path = code.replace("/", "_")
    url = (f"{BASE}/{urllib.parse.quote(path)}/counterparts"
           "?apiVersion=2&fields=FULL&lang=en_GG")
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"_notFound": True}
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(1 + 2 * i)
    return {"_error": str(last)[:120]}


def tas_codes():
    codes = []
    with LIVE.open(encoding="utf-8") as fh:
        for ln in fh:
            if MFR not in ln:
                continue
            d = json.loads(ln)
            c = d.get("connector") or d
            mi = c.get("manufacturerInfo") or {}
            if mi.get("name") == MFR and mi.get("reference"):
                codes.append(mi["reference"])
    return sorted(set(codes))


def normalise(doc):
    out = []
    for p in (doc or {}).get("products", []) or []:
        code = p.get("code")
        if code:
            out.append(code)
    return sorted(set(out))


def cmd_sample(a):
    codes = tas_codes()[:12]
    print(f"{len(codes)} sample codes of {len(tas_codes())} WAGO parts in TAS")
    for c in codes:
        d = fetch(c)
        print(f"  {c:<22} -> {normalise(d) or ('(none)' if 'products' in d or not d else d)}")
    return 0


def cmd_pull(a):
    STAGE.mkdir(parents=True, exist_ok=True)
    done = set()
    if OUT.exists():
        with OUT.open(encoding="utf-8") as fh:
            for ln in fh:
                if ln.strip():
                    done.add(json.loads(ln)["code"])
    codes = [c for c in tas_codes() if c not in done]
    print(f"{len(done)} already fetched; {len(codes)} to go", flush=True)

    n = [0]
    with OUT.open("a", encoding="utf-8") as fh:
        def one(code):
            d = fetch(code)
            rec = {"code": code, "counterparts": normalise(d)}
            if "_error" in d:
                rec["error"] = d["_error"]
            if d.get("_notFound"):
                rec["notFound"] = True
            with _lock:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n[0] += 1
                if n[0] % 500 == 0:
                    fh.flush()
                    print(f"  {n[0]}/{len(codes)}", flush=True)

        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            list(ex.map(one, codes))
    print("pull complete", flush=True)
    return 0


def cmd_write(a):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from we_connectors_mating import build_validator, merge
    from blade_gate import BladeGate
    gate = BladeGate("connector")
    validator = build_validator()

    edges, empties, errors = {}, 0, 0
    with OUT.open(encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            r = json.loads(ln)
            if r.get("error"):
                errors += 1
                continue
            if not r["counterparts"]:
                empties += 1
                continue
            edges[r["code"]] = [{"series": c, "relation": "mates"}
                                for c in r["counterparts"]]
    print(f"{len(edges)} parts with counterparts; {empties} answered 'none'; "
          f"{errors} errored")

    stats = Counter()
    rejected = []
    tmp = LIVE.with_suffix(".ndjson.wmating_tmp")
    with LIVE.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as out:
        for raw in src:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            stats["total"] += 1
            if MFR not in s:
                out.write(s + "\n")
                continue
            obj = json.loads(s)
            c = obj.get("connector") or obj
            mi = c.get("manufacturerInfo") or {}
            ref = mi.get("reference")
            if mi.get("name") != MFR or ref not in edges:
                out.write(s + "\n")
                continue
            ds = mi.setdefault("datasheetInfo", {})
            mating = ds.setdefault("mating", {})
            before = json.dumps(mating.get("matesWith"), sort_keys=True)
            mating["matesWith"] = merge(mating.get("matesWith"), edges[ref])
            if json.dumps(mating["matesWith"], sort_keys=True) == before:
                stats["unchanged"] += 1
                out.write(s + "\n")
                continue
            errs = sorted(validator.iter_errors(c), key=lambda e: e.path)
            if errs:
                stats["rejected_invalid"] += 1
                if len(rejected) < 5:
                    rejected.append(f"{ref}: {errs[0].message[:150]}")
                out.write(s + "\n")
                continue
            ok, why = gate.check(c)
            if not ok:
                stats["rejected_blade"] += 1
                if len(rejected) < 5:
                    rejected.append(f"{ref}: BLADE {why}")
                out.write(s + "\n")
                continue
            stats["patched"] += 1
            stats["edges_written"] += len(edges[ref])
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")

    if a.apply:
        os.replace(tmp, LIVE)
    else:
        tmp.unlink()
    print("APPLIED" if a.apply else "DRY RUN — nothing written")
    for k in ("total", "patched", "edges_written", "unchanged",
              "rejected_invalid", "rejected_blade"):
        print(f"  {k:20} {stats[k]}")
    if rejected:
        print("  -- left unpatched --")
        for r in rejected:
            print(f"     {r}")
    AUDIT.write_text(json.dumps({"stats": dict(stats), "no_counterpart": empties,
                                 "errored": errors, "rejected": rejected}, indent=1))
    if not a.apply:
        print("Re-run with --apply to write.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    p = sub.add_parser("pull")
    p.add_argument("--workers", type=int, default=16)
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return {"sample": cmd_sample, "pull": cmd_pull, "write": cmd_write}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
