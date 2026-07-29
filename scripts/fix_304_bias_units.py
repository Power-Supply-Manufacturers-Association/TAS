#!/usr/bin/env python3
"""ABT #304 REMEDIATION: strip DC-bias curves written with the wrong unit scale.

WHAT WENT WRONG: murata_bias_harvest.py hardcoded microfarads (x1e-6). That is
right for Murata's MLCC module but WRONG for its safety-certified (RDE/RCE/DE/
RHE...) and feedthrough (NFM) lines, which return PICOfarads. The response states
the prefix all along (y_unit='F', y_subunit='p'|'u'|...); it was not read. Result:
4,415 of 5,994 written curves are 1e6 too large -- e.g. a 10 pF RDE stored as
10 uF. The harvester now reads y_subunit and refuses unknown prefixes.

WHAT THIS DOES: removes capacitanceBiasPoints from any record whose curve
disagrees with the part's own nominal capacitance by more than 2x, and clears
those parts from the harvest checkpoint so they are re-fetched with correct
units. Deliberately does NOT rescale in place: the corrupt values are re-derivable
from the source, and re-fetching is authoritative where arithmetic on bad data is
a guess. Curves that agree with nominal (including the 1,311 that predate this
campaign) are left untouched.

Usage: fix_304_bias_units.py [--apply]
"""
import argparse
import json
import os
import sys
from pathlib import Path

TAS = Path.home() / "PSMA" / "TAS"
SRC = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "murata"
DONE = STAGE / "bias_done.json"
OUT = STAGE / "bias.jsonl"

LO, HI = 0.5, 2.0        # curve[0] / nominal must land in here to be believable


def implausible(bp, nominal):
    if not bp or not isinstance(nominal, (int, float)) or nominal <= 0:
        return False
    c0 = (bp[0] or {}).get("capacitance")
    if not isinstance(c0, (int, float)) or c0 <= 0:
        return True
    return not (LO <= c0 / nominal <= HI)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    stripped, kept, ratios = 0, 0, []
    victims = set()
    out_lines = []
    with SRC.open(encoding="utf-8") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            if "capacitanceBiasPoints" not in s:
                out_lines.append(s)
                continue
            obj = json.loads(s)
            c = obj.get("capacitor") or obj
            mi = c.get("manufacturerInfo") or {}
            ds = mi.get("datasheetInfo") or {}
            e = ds.get("electrical") or {}
            bp = e.get("capacitanceBiasPoints")
            cap = e.get("capacitance")
            nominal = cap.get("nominal") if isinstance(cap, dict) else cap
            if not bp or not implausible(bp, nominal):
                kept += 1 if bp else 0
                out_lines.append(s)
                continue
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            ratios.append((pn, nominal, bp[0].get("capacitance")))
            victims.add(pn)
            del e["capacitanceBiasPoints"]
            # drop the provenance entry this campaign added, so the record does not
            # claim a source for data no longer present
            prov = ds.get("provenance")
            if isinstance(prov, list):
                ds["provenance"] = [p for p in prov if not (
                    isinstance(p, dict) and "SimSurfing characteristics" in str(p.get("sourceName")))]
            stripped += 1
            out_lines.append(json.dumps(obj, ensure_ascii=False))

    print(f"curves kept (plausible)   : {kept}")
    print(f"curves STRIPPED (bad unit): {stripped}")
    for pn, nom, c0 in ratios[:4]:
        print(f"   {pn}: nominal={nom:.3e} F vs curve0={c0:.3e} F  (x{c0/nom:.3g})")
    if not a.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    tmp = SRC.with_suffix(".ndjson.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for line in out_lines:
            fh.write(line + "\n")
    os.replace(tmp, SRC)
    print(f"atomically replaced {SRC}")

    # clear the affected parts from the checkpoint so they are re-fetched
    if DONE.exists():
        done = set(json.loads(DONE.read_text()))
        DONE.write_text(json.dumps(sorted(done - victims)))
        print(f"checkpoint: {len(victims)} parts released for re-fetch")
    if OUT.exists():
        rows = [l for l in OUT.read_text(encoding="utf-8").splitlines()
                if l.strip() and json.loads(l).get("partNumber") not in victims]
        OUT.write_text("\n".join(rows) + ("\n" if rows else ""))
        print(f"staging: {len(rows)} good rows retained")
    return 0


if __name__ == "__main__":
    sys.exit(main())
