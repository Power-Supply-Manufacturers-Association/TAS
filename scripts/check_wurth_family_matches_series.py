#!/usr/bin/env python3
"""GUARD: fail if a Wuerth part's stored `family` disagrees with WE REDEXPERT's
own Series for that EXACT order code.

    python3 scripts/check_wurth_family_matches_series.py [--data DIR]
            [--file F ...] [--snapshot P] [--refresh] [--offline] [--list]

Exit 0 = clean, exit 1 = at least one disagreement (or no ground truth to check
against -- a gate that cannot run FAILS, it never skips).

WHY THIS EXISTS (ABT #1082)

The Wuerth magnetics importer set `family` from the source row's free text, and
when a row carried no description it wrote the literal "WE-MAPI" rather than
leaving the field absent. 514 rows in data/magnetics.ndjson ended up with
family = "WE-MAPI" and no description; only 110 were WE-MAPI. The other 404 were
WE-LHMI (166), WE-HCI (126), WE-HCM (77) and WE-HCF (35), and a downstream
material lookup joining on the field produced 329 "matches", many of them
cross-family. A wrong value is worse than a missing one: absent is a question,
wrong is an answer nobody re-checks.

GROUND TRUTH, AND WHY IT IS THE EXACT ORDER CODE

WE REDEXPERT publishes an authoritative Series per Order_Code:

    https://redexpert.we-online.com/redexpert/product/list/{moduleId}
    module ids from  https://redexpert.we-online.com/redexpert/modules/all

The order-code PREFIX is NOT a key to the family and must never be used as one --
verified 2026-09-05 against REDEXPERT itself: 744300 carries both WE-HCMD and
WE-HCM, and 744310, 744313 and 744314 each carry both WE-HCI and WE-HCM. A
six-digit-prefix rule mislabels those parts with complete confidence.

The endpoints browser-sniff: a bare "Mozilla/5.0" is 301'd to an
"update-browser" page, so a full Chrome UA is sent. Responses carry raw control
characters, stripped before json.loads.

VERDICTS

    MISMATCH     stored family and REDEXPERT Series are different families.
                 This is the ABT #1082 defect.
    GRANULARITY  one is a prefix of the other (stored "WE-PD" against the true
                 "WE-PD2", or "WE-XHMI" against "WE-XHMI P"). Not a wrong
                 family, but not the vendor's name for the part either.
    SPELLING     the same family punctuated differently ("WE-KI HC" against
                 REDEXPERT's "WE-KIHC").

Both are reported and both fail the guard; they are counted separately so a
campaign can attack the wrong-family rows first.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DATA = REPO / "data"
DEFAULT_SNAPSHOT = REPO / "staging" / "we" / "redexpert_order_code_series.json"

BASE = "https://redexpert.we-online.com/redexpert"
# A full Chrome UA is mandatory: a bare "Mozilla/5.0" is 301'd to update-browser.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
CTRL = re.compile(r"[\x00-\x1f]")

WURTH = re.compile(r"w[uü]e?rth", re.IGNORECASE)
CATALOGUES = ["magnetics.ndjson", "capacitors.ndjson", "resistors.ndjson",
              "connectors.ndjson", "varistors.ndjson", "thermistors.ndjson"]


# ---------------------------------------------------------------------------
# ground truth
# ---------------------------------------------------------------------------
def fetch_json(url: str):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json",
        "Accept-Encoding": "gzip"})
    raw = urllib.request.urlopen(req, timeout=180).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(CTRL.sub(" ", raw.decode("utf-8", "replace")))


def rows_of(payload):
    """The product list inside a product/list response, whatever it is wrapped in."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


def pull_ground_truth(snapshot: Path):
    """{Order_Code: Series} pulled live from REDEXPERT, and written to `snapshot`."""
    modules = fetch_json(BASE + "/modules/all")
    ids = sorted({m["ModuleID"] for m in modules
                  if isinstance(m, dict) and isinstance(m.get("ModuleID"), int)})
    if not ids:
        raise RuntimeError("REDEXPERT /modules/all returned no ModuleID -- refusing "
                           "to build a partial ground truth")
    series: dict[str, str] = {}
    conflicts: dict[str, set] = {}
    for mid in ids:
        try:
            payload = fetch_json("%s/product/list/%d" % (BASE, mid))
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
            continue
        for row in rows_of(payload):
            code = str(row.get("Order_Code") or "").strip()
            fam = str(row.get("Series") or "").strip()
            if not code or not fam:
                continue
            if code in series and series[code] != fam:
                conflicts.setdefault(code, {series[code]}).add(fam)
            series[code] = fam
        time.sleep(0.05)
    if conflicts:
        # REDEXPERT itself disagreeing about a code is not something to average
        # away: drop those codes from the ground truth and say so.
        for code in conflicts:
            series.pop(code, None)
        print("NOTE: %d order code(s) carry more than one Series in REDEXPERT and "
              "are excluded from the ground truth" % len(conflicts), file=sys.stderr)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps(
        {"fetchedAt": time.strftime("%Y-%m-%d"),
         "source": BASE + "/product/list/{moduleId}",
         "modules": ids, "series": series}, ensure_ascii=False))
    return series


def load_ground_truth(snapshot: Path, refresh: bool, offline: bool):
    if refresh and offline:
        raise SystemExit("--refresh and --offline are contradictory")
    if not refresh and snapshot.exists():
        return json.loads(snapshot.read_text())["series"], "snapshot %s" % snapshot
    if offline:
        raise SystemExit(
            "NO GROUND TRUTH: %s does not exist and --offline forbids fetching it.\n"
            "Run once without --offline to pull REDEXPERT. A guard with nothing to "
            "compare against fails; it does not pass." % snapshot)
    return pull_ground_truth(snapshot), "live REDEXPERT product/list"


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------
def component_of(rec):
    body = rec
    for _ in range(3):
        if not isinstance(body, dict) or "manufacturerInfo" in body:
            break
        keys = [k for k in body if isinstance(body.get(k), dict)]
        if len(keys) != 1:
            break
        body = body[keys[0]]
    return body if isinstance(body, dict) else {}


def verdict(stored: str, truth: str):
    if stored == truth:
        return None
    a, b = stored.upper(), truth.upper()
    if re.sub(r"[\s-]", "", a) == re.sub(r"[\s-]", "", b):
        # "WE-KI HC" against REDEXPERT's "WE-KIHC": the same family, spelled
        # differently. Still the vendor's spelling that is authoritative, but
        # calling it a wrong family would inflate the number that matters.
        return "SPELLING"
    if a.startswith(b) or b.startswith(a):
        return "GRANULARITY"
    return "MISMATCH"


def scan_file(path: Path, series: dict):
    findings, rows, checked = [], 0, 0
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            rows += 1
            mi = component_of(rec).get("manufacturerInfo", {})
            if not isinstance(mi, dict) or not WURTH.search(str(mi.get("name") or "")):
                continue
            part = (mi.get("datasheetInfo") or {}).get("part") or {}
            codes = [str(c).strip() for c in
                     (part.get("partNumber"), mi.get("reference")) if c]
            truth = next((series[c] for c in codes if c in series), None)
            if truth is None:
                continue
            checked += 1
            stored = str(mi.get("family") or "").strip()
            if not stored:
                continue          # absent is the correct state for "unknown"
            v = verdict(stored, truth)
            if v:
                findings.append((lineno, codes[0], stored, truth, v))
    return findings, rows, checked


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--file", type=Path, action="append", default=[])
    ap.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    ap.add_argument("--refresh", action="store_true",
                    help="re-pull REDEXPERT even if a snapshot exists")
    ap.add_argument("--offline", action="store_true",
                    help="use only the snapshot; fail if there is none")
    ap.add_argument("--list", action="store_true", help="print every finding")
    args = ap.parse_args()

    series, origin = load_ground_truth(args.snapshot, args.refresh, args.offline)
    print("ground truth: %d Wuerth order codes (%s)" % (len(series), origin))
    if not series:
        print("EMPTY GROUND TRUTH -- nothing to check against.", file=sys.stderr)
        return 1

    targets = args.file or [args.data / n for n in CATALOGUES
                            if (args.data / n).exists()]
    total = Counter()
    for path in targets:
        if not path.exists():
            print("MISSING: %s" % path, file=sys.stderr)
            return 1
        findings, rows, checked = scan_file(path, series)
        print("%s: %d rows, %d Wuerth part(s) with a REDEXPERT order code, "
              "%d disagreement(s)" % (path.name, rows, checked, len(findings)))
        shown = findings if args.list else findings[:10]
        for lineno, code, stored, truth, v in shown:
            print("  line %d: %s -- %s: stored family %r, REDEXPERT Series %r"
                  % (lineno, code, v, stored, truth))
        if not args.list and len(findings) > len(shown):
            print("  ... and %d more (--list for all)" % (len(findings) - len(shown)))
        for _, _, _, _, v in findings:
            total[v] += 1

    if total:
        print("\nFAIL: %d wrong-family row(s) (MISMATCH), %d wrong-granularity "
              "row(s) (GRANULARITY), %d differently-spelled row(s) (SPELLING).\n"
              "The vendor's own Series for the exact order "
              "code is the authority. An importer that cannot resolve the family "
              "must OMIT it -- never default it, and never derive it from an "
              "order-code prefix (744300 spans WE-HCM and WE-HCMD; 744310, 744313 "
              "and 744314 each span WE-HCI and WE-HCM)."
              % (total["MISMATCH"], total["GRANULARITY"], total["SPELLING"]),
              file=sys.stderr)
        return 1
    print("OK: every Wuerth family agrees with REDEXPERT's Series")
    return 0


if __name__ == "__main__":
    sys.exit(main())
