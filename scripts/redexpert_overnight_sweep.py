#!/usr/bin/env python3
"""Unattended overnight sweep of every Wurth REDEXPERT product family.

Runs without an agent in the loop: the REDEXPERT MCP speaks plain HTTP JSON-RPC
with no session handshake, so unlike the TE pull (which needs a browser to pass
Akamai) this can be launched and left.

SCOPE HONESTY -- what this does and does not get:
  DOES  : the full parametric scalar set per part (impedance, impedanceMax +
          impedanceMaxAtFrequency, resistanceDc, ratedCurrent, dimensions, series,
          datasheet/redExpert URLs) for all 32 families.
  DOES NOT: per-point R/X split curves, L-vs-bias, or the IR1@20K / IR2@40K pair
          that ABT #250 and #251 actually need. get_products returns SCALARS only
          -- verified on module 1 (444 ferrites): no curve array of any kind. The
          curve data lives in the REDEXPERT web app's chart endpoints, which need
          browser discovery first (that is where the existing
          we-redexpert-cmc-curves.json cache came from).

Writes ONLY to TAS/staging/we/ -- never touches the live catalogue. Nothing is
promoted into data/*.ndjson without a separate reviewed step.

Usage: redexpert_overnight_sweep.py [--delay 3] [--retries 3]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from redexpert_client import Redexpert  # noqa: E402

STAGE = Path.home() / "PSMA" / "TAS" / "staging" / "we"
LOG = STAGE / "redexpert_sweep.log"
STOP = STAGE / "STOP"

# sortBy is mandatory per the tool description; pick one that exists for the family.
# Impedance suits ferrites/CMCs, Inductance suits inductors, Capacitance capacitors.
SORT_PREFS = ["Impedance", "Inductance", "Capacitance", "RatedCurrent",
              "RatedVoltage", "ResistanceDc", "None"]


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def fetch_module(rx, mod, title, retries, delay):
    """Try each sort key until one is accepted; the server rejects invalid ones."""
    for sort_by in SORT_PREFS:
        for attempt in range(retries):
            try:
                res = rx.call("get_products", {"module": str(mod), "sortBy": sort_by,
                                               "sortOrder": "Asc"})
                if isinstance(res, dict) and res.get("results"):
                    return res, sort_by
                # a string reply is the server's error envelope
                break
            except Exception as e:
                log(f"    module {mod} sortBy={sort_by} attempt {attempt+1}: {str(e)[:90]}")
                time.sleep(delay * (attempt + 1))
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--retries", type=int, default=3)
    a = ap.parse_args()

    STAGE.mkdir(parents=True, exist_ok=True)
    if STOP.exists():
        STOP.unlink()

    rx = Redexpert()
    fams = rx.families()
    families = fams.get("results", []) if isinstance(fams, dict) else []
    log(f"START sweep of {len(families)} REDEXPERT families -> {STAGE}")

    total = 0
    got = 0
    for f in families:
        if STOP.exists():
            log("STOP file present -- exiting cleanly")
            break
        mod, title = f["id"], f["title"]
        out = STAGE / f"redexpert-module-{mod}.json"
        if out.exists():
            log(f"module {mod} ({title}): already staged, skipping")
            continue
        res, sort_by = fetch_module(rx, mod, title, a.retries, a.delay)
        if res is None:
            log(f"module {mod} ({title}): NO DATA (all sort keys rejected)")
            continue
        n = len(res.get("results") or [])
        keys = sorted({k for r in (res.get("results") or [])[:50] for k in r})
        out.write_text(json.dumps({"module": mod, "title": title, "sortBy": sort_by,
                                   "fetchedAt": time.strftime("%Y-%m-%d"),
                                   "source": "redexpert.we-online.com MCP get_products",
                                   **res}))
        total += n
        got += 1
        log(f"module {mod} ({title}): {n} records, sortBy={sort_by} -> {out.name}")
        log(f"    fields: {', '.join(keys[:18])}")
        time.sleep(a.delay)

    log(f"END {got} families staged, {total} records total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
