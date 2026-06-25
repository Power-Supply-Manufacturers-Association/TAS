#!/usr/bin/env python3
"""
Final provenance reconciliation pass.

For every catalog record still lacking provenance after backfill_provenance.py:
  - If it carries a datasheetUrl on a real (non-placeholder) host, the URL IS
    the trace -> stamp provenance (mapped source if known, else a generic
    manufacturerDatasheet entry pointing at that URL). KEEP it.
  - Otherwise it is sourceless agent-generated / early-bulk-build data with NO
    verifiable origin -> MOVE it to <type>.quarantine_unverified.ndjson and drop
    it from the main catalog.

This implements the decision to quarantine the ~12.8k unverifiable no-URL
records (origin commits 48f3c54 / 334fb83 / ddede18 / a8d8aaa / e14249c) rather
than assert a manufacturer source we cannot back up.

Run with --dry-run first.
"""
import json, argparse
from collections import Counter
import backfill_provenance as B  # reuse PATHS, DOMAIN_MAP, MANUF_MAP, host_of, SYNTHETIC_HOSTS, get_di

DATA = B.DATA


def resolve(manufacturer, url):
    """Return a provenance entry dict to KEEP the record, or None to quarantine."""
    h = B.host_of(url)
    if h and not any(s in h for s in B.SYNTHETIC_HOSTS):
        for key, (src, name, date) in B.DOMAIN_MAP:
            if key in h:
                e = {"source": src, "sourceName": name, "sourceUrl": url}
                if date:
                    e["retrievedDate"] = date
                return e
        # real URL on an unmapped host: still traceable via the URL itself
        return {"source": "manufacturerDatasheet",
                "sourceName": f"datasheet ({h})", "sourceUrl": url}
    # no usable URL -> only keep if the manufacturer maps to a known real import
    if manufacturer in B.MANUF_MAP:
        src, name, date = B.MANUF_MAP[manufacturer]
        e = {"source": src, "sourceName": name}
        if url:
            e["sourceUrl"] = url
        if date:
            e["retrievedDate"] = date
        return e
    return None  # -> quarantine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    grand = Counter()
    qbreak = Counter()
    for f, path in B.PATHS.items():
        fn = f"{DATA}/{f}.ndjson"
        try:
            lines = open(fn, encoding="utf-8").read().splitlines()
        except FileNotFoundError:
            continue
        keep, quar = [], []
        n_keep_new = 0
        for line in lines:
            if not line.strip():
                keep.append(line); continue
            rec = json.loads(line)
            mi, di = B.get_di(rec, path)
            if di is None or "provenance" in di:
                keep.append(line); continue
            entry = resolve(mi.get("name", ""), mi.get("datasheetUrl"))
            if entry is None:
                quar.append(line)
                qbreak[f"{f}:{mi.get('name','?')}"] += 1
            else:
                di["provenance"] = [entry]
                keep.append(json.dumps(rec, ensure_ascii=False))
                n_keep_new += 1
        grand[f"{f}_stampkeep"] = n_keep_new
        grand[f"{f}_quarantine"] = len(quar)
        print(f"{f:12} stamp+keep={n_keep_new:6d}  quarantine={len(quar):6d}")
        if not args.dry_run and quar:
            with open(fn, "w", encoding="utf-8") as fh:
                fh.write("\n".join(keep) + "\n")
            with open(f"{DATA}/{f}.quarantine_unverified.ndjson", "a", encoding="utf-8") as fh:
                fh.write("\n".join(quar) + "\n")

    print("\n=== TOTALS ===")
    print("  stamp+keep:", sum(v for k, v in grand.items() if k.endswith("_stampkeep")))
    print("  quarantined:", sum(v for k, v in grand.items() if k.endswith("_quarantine")))
    print("\n=== quarantine breakdown (top 25) ===")
    for k, v in qbreak.most_common(25):
        print(f"  {v:6d}  {k}")
    if args.dry_run:
        print("\nDRY RUN — nothing written.")


if __name__ == "__main__":
    main()
