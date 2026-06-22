#!/usr/bin/env python3
"""Promote ONLY the verified-unique rescued parts into production.

A record qualifies iff it is a reinstate-candidate AND:
  - reference is non-empty, contains a digit, no comma/slash (orderable, not a series),
  - that reference occurs exactly once across all reinstate-candidates (uniquely orderable),
  - passes the completeness gate and tas_validator physics gate,
  - its reference is not already present in the target production file.
Qualifying records are appended to the matching production file (annotations
stripped) and removed from their quarantine file. Everything else is left as-is.

    python3 promote_clean.py            # DRY RUN
    python3 promote_clean.py --apply
"""
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "build"))
sys.path.insert(0, str(HERE))
import tas_validator  # noqa: E402
import triage_quarantine as T  # noqa: E402

DATA = T.DATA
APPLY = "--apply" in sys.argv
PROD_FILE = {("magnetic", None): "magnetics", ("capacitor", None): "capacitors",
             ("resistor", None): "resistors", ("semiconductor", "mosfet"): "mosfets",
             ("semiconductor", "diode"): "diodes", ("semiconductor", "igbt"): "igbts"}


def is_candidate(rec):
    return isinstance(rec.get("_triage"), dict) and \
        rec["_triage"].get("disposition") == "reinstate-candidate"


def value_specific(ref):
    return bool(ref) and "," not in ref and "/" not in ref and bool(re.search(r"\d", ref)) \
        and len(ref) >= 4


def main():
    qfiles = sorted(glob.glob(str(DATA / "*quarantine*.ndjson")))

    # production MPNs per discriminator (avoid reintroducing a duplicate)
    prod_mpn = defaultdict(set)
    for f in set(PROD_FILE.values()):
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
                prod_mpn[d].add(m)

    # reference multiplicity across ALL reinstate-candidates
    refcount = Counter()
    for qf in qfiles:
        if os.path.getsize(qf) == 0:
            continue
        for line in open(qf):
            line = line.strip()
            if not line or line.startswith("version https://git-lfs"):
                break
            rec = json.loads(line)
            if not is_candidate(rec):
                continue
            d, sub, _ = T.component(rec)
            _, mi = T.datasheet_info(rec, d, sub)
            refcount[(d, mi.get("reference") or "")] += 1

    promote = defaultdict(list)        # prod file -> [clean json, ...]
    remove_from = defaultdict(list)    # quarantine file -> kept lines
    skipped = Counter()
    promoted_refs = []

    for qf in qfiles:
        base = os.path.basename(qf)
        if os.path.getsize(qf) == 0:
            continue
        kept = []
        for line in open(qf):
            s = line.strip()
            if not s or s.startswith("version https://git-lfs"):
                kept.append(s)
                continue
            rec = json.loads(s)
            d, sub, _ = T.component(rec)
            _, mi = T.datasheet_info(rec, d, sub) if d else ({}, {})
            ref = mi.get("reference") or ""
            ok = (is_candidate(rec) and value_specific(ref)
                  and refcount[(d, ref)] == 1
                  and T.complete_enough(d, sub, rec))
            if ok:
                try:
                    ok = tas_validator.validate({d: rec[d]}).valid
                except Exception:
                    ok = False
            if ok and ref in prod_mpn.get(d, set()):
                skipped["already-in-production"] += 1
                ok = False
            if ok:
                target = PROD_FILE.get((d, sub))
                promote[target].append(json.dumps({d: rec[d]}))
                promoted_refs.append((target, ref))
                prod_mpn[d].add(ref)  # guard against intra-run dup refs
            else:
                kept.append(s)
        remove_from[base] = kept

    print(f"{'APPLYING' if APPLY else 'DRY RUN'} — promote verified-unique parts\n")
    total = 0
    for pf, recs in sorted(promote.items()):
        cur = sum(1 for _ in open(DATA / f"{pf}.ndjson"))
        total += len(recs)
        print(f"  {pf:11s} +{len(recs):<4d}  (production {cur} -> {cur + len(recs)})")
    print(f"  TOTAL promoted: {total}")
    if skipped:
        print(f"  skipped: {dict(skipped)}")

    if not APPLY:
        print("\n(dry run — re-run with --apply to write changes)")
        return

    for pf, recs in promote.items():
        with open(DATA / f"{pf}.ndjson", "a") as f:
            for r in recs:
                f.write(r + "\n")
    for base, kept in remove_from.items():
        # only rewrite quarantine files we actually pulled from
        with open(DATA / base, "w") as f:
            for l in kept:
                if l:
                    f.write(l + "\n")
    print("\napplied.")


if __name__ == "__main__":
    main()
