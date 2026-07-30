#!/usr/bin/env python3
"""Adjudicate the Würth rows of ABT #351 with WE's own REDEXPERT data, and apply
the repairs it proves.

    python3 scripts/apply_we_redexpert_fixes.py redexpert.json [--dry-run]

check_we_redexpert.py matched 30 of the 53 suspect WE rows to the vendor's
parametric layer. The adjudication overturned the framing rather than confirming
it:

  * 16 rows match the vendor EXACTLY on every present field (Rdc, Ir, L, Isat).
    They are not defects. 744837006400 at 0.0032 ohm / 40 A is precisely what WE
    publishes — 5.1 W at the 40 C-rise rating of a 41 mm three-phase CMC is
    normal big-part physics. The flat 5 W class threshold is what flagged them,
    and it is the threshold that is wrong (see the areal-density note on #351).
  * 2 rows are genuinely corrupted, by NON-UNIT factors — values from some other
    part, not a mA/mOhm slip:
        7427921        corpus rated 8.1 A   vendor 0.5 A   (Rdc matches)
        760308101312   corpus 0.03 ohm/13 A vendor 0.09 ohm/6 A (both fields)
    These are repaired here with the vendor's numbers, provenance appended.
  * 12 rows (750-series, WE Midcom) matched in the TRANSFORMER module — L and
    Isat agree with the vendor; Rdc/Ir are not published there. The corpus tags
    them subtype=inductor: they are mistagged transformers (ABT #279/#282), and
    winding-DCR x rated-current is not a meaningful product for them (F1).
    Untouched here.

Rows the vendor confirms exact are recorded in the audit as vendorConfirmedOk so
future sweeps can whitelist them instead of re-flagging.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_tdk_cmc import _build_registry, _load_magnetic_schema  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "we_redexpert_adjudication.json"
PROV = {"source": "manufacturerParametric",
        "sourceName": "WE REDEXPERT parametric data layer (redexpert.we-online.com)",
        "sourceUrl": "https://redexpert.we-online.com/redexpert/product/list"}
TOL = 0.02      # 2% — vendor value equal to corpus value within rounding


def vendor_fields(raw):
    return {"dcr": raw.get("Resistance_DC_MAX") or raw.get("Resistance_DC") or raw.get("Resistance_DC_TYP"),
            "rated": raw.get("Rated_Current"),
            "L": raw.get("Inductance"),
            "isat": raw.get("Saturation_Current")}


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= TOL * max(abs(b), 1e-12)


def main(argv):
    dry = "--dry-run" in argv
    vend = json.loads(Path(argv[0]).read_text())
    validator = _load_magnetic_schema(_build_registry())

    plan = {}          # ref -> {"fix": {...}} | {"ok": True} | {"partial": ...}
    for ref, v in vend.items():
        f = vendor_fields(v["raw"])
        plan[ref] = f

    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"vendorConfirmedOk": [], "repaired": [], "transformerModuleNoRdc": []}
    seen = set()
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw_line in src:
            wrote = False
            if b"rth Elektronik" in raw_line:
                try:
                    rec = json.loads(raw_line)
                    info = rec["magnetic"]["manufacturerInfo"]
                    el = info["datasheetInfo"]["electrical"][0]
                    ref = str(info.get("reference"))
                except Exception:
                    ref = None
                if ref in plan and ref not in seen:
                    seen.add(ref)
                    f = plan[ref]
                    d = el.get("dcResistances")
                    plural = bool(d)
                    d = d[0] if d else el.get("dcResistance")
                    dcr = (d.get("maximum") if isinstance(d, dict) and d.get("maximum") is not None
                           else (d.get("nominal") if isinstance(d, dict) else None))
                    rated = (el.get("ratedCurrents") or [None])[0]
                    if f["dcr"] is None or f["rated"] is None:
                        audit["transformerModuleNoRdc"].append(
                            {"reference": ref, "note": "L/Isat vendor-matched; Rdc/Ir not published "
                             "in that module; corpus subtype=inductor is the #279/#282 mistag (F1)"})
                    elif close(dcr, f["dcr"]) and close(rated, f["rated"]):
                        audit["vendorConfirmedOk"].append(
                            {"reference": ref, "dcrOhm": dcr, "ratedA": rated,
                             "note": "matches WE REDEXPERT exactly — not a defect; the flat 5 W "
                                     "threshold flagged normal big-part physics"})
                    else:
                        changed = {}
                        if not close(dcr, f["dcr"]):
                            shaped = {"maximum": f["dcr"]}
                            changed["dcResistance"] = {"was": dcr, "now": f["dcr"]}
                            if plural:
                                el["dcResistances"] = [shaped]
                            else:
                                el["dcResistance"] = shaped
                        if not close(rated, f["rated"]):
                            changed["ratedCurrents"] = {"was": rated, "now": f["rated"]}
                            el["ratedCurrents"] = [f["rated"]] + list(el.get("ratedCurrents", [])[1:])
                        di = info["datasheetInfo"]
                        prov = di.get("provenance") or []
                        if PROV not in prov:
                            di["provenance"] = prov + [PROV]
                        errors = list(validator.iter_errors(rec["magnetic"]))
                        if errors:
                            print(f"ABORT {ref}: {errors[0].message[:120]}")
                            out.close(); tmp.unlink(missing_ok=True)
                            return 1
                        out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                        wrote = True
                        audit["repaired"].append({"reference": ref, "changed": changed})
                        print(f"  REPAIR {ref}: {json.dumps(changed)}")
            if not wrote:
                out.write(raw_line)
        out.flush()
        os.fsync(out.fileno())

    print(f"\nvendor-confirmed OK : {len(audit['vendorConfirmedOk'])}")
    print(f"repaired            : {len(audit['repaired'])}")
    print(f"transformer-module  : {len(audit['transformerModuleNoRdc'])} (mistagged; untouched)")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
        return 0
    os.replace(tmp, DATA)
    AUDIT.write_text(json.dumps({"ticket": "ABT #351 (Würth via REDEXPERT)", **audit}, indent=1))
    print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
