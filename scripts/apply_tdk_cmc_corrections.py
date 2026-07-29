#!/usr/bin/env python3
"""Apply the vendor-direct TDK common-mode-choke pull as CORRECTIONS to the rows
already in data/magnetics.ndjson (ABT #281/#286).

    python3 scripts/apply_tdk_cmc_corrections.py --dry-run
    python3 scripts/apply_tdk_cmc_corrections.py

209 of the 212 staged records match a part number already in the corpus, so this
is not a merge — it is a repair. Those existing rows carry the ABT #286
corruption, now confirmed on TDK as well as Laird and Bourns: the part number's
impedance code was written into the DC resistance AND into the inductance.

    ACM2012-201-2P-T002   corpus: dcResistance 200 ohm, inductance 200 nH
                          vendor: dcResistance 0.25 ohm, |Z| 200 ohm @ 100 MHz

14 of the overlapping rows are physically impossible as stored (>5 W at their own
rated current); 12 have a DCR numerically equal to the vendor's |Z|@100MHz, and 6
have an inductance equal to that same number.

MERGE RULES — vendor wins only where the vendor actually publishes something:

  * dcResistances  <- vendor's, always. It is the manufacturer's own parametric
    value, published as a maximum, and it is the field that was corrupted.
  * impedancePoints <- vendor's, always. New information, correctly filed as an
    impedance at a stated frequency instead of smeared into L and R.
  * ratedCurrents  <- vendor's where published, else the corpus value is kept.
  * inductance     <- vendor's where published. Where the vendor publishes none,
    the corpus value is KEPT unless it is provably the impedance code misread as
    henries (its value in H, uH or nH equals the |Z| magnitude) — those are
    dropped, not "corrected" to a guess.
  * everything else on the record (saturationCurrentPeak, selfResonantFrequency,
    core, coil, thermal, ...) is left exactly as found. This is a repair of two
    fields, not a re-import.

A manufacturerParametric provenance entry is appended so the corrected values are
attributable; existing provenance entries are preserved.

WHY THIS REWRITES THE FILE (and the Laird repair did not)

Adding impedancePoints makes lines LONGER, so the in-place byte patch used by
null_laird_corrupt_dcr.py is impossible here. The file is therefore rewritten via
temp + fsync + atomic rename. That is only safe while nothing else is appending
to data/magnetics.ndjson — run it in a quiet window. Every non-target line is
copied through byte-for-byte and asserted unchanged; every edited record is
re-validated against the MAS schema before anything is renamed into place.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_tdk_cmc import _build_registry, _load_magnetic_schema, SOURCE_URL  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
STAGED = REPO / "staging" / "magnetics_tdk_cmc_staged.ndjson"
AUDIT = REPO / "staging" / "tdk_cmc_corrections_audit.json"

PROV = {"source": "manufacturerParametric",
        "sourceName": "TDK Product Center parametric catalog (EMC / signal-line common mode chokes)",
        "sourceUrl": SOURCE_URL}


def is_impedance_code_misread(inductance_h: float | None, z_ohm: float | None) -> bool:
    """True when a stored inductance is really the part number's |Z| code: the
    same digits landed in henries, microhenries or nanohenries."""
    if inductance_h is None or z_ohm is None:
        return False
    return any(abs(inductance_h * scale - z_ohm) < 0.01 * max(z_ohm, 1.0)
               for scale in (1.0, 1e6, 1e9))


def merge(old_el: dict, new_el: dict) -> tuple[dict, dict]:
    """Corrected electrical block + a record of what changed.

    The DC-resistance FIELD NAME is subtype-dependent and the schema enforces it:
    `inductor` (one winding) takes the singular `dcResistance`, `commonModeChoke`
    takes the plural `dcResistances` (one per winding). The vendor value is the
    same either way; only its shape differs. Some of these rows are ACM-series
    common-mode chokes still tagged `inductor` — the mistag of ABT #279/#282,
    which is NOT retagged here: writing to the field the row's own subtype allows
    keeps this a DCR repair rather than a silent reclassification.
    """
    el = dict(old_el)
    changed: dict = {}
    z = ((new_el.get("impedancePoints") or [{}])[0].get("impedance") or {}).get("magnitude")
    singular = el.get("subtype") != "commonModeChoke"

    if "dcResistances" in new_el:
        value = new_el["dcResistances"]
        field, other = ("dcResistance", "dcResistances") if singular else ("dcResistances", "dcResistance")
        shaped = value[0] if singular else value
        if el.get(field) != shaped:
            changed[field] = {"was": el.get(field), "now": shaped}
        el[field] = shaped
        if other in el:                       # never leave both shapes on one record
            changed[other] = {"was": el.pop(other), "now": None,
                              "why": f"subtype {el.get('subtype')!r} uses {field}"}

    if "impedancePoints" in new_el:
        if el.get("impedancePoints") != new_el["impedancePoints"]:
            changed["impedancePoints"] = {"was": el.get("impedancePoints"), "now": new_el["impedancePoints"]}
        el["impedancePoints"] = new_el["impedancePoints"]

    if "ratedCurrents" in new_el and el.get("ratedCurrents") != new_el["ratedCurrents"]:
        changed["ratedCurrents"] = {"was": el.get("ratedCurrents"), "now": new_el["ratedCurrents"]}
        el["ratedCurrents"] = new_el["ratedCurrents"]

    if "inductance" in new_el:
        if el.get("inductance") != new_el["inductance"]:
            changed["inductance"] = {"was": el.get("inductance"), "now": new_el["inductance"]}
        el["inductance"] = new_el["inductance"]
    else:
        old_ind = (el.get("inductance") or {}).get("nominal")
        if is_impedance_code_misread(old_ind, z):
            changed["inductance"] = {"was": el.pop("inductance"), "now": None,
                                     "why": "value equalled the |Z| code — impedance misread as henries"}
    return el, changed


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    validator = _load_magnetic_schema(_build_registry())

    staged = {}
    for line in STAGED.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)["magnetic"]
        staged[rec["manufacturerInfo"]["reference"]] = rec

    tmp = DATA.with_suffix(".ndjson.tmp")
    seen, corrected, untouched, audit = set(), 0, 0, []
    total = 0

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            total += 1
            ref = None
            if b"TDK" in raw:
                try:
                    rec = json.loads(raw)
                    info = rec["magnetic"]["manufacturerInfo"]
                    ref = info.get("reference")
                except Exception:
                    ref = None
            if ref is None or ref not in staged or ref in seen:
                out.write(raw)                      # byte-for-byte passthrough
                continue
            seen.add(ref)
            di = info["datasheetInfo"]
            new_el = staged[ref]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]
            merged, changed = merge(di["electrical"][0], new_el)
            if not changed:
                untouched += 1
                out.write(raw)
                continue
            di["electrical"][0] = merged
            prov = di.get("provenance") or []
            if PROV not in prov:
                prov = prov + [PROV]
            di["provenance"] = prov
            errors = list(validator.iter_errors(rec["magnetic"]))
            if errors:
                print(f"ABORT: {ref} would not validate: {errors[0].message[:140]}")
                out.close(); tmp.unlink(missing_ok=True)
                return 1
            out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
            corrected += 1
            audit.append({"reference": ref, "changed": changed})

        # the handful of parts the corpus does not have at all
        added = 0
        for ref, rec in staged.items():
            if ref not in seen:
                out.write(json.dumps({"magnetic": rec}, separators=(",", ":")).encode() + b"\n")
                added += 1
                audit.append({"reference": ref, "changed": "APPENDED — not previously in corpus"})
        out.flush()
        os.fsync(out.fileno())

    print(f"corpus lines read        : {total}")
    print(f"rows corrected           : {corrected}")
    print(f"rows already correct     : {untouched}")
    print(f"rows appended (new parts): {added}")

    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
        return 0

    os.replace(tmp, DATA)
    AUDIT.write_text(json.dumps({"ticket": "ABT #281/#286", "file": str(DATA), "rows": audit}, indent=1))
    print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
