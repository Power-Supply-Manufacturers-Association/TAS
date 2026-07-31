#!/usr/bin/env python3
"""Resolve Sullins' bare "Nylon" insulator from the part drawings.

Sullins' parametric grid says only "Nylon" for 22,490 parts. That is not a material:
PA6, PA46, PA66 and PA9T are different polymers with different temperature ratings, and
the registry distinguishes them, so vendor_connector_materials.py refuses to guess and
leaves the housing empty.

The drawings say it exactly. Every Sullins row carries a drawing URL, and the drawing
prints a materials note:

    1. INSULATOR MATERIAL: NYLON 9T, UL 94V-0, BLACK
    2. CONTACT MATERIAL: BRASS

and 84 drawings cover all 22,490 of those parts (195 cover the whole catalogue), because
one drawing serves a whole series. So this is 84 fetches, not 22,490 — deterministic text
off the manufacturer's own drawing, no model and no inference.

WHAT IS NOT RESOLVED. Some drawings say "INSULATOR MATERIAL: SEE PART NUMBER CODING" —
the polymer is a field of the MPN and the drawing does not state which. Those are counted
and left alone rather than decoded by pattern-matching part numbers. A drawing whose note
names a polymer the registry does not define is likewise reported, not approximated.

  sullins_drawing_materials.py fetch    # -> staging/sullins/drawings/*.txt (cached)
  sullins_drawing_materials.py map      # what the drawings say, per material
  sullins_drawing_materials.py write [--apply]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
STAGE = TAS / "staging" / "sullins"
DRAWINGS = STAGE / "drawings"
MAPFILE = STAGE / "drawing_materials.json"
AUDIT = STAGE / "drawing_materials_audit.json"
LIVE = TAS / "data" / "connectors.ndjson"
ROWS = Path("/tmp/sullins/rows.jsonl")
MFR = "Sullins Connector Solutions"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from we_connectors_materials import HOUSING  # noqa: E402

# Exactly as the drawings write it. Same discipline as every other material map here:
# a string that does not name one polymer is not mapped.
DRAWING_HOUSING = {
    "nylon 9t": "pa9t",
    "nylon 46": "pa46",
    "nylon 66": "pa66-nylon",
    "nylon 6t": "ppa-pa6t-gf",
    **HOUSING,
}
# The note is free text on a drawing; take only the material token before the
# UL rating / colour / glass-fill qualifiers that follow it.
NOTE = re.compile(r"INSULATOR\s+MATERIAL\s*:?\s*([^\n,]+)", re.I)


def rows():
    if not ROWS.exists():
        sys.exit(f"missing {ROWS} — re-run scripts/sullins_fetch.py")
    with ROWS.open(encoding="utf-8") as fh:
        for ln in fh:
            if ln.strip():
                yield json.loads(ln)


def drawing_id(url):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", url.rsplit("/", 1)[-1])


def cmd_fetch(a):
    DRAWINGS.mkdir(parents=True, exist_ok=True)
    urls = sorted({r["drawing"] for r in rows() if r.get("drawing")})
    todo = [u for u in urls
            if not (DRAWINGS / (drawing_id(u) + ".txt")).exists()]
    print(f"{len(urls)} distinct drawings; {len(todo)} to fetch")

    def one(url):
        did = drawing_id(url)
        pdf = DRAWINGS / did
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                pdf.write_bytes(r.read())
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {url}: {str(e)[:80]}", file=sys.stderr)
            return
        try:
            txt = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True,
                                 timeout=120).stdout.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL pdftotext {did}: {str(e)[:80]}", file=sys.stderr)
            return
        (DRAWINGS / (did + ".txt")).write_text(txt, encoding="utf-8")
        pdf.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, todo))
    print("fetch complete")
    return 0


def read_notes():
    """{drawing_url: raw insulator-material note}"""
    out = {}
    for url in sorted({r["drawing"] for r in rows() if r.get("drawing")}):
        p = DRAWINGS / (drawing_id(url) + ".txt")
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        # Skip the revision block: it records what the material USED TO BE
        # ("UPGRADE INSULATOR MATERIAL NYLON 9T (WAS NYLON 6T)"), and reading that
        # as the current note would put the superseded polymer on the part.
        best = None
        for m in NOTE.finditer(txt):
            head = txt[max(0, m.start() - 60):m.start()].upper()
            if "WAS " in head or "UPGRADE" in head or "UPDATE" in head:
                continue
            best = m.group(1).strip()
            break
        if best:
            out[url] = best
    return out


def resolve(note):
    s = re.sub(r"\s+", " ", note).strip().lower()
    s = s.split(" ul ")[0].split(", ul")[0].strip().strip(".")
    for cand in (s, s.split(",")[0].strip()):
        if cand in DRAWING_HOUSING:
            return DRAWING_HOUSING[cand]
    return None


def cmd_map(a):
    notes = read_notes()
    per_part = Counter()
    unresolved = Counter()
    resolved = Counter()
    grid_by_url = defaultdict(Counter)
    for r in rows():
        u = r.get("drawing")
        if u:
            grid_by_url[u][r["attrs"].get("Insulator Material") or "(none)"] += 1
    for u, note in sorted(notes.items()):
        ref = resolve(note)
        n = sum(grid_by_url[u].values())
        if ref:
            resolved[ref] += n
        else:
            unresolved[re.sub(r"\s+", " ", note).strip()[:60]] += n
        per_part[u] = n

    print(f"{len(notes)} drawings carry an insulator note")
    print("\n-- resolves to registry ids (parts) --")
    for k, v in resolved.most_common():
        print(f"  {v:7d}  {k}")
    print("\n-- notes that name no single registry polymer (NOT mapped) --")
    for k, v in unresolved.most_common(15):
        print(f"  {v:7d}  {k}")
    MAPFILE.write_text(json.dumps(
        {u: {"note": n, "ref": resolve(n)} for u, n in sorted(notes.items())},
        ensure_ascii=False, indent=1))
    print(f"\nwrote {MAPFILE}")
    return 0


def cmd_write(a):
    from blade_gate import BladeGate
    from merge_staged_connectors import build_validator
    gate = BladeGate("connector")
    validator = build_validator()

    dmap = json.loads(MAPFILE.read_text())
    by_part = {}
    for r in rows():
        u = r.get("drawing")
        e = dmap.get(u or "")
        # Only the parts the grid left unresolved. A part whose grid cell already
        # named its polymer keeps that: the grid is the vendor's structured field,
        # and overwriting it from free text would be a downgrade in provenance.
        if e and e.get("ref") and (r["attrs"].get("Insulator Material") or "") == "Nylon":
            by_part[r["partNumber"]] = e["ref"]
    print(f"{len(by_part)} parts gain a housing from their drawing")

    stats = Counter()
    rejected = []
    tmp = LIVE.with_suffix(".ndjson.draw_tmp")
    with LIVE.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as out:
        for raw in src:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            stats["total"] += 1
            if MFR not in s:
                out.write(s + "\n")
                continue
            obj = json.loads(s)
            c = obj.get("connector") or obj
            mi = c.get("manufacturerInfo") or {}
            ref = mi.get("reference")
            if mi.get("name") != MFR or ref not in by_part:
                out.write(s + "\n")
                continue
            ds = mi.setdefault("datasheetInfo", {})
            mat = dict(ds.get("material") or {})
            if mat.get("housingMaterialRef"):
                stats["already_had_housing"] += 1
                out.write(s + "\n")
                continue
            mat["housingMaterialRef"] = by_part[ref]
            ds["material"] = mat
            errs = sorted(validator.iter_errors(c), key=lambda e: e.path)
            if errs:
                stats["rejected_invalid"] += 1
                if len(rejected) < 5:
                    rejected.append(f"{ref}: {errs[0].message[:150]}")
                out.write(s + "\n")
                continue
            ok, why = gate.check(c)
            if not ok:
                stats["rejected_blade"] += 1
                if len(rejected) < 5:
                    rejected.append(f"{ref}: BLADE {why}")
                out.write(s + "\n")
                continue
            stats["patched"] += 1
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")

    if a.apply:
        os.replace(tmp, LIVE)
    else:
        tmp.unlink()
    print("APPLIED" if a.apply else "DRY RUN — nothing written")
    for k in ("total", "patched", "already_had_housing", "rejected_invalid",
              "rejected_blade"):
        print(f"  {k:22} {stats[k]}")
    if rejected:
        for r in rejected:
            print(f"     {r}")
    AUDIT.write_text(json.dumps({"stats": dict(stats), "rejected": rejected}, indent=1))
    if not a.apply:
        print("Re-run with --apply to write.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    sub.add_parser("map")
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return {"fetch": cmd_fetch, "map": cmd_map, "write": cmd_write}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
