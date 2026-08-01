#!/usr/bin/env python3
"""Re-read Würth electrolytic ESR from Würth's own database (ABT #439).

WHAT WENT WRONG. Qarlos sampled one Würth capacitor and found ESR = 1.479 mOhm on a
68 uF / 25 V can rated 0.132 A ripple. That part dissipates I^2*R = 26 microwatts at its
rated ripple; no can is rated by a self-heating of 26 uW. Sweeping the catalogue on that
physics — stated ripple against stated ESR — returns 732 records, and every one of them
is Würth. A defect that stops exactly at one vendor's boundary is one importer's, not a
property of electrolytics.

The records say so themselves. Their provenance reads:

  "Würth REDEXPERT product list, module 20 — this order code confirmed present in
   Würth's own catalogue (electrical values not re-read)"

The #391 re-citation campaign verified that these order codes EXIST at the manufacturer
and was explicit that it had not re-read the numbers. So the ESR is whatever an older,
unattributed import left behind, carrying a citation that was only ever a claim about the
part number. This finishes that job for module 20.

NOT A UNITS FIX. The obvious reading is a factor-of-1000 slip, and it is wrong: against
Würth's published values the stored numbers are too small by 27x to 645x, median 118x.
There is no scale factor to apply and no arithmetic that recovers them, which is the
whole argument for re-reading rather than repairing. Nothing here computes an ESR.

WHAT IS WRITTEN
  esr <- Resistance_ESR from /redexpert/product/list/20, per order code.
  provenance <- a citation that says the electrical value was actually read this time.

WHAT IS NOT WRITTEN
  esrFrequency. Würth publishes Frequency_Ripple (120 Hz or 100 kHz per part), but that is
  the frequency the RIPPLE CURRENT is rated at, and it is NOT the basis of this ESR:
  across both cohorts (2*pi*120*C*ESR)/DF has median 0.14 and 0.09, so the value is far
  below the 120 Hz ESR the dissipation factor implies. It is presumably a high-frequency
  figure, but "presumably" is not a measurement condition. An absent frequency is honest;
  a guessed one silently licenses every comparison made against it.

  we_electrolytic_esr_recite.py fetch     # -> staging/we/elko_module20.json
  we_electrolytic_esr_recite.py apply [--write]
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
LIVE = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "we"
RAW = STAGE / "elko_module20.json"
AUDIT = TAS / "staging" / "we_elko_esr_audit.json"
MFR = "Würth Elektronik"
URL = "https://redexpert.we-online.com/redexpert/product/list/20"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
TODAY = "2026-08-01"

CITATION = {
    "source": "manufacturerDatabase",
    "sourceName": ("Würth REDEXPERT product list, module 20 (Electrolyte Capacitors) — "
                   "ESR re-read from Resistance_ESR, the manufacturer's own value"),
    "sourceUrl": URL,
    "retrievedDate": TODAY,
}
# The citation this replaces: it only ever claimed the ORDER CODE was real.
STALE_MARK = "electrical values not re-read"


def cmd_fetch(a):
    STAGE.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(URL, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read().decode("utf-8", "replace")
    # REDEXPERT responses carry raw control characters that json.loads rejects.
    doc = json.loads(re.sub(r"[\x00-\x1f]", "", raw))
    RAW.write_text(json.dumps(doc, ensure_ascii=False))
    data = doc["Data"]
    with_esr = sum(1 for p in data
                   if isinstance(p.get("Resistance_ESR"), (int, float))
                   and p["Resistance_ESR"] > 0)
    print(f"{len(data)} Würth electrolytics; {with_esr} publish Resistance_ESR")
    print(f"wrote {RAW}")
    return 0


def cmd_apply(a):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from blade_gate import BladeGate
    from merge_staged_connectors import build_validator  # noqa: F401  (connector-only)
    gate = BladeGate("capacitor")

    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    by_id = {}
    for repo in ("PEAS", "CAS"):
        d = TAS.parent / repo / "schemas"
        for p in d.rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by_id[s["$id"]] = s
    reg = Registry().with_resources(
        [(k, Resource(contents=s, specification=DRAFT202012)) for k, s in by_id.items()])
    validator = Draft202012Validator(
        json.loads((TAS.parent / "CAS" / "schemas" / "capacitor.json").read_text()),
        registry=reg)

    if not RAW.exists():
        sys.exit(f"missing {RAW} — run `we_electrolytic_esr_recite.py fetch` first")
    we = {str(p.get("Order_Code")): p for p in json.loads(RAW.read_text())["Data"]}

    stats = Counter()
    ratios = []
    rejected = []
    tmp = LIVE.with_suffix(".ndjson.esr_tmp")
    with LIVE.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as out:
        for line in src:
            s = line.rstrip("\n")
            if not s.strip():
                continue
            stats["total"] += 1
            # Prefilter on a substring that survives BOTH encodings: part of this file
            # is written with ensure_ascii, so the vendor appears as "W\\u00fcrth
            # Elektronik" on some lines and "Würth Elektronik" on others. Matching the
            # precomposed form alone silently skipped 1,356 of 1,357 records — the
            # scan reported "1 patched" and looked like there was nothing to fix.
            if "rth Elektronik" not in s:
                out.write(s + "\n")
                continue
            obj = json.loads(s)
            c = obj.get("capacitor") or obj
            mi = c.get("manufacturerInfo") or {}
            if mi.get("name") != MFR:
                out.write(s + "\n")
                continue
            p = we.get(str(mi.get("reference")))
            if not p:
                stats["we_not_in_module20"] += 1
                out.write(s + "\n")
                continue
            true_esr = p.get("Resistance_ESR")
            if not isinstance(true_esr, (int, float)) or true_esr <= 0:
                stats["no_published_esr"] += 1
                out.write(s + "\n")
                continue

            di = mi.setdefault("datasheetInfo", {})
            elec = di.setdefault("electrical", {})
            old = elec.get("esr")
            if isinstance(old, (int, float)) and old > 0:
                ratios.append(true_esr / old)
                if true_esr / old > 2 or old / true_esr > 2:
                    stats["materially_wrong"] += 1
            elec["esr"] = true_esr
            # An ESR whose frequency we cannot establish must not carry a guessed one.
            elec.pop("esrFrequency", None)

            prov = [x for x in (di.get("provenance") or [])
                    if STALE_MARK not in (x.get("sourceName") or "")]
            if not any(x.get("sourceName") == CITATION["sourceName"] for x in prov):
                prov.append(dict(CITATION))
            di["provenance"] = prov

            errs = sorted(validator.iter_errors(c), key=lambda e: e.path)
            if errs:
                stats["rejected_invalid"] += 1
                if len(rejected) < 5:
                    rejected.append(f"{mi.get('reference')}: {errs[0].message[:150]}")
                out.write(s + "\n")          # ORIGINAL line untouched
                continue
            ok, why = gate.check(c)
            if not ok:
                stats["rejected_blade"] += 1
                if len(rejected) < 5:
                    rejected.append(f"{mi.get('reference')}: BLADE {why}")
                out.write(s + "\n")
                continue

            stats["patched"] += 1
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")

    if a.write:
        os.replace(tmp, LIVE)
    else:
        tmp.unlink()

    print("APPLIED" if a.write else "DRY RUN — nothing written")
    for k in ("total", "patched", "materially_wrong", "we_not_in_module20",
              "no_published_esr", "rejected_invalid", "rejected_blade"):
        print(f"  {k:22} {stats[k]}")
    if ratios:
        ratios.sort()
        print(f"  ratio published/stored: min {min(ratios):.1f} "
              f"median {ratios[len(ratios)//2]:.1f} max {max(ratios):.1f}")
    for r in rejected:
        print(f"    {r}")
    AUDIT.write_text(json.dumps({"stats": dict(stats), "rejected": rejected},
                                indent=1))
    if not a.write:
        print("Re-run with --write to apply.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    ap2 = sub.add_parser("apply")
    ap2.add_argument("--write", action="store_true")
    a = ap.parse_args()
    return {"fetch": cmd_fetch, "apply": cmd_apply}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
