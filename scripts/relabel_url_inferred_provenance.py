#!/usr/bin/env python3
"""Stop 52,439 records from claiming a retrieval that never happened.

    python3 scripts/relabel_url_inferred_provenance.py [FILE ...] [--dry-run]

THE DEFECT. backfill_provenance.py writes provenance[] onto records that lack it,
months after the fact. Its first rule treats "the record's own datasheetUrl host
matches a known vendor" as SELF-EVIDENCING, and stamps a concrete source, a
sourceName, and a specific retrievedDate. The script contains no HTTP call of any
kind — it never fetches the URL. So a record only had to CARRY a plausible vendor
URL string to be credited with having been scraped from that vendor on that date.

That is how the ABT #351 batch of 195 invented Coilcraft magnetics came to hold
better-looking provenance than most genuine rows: the fabricator minted real family
names and a URL on the right domain, and the back-fill supplied the audit trail.

The same hole was found once before, in ABT #247, and the fix was applied to only
half of it. backfill_provenance.py's own comment says the manufacturer-name
fallback is "exactly how 177 fabricated parts came to look legitimately sourced and
reached production", and commit 21c7ae3 relabelled those 8,008 name-only stamps.
The URL path was left classified as self-evidencing, and still asserts a date.

WHAT THIS DOES. A URL is evidence only if someone fetched it and found the part
there. Until that check runs, these entries state an inference, so they are made to
say so: retrievedDate is REMOVED (that date belonged to the campaign, never to this
record) and sourceName gains an explicit marker. sourceUrl is KEPT — it is the
claim that verification will test — and `source` is left alone so the entry still
records which vendor is being claimed.

WHY IT DOES NOT TOUCH EVERY RECORD. Only entries provably written by the back-fill
are relabelled, identified by the (sourceName, retrievedDate) pairs hardcoded in
its DOMAIN_MAP and MANUF_MAP. Those strings were invented by that script, so the
match is exact. Provenance recorded by a real importer at the moment it read the
vendor — which IS earned — is left untouched, and so are records carrying a second,
corroborating source.

Reversible by design: verification can promote an entry back by re-adding the date
once a fetch confirms the URL serves that part.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "url_inferred_provenance_audit.json"

MARKER = " [inferred from the record's own URL — this record was not verified against that source]"

_spec = importlib.util.spec_from_file_location(
    "backfill_provenance", REPO / "scripts" / "backfill_provenance.py")
_bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bf)

# (sourceName, retrievedDate) pairs this script invented — its exact fingerprint.
BACKFILL_STAMPS = {(name, date) for _, (_, name, date) in _bf.DOMAIN_MAP if date}
BACKFILL_STAMPS |= {(name, date) for (_, name, date) in _bf.MANUF_MAP.values() if date}

PATHS = {
    "mosfets": ("semiconductor", "mosfet"), "diodes": ("semiconductor", "diode"),
    "igbts": ("semiconductor", "igbt"), "bjts": ("semiconductor", "bjt"),
    "capacitors": ("capacitor",), "magnetics": ("magnetic",), "resistors": ("resistor",),
    "varistors": ("varistor",), "connectors": ("connector",), "controllers": ("controller",),
}


def _di(rec, path):
    o = rec
    for k in path:
        if not isinstance(o, dict) or k not in o:
            return None
        o = o[k]
    mi = o.get("manufacturerInfo") if isinstance(o, dict) else None
    return mi.get("datasheetInfo") if isinstance(mi, dict) else None


def relabel(di):
    """Returns True when this record's sole, unearned URL stamp was downgraded."""
    prov = di.get("provenance") or []
    if len(prov) != 1 or not isinstance(prov[0], dict):
        return False                       # corroborated by a second source
    e = prov[0]
    if not e.get("sourceUrl"):
        return False                       # nothing to verify later
    if (e.get("sourceName"), e.get("retrievedDate")) not in BACKFILL_STAMPS:
        return False                       # not this script's output
    e.pop("retrievedDate", None)
    if MARKER not in str(e.get("sourceName", "")):
        e["sourceName"] = f"{e.get('sourceName', '')}{MARKER}"
    return True


def process(path, dry):
    key = path.name.replace(".ndjson", "")
    if key not in PATHS:
        return None
    tmp = path.with_suffix(".ndjson.tmp")
    hit = total = 0
    with open(path, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            total += 1
            line = raw
            if b'"retrievedDate"' in raw:
                try:
                    rec = json.loads(raw)
                    di = _di(rec, PATHS[key])
                    if di is not None and relabel(di):
                        hit += 1
                        line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
                except Exception:
                    line = raw
            out.write(line)
        out.flush()
        os.fsync(out.fileno())
    if dry:
        tmp.unlink(missing_ok=True)
    else:
        os.replace(tmp, path)
    return {"file": path.name, "rows": total, "relabelled": hit}


def main(argv):
    dry = "--dry-run" in argv
    names = [a for a in argv if not a.startswith("--")]
    files = [Path(n) if os.path.sep in n else DATA / n for n in names] or \
            [DATA / f"{k}.ndjson" for k in PATHS]
    results = []
    for p in files:
        if not p.exists():
            print(f"  skip {p.name} (absent)")
            continue
        r = process(p, dry)
        if r:
            results.append(r)
            print(f"  {r['file']:24} {r['relabelled']:7} of {r['rows']} rows relabelled")
    total = sum(r["relabelled"] for r in results)
    print(f"\n{total} provenance entries downgraded to inferred-not-verified")
    if dry:
        print("--dry-run: nothing written")
    else:
        AUDIT.write_text(json.dumps(
            {"ticket": "ABT #351 root cause (unverified URL provenance)",
             "marker": MARKER, "files": results}, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
