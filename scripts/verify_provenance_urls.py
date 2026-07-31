#!/usr/bin/env python3
"""Fetch every cited provenance URL and record whether it is really there.

    python3 scripts/verify_provenance_urls.py QUEUE.json OUT.jsonl [--host H] [--limit N]

Provenance is a claim about an act that occurred. Until someone fetches the URL, a
record saying "scraped from Coilcraft on 2026-06-22" is an assertion, not a trace —
which is how 195 invented parts came to hold better provenance than genuine rows
(ABT #351). relabel_url_inferred_provenance.py has already downgraded every
unearned stamp to inferred-not-verified; this is the pass that earns them back.

WHAT A VERDICT MEANS. The point is to separate "this URL is not real" from "I could
not reach it", because only the first is evidence against the record:

  LIVE     the URL serves a real document (2xx, plausible size/type)
  SOFT404  2xx, but the body is one a vendor serves for URLs that do not exist.
           Detected by CONTENT LENGTH SHARED ACROSS MANY DISTINCT URLs on the same
           host: a real product page or datasheet is unique to its part, so when
           dozens of different URLs return byte-identical responses, that response
           is a fallback page. This is exactly the Coilcraft signature — all 17
           fabricated family URLs returned the same 176,381-byte "Power Inductors"
           category page while genuine ones differed (235 KB, 158 KB, 215 KB).
  DEAD     4xx/5xx that is not a bot gate — the cited page does not exist
  BLOCKED   403/429/challenge from Cloudflare/Akamai. NOT evidence of anything; the
           record stays inferred and needs a browser pass (see
           pull_coilcraft_parametric.md for the MCP-browser route that clears these)
  ERROR    DNS/TLS/timeout after retries — retryable, never a verdict against a row

A DEAD or SOFT404 URL does not by itself condemn the part: a vendor can move a
datasheet. It means the CITATION is worthless and the record must be re-sourced or
quarantined, which is a judgement for the ticket, not for this script.

POLITENESS IS PART OF THE DESIGN. 86,000 URLs across ~18 vendor hosts is a real
load on someone else's servers. Requests are HEAD-only (no body transfer), capped at
MAX_PER_HOST concurrent per host with a delay between them, and the run is
RESUMABLE — every result is appended to OUT.jsonl immediately, so an interrupted run
re-reads what it already has and never re-fetches a URL. Rerunning is cheap;
hammering is not.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from collections import Counter, defaultdict
from itertools import zip_longest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
MAX_PER_HOST = 2
HOST_DELAY = 0.35          # seconds between requests to the same host
TIMEOUT = 25
RETRIES = 2

_host_lock = defaultdict(threading.Lock)
_host_last = defaultdict(float)
_write_lock = threading.Lock()


def host_of(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


# Hosts that accept a GET but hang forever on HEAD (st.com read-times-out on every
# HEAD, which showed up as 41 identical ReadTimeouts in the pilot). Learned at
# runtime as well as seeded, so one stall teaches the rest of the run.
_head_hostile = {"st.com"}


def _shape(r):
    return {
        "status": r.status_code,
        "finalUrl": r.url,
        "contentType": (r.headers.get("Content-Type") or "").split(";")[0].strip(),
        "contentLength": int(r.headers["Content-Length"])
        if (r.headers.get("Content-Length") or "").isdigit() else None,
        "redirected": r.url.rstrip("/") != r.request.url.rstrip("/"),
    }


def _ranged_get(url):
    """Ask for a single byte: enough for status/type/redirect, no file transfer."""
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "*/*", "Range": "bytes=0-0"},
                     timeout=TIMEOUT, allow_redirects=True, stream=True)
    try:
        out = _shape(r)
        # a 206 reports the SLICE length; the whole-file size is in Content-Range
        cr = r.headers.get("Content-Range", "")
        if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
            out["contentLength"] = int(cr.rsplit("/", 1)[1])
        return out
    finally:
        r.close()


def fetch(url):
    """HEAD the URL, politely, falling back to a ranged GET when HEAD is refused."""
    h = host_of(url)
    last = None
    for attempt in range(RETRIES + 1):
        with _host_lock[h]:
            wait = HOST_DELAY - (time.monotonic() - _host_last[h])
            if wait > 0:
                time.sleep(wait)
            _host_last[h] = time.monotonic()
        try:
            if h in _head_hostile:
                return _ranged_get(url)
            r = requests.head(url, headers={"User-Agent": UA, "Accept": "*/*"},
                              timeout=TIMEOUT, allow_redirects=True)
            if r.status_code in (403, 405, 501):
                return _ranged_get(url)      # refuses HEAD, may still serve GET
            return _shape(r)
        except requests.exceptions.ReadTimeout as e:
            # HEAD hung. Try GET once, and remember the host so the rest of the run
            # skips straight to it instead of burning a timeout per URL.
            last = f"ReadTimeout: {e}"[:160]
            _head_hostile.add(h)
            try:
                return _ranged_get(url)
            except Exception as e2:                             # noqa: BLE001
                last = f"{type(e2).__name__}: {e2}"[:160]
        except Exception as e:                                  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"[:160]
        time.sleep(1.5 * (attempt + 1))
    return {"status": None, "error": last}


BLOCK_STATUS = {401, 403, 429, 503}


def classify(res):
    s = res.get("status")
    if s is None:
        return "ERROR"
    if s in BLOCK_STATUS:
        return "BLOCKED"
    if s >= 400:
        return "DEAD"
    return "LIVE"        # SOFT404 is assigned later, from cross-URL body sizes


def main(argv):
    queue_path, out_path = Path(argv[0]), Path(argv[1])
    only_host = argv[argv.index("--host") + 1] if "--host" in argv else None
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None

    queue = json.loads(queue_path.read_text())
    urls = [u for u in queue if not only_host or host_of(u) == only_host]

    done = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["url"])
            except Exception:
                pass
    todo = [u for u in urls if u not in done]
    if limit:
        todo = todo[:limit]

    # INTERLEAVE BY HOST. The queue arrives grouped by vendor, so working it in order
    # means every worker is waiting on the SAME host's rate limit while seventeen other
    # hosts sit idle — the first run managed 80 URLs/min against a theoretical 340.
    # Round-robining across hosts is both faster and gentler: no vendor sees a
    # sustained burst, and the per-host spacing below is still enforced.
    buckets = defaultdict(list)
    for u in todo:
        buckets[host_of(u)].append(u)
    todo = [u for group in zip_longest(*buckets.values()) for u in group if u]

    print(f"{len(urls)} URLs in scope, {len(done)} already verified, {len(todo)} to fetch")

    hosts = Counter(host_of(u) for u in todo)
    workers = max(2, min(32, MAX_PER_HOST * max(1, len(hosts))))
    print(f"{len(hosts)} hosts, {workers} workers, {MAX_PER_HOST}/host, {HOST_DELAY}s apart\n")

    counts = Counter()
    out = out_path.open("a", encoding="utf-8")

    def run(url):
        res = fetch(url)
        rec = {"url": url, "host": host_of(url), "verdict": classify(res),
               "refs": len(queue[url]), **res}
        with _write_lock:
            out.write(json.dumps(rec) + "\n")
            out.flush()
            counts[rec["verdict"]] += 1
            n = sum(counts.values())
            if n % 200 == 0:
                print(f"  {n}/{len(todo)}  " + "  ".join(f"{k}:{v}" for k, v in counts.most_common()))
        return rec

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(run, todo))
    out.close()

    print("\nverdicts this run: " + "  ".join(f"{k}:{v}" for k, v in counts.most_common()))
    print(f"-> {out_path}   (re-run to resume; then: verify_provenance_urls.py --report)")
    return 0


def report(out_path):
    """Assign SOFT404: a 2xx whose body is the vendor's page for URLs that don't exist.

    Two signals, and the distinction between them matters. Many distinct URLs
    collapsing onto ONE response is normal when a vendor publishes a single family
    datasheet under several part-number filenames — TI serves lmg3410r070.pdf and
    lmg3411r070.pdf as the same 1,119,594-byte document, and those citations are
    perfectly good. What is NOT good is many URLs collapsing onto a generic HTML
    PAGE, which is what a site serves when the path means nothing to it.

    So a shared response is only condemned when it is HTML. A shared PDF is a family
    datasheet; a shared web page is a fallback. Redirect target is checked first
    because it is unambiguous, with content length as the fallback signal for hosts
    that serve their 'not found' page at 200 without redirecting (Coilcraft's
    176,381-byte "Power Inductors" page never redirects).
    """
    rows = [json.loads(l) for l in Path(out_path).open(encoding="utf-8")]
    by_host = defaultdict(list)
    for r in rows:
        by_host[r["host"]].append(r)
    soft = 0

    def is_page(r):
        return "html" in (r.get("contentType") or "").lower()

    for h, rs in by_host.items():
        live = [r for r in rs if r["verdict"] == "LIVE"]
        if len(live) < 6:
            continue
        finals = Counter(r.get("finalUrl") for r in live if r.get("finalUrl"))
        sizes = Counter(r["contentLength"] for r in live if r.get("contentLength"))
        bad_final = {u for u, n in finals.items()
                     if n > 5 and n > 0.20 * len(live)}
        bad_size = {s for s, n in sizes.items() if n > 5 and n > 0.20 * len(live)}
        for r in live:
            if not is_page(r):
                continue          # a shared PDF is a family datasheet, not a 404
            if r.get("finalUrl") in bad_final or r.get("contentLength") in bad_size:
                r["verdict"] = "SOFT404"
                soft += 1
    # A host that times out on essentially every URL is gating us, not broken. st.com
    # answers / in 0.09 s by curl and read-times-out on every deep product page — that
    # is Akamai, and calling it ERROR invites an endless retry loop. BLOCKED is the
    # honest verdict: unverifiable by this route, needs the MCP-browser pass.
    for h, rs in by_host.items():
        errs = [r for r in rs if r["verdict"] == "ERROR"]
        if len(rs) >= 10 and len(errs) >= 0.9 * len(rs):
            for r in errs:
                r["verdict"] = "BLOCKED"
                r["blockedReason"] = f"{len(errs)}/{len(rs)} URLs on {h} timed out — host-level gate"

    v = Counter(r["verdict"] for r in rows)
    recs = Counter()
    for r in rows:
        recs[r["verdict"]] += r.get("refs", 0)
    print(f"{len(rows)} URLs verified ({soft} reclassified SOFT404 by shared body size)\n")
    print(f"{'verdict':10} {'URLs':>8} {'records':>10}")
    for k, n in v.most_common():
        print(f"{k:10} {n:8} {recs[k]:10}")
    print("\nby host:")
    for h, rs in sorted(by_host.items(), key=lambda kv: -len(kv[1]))[:20]:
        c = Counter(r["verdict"] for r in rs)
        print(f"  {h:28} " + "  ".join(f"{k}:{n}" for k, n in c.most_common()))
    Path(str(out_path) + ".classified").write_text(
        "\n".join(json.dumps(r) for r in rows))
    print(f"\n-> {out_path}.classified")
    return 0


if __name__ == "__main__":
    if "--report" in sys.argv:
        raise SystemExit(report(sys.argv[1]))
    raise SystemExit(main(sys.argv[1:]))
