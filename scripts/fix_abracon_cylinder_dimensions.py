#!/usr/bin/env python3
"""Give Abracon's leaded cylinders a real diameter instead of width = 0 (ABT #386).

    python3 scripts/fix_abracon_cylinder_dimensions.py [--dry-run] [--cache DIR]

THE DEFECT. 633 Abracon rows carry `width: 0` exactly — not a rounding artifact, a
literal zero written by the "Abracon parametric API (scraped JSON)" importer. All of
them are AIAP / AIUR / AIRD / AISR parts: axial and radial LEADED CYLINDERS. A
cylinder has a diameter and a body length; it has no width, so the importer had a
value with nowhere to go and wrote 0 rather than leaving the field out.

MAS already models this properly — magneticDatasheetMechanical has a `diameter`
field alongside length/width/height, and nothing in it is required. The importer
simply did not use it.

A zero dimension is not a cosmetic gap. Every check that relates power or energy to
size divides by surface area, so a row whose area is zero is silently EXEMPT from
the physics that would otherwise test it — including MAG_DISS_DENSITY, the check
that exposed the fabricated Coilcraft batch in ABT #351. These 633 rows have never
been examined by it.

AND THE DIAMETER IT DID WRITE IS WRONG. For AIAP-01 the datasheet states
"9.14 x ϕ3.9mm". The corpus holds height 9.14 (the body length, correct) and length
3.3 — not the 3.9 the vendor publishes. So this repair is not only filling a hole;
it is correcting a dimension that was wrong in a way no electrical check could see.

HOW A PART GETS ITS DIMENSIONS. Each Abracon series sheet publishes one or more
package variants as "<body length> x ϕ<diameter>mm", and the per-part table's last
column gives the variant code (A, B, ...) for that part number:

    AIAP-01-1R0   1.0   K   0.030   190   3300   1R0   A
                                                        ^ dimension code

The code-to-variant mapping is not itself written in the text layer (the dimension
drawings are images), so it is taken as document order — first variant is A, second
is B — and then CONFIRMED against evidence rather than trusted: the importer already
read each part's body length correctly into `height`, so a code assignment is only
accepted when the variant's body length matches the height already in the record. A
series where that check fails is reported and left alone, never guessed at.

RESULT PER ROW: `diameter` and `height` set from the vendor string, `width` and the
bogus `length` REMOVED, and provenance recording the datasheet actually fetched.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validator" / "build-ninja"))
from extract_tdk_cmc import _build_registry, _load_magnetic_schema  # noqa: E402
import tas_validator  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "abt386_abracon_dimension_audit.json"
CACHE = Path(os.environ.get("ABRACON_CACHE", "/tmp/abracon_ds"))
TODAY = "2026-07-31"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")

# "9.14 x ϕ3.9mm"  ->  (body length, diameter) in mm. The phi may be ϕ, φ or Ø.
DIM_RE = re.compile(r"([\d.]+)\s*[xX×]\s*[ϕφØø]\s*([\d.]+)\s*mm")
CODE_RE = re.compile(r"^\s*(AI[A-Z]{2}-?\d+[A-Za-z]?-\S+)\s+.*?\s([A-Z])\s*$")


def pdf_text(path):
    import subprocess
    r = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                       capture_output=True, text=True)
    return r.stdout


def fetch(url, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / (re.sub(r"[^A-Za-z0-9._-]", "_", url.rsplit("/", 1)[-1]))
    if p.exists() and p.stat().st_size > 20_000:
        return p, "cached"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    except Exception as e:                                        # noqa: BLE001
        return None, f"{type(e).__name__}"
    time.sleep(0.4)
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return None, f"HTTP {r.status_code}"
    p.write_bytes(r.content)
    return p, "fetched"


def nom(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("nominal", "maximum", "minimum"):
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
    return None


def scan_corpus():
    """refs needing repair, grouped by the datasheet each one already cites."""
    by_url = defaultdict(list)
    for raw in open(DATA, "rb"):
        if b"Abracon" not in raw:
            continue
        try:
            rec = json.loads(raw)
            mi = rec["magnetic"]["manufacturerInfo"]
            if str(mi.get("name")) != "Abracon":
                continue
            di = mi["datasheetInfo"]
            m = di.get("mechanical") or {}
        except Exception:
            continue
        if nom(m.get("width")) != 0:
            continue
        by_url[str(mi.get("datasheetUrl"))].append(
            (str(mi.get("reference")), nom(m.get("height")), nom(m.get("length"))))
    return by_url


def build_plan(by_url, cache):
    """url -> {ref: (diameter_m, height_m)} plus a per-series report."""
    plan, report = {}, []
    for url, refs in sorted(by_url.items()):
        path, how = fetch(url, cache)
        if not path:
            report.append({"datasheet": url, "rows": len(refs), "status": f"unreachable ({how})"})
            continue
        text = pdf_text(path)
        variants = []
        for m in DIM_RE.finditer(text):
            v = (float(m.group(1)), float(m.group(2)))
            if v not in variants:
                variants.append(v)
        if not variants:
            report.append({"datasheet": url, "rows": len(refs),
                           "status": "no '<len> x phi<dia>mm' string in the text layer"})
            continue
        codes = {}
        for line in text.splitlines():
            m = CODE_RE.match(line)
            if m:
                codes[m.group(1).upper()] = m.group(2)
        letters = [chr(ord("A") + i) for i in range(len(variants))]
        by_letter = dict(zip(letters, variants))

        # CONFIRM the document-order assumption: the height already in the record was
        # read from this same sheet, so it must equal the assigned variant's length.
        assigned, mismatched = {}, 0
        for ref, height_m, _ in refs:
            letter = codes.get(ref.upper())
            var = by_letter.get(letter) if letter else (variants[0] if len(variants) == 1 else None)
            if var is None:
                mismatched += 1
                continue
            if height_m is None or abs(var[0] / 1000 - height_m) > 1e-6:
                mismatched += 1
                continue
            assigned[ref] = (var[1] / 1000, var[0] / 1000)
        report.append({"datasheet": url, "rows": len(refs), "status": how,
                       "variants": [f"{L} x phi{D} mm" for L, D in variants],
                       "confirmed": len(assigned), "unconfirmed": mismatched})
        plan.update(assigned)
    return plan, report


def main(argv):
    dry = "--dry-run" in argv
    cache = Path(argv[argv.index("--cache") + 1]) if "--cache" in argv else CACHE
    by_url = scan_corpus()
    print(f"{sum(len(v) for v in by_url.values())} rows with width=0 across "
          f"{len(by_url)} datasheets\n")
    plan, report = build_plan(by_url, cache)
    for r in sorted(report, key=lambda x: -x["rows"]):
        print(f"  {r['rows']:4} rows  {r['datasheet'].rsplit('/',1)[-1]:18} {r['status']:14} "
              f"{r.get('variants','')} confirmed={r.get('confirmed','-')} "
              f"unconfirmed={r.get('unconfirmed','-')}")
    print(f"\n{len(plan)} rows have a CONFIRMED vendor diameter")
    if not plan:
        print("nothing to apply")
        return 0

    validator = _load_magnetic_schema(_build_registry())
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #386 (Abracon cylinders)", "date": TODAY,
             "datasheets": report, "repaired": []}
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            wrote = False
            if b"Abracon" in raw:
                try:
                    rec = json.loads(raw)
                    mi = rec["magnetic"]["manufacturerInfo"]
                    ref = str(mi.get("reference"))
                except Exception:
                    ref = None
                if ref in plan:
                    dia, h = plan.pop(ref)
                    di = mi["datasheetInfo"]
                    mech = di.get("mechanical") or {}
                    was = {k: nom(mech.get(k)) for k in ("length", "width", "height")}
                    mech.pop("width", None)
                    mech.pop("length", None)
                    mech["diameter"] = {"nominal": dia}
                    mech["height"] = {"nominal": h}
                    di["mechanical"] = mech
                    di["provenance"] = [{
                        "source": "manufacturerDatasheet",
                        "sourceName": "Abracon series datasheet, package variant table "
                                      "(fetched and read)",
                        "sourceUrl": str(mi.get("datasheetUrl")),
                        "retrievedDate": TODAY}]
                    if not list(validator.iter_errors(rec["magnetic"])):
                        vd = tas_validator.validate(json.dumps(rec))
                        if not [f for f in vd.findings
                                if str(f.severity).upper() == "IMPOSSIBLE"]:
                            out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                            wrote = True
                            audit["repaired"].append(
                                {"reference": ref, "wasMm": {k: (v * 1000 if v else v)
                                                             for k, v in was.items()},
                                 "nowMm": {"diameter": dia * 1000, "height": h * 1000}})
            if not wrote:
                out.write(raw)
        out.flush()
        os.fsync(out.fileno())

    print(f"repaired {len(audit['repaired'])}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("--dry-run: nothing replaced")
    else:
        os.replace(tmp, DATA)
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
