#!/usr/bin/env python3
"""Quarantine cleanup — the SAFE, production-untouching pass.

  - drop  duplicate_internal + duplicate_production         (redundant rows)
  - archive  synthetic + series_stub  -> discarded/         (junk / placeholders)
  - retain  keep / zero_ohm / repair / reinstate            (deduped, in quarantine)
       reinstate candidates get a "_triage":{"disposition":"reinstate-candidate"}
       marker so a later, datasheet-verified pass can promote them to production.

Deliberately does NOT promote anything into the production catalog: the
reinstate bucket is contaminated with empty/placeholder references that cannot
be separated from real parts without per-part datasheet verification, and we do
not inject unverified records into the authoritative catalog. Intentional
archives (mosfets.quarantine_discontinued, *_zero_r, *_mapi_stubs) are left
untouched. Everything is split-file + git-LFS, so fully reversible.

    python3 execute_cleanup.py            # DRY RUN
    python3 execute_cleanup.py --apply
"""
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "build"))
sys.path.insert(0, str(HERE))
import tas_validator  # noqa: E402
import triage_quarantine as T  # noqa: E402

DATA = T.DATA
DISCARD = DATA / "discarded"
APPLY = "--apply" in sys.argv
DATE = "2026-06-19"
# Intentional archives — not defect quarantine; leave exactly as-is.
LEAVE_ALONE = ("discontinued", "_zero_r", "mapi_stubs")


def disposition(rec, base, seen, prod_mpns):
    d, sub, _ = T.component(rec)
    mpn = T.get_mpn(rec, d, sub)
    dsurl = T.get_dsurl(rec, d, sub)
    h = T.content_hash(rec, d)
    if d and mpn and mpn in prod_mpns.get(d, set()):
        return "duplicate_production"
    if h in seen:
        return "duplicate_internal"
    seen.add(h)
    if "synthetic" in base or (not mpn and not dsurl):
        return "synthetic"
    if T.is_series_stub(d, sub, rec):
        return "series_stub"
    if T.is_zero_ohm(d, sub, rec):
        return "zero_ohm"
    if d is None:
        return "repair" if (mpn and dsurl) else "keep"
    try:
        ok = tas_validator.validate({d: rec[d]}).valid
    except Exception:
        ok = False
    if T.complete_enough(d, sub, rec) and ok:
        return "reinstate"
    return "repair" if (mpn and dsurl) else "keep"


def main():
    prod_mpns = defaultdict(set)
    for f in T.PROD:
        p = DATA / f"{f}.ndjson"
        if not p.exists():
            continue
        for line in open(p):
            if not line.strip() or line.startswith("version https://git-lfs"):
                break
            rec = json.loads(line)
            d, sub, _ = T.component(rec)
            m = T.get_mpn(rec, d, sub)
            if m:
                prod_mpns[d].add(m)

    seen = set()
    keep_by_file = defaultdict(list)
    discard = defaultdict(list)
    stats = defaultdict(Counter)

    for qf in sorted(glob.glob(str(DATA / "*quarantine*.ndjson"))):
        base = os.path.basename(qf)
        if os.path.getsize(qf) == 0 or any(t in base for t in LEAVE_ALONE):
            continue
        for line in open(qf):
            line = line.strip()
            if not line or line.startswith("version https://git-lfs"):
                break
            rec = json.loads(line)
            disp = disposition(rec, base, seen, prod_mpns)
            stats[base][disp] += 1
            if disp in ("synthetic", "series_stub"):
                # archive name ends in .ndjson so git-LFS tracks it
                stem = base[:-len(".ndjson")] if base.endswith(".ndjson") else base
                discard[f"{stem}.{disp}.ndjson"].append(line)
            elif disp == "reinstate":
                rec["_triage"] = {"disposition": "reinstate-candidate", "date": DATE}
                keep_by_file[base].append(json.dumps(rec))
            elif disp in ("keep", "zero_ohm", "repair"):
                keep_by_file[base].append(line)
            # duplicate_* -> dropped

    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — quarantine cleanup (production untouched)\n")
    print("Quarantine files: before -> after (kept = keep/zero_ohm/repair/reinstate, deduped)")
    tot_b = tot_a = 0
    for base in sorted(stats):
        before = sum(stats[base].values())
        after = len(keep_by_file.get(base, []))
        rc = stats[base].get("reinstate", 0)
        tot_b += before
        tot_a += after
        print(f"  {base:<48} {before:>6} -> {after:<6}" + (f"  ({rc} reinstate-candidates)" if rc else ""))
    print(f"  {'TOTAL':<48} {tot_b:>6} -> {tot_a}")
    print("\nArchived to discarded/:")
    da = 0
    for name, recs in sorted(discard.items()):
        da += len(recs)
        print(f"  {name:<60} {len(recs)}")
    print(f"  {'TOTAL archived':<60} {da}")
    print(f"\nLeft untouched (intentional archives): {', '.join(LEAVE_ALONE)}")

    if not APPLY:
        print("\n(dry run — re-run with --apply to write changes)")
        return

    DISCARD.mkdir(exist_ok=True)
    for base, lines in keep_by_file.items():
        with open(DATA / base, "w") as f:
            for l in lines:
                f.write(l + "\n")
    for qf in glob.glob(str(DATA / "*quarantine*.ndjson")):
        base = os.path.basename(qf)
        if base in stats and base not in keep_by_file:
            open(qf, "w").close()  # everything dropped/archived
    for name, lines in discard.items():
        with open(DISCARD / name, "w") as f:
            for l in lines:
                f.write(l + "\n")
    print("\napplied.")


if __name__ == "__main__":
    main()
