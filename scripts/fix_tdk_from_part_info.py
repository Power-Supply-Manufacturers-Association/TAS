#!/usr/bin/env python3
"""Repair the last TDK rows of ABT #351 from product.tdk.com per-part
DETAILED INFORMATION pages (captured by pull_tdk_part_info.mjs).

    python3 scripts/fix_tdk_from_part_info.py tdk_info.json [--dry-run]

These are the rows no parametric grid could reach. The info page carries the
full spec table per part, and two of its distinctions decide the repair:

  Rated Current (Temperature Rise)  -> the THERMAL rating; this is what
                                       ratedCurrents means and what DCR*I^2 tests.
  Rated Current (L Change)          -> the SATURATION current. Where a part
                                       publishes ONLY this (the B82559 ERU flat-
                                       wire family), the corpus storing it as
                                       ratedCurrents is the F2 mis-classification
                                       that made these rows look impossible. It is
                                       moved to saturationCurrentPeak and the
                                       unsupported thermal claim is dropped —
                                       TDK publishes no thermal rating for them.

TWO VENDOR-SIDE UNIT ERRORS ARE HANDLED, NOT COPIED:

  B82559A5472A033 lists DC Resistance Typ = 1.2 mOhm and Max = 1.2 Ohm — the same
  number with the unit 1000x apart. A max cannot be 1000x its typ, and 1.2 ohm at
  the part's own 81 A would be 7.9 kW, so the Max field's unit is the error. The
  Typ value is used and the substitution is recorded per row. This is the same
  class as Vanguard's mislabelled current column: the vendor's web layer is not
  automatically right, and physics is the tiebreak.

  The corpus's own inductance is NOT trusted as an anchor here — for
  VLBUC1206011085NMF4 it reads 85 nH against the page's 80 nH, which is why the
  grid pass could not anchor it. Identity comes from the exact part number the
  info page was fetched for, so no anchor is needed.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validator" / "build-ninja"))
from extract_tdk_cmc import _build_registry, _load_magnetic_schema  # noqa: E402
import tas_validator  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "tdk_part_info_repair_audit.json"
PROV = {"source": "manufacturerParametric",
        "sourceName": "TDK Product Center per-part detailed information",
        "sourceUrl": "https://product.tdk.com/en/search/"}

SCALE = {"": 1.0, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "n": 1e-9, "p": 1e-12,
         "k": 1e3, "M": 1e6, "G": 1e9}


def qty(cell: str, unit_char: str):
    """First '<number><prefix><unit>' in the cell, in SI. '138μΩ' -> 1.38e-4."""
    if not cell:
        return None
    m = re.search(rf"([\d.]+)\s*([mµμunpkMG]?){unit_char}", cell)
    if not m:
        m = re.search(r"([\d.]+)", cell)
        return float(m.group(1)) if m else None
    return float(m.group(1)) * SCALE.get(m.group(2), 1.0)


def main(argv):
    dry = "--dry-run" in argv
    specs = json.loads(Path(argv[0]).read_text())
    validator = _load_magnetic_schema(_build_registry())
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"repaired": [], "skipped": []}
    seen = set()

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            wrote = False
            if b"TDK" in raw:
                try:
                    rec = json.loads(raw)
                    info = rec["magnetic"]["manufacturerInfo"]
                    di = info["datasheetInfo"]
                    el = di["electrical"][0]
                    ref = str(info.get("reference"))
                except Exception:
                    ref = None
                if ref in specs and ref not in seen:
                    seen.add(ref)
                    f = specs[ref]["fields"]
                    why = None
                    changed = {}

                    v_l = qty(f.get("Inductance", ""), "H")
                    dcr_typ = qty(f.get("DC Resistance [Typ.]", ""), "Ω")
                    dcr_max = qty(f.get("DC Resistance [Max.]", ""), "Ω")
                    thermal = qty(f.get("Rated Current (Temperature Rise) [Typ.]", ""), "A")
                    lchange = qty(f.get("Rated Current (L Change) [Typ.]", ""), "A")

                    # A max 100x its typ is a vendor unit slip on the max field.
                    v_dcr, dcr_note = dcr_max, None
                    if dcr_typ and dcr_max and dcr_max > 100 * dcr_typ:
                        v_dcr = dcr_typ
                        dcr_note = (f"page lists Max {dcr_max} ohm against Typ {dcr_typ} ohm — a max "
                                    f"cannot be 1000x its typ; the Max field's unit is wrong, Typ used")
                    if v_dcr is None:
                        v_dcr = dcr_typ
                    if v_dcr is None:
                        why = "info page carries no DC resistance"

                    if why is None:
                        d = el.get("dcResistances")
                        plural = bool(d)
                        d = d[0] if d else el.get("dcResistance")
                        old_dcr = (d.get("maximum") if isinstance(d, dict) and d.get("maximum") is not None
                                   else (d.get("nominal") if isinstance(d, dict) else None))
                        old_rated = (el.get("ratedCurrents") or [None])[0]
                        old_l = (el.get("inductance") or {}).get("nominal")

                        if old_dcr is None or abs(old_dcr - v_dcr) > 0.02 * v_dcr:
                            changed["dcResistance"] = {"was": old_dcr, "now": v_dcr}
                            if dcr_note:
                                changed["dcResistance"]["vendorUnitError"] = dcr_note
                            shaped = {"maximum": v_dcr}
                            if plural:
                                el["dcResistances"] = [shaped]
                            else:
                                el["dcResistance"] = shaped

                        if v_l and (old_l is None or abs(old_l - v_l) > 0.02 * v_l):
                            changed["inductance"] = {"was": old_l, "now": v_l}
                            el["inductance"] = {"nominal": v_l}

                        if thermal:
                            if old_rated is None or abs(old_rated - thermal) > 0.02 * thermal:
                                changed["ratedCurrents"] = {"was": old_rated, "now": thermal}
                                el["ratedCurrents"] = [thermal] + list(el.get("ratedCurrents", [])[1:])
                            if lchange:
                                el["saturationCurrentPeak"] = lchange
                                changed["saturationCurrentPeak"] = {"now": lchange}
                        elif lchange:
                            # Only a saturation figure is published: the corpus's
                            # ratedCurrents was that value mis-filed (F2).
                            changed["ratedCurrents"] = {
                                "was": old_rated, "now": None,
                                "why": "TDK publishes only an L-Change (saturation) current for this "
                                       "part; the corpus stored it as a thermal rating"}
                            el.pop("ratedCurrents", None)
                            el["saturationCurrentPeak"] = lchange
                            changed["saturationCurrentPeak"] = {"now": lchange}

                        if not changed:
                            why = "info page agrees with the corpus"
                        else:
                            prov = di.get("provenance") or []
                            entry = dict(PROV, sourceUrl=specs[ref]["url"])
                            if entry not in prov:
                                di["provenance"] = prov + [entry]
                            if list(validator.iter_errors(rec["magnetic"])):
                                why = "schema-invalid after repair"
                            else:
                                vd = tas_validator.validate(json.dumps(rec))
                                bad = [str(x.code) for x in vd.findings
                                       if str(x.severity).endswith("Impossible")]
                                if bad:
                                    why = f"Blade Runner IMPOSSIBLE after repair: {bad}"
                    if why is None:
                        out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                        wrote = True
                        audit["repaired"].append({"reference": ref, "changed": changed,
                                                  "source": specs[ref]["url"]})
                        print(f"  {ref:22} {json.dumps(changed)[:120]}")
                    else:
                        audit["skipped"].append({"reference": ref, "why": why})
            if not wrote:
                out.write(raw)
        out.flush()
        os.fsync(out.fileno())

    print(f"\nrepaired {len(audit['repaired'])}, skipped {len(audit['skipped'])}")
    for s in audit["skipped"]:
        print(f"  SKIP {s['reference']}: {s['why'][:90]}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
    else:
        os.replace(tmp, DATA)
        AUDIT.write_text(json.dumps({"ticket": "ABT #351 (TDK per-part info)", **audit}, indent=1))
        print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
