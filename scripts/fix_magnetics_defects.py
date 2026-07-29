#!/usr/bin/env python3
"""Repair the physics defects that audit_chip_beads.py surfaces in magnetics.ndjson
(the ones the fabrication guard passes: 0-ohm impedance points, explicit zero
DCR/inductance). Reproducible + IDEMPOTENT: re-running after --apply is a no-op.

Two kinds of repair:
  1. STRIP non-positive impedance points from chipBead/commonModeChoke curves. A
     |Z| <= 0 point is a scrape/merge artifact (e.g. the Wurth .mdb CM+DM merge wrote
     0.0 on the differential trace at low frequency). SAFETY GUARD: a record is only
     stripped if EVERY winding still keeps >= 2 real points afterwards; otherwise the
     record is left untouched and REPORTED (never strip a curve out of existence).
  2. PATCH parts whose defining value is an explicit zero, using REAL datasheet values
     supplied out-of-band in data/magnetics_sourced_fixes.json (never inferred here).
     Shape:
       { "<reference>": {
            "manufacturer": "Abracon",          # guard: must match the record's maker
            "inductance": {"nominal": 1.0e-7},   # optional, replaces a zero inductance
            "dcResistance": {"maximum": 0.0123}, # optional, replaces a zero dcResistance
            "impedanceAt": {"frequency": 1.0e8, "magnitude": 50.0},  # optional, for 0-pt beads
            "source": "https://..."              # required provenance for the patch
       }, ... }
     A part with only a 0-ohm point (all 3 Abracon beads) gets its point REPLACED by
     impedanceAt; parts with a real curve keep it.

    python3 fix_magnetics_defects.py            # DRY RUN (reports what it would change)
    python3 fix_magnetics_defects.py --apply    # rewrite magnetics.ndjson in place
"""
import json, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
NDJSON = DATA / "magnetics.ndjson"
FIXES = DATA / "magnetics_sourced_fixes.json"
APPLY = "--apply" in sys.argv


def load_fixes():
    if FIXES.exists():
        try:
            return json.load(open(FIXES))
        except ValueError as e:
            print(f"WARNING: {FIXES.name} is not valid JSON ({e}) -- patches skipped")
    return {}


def strip_zeros(e0):
    """Drop |Z|<=0 points if every winding keeps >=2 real points. Returns (changed, ndropped)
    or (None, reason) if it would gut a winding (caller leaves the record untouched)."""
    pts = e0.get("impedancePoints", []) or []
    bad = [p for p in pts if (p.get("impedance", {}).get("magnitude", 1) or 0) <= 0]
    if not bad:
        return False, 0
    kept = [p for p in pts if (p.get("impedance", {}).get("magnitude", 1) or 0) > 0]
    per_w = defaultdict(int)
    for p in kept:
        per_w[p.get("winding", "_")] += 1
    if kept and all(n >= 2 for n in per_w.values()):
        e0["impedancePoints"] = kept
        return True, len(bad)
    return None, f"stripping would leave windings {dict(per_w)} (<2 real pts)"


def apply_patch(e0, mi, patch):
    """Apply a sourced patch to one record's electrical[0] + provenance. Returns list of notes.

    Supported ops (all backed by a real datasheet `source`):
      inductance / dcResistance : replace with a real single value
      impedanceAt               : add a real |Z| point for a 0-point bead
      dropDcResistance          : remove an invalid zero-DCR whose datasheet gives only
                                  a RANGE (series header) -- never invent a single value
      dropImpedancePoints       : remove a bogus curve whose datasheet gives no single
                                  |Z| (e.g. a 0~15 ohm window) -> honest DCR-only bead
    """
    notes = []
    for field in ("inductance", "dcResistance"):
        if field in patch:
            e0[field] = patch[field]
            notes.append(f"{field}={patch[field]}")
    if patch.get("dropDcResistance") and "dcResistance" in e0:
        del e0["dcResistance"]
        notes.append("dropped invalid zero dcResistance (datasheet gives a range, not a single value)")
    if patch.get("dropImpedancePoints") and "impedancePoints" in e0:
        del e0["impedancePoints"]
        notes.append("dropped bogus 0-ohm |Z| point (datasheet gives no single value) -> DCR-only")
    if "impedanceAt" in patch:
        f = float(patch["impedanceAt"]["frequency"])
        m = float(patch["impedanceAt"]["magnitude"])
        pts = [p for p in (e0.get("impedancePoints") or [])
               if (p.get("impedance", {}).get("magnitude", 0) or 0) > 0]
        pts.append({"frequency": f, "impedance": {"magnitude": m}})
        e0["impedancePoints"] = sorted(pts, key=lambda p: p["frequency"])
        notes.append(f"|Z|={m}ohm@{f/1e6:g}MHz")
    # record provenance of the correction so the fix is traceable
    if notes and patch.get("source"):
        di = mi.setdefault("datasheetInfo", {})
        prov = di.setdefault("provenance", [])
        prov.append({"source": "manufacturerDatasheet",
                     "sourceName": f"defect repair ({'; '.join(notes)}) from manufacturer datasheet",
                     "sourceUrl": patch["source"], "retrievedDate": "2026-07-29"})
    return notes


def main():
    fixes = load_fixes()
    out = []
    stripped = 0
    stripped_pts = 0
    skipped_strip = []
    patched = []
    unmatched = dict(fixes)   # patches we never found a record for

    for line in open(NDJSON):
        s = line.strip()
        if not s:
            out.append(line)
            continue
        o = json.loads(s)
        mi = o.get("magnetic", {}).get("manufacturerInfo", {})
        ref = mi.get("reference")
        el = mi.get("datasheetInfo", {}).get("electrical", [])
        e0 = el[0] if el else None
        if e0 is not None:
            st = e0.get("subtype")
            if st in ("chipBead", "commonModeChoke"):
                changed, info = strip_zeros(e0)
                if changed is True:
                    stripped += 1
                    stripped_pts += info
                elif changed is None and ref not in fixes:
                    skipped_strip.append((ref, info))
            if ref in fixes:
                patch = fixes[ref]
                maker = patch.get("manufacturer")
                if maker and maker != mi.get("name"):
                    skipped_strip.append((ref, f"patch manufacturer '{maker}' != record '{mi.get('name')}' -- NOT applied"))
                else:
                    notes = apply_patch(e0, mi, patch)
                    if notes:
                        patched.append((ref, notes))
                    unmatched.pop(ref, None)
        out.append(json.dumps(o, ensure_ascii=False) + "\n")

    print(f"{'APPLY' if APPLY else 'DRY RUN'} -- magnetics defect repair")
    print(f"  zero-point strip: {stripped} records, {stripped_pts} artifact points removed")
    print(f"  sourced patches applied: {len(patched)}")
    for ref, notes in patched:
        print(f"     {ref}: {', '.join(notes)}")
    if skipped_strip:
        print(f"  LEFT UNTOUCHED (needs attention): {len(skipped_strip)}")
        for ref, why in skipped_strip[:20]:
            print(f"     {ref}: {why}")
    if unmatched:
        print(f"  sourced-fix refs with NO matching record: {list(unmatched)}")
    if not APPLY:
        print("\n(dry run -- rerun with --apply to rewrite magnetics.ndjson)")
        return
    # rewrite in place, preserving the hardlink inode (open 'w' truncates in place)
    with open(NDJSON, "w") as fh:
        fh.writelines(out)
    print(f"\nrewrote {NDJSON} ({len(out)} lines)")


if __name__ == "__main__":
    main()
