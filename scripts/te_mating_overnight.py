#!/usr/bin/env python3
"""ABT #249: unattended overnight runner for the TE mating pull.

Drives its OWN chromium instead of the Playwright MCP, so it runs without an agent
in the loop.

Why it must run in a browser at all: api.te.com is Akamai-gated. Raw curl gets 403;
only an in-page fetch from a te.com origin is served. See ABT #249.

*** DELIBERATE, USER-GRANTED EXCEPTION TO THE ALWAYS-HEADLESS RULE (2026-07-28) ***
This scraper runs HEADED. The standing house rule is that Playwright always runs
headless, never --headed. That rule exists for TESTS; this is a scraper, and headless
simply cannot do the job here. Measured, not assumed -- three headless configurations
were probed against TE and ALL three got HTTP 403 with ZERO Akamai cookies issued:
    A. cold headless, default UA                              -> 403
    B. headless + real Chrome UA + AutomationControlled off
       + multi-page warm-up                                   -> 403
    C. B + persistent profile directory (cookies across runs) -> 403
No _abck / bm_sz cookie is ever set, so the block lands at page load: it is bot
fingerprinting, not rate limiting. The Playwright MCP session succeeds precisely
because ~/.claude.json runs it headed under WSLg (DISPLAY=:0, no --headless flag).
The exception is scoped to THIS script only -- do not copy it into tests or any
other project. Requires a working X display (WSLg).

Hardening, all learned the hard way this session:
  * ORIGIN GUARD — if the page loses the te.com origin every fetch raises
    "TypeError: Failed to fetch" and the batch silently returns nothing. The runner
    re-navigates and never records an errored part as done.
  * 403 THROTTLE — Akamai starts 403ing after a few hundred consecutive requests.
    Per-request delay + exponential backoff + re-navigation on a 403 burst.
  * NEVER MARK ERRORED PARTS DONE — they stay pending and are retried next pass,
    so throttling costs time, never coverage.
  * Checkpoints after every batch, so a kill at any moment loses at most one batch.

Stop it cleanly by creating the file staging/te/STOP (or just kill it).

Usage: te_mating_overnight.py [--batch 120] [--delay 0.25] [--write-every 5]
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

TAS = Path.home() / "PSMA" / "TAS"
STAGE = TAS / "staging" / "te"
SCRIPTS = TAS / "scripts"
RESULTS = STAGE / "te_mates_raw.jsonl"
DONE = STAGE / "te_done.json"
WORKLIST = STAGE / "te_worklist.json"
LIMIT = STAGE / "te_limit.json"
STOP = STAGE / "STOP"
LOG = STAGE / "overnight.log"

SEED_URL = "https://www.te.com/en/product-179843-4.html"

PULL_JS = r"""
async (args) => {
  if (!location.origin.includes('te.com')) {
    return {aborted: true, origin: location.origin, results: []};
  }
  const {pns, delayMs} = args;
  const base = 'https://api.te.com/api/v1/search/service/product/related-products';
  const q = (pn, r) => `${base}?c=usa&l=en&tcpn=${encodeURIComponent(pn)}` +
      `&dist_region=North%20America&s=100&r=${r}&mediaType=jsonns&has_ida=y&storeid=TEUSA`;
  const realPn = (p) => p.representativeTcpn || p.marketingPartNumNormalized ||
      ((p.id || '').includes('!') ? (p.id || '').split('!').pop() : (p.id || null));
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const results = [];
  let forbidden = 0;
  for (const pn of pns) {
    const rec = {pn, compatible: [], associated: [], err: null};
    try {
      for (const [r, key] of [[0, 'compatible'], [3, 'associated']]) {
        const resp = await fetch(q(pn, r));
        if (resp.status === 403) { forbidden++; rec.err = `HTTP 403 r=${r}`; continue; }
        if (!resp.ok) { rec.err = `HTTP ${resp.status} r=${r}`; continue; }
        const j = await resp.json();
        const cp = (j.results || {}).compatibleProducts || {};
        for (const p of (cp.products || [])) {
          rec[key].push({pn: realPn(p), desc: (p.friendlyDescription || '').slice(0, 170)});
        }
        await sleep(delayMs);
      }
    } catch (e) { rec.err = String(e).slice(0, 120); }
    results.push(rec);
    if (forbidden > 40) break;   // throttled hard -- bail, let the runner back off
  }
  return {aborted: false, origin: location.origin, forbidden, results};
}
"""


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def state():
    wl = json.loads(WORKLIST.read_text())
    lim = json.loads(LIMIT.read_text())["limit"] if LIMIT.exists() else len(wl)
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    wl = wl[:lim]
    return wl, done, [p for p in wl if p not in done]


def record(good):
    with RESULTS.open("a", encoding="utf-8") as fh:
        for r in good:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    done.update(r["pn"] for r in good if r.get("pn"))
    DONE.write_text(json.dumps(sorted(done)))


def classify_and_write():
    for cmd in (["python3", str(SCRIPTS / "te_mating_classify.py"), str(RESULTS)],
                ["python3", str(SCRIPTS / "te_mating_write.py"), "--apply"]):
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(TAS))
        tail = (p.stdout or p.stderr).strip().splitlines()[-4:]
        log(f"  {Path(cmd[1]).name}: " + " | ".join(t.strip() for t in tail))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=120)
    ap.add_argument("--delay", type=float, default=0.25, help="seconds between requests")
    ap.add_argument("--write-every", type=int, default=5, help="batches per catalogue write")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    wl, done, todo = state()
    log(f"START tranche={len(wl)} done={len(done)} remaining={len(todo)} "
        f"batch={a.batch} delay={a.delay}s")

    backoff = 30
    batches = 0
    with sync_playwright() as pw:
        # HEADED on purpose -- see the module docstring. Headless is 403'd by Akamai
        # before a single cookie is issued, so this is the only configuration that
        # can do the work at all. Scoped to this scraper.
        browser = pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080},
                                  locale="en-US")
        page = ctx.new_page()
        page.goto(SEED_URL, timeout=120000, wait_until="domcontentloaded")
        time.sleep(5)      # let Akamai's JS run and set _abck / bm_sz
        log(f"origin ready: {page.evaluate('() => location.origin')}")

        while True:
            if STOP.exists():
                log("STOP file present -- exiting cleanly")
                break
            wl, done, todo = state()
            if not todo:
                log("worklist exhausted")
                break

            batch = todo[:a.batch]
            try:
                out = page.evaluate(PULL_JS, {"pns": batch, "delayMs": int(a.delay * 1000)})
            except Exception as e:
                log(f"evaluate failed ({str(e)[:90]}) -- re-navigating, backoff {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 900)
                try:
                    page.goto(SEED_URL, timeout=90000)
                except Exception:
                    pass
                continue

            if out.get("aborted"):
                log(f"origin lost ({out.get('origin')}) -- re-navigating")
                page.goto(SEED_URL, timeout=90000)
                continue

            recs = out["results"]
            good = [r for r in recs if not r.get("err")]
            bad = [r for r in recs if r.get("err")]
            forbidden = out.get("forbidden", 0)

            if good:
                record(good)
                backoff = 30
            batches += 1
            wl, done, todo = state()
            log(f"batch {batches}: {len(good)} ok, {len(bad)} errored "
                f"({forbidden} x 403) | done {len(done)}/{len(wl)} remaining {len(todo)}")

            if forbidden > 0 or not good:
                log(f"  throttled -- sleeping {backoff}s and re-navigating")
                time.sleep(backoff)
                backoff = min(backoff * 2, 900)
                try:
                    page.goto(SEED_URL, timeout=90000)
                except Exception:
                    pass

            if batches % a.write_every == 0:
                log("  -> classify + write")
                classify_and_write()

        log("final classify + write")
        classify_and_write()
        browser.close()
    wl, done, todo = state()
    log(f"END done={len(done)}/{len(wl)} remaining={len(todo)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
