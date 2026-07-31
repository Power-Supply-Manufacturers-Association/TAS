#!/usr/bin/env python3
"""Re-source Taiyo Yuden citations against the live TY-COMPAS site (ABT #391).

    probe:  python3 scripts/resource_taiyo_yuden_citations.py probe WORKLIST.json OUT.jsonl
    apply:  python3 scripts/resource_taiyo_yuden_citations.py apply OUT.jsonl [--dry-run]

WHY. Phase-1 verification found EVERY Taiyo Yuden citation in the catalogue dead:
10,378 records cite

    https://www.yuden.co.jp/product/<MPN>

and every one of them returns a hard 404. Like Murata's, this is a URL scheme that
no longer exists, not a catalogue of missing parts — the same parts resolve fine
under the current scheme.

THE CHECK IS STRONGER HERE THAN FOR MURATA. TY-COMPAS serves a real per-part page:

    https://ds.yuden.co.jp/TYCOMPAS/or/detail?pn=<MPN>

    MSAST021SCG0R2BWNA01  -> 200, 93,500 bytes, page contains the part number
    NOTAREALPART123456    -> 404,  4,305 bytes

So a single request confirms both that the part exists AND that the page is about
that part — which is the standard a citation has to meet. A 200 alone is NOT
accepted: the body must contain the part number, because a 200 that does not
mention the part is exactly the failure mode that let a Bourns datasheet stand as
the source for a Sumida part (ABT #385).

Parts that fail either test are recorded as unresolved and NOT repaired. A dead
citation replaced by a guess is worse than a dead citation, because it looks fixed.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "taiyo_yuden_recitation_audit.json"
TODAY = "2026-07-31"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
DETAIL = "https://ds.yuden.co.jp/TYCOMPAS/or/detail?pn="
DELAY = 0.3
MIN_BYTES = 20_000          # the 404 shell is ~4 KB; a real page is ~90 KB

_lock = threading.Lock()
_last = [0.0]
_write = threading.Lock()

PATHS = {"capacitors": ("capacitor",), "magnetics": ("magnetic",),
         "resistors": ("resistor",)}


def probe_part(part):
    with _lock:
        wait = DELAY - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    url = DETAIL + part
    for attempt in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=45)
            body = r.text
            return {"status": r.status_code, "bytes": len(body),
                    "mentionsPart": part.upper() in body.upper(),
                    "ok": r.status_code == 200 and len(body) >= MIN_BYTES
                          and part.upper() in body.upper()}
        except Exception as e:                                    # noqa: BLE001
            err = f"{type(e).__name__}"
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
        res = probe_part(ref)
        cat = worklist[ref][0] if isinstance(worklist[ref], list) else worklist[ref]
        rec = {"reference": ref, "catalogue": cat, **res}
        if res.get("ok"):
            rec["newUrl"] = DETAIL + ref
        with _write:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            counts["confirmed" if res.get("ok") else
                   ("error" if res.get("error") else "unresolved")] += 1
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
    byfile = {}
    unresolved = []
    for line in Path(argv[0]).open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("ok") and r.get("newUrl"):
            byfile.setdefault(r["catalogue"], {})[r["reference"]] = r["newUrl"]
        elif not r.get("error"):
            unresolved.append(r["reference"])
    audit = {"ticket": "ABT #391 (Taiyo Yuden re-citation)", "date": TODAY,
             "repaired": {}, "unresolvedCount": len(unresolved),
             "unresolved": unresolved[:500]}

    for cat, byref in byfile.items():
        path = DATA / f"{cat}.ndjson"
        if not path.exists() or cat not in PATHS:
            continue
        keys = PATHS[cat]
        tmp = path.with_suffix(".ndjson.tmp")
        hit = 0
        with open(path, "rb") as src, open(tmp, "wb") as out:
            for raw in src:
                wrote = False
                if b"yuden" in raw.lower():
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
                        new = byref[ref]
                        mi["datasheetUrl"] = new
                        mi["datasheetInfo"]["provenance"] = [{
                            "source": "manufacturerParametric",
                            "sourceName": "Taiyo Yuden TY-COMPAS detail page — fetched, and the "
                                          "page confirmed to carry this exact part number "
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
        print(f"  {cat:12} {hit} rows re-cited")
        if dry:
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, path)

    print(f"\nunresolved: {len(unresolved)} parts (NOT repaired)")
    if dry:
        print("--dry-run: nothing replaced")
    else:
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        raise SystemExit(cmd_probe(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "apply":
        raise SystemExit(cmd_apply(sys.argv[2:]))
    print(__doc__)
    raise SystemExit(2)
