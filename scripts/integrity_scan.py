#!/usr/bin/env python3
"""Standing integrity scan: run every guard, report only what is NEW.

    python3 scripts/integrity_scan.py [--json PATH] [--baseline PATH] [--quiet]

WHY THIS EXISTS. A one-shot repair cannot hold an invariant. Proven on 2026-09-04:
strip_provenance_narrative.py cleaned 14 catalogues, and 410 fresh violations were
written into connectors within six hours by later commits. Every large defect cohort
found in the 2026-09-05 audit came from an IMPORTER, not from a thousand
independently bad rows -- 549 TDK chip beads minted an identical 1e-09 H, 404 Wuerth
rows took a "WE-MAPI" default, 716 onsemi diodes were all called "rectifier". So the
guards have to run on a schedule, not once.

TOKENS. This costs ZERO model tokens. It is plain Python over the NDJSON files, and
it is the whole point of the design: the expensive judgement (is this rule measuring
the parts or measuring my parser?) was spent once, when each guard was written and
counter-checked. Running them is arithmetic.

QUIET BY DEFAULT, WHICH IS THE HARD PART. A nightly job that prints 1,265 known
findings every night is a job nobody reads. This fingerprints every finding and
reports only the ones absent from the baseline, so a clean night prints nothing and
exits 0, and a NEW defect is impossible to miss. Findings that disappear are reported
too -- a guard that stops firing is either a fix or a broken guard, and the
difference matters.

EXIT CODES
    0   no new findings (known findings may still exist; see the JSON)
    1   NEW findings appeared -- someone should look
    2   a guard could not run. A check that cannot run FAILS, it never skips.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO / "staging" / "integrity" / "baseline.json"

# The house guards all print findings in one shape, verified against their real
# output: "  line 4385: TDK KCZ1210AH900HRTD25 -- QUANTITY_MISMATCH inductance: ...".
# The identity is everything before " -- ", which is manufacturer + part for some
# guards and a bare order code for others; either is stable per finding.
LINE_FINDING = re.compile(r"^\s*line \d+:\s*(?P<id>.+?)\s+--\s+(?P<why>.+)$")

# A summary line is not a finding. Counting it as one makes the report grow a phantom
# entry every time a real count changes.
SUMMARY = re.compile(r"^\s*(?:FAIL|OK|WARN)[:.]|^\s*\d[\d,]* rows? trimmed|"
                     r"^\s*zero dangling|^\s*--check:|^\s*\.\.\. and \d+ more|"
                     # catalogue_audit's own headers: "capacitors.ndjson: 230,000
                     # rows, 12 finding(s)" and "  [units] 47". Neither is a
                     # finding, and counting them adds a phantom entry to the
                     # diff every time a real count moves.
                     r"^\S+\.ndjson: [\d,]+ rows|^\s*\[\w+\] [\d,]+$|"
                     r"^[\d,]+ live rows over|^report written to|^=+$")

GUARDS = [
    ("fabricated", ["python3", "scripts/check_no_fabricated_parts.py"], LINE_FINDING),
    ("constant_cohorts", ["python3", "scripts/check_no_constant_cohorts.py"], LINE_FINDING),
    ("wurth_family",
     ["python3", "scripts/check_wurth_family_matches_series.py", "--offline"], LINE_FINDING),
    ("provenance_narrative",
     ["python3", "scripts/strip_provenance_narrative.py", "--check"], LINE_FINDING),
    ("component_uris", ["python3", "scripts/check_component_uris.py"], LINE_FINDING),
    ("topology", ["python3", "scripts/validate_topology.py"], LINE_FINDING),
    # The six per-catalogue audits that were being done by hand at ~900k tokens a
    # night. Aggregate findings (one line per cohort/field/reason) keep the diff
    # readable where the row counts run to six figures.
    ("catalogue_audit", ["python3", "scripts/catalogue_audit.py"], LINE_FINDING),
]

# How each guard says "I ran and found nothing". Absence of this on a zero-finding
# run means the guard did not actually complete -- the disk-full crash on
# 2026-09-05 exited 0 from a traceback and would otherwise have read as clean.
CLEAN_MARKER = re.compile(r"OK: no fabricated|--check: clean|zero dangling references|"
                          r"\bno (?:findings|violations|mismatches)\b|"
                          r"^OK\b|all URIs resolve|0 error", re.M)


def fingerprint(guard: str, ident: str, why: str) -> str:
    """Stable identity for a finding. Numbers inside `why` are normalised out so a
    cohort growing from 549 to 550 members is the same finding, not a new one."""
    norm = re.sub(r"\d+(?:\.\d+)?(?:e-?\d+)?", "#", why.lower())
    return hashlib.sha256(f"{guard}\x00{ident}\x00{norm}".encode()).hexdigest()[:16]


def run_guard(name, argv, line_re, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=REPO, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"guard": name, "ran": False, "error": f"timed out after {timeout}s",
                "findings": [], "seconds": round(time.time() - t0, 1)}
    except FileNotFoundError as e:
        return {"guard": name, "ran": False, "error": str(e), "findings": [],
                "seconds": round(time.time() - t0, 1)}

    out = (p.stdout or "") + (p.stderr or "")
    findings = []
    for line in out.splitlines():
        if SUMMARY.match(line):
            continue
        m = line_re.match(line)
        if m:
            ident = m.group("id").strip()
            why = m.group("why").strip()
            findings.append({"id": ident, "why": why,
                             "fp": fingerprint(name, ident, why)})

    # A guard that crashed is NOT a clean guard, and this is the case that bit us:
    # on 2026-09-05 strip_provenance_narrative died on "No space left on device"
    # and still exited 0. Zero findings only counts as clean when the guard SAYS
    # it finished, and a traceback is never clean whatever the exit code.
    crashed = "Traceback (most recent call last)" in out
    said_clean = bool(CLEAN_MARKER.search(out))
    if crashed:
        err = "traceback in output -- the run did not complete"
    elif p.returncode not in (0, 1):
        err = f"exit {p.returncode}"
    elif not findings and not said_clean:
        err = "no findings and no completion marker -- cannot tell clean from crashed"
    else:
        err = None
    # Guards truncate their own output ("... and N more"), so the parsed lines are a
    # SAMPLE, not the population -- wurth_family prints ~10 of its 1,265. Rather than
    # infer a total from prose (a first attempt read "959,611 rows scanned" as 959,611
    # findings), keep the guard's own verdict line verbatim and let it speak.
    verdict = ""
    for line in out.splitlines():
        if re.match(r"^\s*(?:FAIL|OK)\b", line):
            verdict = line.strip()
    return {"guard": name, "ran": err is None, "exit": p.returncode, "error": err,
            "findings": findings, "sampled": len(findings), "verdict": verdict,
            "seconds": round(time.time() - t0, 1)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=Path, help="write the full result here")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                    help="known-findings file; new ones are reported against it")
    ap.add_argument("--update-baseline", action="store_true",
                    help="accept the current findings as the new baseline")
    ap.add_argument("--timeout", type=int, default=3600, help="per-guard seconds")
    ap.add_argument("--only", help="run just this guard")
    ap.add_argument("--quiet", action="store_true", help="print only new//gone findings")
    args = ap.parse_args(argv)

    guards = [g for g in GUARDS if not args.only or g[0] == args.only]
    results = [run_guard(n, a, r, args.timeout) for n, a, r in guards]

    known = {}
    if args.baseline.exists():
        known = {f["fp"]: f for f in json.loads(args.baseline.read_text()).get("findings", [])}

    current = {}
    for res in results:
        for f in res["findings"]:
            current[f["fp"]] = dict(f, guard=res["guard"])

    new = [f for fp, f in current.items() if fp not in known]
    gone = [f for fp, f in known.items() if fp not in current]
    broken = [r for r in results if not r["ran"]]

    if not args.quiet:
        for r in results:
            state = "OK" if r["ran"] else "COULD NOT RUN"
            print(f"  {r['guard']:22} {state:14} {r['seconds']:>7.1f}s  "
                  + (r["error"] or r["verdict"] or "no verdict line")[:96])

    if broken:
        print(f"\nFAIL: {len(broken)} guard(s) could not run -- "
              "a check that cannot run FAILS, it never skips:")
        for r in broken:
            print(f"  {r['guard']}: {r['error']}")

    if new:
        print(f"\nNEW: {len(new)} finding(s) not in the baseline:")
        for f in new[:40]:
            print(f"  [{f['guard']}] {f['id']}: {f['why'][:130]}")
        if len(new) > 40:
            print(f"  ... and {len(new) - 40} more")
    if gone and not args.quiet:
        print(f"\nGONE: {len(gone)} baseline finding(s) no longer fire "
              "(a fix, or a guard that stopped looking -- check which):")
        for f in gone[:10]:
            print(f"  [{f.get('guard','?')}] {f['id']}: {f['why'][:110]}")
        if len(gone) > 10:
            print(f"  ... and {len(gone) - 10} more")

    payload = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "guards": [{k: v for k, v in r.items() if k != "findings"} for r in results],
               "counts": {"known": len(known), "current": len(current),
                          "new": len(new), "gone": len(gone)},
               "findings": list(current.values()), "new": new, "gone": gone}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=1))
    if args.update_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps({"generated": payload["generated"],
                                             "findings": list(current.values())}, indent=1))
        print(f"\nbaseline updated: {len(current)} known finding(s)")

    if broken:
        return 2
    if new:
        return 1
    if not args.quiet:
        print(f"\nno new findings among {len(current)} sampled "
              "(each guard's own verdict line is above)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
