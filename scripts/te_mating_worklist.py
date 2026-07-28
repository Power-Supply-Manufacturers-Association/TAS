#!/usr/bin/env python3
"""ABT #249 phase 1: build the TE mating pull work list + the gender lookup table.

Gender-verified mapping (user decision 2026-07-27): a counterpart is only recorded
as relation='mates' when BOTH sides carry matingPolarity and they are complementary
(male<->female) with matching positions and non-conflicting pitch. Everything else
records as 'optionalCompanion' -- we never assert a mate we cannot evidence.

Consequence, and why the work list is ordered: a source part with no matingPolarity
can NEVER reach 'mates', so pulling it only ever yields optionalCompanion edges.
Polarity-bearing parts are emitted first so the highest-value 19,090 are pulled
before the 16,102 that can only produce weak edges.

Outputs (to TAS/staging/te/):
  te_worklist.json  — part numbers in priority order
  te_lookup.json    — {partNumber: {polarity, positions, pitch, family, series}}
"""
import json
import sys
from pathlib import Path

TAS = Path.home() / "PSMA" / "TAS"
SRC = TAS / "data" / "connectors.ndjson"
OUT = TAS / "staging" / "te"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    lookup = {}
    with SRC.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or "TE Connectivity" not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = o.get("connector") or o
            mi = c.get("manufacturerInfo") or {}
            if mi.get("name") != "TE Connectivity":
                continue
            ref = mi.get("reference")
            if not ref:
                continue
            ds = mi.get("datasheetInfo") or {}
            part = ds.get("part") or {}
            mech = ds.get("mechanical") or {}
            lookup[ref] = {
                "polarity": part.get("matingPolarity"),
                "positions": mech.get("positions"),
                "pitch": mech.get("pitch"),
                "family": (ds.get("familyDetails") or {}).get("family"),
                "series": part.get("series") or mi.get("family"),
            }

    with_pol = [k for k, v in lookup.items() if v["polarity"] in ("male", "female")]
    without = [k for k, v in lookup.items() if v["polarity"] not in ("male", "female")]
    worklist = with_pol + without

    (OUT / "te_lookup.json").write_text(json.dumps(lookup))
    (OUT / "te_worklist.json").write_text(json.dumps(worklist))

    print(f"TE parts with a reference : {len(lookup)}")
    print(f"  polarity-bearing (can reach 'mates') : {len(with_pol)}")
    print(f"  no polarity (optionalCompanion only) : {len(without)}")
    print(f"wrote {OUT/'te_worklist.json'} and {OUT/'te_lookup.json'}")

    # a first batch to prove the pipeline end to end
    print("\nfirst 5 of the priority worklist:", worklist[:5])
    return 0


if __name__ == "__main__":
    sys.exit(main())
