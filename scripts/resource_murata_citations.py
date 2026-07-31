#!/usr/bin/env python3
"""Re-source Murata citations against Murata's live PIM (ABT #391).

    probe:  python3 scripts/resource_murata_citations.py probe WORKLIST.json OUT.jsonl
    apply:  python3 scripts/resource_murata_citations.py apply OUT.jsonl [--dry-run]

WHY THESE 22,144 RECORDS NEED IT. Phase-1 verification found that essentially every
murata.com citation in the catalogue resolves to nothing. The cited shape,

    https://www.murata.com/products/productdetail?partno=<MPN>

returns a 5,965-byte HTML shell for every part — 21,478 URLs, byte-identical bodies,
no redirect — and following one in a real browser lands on
pim.murata.com/404/NotFound.html. Murata migrated to a new PIM system and the entire
old citation scheme died with it.

THE PARTS THEMSELVES ARE FINE. GRM32ER60E337ME05, whose citation 404s, resolves
perfectly against Murata's own API. This is a dead URL scheme, not missing parts,
which is why the fix is to re-cite rather than to quarantine.

HOW A PART IS CONFIRMED. Murata's PIM exposes a category resolver that needs nothing
but the part number:

    GET pimapi.murata.com/public/api/pim/v1/products/search/cross-categories
        ?partNum=<MPN>&languageRegion=en-global

A real part returns its category hierarchy ({"resultLayer1List": [{"productCategoryId":
"capacitor", ...}]}); an invented one returns {}. That makes it an existence oracle
for the exact part number, which is precisely what a citation has to be able to
claim. The replacement URL is the current detail page,
pim.murata.com/en-global/pim/details/?partNum=<MPN>, which was confirmed in a browser
to render the real part.

WHAT THE NEW PROVENANCE DOES AND DOES NOT CLAIM. It says: this exact part number was
confirmed to exist in Murata's catalogue, by calling their API, on this date — and it
carries a real retrievedDate because a real retrieval happened. It does NOT claim the
stored electrical values were re-read from the vendor; confirming those needs the
parametric sheet and is a separate pass. Saying exactly what was verified, and no
more, is the whole point of the exercise.

A part the resolver does not know is NOT repaired and NOT silently dropped. It is
recorded as unknown-to-vendor, which is a much stronger signal than a dead URL: the
part number itself may be invented, and that is a quarantine decision for a human.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "murata_recitation_audit.json"
TODAY = "2026-07-31"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
XCAT = "https://pimapi.murata.com/public/api/pim/v1/products/search/cross-categories"
DETAIL = "https://pim.murata.com/en-global/pim/details/?partNum="

DELAY = 0.25          # one host, so pace it deliberately
_lock = threading.Lock()
_last = [0.0]
_write = threading.Lock()

PATHS = {"capacitors": ("capacitor",), "magnetics": ("magnetic",)}


def resolve(part):
    with _lock:
        wait = DELAY - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    url = f"{XCAT}?{urllib.parse.urlencode({'partNum': part, 'languageRegion': 'en-global'})}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.loads(r.read())
            cats = [c.get("productCategoryId") for c in (d.get("resultLayer1List") or [])]
            return {"exists": bool(cats), "categories": cats}
        except Exception as e:                                    # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:120]
            time.sleep(1.0 * (attempt + 1))
    return {"error": err}


def cmd_probe(argv):
    worklist = json.loads(Path(argv[0]).read_text())
    out_path = Path(argv[1])
    done = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["reference"])
            except Exception:
                pass
    todo = [r for r in worklist if r not in done]
    print(f"{len(worklist)} parts, {len(done)} already probed, {len(todo)} to go")
    counts = Counter()
    fh = out_path.open("a", encoding="utf-8")

    def run(ref):
        res = resolve(ref)
        cat, old_url, verdict = worklist[ref]
        rec = {"reference": ref, "catalogue": cat, "oldUrl": old_url,
               "oldVerdict": verdict, **res}
        if res.get("exists"):
            rec["newUrl"] = DETAIL + urllib.parse.quote(ref)
        with _write:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            counts["exists" if res.get("exists") else
                   ("error" if res.get("error") else "unknown-to-vendor")] += 1
            n = sum(counts.values())
            if n % 250 == 0:
                print(f"  {n}/{len(todo)}  " + "  ".join(f"{k}:{v}" for k, v in counts.most_common()))

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(run, todo))
    fh.close()
    print("\n" + "  ".join(f"{k}:{v}" for k, v in counts.most_common()))
    return 0


def cmd_apply(argv):
    dry = "--dry-run" in argv
    results = {}
    for line in Path(argv[0]).open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("exists") and r.get("newUrl"):
            results.setdefault(r["catalogue"], {})[r["reference"]] = r
    audit = {"ticket": "ABT #391 (Murata re-citation)", "date": TODAY,
             "repaired": Counter(), "unknownToVendor": [], "files": {}}
    for line in Path(argv[0]).open(encoding="utf-8"):
        r = json.loads(line)
        if not r.get("exists") and not r.get("error"):
            audit["unknownToVendor"].append({"reference": r["reference"],
                                             "catalogue": r["catalogue"]})

    for cat, byref in results.items():
        path = DATA / f"{cat}.ndjson"
        if not path.exists():
            continue
        keys = PATHS[cat]
        tmp = path.with_suffix(".ndjson.tmp")
        hit = 0
        with open(path, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                wrote = False
                if b"Murata" in raw:
                    try:
                        rec = json.loads(raw)
                        o = rec
                        for k in keys:
                            o = o[k]
                        mi = o["manufacturerInfo"]
                        ref = str(mi.get("reference"))
                    except Exception:
                        ref = None
                    if ref in byref:
                        di = mi["datasheetInfo"]
                        new = byref[ref]["newUrl"]
                        mi["datasheetUrl"] = new
                        di["provenance"] = [{
                            "source": "manufacturerParametric",
                            "sourceName": "Murata PIM cross-categories API — this exact part "
                                          "number confirmed to exist in Murata's catalogue "
                                          "(electrical values not re-read)",
                            "sourceUrl": new,
                            "retrievedDate": TODAY}]
                        out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                        wrote = True
                        hit += 1
                if not wrote:
                    out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        audit["repaired"][cat] = hit
        audit["files"][cat] = str(path)
        print(f"  {cat:12} {hit} rows re-cited")
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, path)

    print(f"\nunknown to vendor: {len(audit['unknownToVendor'])} parts "
          f"(NOT repaired — the part number itself may be invented)")
    if dry:
        print("--dry-run: nothing replaced")
    else:
        audit["repaired"] = dict(audit["repaired"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "probe":
        raise SystemExit(cmd_probe(sys.argv[2:]))
    if sys.argv[1] == "apply":
        raise SystemExit(cmd_apply(sys.argv[2:]))
    print(__doc__)
    raise SystemExit(2)
