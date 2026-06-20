#!/usr/bin/env python3
"""Phase 0 — read-only triage of every TAS quarantine file.

Classifies each quarantined record into a disposition and writes a manifest
(validator/quarantine_triage_manifest.ndjson) plus a summary table. Makes NO
changes to any data file.

Dispositions:
  duplicate_production  MPN already present complete in a production file
  duplicate_internal    exact content-duplicate of an earlier quarantined record
  synthetic             from a *_synthetic file, or has no real MPN/datasheet
  reinstate             passes BOTH the completeness gate and the physics gate
  zero_ohm              resistor with R==0 (real 0R jumper; needs a validator policy)
  repair                real MPN + datasheet URL but fails the completeness gate
  keep                  fails, unsalvageable, not synthetic
"""
import json
import math
import glob
import hashlib
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "build"))
import tas_validator  # noqa: E402

DATA = HERE.parent / "data"
PROD = ["magnetics", "capacitors", "resistors", "mosfets", "diodes", "igbts"]
DISC_OF = {"magnetics": "magnetic", "capacitors": "capacitor", "resistors": "resistor",
           "mosfets": "semiconductor", "diodes": "semiconductor", "igbts": "semiconductor"}
ANNOT = {"_validatorQuarantine", "quarantineInfo", "distributorsInfo", "substitutesInfo"}


def component(rec):
    """Return (disc, sub, comp_obj) or (None, None, None)."""
    for d in ("magnetic", "capacitor", "resistor", "semiconductor"):
        if d in rec:
            o = rec[d]
            sub = None
            if d == "semiconductor" and isinstance(o, dict):
                sub = next((s for s in ("mosfet", "diode", "igbt") if s in o), None)
            return d, sub, o
    return None, None, None


def datasheet_info(rec, d, sub):
    o = rec[d]
    if sub:
        o = o.get(sub, {})
    return o.get("manufacturerInfo", {}).get("datasheetInfo", {}), \
        o.get("manufacturerInfo", {})


def get_mpn(rec, d, sub):
    if d:
        _, mi = datasheet_info(rec, d, sub)
        if mi.get("reference"):
            return mi["reference"]
    qi = rec.get("quarantineInfo", {})
    return qi.get("mpn") or rec.get("manufacturerInfo", {}).get("reference")


def get_dsurl(rec, d, sub):
    if d:
        _, mi = datasheet_info(rec, d, sub)
        if mi.get("datasheetUrl"):
            return mi["datasheetUrl"]
    return rec.get("manufacturerInfo", {}).get("datasheetUrl")


def present(elec, key):
    return key in elec and elec[key] is not None


def complete_enough(d, sub, rec):
    """Completeness gate: are the key electrical fields for this type present?"""
    di, _ = datasheet_info(rec, d, sub)
    elec = di.get("electrical")
    if d == "magnetic":
        if not isinstance(elec, list) or not elec:
            return False
        op = elec[0]
        return ("inductance" in op and op["inductance"] is not None) or "impedance" in op
    if not isinstance(elec, dict):
        return False
    if d == "capacitor":
        return present(elec, "capacitance") and present(elec, "ratedVoltage")
    if d == "resistor":
        return present(elec, "resistance") and present(elec, "powerRating")
    if sub == "mosfet":
        return all(present(elec, k) for k in ("drainSourceVoltage", "onResistance",
                                              "gateThresholdVoltage"))
    if sub == "diode":
        return present(elec, "reverseVoltage") and (
            present(elec, "forwardVoltage") or present(elec, "clampingVoltage"))
    if sub == "igbt":
        return all(present(elec, k) for k in ("collectorEmitterVoltage",
                                              "continuousCollectorCurrent",
                                              "collectorEmitterSaturation"))
    return False


def content_hash(rec, d):
    """Stable hash of the component subset, ignoring quarantine annotations."""
    core = {d: rec[d]} if d else {k: v for k, v in rec.items() if k not in ANNOT}
    return hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()


def primary_value(d, sub, rec):
    if d is None:
        return None
    di, _ = datasheet_info(rec, d, sub)
    elec = di.get("electrical")
    if d == "magnetic":
        return elec[0].get("inductance") if isinstance(elec, list) and elec else None
    if not isinstance(elec, dict):
        return None
    return elec.get({"capacitor": "capacitance", "resistor": "resistance"}.get(d)
                    or {"mosfet": "drainSourceVoltage", "diode": "reverseVoltage",
                        "igbt": "collectorEmitterVoltage"}.get(sub))


def is_series_stub(d, sub, rec):
    """A series/family placeholder rather than an orderable part: comma-joined
    reference, or a primary value whose min/max span is far wider than any real
    tolerance (often a fabricated geometric-mean nominal over the series range)."""
    _, mi = datasheet_info(rec, d, sub) if d else ({}, rec.get("manufacturerInfo", {}))
    ref = mi.get("reference") or ""
    # Comma- or slash-joined family lists (e.g. "AC, AC-AT", "D/CRCW") are series
    # identifiers, not orderable part numbers.
    if "," in ref or "/" in ref:
        return True
    val = primary_value(d, sub, rec)
    if isinstance(val, dict):
        nom, mn, mx = val.get("nominal"), val.get("minimum"), val.get("maximum")
        if mn and mx and mn > 0:
            if mx / mn > 3.0:  # >3x span = series range, not a part tolerance
                return True
            # Fabricated nominal == geometric mean of the series min/max: a
            # placeholder synthesised for the series, not a real part value.
            if nom and abs(nom - math.sqrt(mn * mx)) <= 1e-6 * nom:
                return True
    return False


def is_zero_ohm(d, sub, rec):
    if d != "resistor":
        return False
    di, _ = datasheet_info(rec, d, sub)
    r = di.get("electrical", {}).get("resistance")
    if isinstance(r, dict):
        r = r.get("nominal", r.get("minimum"))
    return r == 0


def main():
    prod_mpns = defaultdict(set)
    for f in PROD:
        p = DATA / f"{f}.ndjson"
        if not p.exists():
            continue
        with open(p) as fh:
            for line in fh:
                if not line.strip() or line.startswith("version https://git-lfs"):
                    break
                rec = json.loads(line)
                d, sub, _ = component(rec)
                m = get_mpn(rec, d, sub)
                if m:
                    prod_mpns[d].add(m)

    seen = set()
    summary = defaultdict(Counter)
    manifest = open(HERE / "quarantine_triage_manifest.ndjson", "w")

    for qf in sorted(glob.glob(str(DATA / "*quarantine*.ndjson"))):
        base = os.path.basename(qf)
        if os.path.getsize(qf) == 0:
            continue
        synthetic_file = "synthetic" in base
        with open(qf) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("version https://git-lfs"):
                    break
                rec = json.loads(line)
                d, sub, _ = component(rec)
                mpn = get_mpn(rec, d, sub)
                dsurl = get_dsurl(rec, d, sub)

                h = content_hash(rec, d)
                if d and mpn and mpn in prod_mpns.get(d, set()):
                    disp = "duplicate_production"
                elif h in seen:
                    disp = "duplicate_internal"
                else:
                    seen.add(h)
                    if synthetic_file or (not mpn and not dsurl):
                        disp = "synthetic"
                    elif is_series_stub(d, sub, rec):
                        disp = "series_stub"
                    elif is_zero_ohm(d, sub, rec):
                        disp = "zero_ohm"
                    elif d is None:
                        disp = "repair" if (mpn and dsurl) else "keep"
                    else:
                        try:
                            physics_ok = tas_validator.validate({d: rec[d]}).valid
                        except Exception:
                            physics_ok = False
                        if complete_enough(d, sub, rec) and physics_ok:
                            disp = "reinstate"
                        elif mpn and dsurl:
                            disp = "repair"
                        else:
                            disp = "keep"

                summary[base][disp] += 1
                manifest.write(json.dumps({"file": base, "mpn": mpn, "disc": d, "sub": sub,
                                           "disposition": disp}) + "\n")
    manifest.close()

    print(f"{'quarantine file':<48}{'total':>7}  dispositions")
    grand = Counter()
    for base in sorted(summary):
        c = summary[base]
        grand.update(c)
        tot = sum(c.values())
        parts = "  ".join(f"{k}={v}" for k, v in c.most_common())
        print(f"{base:<48}{tot:>7}  {parts}")
    print("\n=== GRAND TOTAL by disposition ===")
    for k, v in grand.most_common():
        print(f"  {v:>8}  {k}")
    print(f"\nmanifest -> {HERE / 'quarantine_triage_manifest.ndjson'}")


if __name__ == "__main__":
    main()
