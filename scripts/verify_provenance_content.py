#!/usr/bin/env python3
"""Phase 2: confirm the cited document actually mentions the part (ABT #391).

    python3 scripts/verify_provenance_content.py QUEUE.json PHASE1.jsonl OUT.jsonl
                                                 [--limit N] [--cache DIR]

Phase 1 (verify_provenance_urls.py) asks "is this URL real?". That is necessary and
not sufficient. A LIVE URL proves a document exists; it says nothing about whether
the part is in it. The Sumida row on ABT #385 is the case in point: its citation
resolved perfectly — to a BOURNS datasheet, for a SUMIDA part. Phase 1 would have
called that LIVE and been right, and the citation was still worthless.

So this pass downloads each LIVE document and looks for the part number in it. Only
a record whose part is FOUND has provenance that can honestly carry a retrievedDate.

MATCHING, and why it is deliberately generous. Vendors write part numbers in ways a
literal comparison misses:

  * packaging and tolerance suffixes the catalogue keeps and the table drops
    (SRP1265A-R56M vs the datasheet's row "SRP1265A-R56M" but AIAP-01-100K-T vs a
    table listing "AIAP-01-100K")
  * separators that differ (SER2918H-332KL / SER2918H 332KL)
  * PDFs whose text layer breaks a number across spaces

Each candidate form is tried in turn and the LOOSEST form that matched is recorded,
so a later reader can see HOW confident the match is rather than just that one
happened. A family-level match is reported as FAMILY_ONLY, never as FOUND: it means
the document covers the series but does not list this exact part, which is precisely
the AIAP-01 situation on ABT #386 where 51 corpus part numbers appear nowhere in the
datasheet they cite.

WHAT THIS COSTS. Documents are cached by URL, not per record — 86,000 URLs back
318,391 records, so most downloads serve many parts. Size is capped: a datasheet
beyond MAX_BYTES is fetched only as far as the cap, since a part number in a 40 MB
catalogue is not the kind of citation we want anyway. Nothing is re-downloaded
between runs, and results append as they are produced, so this is interruptible.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path
from urllib.parse import urlparse

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
MAX_PER_HOST = 2
HOST_DELAY = 0.4
TIMEOUT = 60
MAX_BYTES = 25 * 1024 * 1024

_host_lock = defaultdict(threading.Lock)
_host_last = defaultdict(float)
_write_lock = threading.Lock()


def host_of(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def download(url, cache: Path):
    cache.mkdir(parents=True, exist_ok=True)
    import hashlib
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    p = cache / key
    if p.exists():
        return p, "cached"
    h = host_of(url)
    with _host_lock[h]:
        wait = HOST_DELAY - (time.monotonic() - _host_last[h])
        if wait > 0:
            time.sleep(wait)
        _host_last[h] = time.monotonic()
    try:
        with requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                          stream=True) as r:
            if r.status_code >= 400:
                return None, f"HTTP {r.status_code}"
            buf = bytearray()
            for chunk in r.iter_content(65536):
                buf += chunk
                if len(buf) >= MAX_BYTES:
                    break
            p.write_bytes(bytes(buf))
            return p, "fetched"
    except Exception as e:                                        # noqa: BLE001
        return None, type(e).__name__


def text_of(path: Path):
    head = path.read_bytes()[:5]
    if head.startswith(b"%PDF"):
        r = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                           capture_output=True, text=True, errors="replace")
        return r.stdout
    try:
        return path.read_text(errors="replace")
    except Exception:                                             # noqa: BLE001
        return ""


def normalise(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


# Suffixes vendors routinely omit from the part table: packaging, tape-and-reel,
# RoHS and tolerance letters. Stripped only when looking for a LOOSER match, and
# the level that matched is always reported.
SUFFIX = re.compile(r"[-_ ]?(T|TR|RC|LF|L|E|B|CT|ND|G|GT|P|PB|Y)$", re.I)


def candidates(ref):
    """Progressively looser forms of a part number, most exact first."""
    forms = [("exact", ref)]
    stripped = SUFFIX.sub("", ref)
    if stripped != ref:
        forms.append(("suffix-stripped", stripped))
    base = re.split(r"[-_]", ref)[0]
    if base and base != ref and len(base) >= 4:
        forms.append(("family", base))
    return forms


def check(text, refs):
    """For each ref, the tightest form found in this document."""
    flat = normalise(text)
    out = {}
    for ref in refs:
        verdict, how = "ABSENT", None
        for level, form in candidates(ref):
            if len(normalise(form)) < 4:
                continue
            if normalise(form) in flat:
                verdict = "FAMILY_ONLY" if level == "family" else "FOUND"
                how = level
                break
        out[ref] = {"verdict": verdict, "matchedAs": how}
    return out


def main(argv):
    queue = json.loads(Path(argv[0]).read_text())
    phase1 = [json.loads(l) for l in Path(argv[1]).open(encoding="utf-8")]
    out_path = Path(argv[2])
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    cache = Path(argv[argv.index("--cache") + 1]) if "--cache" in argv \
        else Path("/tmp/tas_provenance_docs")

    live = [r["url"] for r in phase1 if r.get("verdict") == "LIVE"]
    done = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["url"])
            except Exception:
                pass
    todo = [u for u in live if u not in done]
    if limit:
        todo = todo[:limit]
    buckets = defaultdict(list)
    for u in todo:
        buckets[host_of(u)].append(u)
    todo = [u for g in zip_longest(*buckets.values()) for u in g if u]
    print(f"{len(live)} LIVE URLs, {len(done)} already checked, {len(todo)} to fetch")

    counts = Counter()
    fh = out_path.open("a", encoding="utf-8")

    def run(url):
        path, how = download(url, cache)
        # queue entries are [catalogue_file, part_number] — the PART NUMBER is what
        # has to appear in the document; the catalogue name obviously never will.
        refs = [pair[1] for pair in queue.get(url, []) if len(pair) > 1]
        rec = {"url": url, "host": host_of(url), "refs": len(refs)}
        if not path:
            rec["error"] = how
            rec["results"] = {}
        else:
            txt = text_of(path)
            rec["textChars"] = len(txt)
            rec["results"] = check(txt, refs) if txt.strip() else {}
            if not txt.strip():
                rec["error"] = "no extractable text (scanned image?)"
        with _write_lock:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            for v in rec["results"].values():
                counts[v["verdict"]] += 1
            if rec.get("error"):
                counts["DOC_UNREADABLE"] += 1
            n = sum(counts.values())
            if n % 100 == 0:
                print(f"  {n}  " + "  ".join(f"{k}:{v}" for k, v in counts.most_common()))

    with ThreadPoolExecutor(max_workers=min(24, MAX_PER_HOST * max(1, len(buckets)))) as ex:
        list(ex.map(run, todo))
    fh.close()
    print("\n" + "  ".join(f"{k}:{v}" for k, v in counts.most_common()))
    print(f"-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
