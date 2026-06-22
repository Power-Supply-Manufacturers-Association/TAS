#!/usr/bin/env python3
"""Verify the 'rescued' (reinstate-candidate) records and try to extract MORE
genuine parts from the post-cleanup quarantine.

Checks per candidate:
  - physics: tas_validator.validate(...).valid is True
  - complete: type's key electrical fields present
  - real orderable MPN: non-empty reference, has a digit, not a series/family id
  - unique: not an exact content-duplicate, and reference not already in production
Then scans the 'repair'/'keep' leftovers for additional records that meet the
same bar (extract-more)."""
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


def real_mpn(ref):
    """A reference that looks like an orderable part, not a series/family."""
    if not ref or "," in ref or "/" in ref:
        return False
    return bool(re.search(r"\d", ref)) and len(ref) >= 4


def prod_index():
    by_mpn = defaultdict(set)
    content = set()
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
                by_mpn[d].add(m)
            content.add(T.content_hash(rec, d))
    return by_mpn, content


def assess(rec):
    """Return (genuine, reasons) for a single record."""
    d, sub, _ = T.component(rec)
    if d is None:
        return False, ["no-discriminator"]
    _, mi = T.datasheet_info(rec, d, sub)
    ref = mi.get("reference") or ""
    reasons = []
    if not real_mpn(ref):
        reasons.append("not-real-mpn")
    if T.is_series_stub(d, sub, rec):
        reasons.append("series-stub")
    if not T.complete_enough(d, sub, rec):
        reasons.append("incomplete")
    try:
        if not tas_validator.validate({d: rec[d]}).valid:
            reasons.append("physics-invalid")
    except Exception:
        reasons.append("malformed")
    return (not reasons), reasons


def main():
    prod_by_mpn, prod_content = prod_index()

    cand_total = 0
    cand_valid = cand_complete = 0
    seen_content = set()
    seen_ref = defaultdict(Counter)            # disc -> ref -> count (repeat detection)
    bad = Counter()
    clean = []                                 # genuine, unique, not in production
    dup_content = dup_prodmpn = 0

    # also scan non-candidate leftovers (repair/keep) for extract-more
    extra_clean = []

    for qf in glob.glob(str(DATA / "*quarantine*.ndjson")):
        if os.path.getsize(qf) == 0:
            continue
        for line in open(qf):
            line = line.strip()
            if not line or line.startswith("version https://git-lfs"):
                break
            rec = json.loads(line)
            is_cand = isinstance(rec.get("_triage"), dict) and \
                rec["_triage"].get("disposition") == "reinstate-candidate"
            d, sub, _ = T.component(rec)
            genuine, reasons = assess(rec)
            h = T.content_hash(rec, d)
            mpn = T.get_mpn(rec, d, sub)

            if is_cand:
                cand_total += 1
                if "physics-invalid" not in reasons and "malformed" not in reasons:
                    cand_valid += 1
                if "incomplete" not in reasons:
                    cand_complete += 1
                for r in reasons:
                    bad[r] += 1
                if genuine:
                    if h in prod_content or (mpn and mpn in prod_by_mpn.get(d, set())):
                        dup_prodmpn += 1
                    elif h in seen_content:
                        dup_content += 1
                    else:
                        seen_content.add(h)
                        seen_ref[d][mpn] += 1
                        clean.append((d, sub, mpn))
            else:
                # extract-more: leftover that is genuine + unique
                if genuine and h not in prod_content and h not in seen_content \
                        and not (mpn and mpn in prod_by_mpn.get(d, set())):
                    seen_content.add(h)
                    extra_clean.append((d, sub, mpn))

    print("=== reinstate-candidates: validation & uniqueness ===")
    print(f"  total candidates           : {cand_total}")
    print(f"  pass physics validation    : {cand_valid}")
    print(f"  complete (key fields)      : {cand_complete}")
    print(f"  rejected reasons           : {dict(bad)}")
    print(f"  dropped as prod-duplicate  : {dup_prodmpn}")
    print(f"  dropped as content-dup     : {dup_content}")
    print(f"  -> CLEAN rescuable (genuine, unique, valid, not in prod): {len(clean)}")
    by_type = Counter(d if not sub else sub for d, sub, _ in clean)
    print(f"     by type: {dict(by_type)}")

    # repeated references among the clean set (same orderable MPN twice = suspicious)
    repeats = {d: {r: c for r, c in refs.items() if r and c > 1} for d, refs in seen_ref.items()}
    repeats = {d: v for d, v in repeats.items() if v}
    print(f"  repeated references in clean set: "
          f"{sum(sum(v.values()) for v in repeats.values())} rows across "
          f"{sum(len(v) for v in repeats.values())} refs")
    for d, v in repeats.items():
        ex = list(v.items())[:3]
        print(f"     {d}: {len(v)} refs repeat, e.g. {ex}")

    print("\n=== extract-more: genuine parts found in repair/keep leftovers ===")
    em = Counter(d if not sub else sub for d, sub, _ in extra_clean)
    print(f"  additional genuine+unique records: {len(extra_clean)}  by type: {dict(em)}")


if __name__ == "__main__":
    main()
