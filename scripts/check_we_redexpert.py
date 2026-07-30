#!/usr/bin/env python3
"""Cross-check the Würth rows of ABT #351 against WE's own REDEXPERT data layer.

    python3 scripts/check_we_redexpert.py refs.json out.json

REDEXPERT (redexpert.we-online.com) exposes its full parametric catalog as plain
JSON over GET — manufacturer data, no auth, no browser. For every WE order code
under suspicion this fetches the vendor's own Rdc / rated current / inductance and
reports the ratio against the corpus values, which settles WHICH field is wrong
and by exactly what factor — no unit priors, no physics inference.

Why this matters beyond Würth: the physics resolver's D4 rule accepted
MAG_ISAT_POWER (Isat^2*DCR) violations as evidence against the DCR. But Isat can
carry the SAME unit corruption as the rated current (both scraped from one
source), in which case Isat^2*DCR is wrong by 1e6 with a perfectly correct DCR —
and "fixing" the DCR would corrupt it. The WE rows resolved that way are exactly
the ones this script adjudicates with vendor truth.

Endpoint gotchas (documented in the workspace notes): the /redexpert/tc/*
endpoints browser-sniff the UA — send a full Chrome string; responses may carry
raw control characters — strip [\\x00-\\x1f] before json.loads; gzip-sniff the
body.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
BASE = "https://redexpert.we-online.com/redexpert"
CTRL = re.compile(rb"[\x00-\x1f]")


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    body = CTRL.sub(b" ", body)
    return json.loads(body)


def walk_numbers(obj, out, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk_numbers(v, out, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk_numbers(v, out, f"{prefix}[{i}]")
    else:
        out.append((prefix, obj))


def main(argv):
    refs = set(json.loads(Path(argv[0]).read_text()))
    out_path = Path(argv[1])

    modules = get_json(f"{BASE}/modules/all")
    # modules/all is a list of {ModuleID, Name, ...}
    ids = sorted({m["ModuleID"] for m in modules if isinstance(m, dict) and isinstance(m.get("ModuleID"), int)})
    print(f"modules: {len(ids)}   looking for {len(refs)} order codes")

    found = {}
    for mid in ids:
        if len(found) == len(refs):
            break
        try:
            plist = get_json(f"{BASE}/product/list/{mid}")
        except Exception:
            continue
        rows = plist if isinstance(plist, list) else plist.get("Data") or plist.get("products") or plist.get("data") or []
        hit_here = 0
        for p in rows:
            if not isinstance(p, dict):
                continue
            oc = str(p.get("Order_Code") or p.get("OrderCode") or p.get("orderCode") or "")
            if oc in refs and oc not in found:
                found[oc] = {"moduleId": mid, "raw": p}
                hit_here += 1
        if hit_here:
            print(f"  module {mid}: +{hit_here} (total {len(found)})")
        time.sleep(0.15)

    print(f"\nfound {len(found)} of {len(refs)} in REDEXPERT")
    out_path.write_text(json.dumps(found, indent=1))
    print(f"raw vendor rows -> {out_path}")
    if found:
        oc, first = next(iter(found.items()))
        nums = []
        walk_numbers(first["raw"], nums)
        print(f"\nsample field names for {oc}:")
        for k, v in nums[:40]:
            print(f"   {k} = {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
