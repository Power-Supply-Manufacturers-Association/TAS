#!/usr/bin/env python3
"""Repair the TDK rows of the ABT #351 density-impossible queue from the
product.tdk.com parametric grids captured by pull_tdk_inductor_specs.mjs.

    python3 scripts/fix_tdk_from_grid.py tdk_specs.json queue288.json [--dry-run]

Each captured entry carries its own grid header, and two grid shapes occur:

  INDUCTOR grids: Inductance | Rated Current / A | Rdc (Max.) / Ω | SRF ...
     -> anchor on the corpus inductance (2%), then dcr/rated from the vendor
        cells where they differ by >2%.
  CMC grids (the ACM12V D1 rows): Common-mode Impedance / Ω ("350 at 100MHz") |
     Common-mode Inductance / mH | Rated Current (Max.) / A | Rdc / Ω
     -> the ACM2012-corrections treatment: impedancePoints gets the |Z| at its
        stated frequency, dcResistance and ratedCurrents get the vendor values,
        and the code-stamped inductance is replaced by the vendor's L (or removed
        if the vendor publishes none). No anchor is possible — the corpus L IS
        the corrupted field on these — so the match is the exact part number.

Gates as everywhere in this campaign: MAS schema, Blade Runner zero IMPOSSIBLE,
areal-density passes with the vendor numbers, every prior value audited.
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
AUDIT = REPO / "staging" / "tdk_grid_repair_audit.json"
PROV = {"source": "manufacturerParametric",
        "sourceName": "TDK Product Center parametric grid (product.tdk.com)",
        "sourceUrl": "https://product.tdk.com/en/search/"}
MU = "µμ"          # micro sign + greek mu


def parse_qty(cell: str):
    """'1μH' / '470 nH' / '0.45 Max.' / '2.6' -> value in SI, or None."""
    if not cell:
        return None
    m = re.match(rf"^\s*([\d.]+)\s*([{MU}munkMG]?)(H|Ω|A)?", cell.strip())
    if not m:
        return None
    scale = {"": 1.0, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
             "n": 1e-9, "k": 1e3, "M": 1e6, "G": 1e9}.get(m.group(2), 1.0)
    try:
        return float(m.group(1)) * scale
    except ValueError:
        return None


def parse_z_at(cell: str):
    """'350 at 100MHz' -> (350.0, 1e8)."""
    m = re.match(r"^\s*([\d.]+)\s*at\s*([\d.]+)\s*(k|M|G)?Hz", cell or "")
    if not m:
        return None, None
    return (float(m.group(1)),
            float(m.group(2)) * {"k": 1e3, "M": 1e6, "G": 1e9, None: 1.0}[m.group(3)])


def col(head, pattern):
    for i, h in enumerate(head):
        if re.search(pattern, h, re.I):
            return i
    return None


def nominal(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("nominal", "maximum", "minimum"):
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
    return None


def density_ok(dcr, rated, dims_m):
    if len(dims_m) < 2 or not dcr or not rated:
        return True
    watts = dcr * rated * rated
    # below the absolute floor pad conduction dominates and the surface model is
    # invalid — the tiny-TFM/Murata-0402 lesson (MAG_DISS_POWER_FLOOR_W)
    if watts <= 5.0:
        return True
    l, w = dims_m[0], dims_m[1]
    h = dims_m[2] if len(dims_m) > 2 else min(l, w)
    return watts / (2 * (l * w + l * h + w * h) * 1e4) <= 2.5


def main(argv):
    dry = "--dry-run" in argv
    specs = json.loads(Path(argv[0]).read_text())
    queue = {r["ref"] for r in json.loads(Path(argv[1]).read_text()) if r["mk"] == "TDK"}
    print(f"TDK queue {len(queue)}; captured grid rows {len(specs)}")

    validator = _load_magnetic_schema(_build_registry())
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"repaired": [], "skipped": []}
    seen = set()
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw_line in src:
            wrote = False
            if b"TDK" in raw_line:
                try:
                    rec = json.loads(raw_line)
                    info = rec["magnetic"]["manufacturerInfo"]
                    di = info["datasheetInfo"]
                    el = di["electrical"][0]
                    ref = str(info.get("reference"))
                except Exception:
                    ref = None
                if ref in queue and ref not in seen and str(info.get("name")) == "TDK":
                    seen.add(ref)
                    # the grid lists parts without packaging suffixes (-CA, -D,
                    # tape codes) that the corpus reference carries
                    entry = None
                    for cand in (ref, re.sub(r"-CA$", "", ref), re.sub(r"-D$", "", ref)):
                        entry = specs.get(cand)
                        if entry:
                            break
                    why = None
                    if not entry:
                        why = "part not captured from any TDK grid"
                    else:
                        head, row = entry["head"], entry["row"]
                        is_cmc = col(head, r"Common-mode Impedance") is not None
                        # the line-filter grid (automotive power-line CMCs like
                        # ACM12V) publishes Inductance + Rated Current but NO Rdc
                        # and no impedance column
                        # decide by the CELL, not the column: line-filter rows
                        # (ACM12V) sit in a grid that has an Rdc column but leaves
                        # it empty for these parts
                        _di = col(head, r"^Rdc")
                        _rdc_cell = parse_qty(row[_di]) if _di is not None else None
                        no_rdc_grid = (not is_cmc and _rdc_cell is None
                                       and col(head, r"^Inductance") is not None
                                       and col(head, r"^Rated Current") is not None)
                        d = el.get("dcResistances")
                        plural = bool(d)
                        d = d[0] if d else el.get("dcResistance")
                        dcr = (d.get("maximum") if isinstance(d, dict) and d.get("maximum") is not None
                               else (d.get("nominal") if isinstance(d, dict) else None))
                        rated = (el.get("ratedCurrents") or [None])[0]
                        L = nominal(el.get("inductance"))
                        changed = {}
                        if no_rdc_grid:
                            # D1 rows: the corpus L and DCR both carry the MPN's
                            # EIA code, so no anchor is possible — the exact part
                            # number is the row identity. Vendor gives the real L
                            # and rated current; it publishes no DCR, so a
                            # code-valued corpus DCR is REMOVED (Laird treatment),
                            # never guessed.
                            li = col(head, r"^Inductance")
                            ri = col(head, r"^Rated Current")
                            v_l = parse_qty(row[li]) if li is not None else None
                            v_r = parse_qty(row[ri]) if ri is not None else None
                            m = re.search(r"[-_](\d{3})(?=[-_]|$)", ref)
                            code = int(m.group(1)[:2]) * 10 ** int(m.group(1)[2]) if m else None
                            code_dcr = code is not None and dcr is not None and abs(dcr - code) < 0.01
                            if v_l is None or v_r is None:
                                why = "line-filter row missing L or rated current"
                            elif not code_dcr:
                                why = "corpus DCR is not the MPN code — not the D1 case; no vendor DCR to compare"
                            else:
                                changed["dcResistance"] = {"was": dcr, "now": None,
                                    "why": "the MPN impedance code; vendor grid publishes no DCR"}
                                if plural:
                                    el.pop("dcResistances", None)
                                else:
                                    el.pop("dcResistance", None)
                                dcr = None
                                if L is None or abs(L - v_l) > 0.02 * v_l:
                                    changed["inductance"] = {"was": L, "now": v_l}
                                    el["inductance"] = {"nominal": v_l}
                                if rated is None or abs(rated - v_r) > 0.02 * v_r:
                                    changed["ratedCurrents"] = {"was": rated, "now": v_r}
                                    el["ratedCurrents"] = [v_r] + list(el.get("ratedCurrents", [])[1:])
                                    rated = v_r
                        elif is_cmc:
                            zi = col(head, r"Common-mode Impedance")
                            li = col(head, r"Common-mode Inductance")
                            ri = col(head, r"Rated Current")
                            di_ = col(head, r"^Rdc")
                            z, zf = parse_z_at(row[zi]) if zi is not None else (None, None)
                            v_l = parse_qty(row[li]) * 1e-3 if (li is not None and parse_qty(row[li]) is not None and "mH" in head[li]) else parse_qty(row[li]) if li is not None else None
                            v_r = parse_qty(row[ri]) if ri is not None else None
                            v_d = parse_qty(row[di_]) if di_ is not None else None
                            if z is None and v_d is None:
                                why = "CMC grid row carries no |Z| and no Rdc"
                            else:
                                if z is not None and zf is not None:
                                    changed["impedancePoints"] = {"now": [z, zf]}
                                    el["impedancePoints"] = [{"frequency": zf,
                                                              "impedance": {"magnitude": z}}]
                                if v_d is not None and (dcr is None or abs(dcr - v_d) > 0.02 * v_d):
                                    changed["dcResistance"] = {"was": dcr, "now": v_d}
                                    shaped = {"maximum": v_d}
                                    if plural:
                                        el["dcResistances"] = [shaped]
                                    else:
                                        el["dcResistance"] = shaped
                                    dcr = v_d
                                if v_r is not None and (rated is None or abs(rated - v_r) > 0.02 * v_r):
                                    changed["ratedCurrents"] = {"was": rated, "now": v_r}
                                    el["ratedCurrents"] = [v_r] + list(el.get("ratedCurrents", [])[1:])
                                    rated = v_r
                                if v_l is not None:
                                    if L is None or abs(L - v_l) > 0.02 * v_l:
                                        changed["inductance"] = {"was": L, "now": v_l}
                                        el["inductance"] = {"nominal": v_l}
                                elif L is not None and changed:
                                    changed["inductance"] = {"was": L, "now": None,
                                                             "why": "vendor publishes no L; corpus value was the MPN code"}
                                    el.pop("inductance", None)
                        else:
                            li = col(head, r"^Inductance")
                            ri = col(head, r"^Rated Current")
                            di_ = col(head, r"^Rdc")
                            v_l = parse_qty(row[li]) if li is not None else None
                            v_r = parse_qty(row[ri]) if ri is not None else None
                            v_d = parse_qty(row[di_]) if di_ is not None else None
                            if v_l is None or L is None or abs(v_l - L) > 0.02 * max(L, 1e-12):
                                why = f"L anchor failed (grid {v_l} vs corpus {L})"
                            elif v_d is None or v_r is None:
                                why = "grid row missing Rdc or rated current"
                            else:
                                if abs((dcr or 0) - v_d) > 0.02 * v_d:
                                    changed["dcResistance"] = {"was": dcr, "now": v_d}
                                    shaped = {"maximum": v_d}
                                    if plural:
                                        el["dcResistances"] = [shaped]
                                    else:
                                        el["dcResistance"] = shaped
                                    dcr = v_d
                                if abs((rated or 0) - v_r) > 0.02 * v_r:
                                    changed["ratedCurrents"] = {"was": rated, "now": v_r}
                                    el["ratedCurrents"] = [v_r] + list(el.get("ratedCurrents", [])[1:])
                                    rated = v_r
                        if why is None:
                            mech = di.get("mechanical") or {}
                            dims = [nominal(mech.get(k)) for k in ("length", "width", "height")]
                            dims = [x for x in dims if x and x > 0]
                            if not changed:
                                why = "grid agrees with corpus — row remains unexplained"
                            elif not density_ok(dcr, rated, dims):
                                why = "still density-impossible with the vendor's values"
                            else:
                                prov = di.get("provenance") or []
                                if PROV not in prov:
                                    di["provenance"] = prov + [PROV]
                                if list(validator.iter_errors(rec["magnetic"])):
                                    why = "schema-invalid after repair"
                                else:
                                    vd = tas_validator.validate(json.dumps(rec))
                                    if any(str(f.severity).endswith("Impossible") for f in vd.findings):
                                        why = "Blade Runner IMPOSSIBLE after repair"
                        if why is None:
                            out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                            wrote = True
                            audit["repaired"].append({"reference": ref, "changed": changed,
                                                      "grid": entry["category"]})
                            print(f"  {ref:26} {json.dumps(changed)[:110]}")
                    if why:
                        audit["skipped"].append({"reference": ref, "why": why})
            if not wrote:
                out.write(raw_line)
        out.flush()
        os.fsync(out.fileno())

    print(f"\nrepaired {len(audit['repaired'])}, skipped {len(audit['skipped'])}")
    from collections import Counter
    for w, n in Counter(s["why"][:60] for s in audit["skipped"]).most_common(8):
        print(f"  {n:3}x SKIP {w}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
    else:
        os.replace(tmp, DATA)
        AUDIT.write_text(json.dumps({"ticket": "ABT #351 (TDK grids)", **audit}, indent=1))
        print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
