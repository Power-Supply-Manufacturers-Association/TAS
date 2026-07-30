#!/usr/bin/env python3
"""Repair the Shenzhen Sunlord rows of the ABT #351 density-impossible queue from
the MWLA family datasheets the rows themselves cite (DigiKey media mirror,
curl-open).

    python3 scripts/fix_sunlord_from_datasheets.py queue288.json [--dry-run]

The MWLA-S sheet's table extracts cleanly with pdftotext -raw:

    Units   μH    Hz, V     mΩ    A     A
    Symbol  L     -         DCR   Isat  Irms
    MWLA0624S-4R7MT  4.7  100k, 1.0V  44  9.5  5.5

Row shape after the part number: [L, 100, 1.0, DCR, Isat, Irms] — the test
condition "100k, 1.0V" contributes two numbers that are skipped by position.
The Units line is REQUIRED to state mΩ for the DCR column before any scaling,
and the corpus inductance must anchor the L cell within 2%. ratedCurrents maps
to Irms (the thermal rating); saturationCurrentPeak, where the record has one,
to Isat. Usual gates: MAS schema, Blade Runner, areal density, full audit.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validator" / "build-ninja"))
from extract_tdk_cmc import _build_registry, _load_magnetic_schema  # noqa: E402
import tas_validator  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "sunlord_datasheet_repair_audit.json"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")
_cache: dict[str, str] = {}


def sheet_raw(url: str) -> str:
    if url in _cache:
        return _cache[url]
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = r.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        path = f.name
    out = subprocess.run(["pdftotext", "-raw", path, "-"], capture_output=True, timeout=120)
    os.unlink(path)
    text = out.stdout.decode("utf-8", "replace") if out.returncode == 0 else ""
    _cache[url] = text
    time.sleep(0.4)
    return text


def dcr_col_is_milliohm(text: str) -> bool:
    return bool(re.search(r"(?im)^Units\s+[µμu]H\s+Hz,\s*V\s+m[ΩW]\s+A\s+A", text))


def part_row(text: str, ref: str):
    for line in text.splitlines():
        if not line.startswith(ref):
            continue
        nums = [float(x) for x in re.findall(r"\d+\.?\d*", line[len(ref):].replace(",", ""))]
        # [L, 100(k), 1.0(V), DCR, Isat, Irms]
        if len(nums) >= 6:
            return {"L_uH": nums[0], "dcr_mohm": nums[3], "isat": nums[4], "irms": nums[5]}
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
    if len(dims_m) < 2:
        return True
    l, w = dims_m[0], dims_m[1]
    h = dims_m[2] if len(dims_m) > 2 else min(l, w)
    return dcr * rated * rated / (2 * (l * w + l * h + w * h) * 1e4) <= 2.5


def main(argv):
    dry = "--dry-run" in argv
    queue = {r["ref"] for r in json.loads(Path(argv[0]).read_text())
             if r["mk"].startswith("Shenzhen Sunlord")}
    print(f"Sunlord rows in the queue: {len(queue)}")

    validator = _load_magnetic_schema(_build_registry())
    tmp = DATA.with_suffix(".ndjson.tmp")
    audit = {"repaired": [], "skipped": []}
    seen = set()
    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw_line in src:
            wrote = False
            if b"MWLA" in raw_line and b"Sunlord" in raw_line:
                try:
                    rec = json.loads(raw_line)
                    info = rec["magnetic"]["manufacturerInfo"]
                    di = info["datasheetInfo"]
                    el = di["electrical"][0]
                    ref = str(info.get("reference"))
                except Exception:
                    ref = None
                if ref in queue and ref not in seen:
                    seen.add(ref)
                    url = info.get("datasheetUrl")
                    why = None
                    try:
                        text = sheet_raw(url)
                    except Exception as e:
                        why, text = f"datasheet fetch failed: {e}", ""
                    if why is None and not dcr_col_is_milliohm(text):
                        why = "Units line does not prove a milliohm DCR column"
                    row = part_row(text, ref) if why is None else None
                    if why is None and not row:
                        why = "part row not found in the sheet"
                    L = nominal(el.get("inductance"))
                    if why is None and (L is None or abs(row["L_uH"] * 1e-6 - L) > 0.02 * L):
                        why = f"L anchor failed (sheet {row['L_uH']}uH vs corpus {L})"
                    if why is None:
                        d = el.get("dcResistances")
                        plural = bool(d)
                        d = d[0] if d else el.get("dcResistance")
                        dcr = (d.get("maximum") if isinstance(d, dict) and d.get("maximum") is not None
                               else (d.get("nominal") if isinstance(d, dict) else None))
                        rated = (el.get("ratedCurrents") or [None])[0]
                        isat = nominal(el.get("saturationCurrentPeak"))
                        v_dcr, v_irms, v_isat = row["dcr_mohm"] / 1000.0, row["irms"], row["isat"]
                        changed = {}
                        if dcr and abs(dcr - v_dcr) > 0.05 * v_dcr:
                            changed["dcResistance"] = {"was": dcr, "now": v_dcr}
                            shaped = {"maximum": v_dcr}
                            if plural:
                                el["dcResistances"] = [shaped]
                            else:
                                el["dcResistance"] = shaped
                            dcr = v_dcr
                        if rated and abs(rated - v_irms) > 0.05 * v_irms:
                            changed["ratedCurrents"] = {"was": rated, "now": v_irms}
                            el["ratedCurrents"] = [v_irms] + list(el.get("ratedCurrents", [])[1:])
                            rated = v_irms
                        if isat is not None and abs(isat - v_isat) > 0.05 * v_isat:
                            changed["saturationCurrentPeak"] = {"was": isat, "now": v_isat}
                            el["saturationCurrentPeak"] = v_isat
                        mech = di.get("mechanical") or {}
                        dims = [nominal(mech.get(k)) for k in ("length", "width", "height")]
                        dims = [x for x in dims if x and x > 0]
                        if not changed:
                            why = "sheet agrees with corpus — row remains unexplained"
                        elif not density_ok(dcr, rated, dims):
                            why = "still density-impossible with the sheet's values"
                        else:
                            prov = di.get("provenance") or []
                            entry = {"source": "manufacturerDatasheet",
                                     "sourceName": "Sunlord MWLA-S family datasheet (parts table)",
                                     "sourceUrl": url}
                            if entry not in prov:
                                di["provenance"] = prov + [entry]
                            if list(validator.iter_errors(rec["magnetic"])):
                                why = "schema-invalid after repair"
                            else:
                                vd = tas_validator.validate(json.dumps(rec))
                                if any(str(f.severity).upper() == "IMPOSSIBLE" for f in vd.findings):
                                    why = "Blade Runner IMPOSSIBLE after repair"
                        if why is None:
                            out.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")
                            wrote = True
                            audit["repaired"].append({"reference": ref, "changed": changed,
                                                      "datasheet": url})
                            print(f"  {ref:20} {json.dumps(changed)[:105]}")
                    if why:
                        audit["skipped"].append({"reference": ref, "why": why})
            if not wrote:
                out.write(raw_line)
        out.flush()
        os.fsync(out.fileno())

    print(f"\nrepaired {len(audit['repaired'])}, skipped {len(audit['skipped'])}")
    for s in audit["skipped"][:12]:
        print(f"  SKIP {s['reference']}: {s['why'][:80]}")
    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing replaced")
    else:
        os.replace(tmp, DATA)
        AUDIT.write_text(json.dumps({"ticket": "ABT #351 (Sunlord)", **audit}, indent=1))
        print(f"replaced {DATA}\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
