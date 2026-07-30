#!/usr/bin/env python3
"""ABT #390: remove ESR values that are clamps, not measurements.

Blade Runner's GEN_COHORT_CEILING screen (validator/src/corpus.cpp) finds values pinned
at a cohort maximum, shared by many parts, far above the cohort median. On capacitors it
identifies 99 records across five ceramic cohorts:

  Murata  GRM class-2  24 parts @ esr=200 Ω   (1000x cohort median)
  TDK     C   class-2  24 parts @ esr=200 Ω    (500x)
  Samsung CL  class-2  24 parts @ esr=200 Ω    (293x)
  YAGEO   CC  class-2  12 parts @ esr=160 Ω  (10000x)
  KEMET   C   class-2  15 parts @ esr=120 Ω  (14118x)

Four independent manufacturers do not converge on exactly 200.0000 Ω for two dozen parts
each. These are our own pipeline's ceiling, not vendor data — and unlike a wrong number,
a placeholder is indistinguishable from a measurement downstream, so it silently biases
any chart, filter or cross-reference that reads the field.

WHY REMOVE RATHER THAN CORRECT: we do not know the true ESR for these parts. The DF
formula gives ~398 Ω for the 10 pF Murata parts, but DF is specified at a different
frequency than the ESR figure, so that is a different quantity, not a correction.
Inventing a replacement would be exactly the fabrication this catalogue forbids. An
absent field is honest; a placeholder is not.

esrFrequency goes with it: a measurement condition for a measurement we no longer claim
is noise, and leaving it would make the record look partially specified.

Nothing is deleted silently — every removed value is written to
staging/esr_clamp_audit.json with its cohort statistics, so the decision is reversible
and auditable.

  fix_esr_clamps.py            # dry run: report what would change
  fix_esr_clamps.py --apply    # rewrite data/capacitors.ndjson atomically
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "capacitors.ndjson"
AUDIT = TAS / "staging" / "esr_clamp_audit.json"


def clamped_indices():
    """{record index: finding message} for every part Blade Runner calls a ceiling clamp.

    Keyed by INDEX, not by manufacturerInfo.reference: that field is empty on most of
    this catalogue's rows, so keying by it silently matched 1 record out of 99.
    validate_corpus() indexes into the list it was given, so the same parse order that
    built the list is what maps a finding back to its line.
    """
    sys.path.insert(0, str(TAS / "validator" / "build-ninja"))
    import tas_validator as tv

    records = [json.loads(line) for line in SRC.open(encoding="utf-8") if line.strip()]
    out = {}
    for f in tv.validate_corpus(records):
        code = f["code"] if isinstance(f, dict) else f.code
        if code != "GEN_COHORT_CEILING":
            continue
        idx = f["index"] if isinstance(f, dict) else f.index
        msg = f["message"] if isinstance(f, dict) else f.message
        if "esr=" in msg:              # this repair covers the ESR field only
            out[idx] = msg
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    flagged = clamped_indices()
    print(f"Blade Runner flags {len(flagged)} ESR ceiling clamps")
    if not flagged:
        return 0

    removed, out_lines = [], []
    idx = -1
    with SRC.open(encoding="utf-8") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            idx += 1                      # must advance on EVERY parsed line, exactly as
            if idx not in flagged:        # the list handed to validate_corpus() did
                out_lines.append(s)
                continue
            obj = json.loads(s)
            c = obj.get("capacitor") or obj
            mi = c.get("manufacturerInfo") or {}
            ref = mi.get("reference")
            ds = mi.get("datasheetInfo") or {}
            e = ds.get("electrical") or {}
            if "esr" not in e:
                out_lines.append(s)
                continue
            removed.append({
                "reference": ref,
                "partNumber": (ds.get("part") or {}).get("partNumber"),
                "manufacturer": mi.get("name"),
                "esr": e.get("esr"),
                "esrFrequency": e.get("esrFrequency"),
                "finding": flagged[idx],
            })
            e.pop("esr", None)
            e.pop("esrFrequency", None)
            ds.setdefault("provenance", []).append({
                # "manual" is the schema enum value for a human/agent decision. There is no
                # "audit" source and PEAS/utils.json provenance is a CLOSED enum —
                # inventing a value made all 99 records schema-invalid on the first pass.
                "source": "manual",
                "sourceName": ("ABT #390: esr removed — Blade Runner GEN_COHORT_CEILING "
                               "identified it as a pipeline clamp, not a measurement"),
                "retrievedDate": time.strftime("%Y-%m-%d"),
            })
            out_lines.append(json.dumps(obj, ensure_ascii=False))

    print(f"records whose esr would be removed: {len(removed)}")
    for r in removed[:5]:
        print(f"  {r['manufacturer']:12s} {str(r['partNumber'])[:26]:26s} "
              f"esr={r['esr']} f={r['esrFrequency']}")
    if not a.apply:
        print("\nDRY RUN — pass --apply to write")
        return 0

    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(removed, indent=1, ensure_ascii=False))
    tmp = SRC.with_suffix(".ndjson.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for line in out_lines:
            fh.write(line + "\n")
    os.replace(tmp, SRC)
    print(f"applied; {len(removed)} values recorded in {AUDIT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
