#!/usr/bin/env python3
"""Apply the staged rowPitch (#477) and TE derating (#469) datasets to connectors.ndjson.

Both are STAGED, per-series/per-part, vendor-verified datasets produced by the two
extraction agents. This applier is the single serialized writer:
  * rowPitch  -> mechanical.rowPitch, source='derived' + derivation citing the
    verified drawing (dual-row header-class only; the extraction measured the row
    spacing per drawing, so a non-trivial rowPitch != pitch is carried as-is).
  * derating  -> derating.currentVsAmbient (an existing CONAS deratingCurve),
    source='manufacturerDatasheet' + the spec URL.

Safety (same contract as fill_connector_pinout_geometry.py): refuses if another
process holds the file for writing; streams line-by-line copying untouched lines
byte-identical; validates every MODIFIED record against CONAS before the atomic
os.replace; verifies the line count. One invalid modification aborts the run.

Usage: apply_rowpitch_derating.py [--rowpitch FILE] [--derating FILE] [--dry-run]
"""
import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO.parent
DATA = REPO / "data" / "connectors.ndjson"
TODAY = "2026-08-02"


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    res = []
    for repo in ("PEAS", "CIAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS", "TAS", "TDAS", "COAS"):
        sdir = WORKSPACE / repo / "schemas"
        if sdir.is_dir():
            for p in sdir.rglob("*.json"):
                d = json.loads(p.read_text())
                if "$id" in d:
                    res.append((d["$id"], Resource.from_contents(d, default_specification=DRAFT202012)))
    reg = Registry().with_resources(res)
    return Draft202012Validator(json.loads((WORKSPACE / "CONAS" / "schemas" / "connector.json").read_text()), registry=reg)


def num(v):
    if isinstance(v, dict):
        for k in ("nominal", "minimum", "maximum"):
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
        return None
    return float(v) if isinstance(v, (int, float)) else None


def drawing_stem(url):
    if not url:
        return None
    if "drawings-pdf.s3.amazonaws.com/" in url:
        return url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return None


def load_rowpitch(path):
    """Return (sullins_by_drawing, wurth_by_series_pitch)."""
    raw = json.load(open(path))
    sullins = {}
    wurth = {}
    for key, e in raw.items():
        mfr = e.get("mfr") or key.split("|", 1)[0]
        rp = e["rowPitch_m"]
        ev = e.get("evidence_url", "")
        stem = drawing_stem(ev)
        if mfr.startswith("Sullins") and stem:
            sullins[stem] = (rp, ev)
        elif mfr.startswith("W") and "rth" in mfr:  # Wuerth
            # staged key is "<mfr>|<series>@<pitch>mm"; the catalog series is the
            # part before '@'.
            series = key.split("|", 1)[1].split("@", 1)[0] if "|" in key else None
            wurth[(series, round(e["pitch_m"], 6))] = (rp, ev, series)
    return sullins, wurth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rowpitch")
    ap.add_argument("--derating")
    ap.add_argument("--insulation")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lsof = subprocess.run(["lsof", "-F", "a", str(DATA)], capture_output=True, text=True)
    if [l for l in lsof.stdout.splitlines() if l.startswith("a") and ("w" in l or "u" in l)]:
        sys.exit("REFUSING: connectors.ndjson is open for writing by another process")

    sullins = wurth = {}
    if args.rowpitch:
        sullins, wurth = load_rowpitch(args.rowpitch)
        print(f"rowPitch staged: {len(sullins)} Sullins drawings, {len(wurth)} Wurth series")
    derating = json.load(open(args.derating)) if args.derating else {}
    if derating:
        print(f"derating staged: {len(derating)} parts")
    insulation = json.load(open(args.insulation)) if args.insulation else {}
    if insulation:
        print(f"insulation staged: {len(insulation)} parts")

    validator = build_validator()
    stats = Counter()
    tmp = DATA.with_suffix(".ndjson.apply_tmp")
    size0 = DATA.stat().st_size

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            stats["lines"] += 1
            try:
                doc = json.loads(raw)
                c = doc["connector"]
            except Exception:
                out.write(raw)
                continue
            mi = c["manufacturerInfo"]
            di = mi.get("datasheetInfo", {})
            m = di.get("mechanical", {}) or {}
            modified = False

            # --- rowPitch (only when absent and lattice is multi-row) ---
            if "rowPitch" not in m and isinstance(m.get("rows"), int) and m["rows"] >= 2:
                hit = None
                mfr = mi.get("name", "")
                if mfr == "Sullins Connector Solutions":
                    st = drawing_stem(mi.get("datasheetUrl"))
                    if st and st in sullins:
                        hit = sullins[st]
                elif mfr.startswith("W") and "rth" in mfr:
                    key = (di.get("part", {}).get("series"), round(num(m.get("pitch")) or 0, 6))
                    if key in wurth:
                        hit = (wurth[key][0], wurth[key][1])
                if hit:
                    rp, ev = hit
                    m["rowPitch"] = rp
                    di.setdefault("provenance", []).append({
                        "source": "derived",
                        "sourceName": "OpenConverters rowPitch fill 2026-08 (#477, per-drawing verified)",
                        "sourceUrl": ev,
                        "retrievedDate": TODAY,
                        "fields": ["mechanical.rowPitch"],
                        "derivation": ("rowPitch read from the series recommended-footprint drawing "
                                       "(vector pad-grid + text), verified against the catalog pitch; "
                                       "dual-row header-class only."),
                    })
                    stats["rowpitch"] += 1
                    modified = True

            # --- derating.currentVsAmbient (only when absent) ---
            pn = di.get("part", {}).get("partNumber") or mi.get("reference")
            if pn and pn in derating and "currentVsAmbient" not in (di.get("derating") or {}):
                e = derating[pn]
                der = di.setdefault("derating", {})
                der["currentVsAmbient"] = e["currentVsAmbient"]
                if e.get("maxTemperatureRise_K") is not None and "maxTemperatureRise" not in der:
                    der["maxTemperatureRise"] = e["maxTemperatureRise_K"]
                di.setdefault("provenance", []).append({
                    "source": "manufacturerDatasheet",
                    "sourceName": f"TE derating chart digitisation 2026-08 (#469, {e.get('basis','')})",
                    "sourceUrl": e.get("sourceUrl"),
                    "retrievedDate": TODAY,
                    "fields": ["derating.currentVsAmbient"],
                })
                stats["derating"] += 1
                modified = True

            # --- electrical.insulationPaths (only when absent; WAGO #468) ---
            if pn and pn in insulation and "insulationPaths" not in (di.get("electrical") or {}):
                e = insulation[pn]
                di.setdefault("electrical", {})["insulationPaths"] = e["insulationPaths"]
                di.setdefault("provenance", []).append({
                    "source": "manufacturerParametric",
                    "sourceName": "WAGO product classifications API (wago.com), IEC 60664-1 chain (#468)",
                    "sourceUrl": e.get("sourceUrl"),
                    "retrievedDate": TODAY,
                    "fields": ["electrical.insulationPaths"],
                })
                stats["insulation"] += 1
                modified = True

            if not modified:
                out.write(raw)
                continue
            errs = list(validator.iter_errors(c))
            if errs:
                tmp.unlink(missing_ok=True)
                sys.exit(f"ABORT: modified record fails CONAS at line {stats['lines']}: {errs[0].message[:300]}")
            out.write((json.dumps(doc, ensure_ascii=False) + "\n").encode())
            stats["modified"] += 1

        src.seek(0, os.SEEK_END)
        if src.tell() > size0:
            src.seek(size0)
            for raw in src:
                out.write(raw)
                stats["tail"] += 1

    n_out = sum(1 for _ in open(tmp, "rb"))
    if n_out != stats["lines"] + stats["tail"]:
        tmp.unlink()
        sys.exit(f"ABORT: line count {n_out} != {stats['lines'] + stats['tail']}")
    print(f"lines={stats['lines']} rowpitch={stats['rowpitch']} derating={stats['derating']} "
          f"insulation={stats['insulation']} modified={stats['modified']} tail={stats['tail']}")
    if args.dry_run:
        tmp.unlink()
        print("dry-run: no swap")
        return
    os.replace(tmp, DATA)
    print(f"swapped into {DATA}")


if __name__ == "__main__":
    main()
