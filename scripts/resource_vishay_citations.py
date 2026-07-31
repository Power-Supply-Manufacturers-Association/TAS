#!/usr/bin/env python3
"""Re-source Vishay citations via their document-id endpoint (ABT #391).

    probe:  python3 scripts/resource_vishay_citations.py probe WORKLIST.json OUT.jsonl
    apply:  python3 scripts/resource_vishay_citations.py apply OUT.jsonl [--dry-run]

467 Vishay records cite vishay.com/docs/<docId>/<name>.pdf and every one is dead.
The document ID survives, though, and Vishay still resolves it:

    vishay.com/doc?73438     -> 200, application/pdf, 368 KB
    vishay.com/doc?99999999  -> 404

So the citation is recoverable from the corpus's own URL without guessing anything:
pull the numeric id out of the dead path and ask Vishay for that document.

TWO CHECKS, NOT ONE. A 200 is necessary and not sufficient — Vishay's 404 is a
736 KB HTML page, so status alone would accept a soft-404, and even a real PDF may be
the wrong document. A citation is only rewritten when the response IS a PDF (%PDF
magic, not just a content-type header) AND its text contains the part number.

That second check matters here more than for most vendors, because Vishay datasheets
are FAMILY documents: Si7448DP, Si7469DP and Si7456DDP all cite docs/73438/si7469dp.pdf.
That is legitimate — one document really does cover all three — but it means a
filename proves nothing about which parts are inside, so the part number has to be
found in the text.

Documents whose id no longer resolves are left alone and reported. Vishay retires
datasheets; a dead document id says the CITATION is gone, not that the part is.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "vishay_recitation_audit.json"
TODAY = "2026-07-31"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
DOC = "https://www.vishay.com/doc?"
DOC_ID = re.compile(r"/docs?/(\d{4,7})/", re.I)
DELAY = 0.35

_lock = threading.Lock()
_last = [0.0]
_write = threading.Lock()

PATHS = {"capacitors": ("capacitor",), "magnetics": ("magnetic",),
         "resistors": ("resistor",), "varistors": ("varistor",),
         "mosfets": ("semiconductor", "mosfet"), "diodes": ("semiconductor", "diode"),
         "igbts": ("semiconductor", "igbt")}


def pdf_text(blob: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(blob)
        path = fh.name
    try:
        r = subprocess.run(["pdftotext", "-layout", path, "-"],
                           capture_output=True, text=True, errors="replace")
        return r.stdout
    finally:
        os.unlink(path)


def probe(ref, url):
    m = DOC_ID.search(url or "")
    if not m:
        return {"error": "no document id in the cited URL"}
    doc_id = m.group(1)
    with _lock:
        wait = DELAY - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    for attempt in range(3):
        try:
            r = requests.get(DOC + doc_id, headers={"User-Agent": UA}, timeout=60)
            if r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            blob = r.content
            is_pdf = blob[:4] == b"%PDF"
            names = False
            if is_pdf:
                txt = pdf_text(blob)
                flat = re.sub(r"[^A-Z0-9]", "", txt.upper())
                names = re.sub(r"[^A-Z0-9]", "", ref.upper()) in flat
            return {"docId": doc_id, "status": r.status_code, "isPdf": is_pdf,
                    "namesPart": names, "ok": bool(is_pdf and names),
                    "newUrl": DOC + doc_id}
        except Exception as e:                                    # noqa: BLE001
            err = type(e).__name__
            time.sleep(1.5 * (attempt + 1))
    return {"error": err, "inconclusive": True}


def cmd_probe(argv):
    work = json.loads(Path(argv[0]).read_text())
    items = [(r, v[1]) for r, v in work.items()
             if isinstance(v, list) and len(v) > 2 and v[2] == "vishay.com"]
    if not items:
        items = [(r, v[1]) for r, v in work.items() if "vishay.com" in str(v)]
    out_path = Path(argv[1])
    done = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["reference"])
            except Exception:
                pass
    todo = [(r, u) for r, u in items if r not in done]
    print(f"{len(items)} Vishay parts, {len(done)} probed, {len(todo)} to go")
    counts = Counter()
    fh = out_path.open("a", encoding="utf-8")

    def run(item):
        ref, url = item
        res = probe(ref, url)
        cat = work[ref][0]
        rec = {"reference": ref, "catalogue": cat, "oldUrl": url, **res}
        with _write:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            counts["confirmed" if res.get("ok") else
                   ("inconclusive" if res.get("error") else "unresolved")] += 1
            n = sum(counts.values())
            if n % 50 == 0:
                print(f"  {n}/{len(todo)}  " + "  ".join(f"{k}:{v}" for k, v in counts.most_common()))

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(run, todo))
    fh.close()
    print("\n" + "  ".join(f"{k}:{v}" for k, v in counts.most_common()))
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
    bycat = {}
    unresolved = 0
    for line in Path(argv[0]).open(encoding="utf-8"):
        r = json.loads(line)
        if r.get("ok"):
            bycat.setdefault(r["catalogue"], {})[r["reference"]] = r
        elif not r.get("error"):
            unresolved += 1
    audit = {"ticket": "ABT #391 (Vishay re-citation)", "date": TODAY,
             "repaired": {}, "unresolved": unresolved}
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
                if b"ishay" in raw:
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
                        mi["datasheetUrl"] = m["newUrl"]
                        mi["datasheetInfo"]["provenance"] = [{
                            "source": "manufacturerDatasheet",
                            "sourceName": f"Vishay document {m['docId']} — fetched, confirmed to "
                                          f"be a PDF and to name this part "
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
    print(f"\nunresolved (document id gone): {unresolved}")
    if dry:
        print("--dry-run: nothing replaced")
    else:
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "probe":
        raise SystemExit(cmd_probe(sys.argv[2:]))
    if cmd == "apply":
        raise SystemExit(cmd_apply(sys.argv[2:]))
    print(__doc__)
    raise SystemExit(2)
