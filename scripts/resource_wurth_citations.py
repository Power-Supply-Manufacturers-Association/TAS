#!/usr/bin/env python3
"""Re-source Würth citations against the live REDEXPERT catalogue (ABT #391).

    pull:   python3 scripts/resource_wurth_citations.py pull CATALOG.json
    check:  python3 scripts/resource_wurth_citations.py check CATALOG.json WORKLIST.json OUT.json
    apply:  python3 scripts/resource_wurth_citations.py apply OUT.json [--dry-run]

WHY. Phase-1 verification found 3,055 Würth records citing a URL that is gone:
2,961 point at we-online.com/redexpert/spec/<code>, 77 at /components/products/
datasheet/<code>.pdf and 17 at /katalog/en/datasheet/<code>. All dead.

WHY REDEXPERT RATHER THAN URL PROBING. Würth publishes its entire catalogue as JSON:
GET redexpert.we-online.com/redexpert/product/list/<moduleId> returns every part in a
product family with its full parametrics, and /redexpert/modules/all lists the 57
modules. Enumerating that gives the definitive set of real Würth order codes, which
answers the question a URL probe only approximates — does this part EXIST? — and it
does so from the manufacturer's own database rather than from whether a web server
happens to serve a page today.

That distinction matters here more than for other vendors: Würth's dead URLs are
mostly /redexpert/spec/, i.e. links INTO this very system. A 404 there could mean the
part is gone or merely that the link format changed. The catalogue settles it.

GOTCHAS, all real and all previously paid for: the /redexpert/ endpoints sniff the
User-Agent and redirect a bare "Mozilla/5.0" to an update-browser page, so a full
Chrome UA is required; responses can contain raw control characters that break
json.loads and are stripped; and bodies may be gzipped regardless of the request.

A part found in REDEXPERT is re-cited to its module's product list — the exact call
that confirmed it, re-runnable by anyone. A part NOT found is left alone and
reported: Würth discontinues parts, so absence here is a reason to investigate, not
a licence to delete.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "wurth_recitation_audit.json"
TODAY = "2026-07-31"

# A bare "Mozilla/5.0" is redirected to an update-browser page by /redexpert/*.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
BASE = "https://redexpert.we-online.com/redexpert"
LIST_URL = BASE + "/product/list/{}"

PATHS = {"capacitors": ("capacitor",), "magnetics": ("magnetic",),
         "resistors": ("resistor",)}


def get(url, timeout=180):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
    return json.loads(re.sub(rb"[\x00-\x1f]", b" ", body))


def walk_codes(node, out):
    """Order_Code can sit at any depth; the payload shape varies by module."""
    if isinstance(node, dict):
        code = node.get("Order_Code")
        if isinstance(code, (str, int)) and str(code).strip():
            out.add(str(code).strip())
        for v in node.values():
            walk_codes(v, out)
    elif isinstance(node, list):
        for v in node:
            walk_codes(v, out)


def cmd_pull(argv):
    out_path = Path(argv[0])
    modules = get(BASE + "/modules/all")
    ids = [m.get("ModuleID") for m in modules if isinstance(m, dict) and m.get("ModuleID")]
    print(f"{len(ids)} REDEXPERT modules")
    have = {}
    if out_path.exists():
        have = json.loads(out_path.read_text())
    for mid in ids:
        if str(mid) in have:
            continue
        for attempt in range(3):
            try:
                d = get(LIST_URL.format(mid))
                break
            except Exception as e:                                # noqa: BLE001
                print(f"  module {mid} attempt {attempt+1}: {type(e).__name__}")
                time.sleep(4 * (attempt + 1))
        else:
            print(f"  module {mid} FAILED — resume will retry")
            continue
        codes = set()
        walk_codes(d, codes)
        have[str(mid)] = sorted(codes)
        print(f"  module {mid:5} -> {len(codes)} order codes")
        out_path.write_text(json.dumps(have))
        time.sleep(0.6)
    total = len({c for v in have.values() for c in v})
    print(f"\n{total} distinct Würth order codes across {len(have)} modules -> {out_path}")
    return 0


def cmd_check(argv):
    catalog = json.loads(Path(argv[0]).read_text())
    worklist = json.loads(Path(argv[1]).read_text())
    real = {c for v in catalog.values() for c in v}
    module_of = {}
    for mid, codes in catalog.items():
        for c in codes:
            module_of.setdefault(c, mid)
    found, missing = {}, []
    for ref, meta in worklist.items():
        cat = meta[0] if isinstance(meta, list) else meta
        if str(ref) in real:
            found[ref] = {"catalogue": cat, "moduleId": module_of[str(ref)],
                          "newUrl": LIST_URL.format(module_of[str(ref)])}
        else:
            missing.append({"reference": ref, "catalogue": cat})
    print(f"{len(real)} real order codes; of {len(worklist)} cited parts: "
          f"{len(found)} confirmed, {len(missing)} not in REDEXPERT")
    print("  not-found by catalogue:", Counter(m["catalogue"] for m in missing).most_common())
    Path(argv[2]).write_text(json.dumps(
        {"ticket": "ABT #391 (Würth)", "date": TODAY,
         "confirmed": found, "notInRedexpert": missing}, indent=1))
    print(f"-> {argv[2]}")
    return 0


def ref_of(mi):
    r = mi.get("reference")
    if r:
        return str(r)
    part = (mi.get("datasheetInfo") or {}).get("part") or {}
    p = part.get("partNumber")
    return str(p) if p else None


def cmd_apply(argv):
    dry = "--dry-run" in argv
    res = json.loads(Path(argv[0]).read_text())
    confirmed = res["confirmed"]
    bycat = {}
    for ref, m in confirmed.items():
        bycat.setdefault(m["catalogue"], {})[ref] = m
    audit = {"ticket": "ABT #391 (Würth re-citation)", "date": TODAY, "repaired": {},
             "notInRedexpert": len(res["notInRedexpert"])}
    for cat, byref in bycat.items():
        path = DATA / f"{cat}.ndjson"
        if not path.exists() or cat not in PATHS:
            continue
        keys = PATHS[cat]
        tmp = path.with_suffix(".ndjson.tmp")
        hit = 0
        with open(path, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                wrote = False
                if b"rth" in raw and (b"W\xc3\xbcrth" in raw or b"Wurth" in raw
                                      or b"Wuerth" in raw):
                    try:
                        rec = json.loads(raw)
                        o = rec
                        for k in keys:
                            o = o[k]
                        mi = o["manufacturerInfo"]
                        ref = ref_of(mi)
                    except Exception:                             # noqa: BLE001
                        ref = None
                    if ref in byref:
                        m = byref[ref]
                        mi["datasheetInfo"]["provenance"] = [{
                            "source": "manufacturerDatabase",
                            "sourceName": f"Würth REDEXPERT product list, module "
                                          f"{m['moduleId']} — this order code confirmed "
                                          f"present in Würth's own catalogue "
                                          f"(electrical values not re-read)",
                            "sourceUrl": m["newUrl"],
                            "retrievedDate": TODAY}]
                        out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                        wrote = True
                        hit += 1
                if not wrote:
                    out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        audit["repaired"][cat] = hit
        print(f"  {cat:12} {hit} rows re-cited")
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, path)
    if dry:
        print("--dry-run: nothing replaced")
    else:
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "pull":
        raise SystemExit(cmd_pull(sys.argv[2:]))
    if cmd == "check":
        raise SystemExit(cmd_check(sys.argv[2:]))
    if cmd == "apply":
        raise SystemExit(cmd_apply(sys.argv[2:]))
    print(__doc__)
    raise SystemExit(2)
