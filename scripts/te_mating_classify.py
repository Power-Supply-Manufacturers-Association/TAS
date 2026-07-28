#!/usr/bin/env python3
"""ABT #249 phase 2: classify TE related-product edges into CONAS matesWith relations.

GENDER-VERIFIED mapping (user decision 2026-07-27). relation='mates' is asserted ONLY
when the pair is evidenced:
  * both sides carry matingPolarity, and they are complementary (male <-> female), AND
  * positions match on both sides, AND
  * pitch does not conflict (equal, or absent on either side).
Everything else -- unknown polarity, same polarity, mismatched positions, conflicting
pitch, counterpart not in our catalogue -- records as 'optionalCompanion'. We never
assert a mate we cannot evidence (the standard that quarantined #281 and stopped the
#287 divide-by-100 patch).

TE's own typing is coarser than the CONAS enum: it offers only 'All Compatible Parts'
(relation_type 0) and 'Associated Parts' (relation_type 3), and the label lives in the
relationTypeFilterCount facet, not on the product -- so 'Associated' edges are pulled
with r=3 and always map to optionalCompanion.

Usage: te_mating_classify.py <pulled-batch.json> [more.json ...]
"""
import json
import sys
from collections import Counter
from pathlib import Path

TAS = Path.home() / "PSMA" / "TAS"
LOOKUP = TAS / "staging" / "te" / "te_lookup.json"
OUT = TAS / "staging" / "te" / "te_mating_classified.json"

OPPOSITE = {"male": "female", "female": "male"}


def classify(src, dst, lookup):
    """Return (relation, why)."""
    s, d = lookup.get(src), lookup.get(dst)
    if d is None:
        return "optionalCompanion", "counterpart not in catalogue"
    if s is None:
        return "optionalCompanion", "source not in catalogue"
    sp, dp = s.get("polarity"), d.get("polarity")
    if sp not in OPPOSITE or dp not in OPPOSITE:
        return "optionalCompanion", "polarity unknown on one side"
    if OPPOSITE[sp] != dp:
        return "optionalCompanion", f"same polarity ({sp}/{dp})"
    spos, dpos = s.get("positions"), d.get("positions")
    if spos is None or dpos is None:
        return "optionalCompanion", "positions unknown on one side"
    if spos != dpos:
        return "optionalCompanion", f"positions differ ({spos}/{dpos})"
    spi, dpi = s.get("pitch"), d.get("pitch")
    if spi is not None and dpi is not None and abs(spi - dpi) > 1e-9:
        return "optionalCompanion", f"pitch conflict ({spi}/{dpi})"
    return "mates", f"complementary {sp}/{dp}, {spos} positions"


def main(paths):
    lookup = json.loads(LOOKUP.read_text())
    edges = {}
    reasons = Counter()
    rel_counts = Counter()
    n_src = 0

    for p in paths:
        path = Path(p)
        if path.suffix == ".jsonl":
            recs_iter = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        else:
            d = json.loads(path.read_text())
            recs_iter = d["results"] if isinstance(d, dict) else d
        for rec in recs_iter:
            src = rec["pn"]
            n_src += 1
            for key, forced in (("compatible", None), ("associated", "optionalCompanion")):
                for c in rec.get(key) or []:
                    dst = c.get("pn")
                    if not dst or dst == src:
                        continue
                    if forced:
                        rel, why = forced, "TE 'Associated Parts' (relation_type 3)"
                    else:
                        rel, why = classify(src, dst, lookup)
                    prev = edges.get((src, dst))
                    # 'mates' beats 'optionalCompanion' if both paths produced an edge
                    if prev and prev["relation"] == "mates":
                        continue
                    edges[(src, dst)] = {"relation": rel, "why": why}

    for e in edges.values():
        rel_counts[e["relation"]] += 1
        reasons[e["why"].split(" (")[0]] += 1

    print(f"source parts processed : {n_src}")
    print(f"distinct edges         : {len(edges)}")
    print("\n--- relation verdicts ---")
    for k, v in rel_counts.most_common():
        print(f"  {v:6d}  {k}")
    print("\n--- why ---")
    for k, v in reasons.most_common(12):
        print(f"  {v:6d}  {k}")

    out = {}
    for (src, dst), e in edges.items():
        out.setdefault(src, []).append({"series": dst, "relation": e["relation"],
                                        "_why": e["why"]})
    OUT.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {OUT}  ({len(out)} source parts with edges)")

    mates = [(s, m) for s, ms in out.items() for m in ms if m["relation"] == "mates"]
    print("\n--- sample 'mates' verdicts (the evidenced ones) ---")
    for s, m in mates[:8]:
        print(f"  {s:22} -> {m['series']:22} {m['_why']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or [str(TAS.parent / "te_mates_batch01_fixed.json")]))
