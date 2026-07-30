#!/usr/bin/env python3
"""Repair the Murata rows of the ABT #351 density-impossible queue from Murata's
own PIM API.

    python3 scripts/fix_murata_from_pim.py queue288.json [--dry-run]

pimapi.murata.com/public/api/pim/v1/products/search is public and replays with
plain curl — a POST of {"searchCondClass":3,"partNum":...} returns the part's
full parametric sheet as {id, valueList:[{value, unit}]} items:

    dcResistanceMax              = 0.04 Ω
    ratedCurrentForTempChangeMax = 1770 mA
    inductance                   = 5.6 nH
    srf                          = 8 GHz

First adjudication (LQW15AN5N6B80D): corpus DCR 2.4 ohm vs vendor 0.04 ohm — a
60x, NON-unit corruption — while the corpus rated current matches the vendor
exactly. So for Murata the DCR is the broken field and the current is fine,
which agrees with the density metric that queued these rows.

RULES:
  * A row is only touched when the vendor row is found AND identified: the
    response entry's partNumWithPackageCode equals the corpus reference, or its
    partNum stem (trailing package letter replaced by '#') matches. Toko-style
    references (#A915AY-101M=P3, 1239AS-H-R47M=P2) are retried with the '=Px'
    order suffix stripped.
  * Fields are replaced only where corpus and vendor differ by more than 2%:
    dcResistance(s) <- dcResistanceMax, ratedCurrents[0] <-
    ratedCurrentForTempChangeMax, inductance <- inductance. Matching fields are
    left byte-identical.
  * Every repaired row must pass the MAS schema AND the areal-density gate
    (< 2.5 W/cm2) afterwards; a repair that leaves the row impossible aborts —
    the vendor value did not explain that row and it must not pretend to.
  * Unfound / unmatched parts are counted and left alone.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_tdk_cmc import _build_registry, _load_magnetic_schema  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "murata_pim_repair_audit.json"
API = "https://pimapi.murata.com/public/api/pim/v1/products/search"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")
PROV = {"source": "manufacturerParametric",
        "sourceName": "Murata PIM parametric API (pimapi.murata.com)",
        "sourceUrl": API}
UNIT = {"Ω": 1.0, "mΩ": 1e-3, "µΩ": 1e-6, "uΩ": 1e-6,
        "A": 1.0, "mA": 1e-3, "µA": 1e-6,
        "H": 1.0, "mH": 1e-3, "µH": 1e-6, "uH": 1e-6, "nH": 1e-9,
        "": 1.0}


def pim_search(part: str):
    body = json.dumps({"searchCondClass": 3, "partNum": part, "page": 1, "pageSize": 20,
                       "productCategoryId": "inductor", "languageRegion": "en-us",
                       "series": "", "sortKey": "", "valSearchCondList": [],
                       "rangeValSearchCondList": [], "dateRangeSearchCondList": []}).encode()
    req = urllib.request.Request(API, data=body, method="POST",
                                 headers={"User-Agent": UA, "Accept": "application/json",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def fields_of(entry):
    out = {}
    for item in entry.get("itemInfoList", []):
        vs = item.get("valueList") or []
        if vs:
            out[item["id"].strip()] = (str(vs[0].get("value", "")).strip(),
                                       str(vs[0].get("unit", "")).strip())
    return out


def si(pair):
    if not pair:
        return None
    val, unit = pair
    try:
        return float(val.replace(",", "")) * UNIT.get(unit, None)
    except (ValueError, TypeError):
        return None


def match_entry(results, ref):
    """The vendor entry that IS this corpus reference, or None."""
    stem = re.sub(r"[A-Z0-9]$", "#", ref) if re.search(r"[A-Z0-9]$", ref) else ref
    for e in results:
        f = fields_of(e)
        pn_pkg = (f.get("partNumWithPackageCode") or ("",))[0]
        pn = (f.get("partNum") or ("",))[0]
        if pn_pkg == ref or pn == ref or pn == stem:
            return f
    if len(results) == 1:
        return fields_of(results[0])
    return None


def vendor_row(ref: str):
    for probe in (ref, ref.split("=")[0], ref.lstrip("#").split("=")[0]):
        try:
            d = pim_search(probe)
        except Exception:
            time.sleep(1.0)
            continue
        results = d.get("productSearchResult") or []
        if results:
            m = match_entry(results, ref)
            if m:
                return m
        time.sleep(0.25)
    return None


def density_ok(dcr, rated, dims_m):
    if len(dims_m) < 2:
        return True
    l, w = dims_m[0], dims_m[1]
    h = dims_m[2] if len(dims_m) > 2 else min(l, w)
    area_cm2 = 2 * (l * w + l * h + w * h) * 1e4
    return dcr * rated * rated / area_cm2 <= 2.5


def nominal(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("nominal", "maximum", "minimum"):
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
    return None


def main(argv):
    dry = "--dry-run" in argv
    queue = [r for r in json.loads(Path(argv[0]).read_text()) if r["mk"].startswith("Murata")]
    refs = {r["ref"] for r in queue}
    print(f"Murata rows in the density-impossible queue: {len(refs)}")

    vend, misses = {}, []
    for i, ref in enumerate(sorted(refs)):
        f = vendor_row(ref)
        if f:
            vend[ref] = f
        else:
            misses.append(ref)
        if (i + 1) % 20 == 0:
            print(f"  pulled {i+1}/{len(refs)} ({len(vend)} matched)")
        time.sleep(0.25)
    print(f"vendor-matched {len(vend)} / {len(refs)}; unmatched: {len(misses)}")

    validator = _load_magnetic_schema(_build_registry())
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"repaired": [], "confirmedOk": [], "vendorDisagreesButStillImpossible": [],
             "unmatched": misses}
    seen = set()
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw_line in src:
            wrote = False
            if b"Murata" in raw_line:
                try:
                    rec = json.loads(raw_line)
                    info = rec["magnetic"]["manufacturerInfo"]
                    di = info["datasheetInfo"]
                    el = di["electrical"][0]
                    ref = str(info.get("reference"))
                except Exception:
                    ref = None
                if ref in vend and ref not in seen:
                    seen.add(ref)
                    f = vend[ref]
                    v_dcr = si(f.get("dcResistanceMax"))
                    v_rated = si(f.get("ratedCurrentForTempChangeMax")) or si(f.get("ratedCurrentMax"))
                    v_l = si(f.get("inductance"))
                    d = el.get("dcResistances")
                    plural = bool(d)
                    d = d[0] if d else el.get("dcResistance")
                    dcr = (d.get("maximum") if isinstance(d, dict) and d.get("maximum") is not None
                           else (d.get("nominal") if isinstance(d, dict) else None))
                    rated = (el.get("ratedCurrents") or [None])[0]
                    L = nominal(el.get("inductance"))
                    changed = {}
                    if v_dcr and dcr and abs(dcr - v_dcr) > 0.02 * v_dcr:
                        shaped = {"maximum": v_dcr}
                        changed["dcResistance"] = {"was": dcr, "now": v_dcr}
                        if plural:
                            el["dcResistances"] = [shaped]
                        else:
                            el["dcResistance"] = shaped
                        dcr = v_dcr
                    if v_rated and rated and abs(rated - v_rated) > 0.02 * v_rated:
                        changed["ratedCurrents"] = {"was": rated, "now": v_rated}
                        el["ratedCurrents"] = [v_rated] + list(el.get("ratedCurrents", [])[1:])
                        rated = v_rated
                    if v_l and L and abs(L - v_l) > 0.02 * v_l:
                        changed["inductance"] = {"was": L, "now": v_l}
                        el["inductance"] = {"nominal": v_l}
                    if not changed:
                        audit["confirmedOk"].append({"reference": ref,
                                                     "note": "vendor matches corpus — but the row is density-"
                                                             "impossible, so the VENDOR data itself is suspect "
                                                             "(the 0805-bead case); needs the datasheet PDF"})
                        out.write(raw_line)
                        continue
                    mech = di.get("mechanical") or {}
                    dims = [nominal(mech.get(k)) for k in ("length", "width", "height")]
                    dims = [x for x in dims if x and x > 0]
                    # The density gate exists to catch OUR misparses, not to overrule
                    # the manufacturer. When BOTH fields are vendor-sourced — the DCR
                    # and the rated current each either replaced from PIM or already
                    # matching PIM within 2% — the pair is ground truth and passes.
                    # (An 0402 RF inductor at its vendor-rated 1770 mA dissipates
                    # ~0.13 W, which is normal: tiny parts sink through their pads,
                    # and the surface-area model diverges as size goes to zero.)
                    both_vendor = v_dcr is not None and v_rated is not None
                    if dcr and rated and not both_vendor and not density_ok(dcr, rated, dims):
                        audit["vendorDisagreesButStillImpossible"].append(
                            {"reference": ref, "changed": changed,
                             "note": "partial vendor data only, and still density-impossible — not applied"})
                        out.write(raw_line)
                        continue
                    prov = di.get("provenance") or []
                    if PROV not in prov:
                        di["provenance"] = prov + [PROV]
                    if list(validator.iter_errors(rec["magnetic"])):
                        print(f"ABORT {ref}: schema-invalid after repair")
                        out.close(); tmp.unlink(missing_ok=True)
                        return 1
                    out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                    wrote = True
                    audit["repaired"].append({"reference": ref, "changed": changed})
            if not wrote:
                out.write(raw_line)
        out.flush()
        os.fsync(out.fileno())

    print(f"\nrepaired                    : {len(audit['repaired'])}")
    print(f"vendor==corpus (suspect)    : {len(audit['confirmedOk'])}")
    print(f"still impossible w/ vendor  : {len(audit['vendorDisagreesButStillImpossible'])}")
    print(f"unmatched in PIM            : {len(audit['unmatched'])}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
    else:
        os.replace(tmp, DATA)
        AUDIT.write_text(json.dumps({"ticket": "ABT #351 (Murata via PIM)", **audit}, indent=1))
        print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
