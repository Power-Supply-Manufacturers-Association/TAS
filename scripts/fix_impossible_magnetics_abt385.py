#!/usr/bin/env python3
"""Repair the 4 Blade Runner IMPOSSIBLE magnetics of ABT #385, vendor-direct.

    python3 scripts/fix_impossible_magnetics_abt385.py [--dry-run]

Each row is repaired ONLY from a source that was actually fetched in this session,
and the provenance written says so with a real retrieval date. Three of the four are
fully re-sourced. The fourth (Sumida) has no reachable datasheet, so its electrical
data is NOT rewritten — only what is provable without the vendor is touched.

--------------------------------------------------------------------------------
750342585 (Wurth Elektronik) — MAG_RATED_LE_SAT
--------------------------------------------------------------------------------
Fetched: we-online.com/components/products/datasheet/750342585.pdf (394,645 B).

This is a six-winding FLYBACK TRANSFORMER (turns ratio N1:N2:N3:N4:N5:N6 =
35.5:1:2:2:0.75:2), not an inductor. Its datasheet lists SIX resistances, each a
maximum at 20 °C:

    RDC1 1.63 | RDC2 0.015 | RDC3 0.022 | RDC4 0.13 | RDC5 0.065 | RDC6 0.1  ohm

The corpus stored `dcResistance: {nominal: 1.63, maximum: 0.1}` — RDC1 as the
"nominal" and RDC6 as the "maximum". Those are the maxima of two DIFFERENT
WINDINGS, collapsed into one field as though they bracketed one. It is also why the
record looked impossible from the other side: a max cannot be below its own nominal.

Repaired to `{maximum: 1.63}`, the primary's own documented figure. All six values
go into the audit, but not into the record: this row is TYPED as an inductor, and an
inductor carries one winding resistance. Writing six would mean retagging the part
as the transformer it is, which also needs the turns ratio and per-winding
structure — a reclassification rather than this repair, so it is filed separately.

The rated current is REMOVED. The corpus held 1.2506519590120369 A — a value with
no datasheet counterpart, and the precision alone says it was computed, not read.
The datasheet quotes no rated current for the primary; it gives ISAT = 6.2 mA typ
(N1, |dL/L| < 20 %) and per-output currents IOut1..4 = 2 / 0.14 / 0.14 / 0.1 A,
which are secondary-side figures and not what ratedCurrents means. An unsupported
number is worse than a missing one, so the field goes rather than being invented.
L = 2.5 mH and ISAT = 6.2 mA were already right and are left alone.

--------------------------------------------------------------------------------
RLF7045T-220M1R4-D (TDK) — MAG_DCR_GEOM
--------------------------------------------------------------------------------
Fetched: product.tdk.com detailed-information page for this exact part number.

    Inductance                          22 uH +-20% at 100 kHz
    DC Resistance [Typ.] / [Max.]       82 mOhm / 98.4 mOhm
    Rated Current (Temperature Rise)    1.4 A (20 °C rise)
    Rated Current (L Change) [Max.]     1.5 A (30 % down)

Three corpus errors, one root: the part number was mis-parsed. RLF7045T-220M1R4-D
encodes 220M = 22 uH and 1R4 = 1.4 A, and the corpus stored the CURRENT code as the
inductance (1.4 uH) while dropping the rated current entirely. The DC resistance was
stored as 98.4 OHM — the datasheet's 98.4 mOhm with the milli lost, a factor of
1000, which is what made DCR*size^2/L impossible at 3746 against a threshold of
1000. Saturation current 1.5 A was already correct.

--------------------------------------------------------------------------------
CDRH127NP-391MC (Sumida) — MAG_RATED_LE_SAT
--------------------------------------------------------------------------------
Sumida publishes no reachable datasheet for this part (every products.sumida.com
path tried returns 404; the site search is a JS app that yields no document link).
So the electrical data is NOT rewritten. Two things are nevertheless provable
without the vendor and are fixed:

1. The citation is FALSE. The record's datasheetUrl and provenance point at
   bourns.com/docs/Product-Datasheets/SRR1280.pdf — a BOURNS datasheet, cited as
   the source for a SUMIDA part. A Bourns document cannot be the source for this
   record whatever it contains. The citation is removed rather than left standing.
2. The rated current is IMPOSSIBLE. 88.0 A through the record's own 0.7 ohm is
   5.4 kW in a surface-mount drum inductor. It is nulled, not corrected: the
   record's own description reads "390uH .88A", which makes 0.88 A the obvious
   reading, but a description string is internal evidence, not a vendor
   measurement, and this ticket exists because numbers got invented from
   plausible-looking context. The gap is left for a real re-source.

--------------------------------------------------------------------------------
SRP1265A-R56M (Bourns) — MAG_RATED_LE_SAT
--------------------------------------------------------------------------------
bourns.com refused every request for most of this session (503 to curl, connection
timeout to a browser) and then served the datasheet on a later retry. Fetched:
bourns.com/docs/Product-Datasheets/SRP1265A.pdf (292,802 B), family table row:

    SRP1265A-R56M   0.56 uH  +-20%   DCR typ 1.05 / max 1.2 mOhm   Irms 37  Isat 58

Only the saturation current was wrong, and by exactly 1000x: the corpus held
0.058 A against the datasheet's 58 A, which is what put the rated current 640x
ABOVE saturation and made the row impossible. L, DCR and Irms were already correct.

Worth recording that this row was one retry away from being written off as
unverifiable. A vendor being unreachable is a fact about the moment, not about the
part — which is why an unreachable source must never be logged as evidence against
a record.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validator" / "build-ninja"))
from extract_tdk_cmc import _build_registry, _load_magnetic_schema  # noqa: E402
import tas_validator  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "abt385_impossible_repair_audit.json"
TODAY = "2026-07-31"

WE_PROV = {"source": "manufacturerDatasheet",
           "sourceName": "Wuerth Elektronik datasheet 750342585 (fetched and read)",
           "sourceUrl": "https://www.we-online.com/components/products/datasheet/750342585.pdf",
           "retrievedDate": TODAY}
BOURNS_PROV = {"source": "manufacturerDatasheet",
               "sourceName": "Bourns SRP1265A datasheet family table (fetched and read)",
               "sourceUrl": "https://www.bourns.com/docs/Product-Datasheets/SRP1265A.pdf",
               "retrievedDate": TODAY}
TDK_PROV = {"source": "manufacturerParametric",
            "sourceName": "TDK Product Center detailed information (fetched and read)",
            "sourceUrl": "https://product.tdk.com/en/search/inductor/inductor/smd/info"
                         "?part_no=RLF7045T-220M1R4-D",
            "retrievedDate": TODAY}


def repair(ref, rec):
    """Return (changed_dict, provenance_entry) or None to leave the row alone."""
    di = rec["magnetic"]["manufacturerInfo"]["datasheetInfo"]
    el = di["electrical"][0]
    ch = {}

    if ref == "750342585":
        # All six windings are recorded in the audit, but this record is TYPED as an
        # inductor, and an inductor carries one winding resistance. Writing the
        # primary's own documented maximum is faithful; retagging the part as the
        # six-winding transformer it actually is would also need the turns ratio and
        # per-winding structure, which is a reclassification, not this repair.
        windings = [1.63, 0.015, 0.022, 0.13, 0.065, 0.1]
        ch["dcResistance"] = {"was": el.get("dcResistance") or el.get("dcResistances"),
                              "now": {"maximum": 1.63},
                              "why": "datasheet RDC1 (primary) max 1.63 ohm at 20 C; the corpus "
                                     "had held RDC1 as 'nominal' and RDC6 as 'maximum' — the "
                                     "maxima of two DIFFERENT windings collapsed into one field",
                              "allSixWindingsMaxOhm": windings,
                              "note": "record is typed 'inductor' but the datasheet describes a "
                                      "six-winding flyback transformer (N1:N2:N3:N4:N5:N6 = "
                                      "35.5:1:2:2:0.75:2); retagging is filed separately"}
        el.pop("dcResistances", None)
        el["dcResistance"] = {"maximum": 1.63}
        if el.get("ratedCurrents"):
            ch["ratedCurrents"] = {"was": el["ratedCurrents"], "now": None,
                                   "why": "no rated current is published for the primary; the "
                                          "stored 1.2506519590120369 A was computed, not read"}
            el.pop("ratedCurrents", None)
        return ch, WE_PROV

    if ref == "RLF7045T-220M1R4-D":
        ch["inductance"] = {"was": (el.get("inductance") or {}).get("nominal"), "now": 22e-6,
                            "why": "220M = 22 uH +-20%; the corpus stored the MPN's 1R4 current code"}
        el["inductance"] = {"nominal": 22e-6, "minimum": 17.6e-6, "maximum": 26.4e-6}
        d = el.get("dcResistances")
        ch["dcResistance"] = {"was": (d[0] if d else el.get("dcResistance")), "now": 0.0984,
                              "why": "datasheet max 98.4 mOhm; the corpus had 98.4 ohm"}
        el.pop("dcResistances", None)
        el["dcResistance"] = {"nominal": 0.082, "maximum": 0.0984}
        ch["ratedCurrents"] = {"was": el.get("ratedCurrents"), "now": [1.4],
                               "why": "Rated Current (Temperature Rise) 1.4 A at 20 C rise"}
        el["ratedCurrents"] = [1.4]
        el["saturationCurrentPeak"] = 1.5
        return ch, TDK_PROV

    if ref == "SRP1265A-R56M":
        ch["saturationCurrentPeak"] = {
            "was": el.get("saturationCurrentPeak"), "now": 58.0,
            "why": "datasheet Isat 58 A (L drops 20%); the corpus held 0.058 A, exactly 1000x "
                   "low, which is what put rated 37 A above saturation"}
        el["saturationCurrentPeak"] = 58.0
        el["saturationCurrents"] = [{"percentInductanceDrop": 20, "current": 58.0}]
        return ch, BOURNS_PROV

    if ref == "CDRH127NP-391MC":
        mi = rec["magnetic"]["manufacturerInfo"]
        ch["ratedCurrents"] = {"was": el.get("ratedCurrents"), "now": None,
                               "why": "88.0 A through this record's own 0.7 ohm is 5.4 kW; "
                                      "impossible, and no Sumida source is reachable to replace it"}
        el.pop("ratedCurrents", None)
        ch["citation"] = {"was": mi.get("datasheetUrl"), "now": None,
                          "why": "a Bourns SRR1280 datasheet cited as the source for a Sumida part"}
        mi.pop("datasheetUrl", None)
        di["provenance"] = [{"source": "manual",
                             "sourceName": "UNSOURCED — the previous citation was a Bourns "
                                           "datasheet for a Sumida part and has been removed; "
                                           "this record needs re-sourcing from Sumida"}]
        return ch, None

    return None


def main(argv):
    dry = "--dry-run" in argv
    validator = _load_magnetic_schema(_build_registry())
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"ticket": "ABT #385", "date": TODAY, "repaired": [], "blocked": []}
    targets = {"750342585", "RLF7045T-220M1R4-D", "CDRH127NP-391MC", "SRP1265A-R56M"}
    seen = set()

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            wrote = False
            if any(t.encode() in raw for t in targets):
                try:
                    rec = json.loads(raw)
                    mi = rec["magnetic"]["manufacturerInfo"]
                    ref = str(mi.get("reference"))
                except Exception:
                    ref = None
                if ref in targets and ref not in seen:
                    seen.add(ref)
                    res = repair(ref, rec)
                    if res:
                        ch, prov = res
                        di = mi["datasheetInfo"]
                        if prov:
                            di["provenance"] = [prov]
                        errs = list(validator.iter_errors(rec["magnetic"]))
                        if errs:
                            audit["blocked"].append({"reference": ref,
                                                     "why": f"schema-invalid: {errs[0].message[:90]}"})
                        else:
                            vd = tas_validator.validate(json.dumps(rec))
                            bad = [str(f.code) for f in vd.findings
                                   if str(f.severity).upper() == "IMPOSSIBLE"]
                            if bad:
                                audit["blocked"].append({"reference": ref,
                                                         "why": f"still IMPOSSIBLE: {bad}"})
                            else:
                                out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                                wrote = True
                                audit["repaired"].append({"reference": ref, "changed": ch})
                                print(f"  {ref}")
                                for k, v in ch.items():
                                    print(f"      {k}: {json.dumps(v.get('was'))[:44]} -> "
                                          f"{json.dumps(v.get('now'))[:44]}")
                                    print(f"          {v['why'][:96]}")
            if not wrote:
                out.write(raw)
        out.flush()
        os.fsync(out.fileno())

    print(f"\nrepaired {len(audit['repaired'])}, blocked {len(audit['blocked'])}")
    for b in audit["blocked"]:
        print(f"  BLOCKED {b['reference']}: {b['why'][:100]}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
    else:
        os.replace(tmp, DATA)
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
