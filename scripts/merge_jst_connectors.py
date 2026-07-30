#!/usr/bin/env python3
"""ABT #389: merge the staged JST connectors, splitting on rating ambiguity.

The harvest staged 6,319 CONAS-valid JST records. 2,280 of them carry a rating that
JST publishes PER SERIES rather than per part — e.g. "12.25 A (3 circuits, AWG #14) |
2.5 A (40 circuits, AWG #28)" — with nothing on the page saying which figure belongs to
an individual part number. The harvester took the lowest published figure and recorded
the alternatives in provenance.

WHY THAT IS NOT GOOD ENOUGH TO MERGE: CONAS defines the field as "MAXIMUM continuous
current per contact at reference ambient", not a worst case. For a 3-circuit part the
right answer is 12.25 A; storing 2.5 A is wrong by 5x, and wrong in the direction that
silently disqualifies a good part in selection and cross-reference — the exact surface
this catalogue feeds. Omitting the field instead is not an option either: CONAS makes
ratedCurrentPerContact REQUIRED, so a record without it cannot validate.

So the split (user decision, 2026-07-31):
  - unambiguous records            -> data/connectors.ndjson
  - per-series-rating records      -> data/connectors.quarantine_jst_ambiguous_current.ndjson
                                      with quarantineReason, and registered as a Seeker
                                      source so the circuit count can be resolved from
                                      the series PDFs later.
Nothing is discarded and nothing is guessed; the ambiguous parts keep their published
alternatives in provenance and come back when the evidence does.

  merge_jst_connectors.py            # dry run
  merge_jst_connectors.py --apply
"""
import argparse
import json
import sys
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
STAGED = TAS / "staging" / "jst" / "records.ndjson"
LIVE = TAS / "data" / "connectors.ndjson"
QUAR = TAS / "data" / "connectors.quarantine_jst_ambiguous_current.ndjson"
REASON = "jst-per-series-current-rating"

# The harvester marks an affected record with an extra provenance entry quoting the
# published alternatives. That marker is the split key — not a re-derivation of the
# ambiguity, so the two passes cannot disagree.
AMBIGUOUS_HINT = ("alternativ", "lowest", "published")


def is_ambiguous(rec):
    di = ((rec.get("connector") or {}).get("manufacturerInfo") or {}).get("datasheetInfo") or {}
    for p in di.get("provenance") or []:
        sn = (p.get("sourceName") or "").lower()
        if any(h in sn for h in AMBIGUOUS_HINT):
            return True
    return False


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    psma = TAS.parent
    by = {}
    for repo in ("PEAS", "CONAS"):
        for p in (psma / repo / "schemas").rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by[s["$id"]] = s
    reg = Registry().with_resources(
        [(s["$id"], Resource(contents=s, specification=DRAFT202012)) for s in by.values()])
    return Draft202012Validator(
        json.loads((psma / "CONAS" / "schemas" / "connector.json").read_text()), registry=reg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    gate = BladeGate("connector")
    v = build_validator()

    # Existing references, so a re-run cannot double-insert.
    existing = set()
    with LIVE.open(encoding="utf-8") as fh:
        for line in fh:
            if '"reference"' not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            ref = ((o.get("connector") or {}).get("manufacturerInfo") or {}).get("reference")
            if ref:
                existing.add(ref)
    print(f"live catalogue: {len(existing)} referenced connectors")

    clean, ambiguous, dupes, bad, blocked = [], [], 0, [], 0
    with STAGED.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            c = rec.get("connector") or {}
            ref = (c.get("manufacturerInfo") or {}).get("reference")
            if ref in existing:
                dupes += 1
                continue
            errs = sorted(v.iter_errors(c), key=lambda e: e.path)
            if errs:
                bad.append(f"{ref}: {errs[0].message[:120]}")
                continue
            ok, why = gate.check(c)
            if not ok:
                blocked += 1
                continue
            (ambiguous if is_ambiguous(rec) else clean).append(rec)

    print(f"clean -> connectors.ndjson      : {len(clean)}")
    print(f"ambiguous -> Seeker quarantine  : {len(ambiguous)}")
    print(f"already present (skipped)       : {dupes}")
    print(f"schema-invalid                  : {len(bad)}")
    print(f"Blade Runner IMPOSSIBLE         : {blocked}")
    print(gate.summary())
    for b in bad[:5]:
        print("   ", b)
    if not a.apply:
        print("\nDRY RUN — pass --apply to write")
        return 0

    # Append, never rewrite: connectors.ndjson is huge and other processes append to it.
    with LIVE.open("a", encoding="utf-8") as fh:
        for rec in clean:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with QUAR.open("a", encoding="utf-8") as fh:
        for rec in ambiguous:
            rec["quarantineReason"] = REASON
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"appended {len(clean)} to {LIVE.name} and {len(ambiguous)} to {QUAR.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
