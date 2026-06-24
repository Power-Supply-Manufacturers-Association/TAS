#!/usr/bin/env python3
"""Convert WIMA parametric-search JSON (/tmp/wima/*.json) -> CAS/capacitor NDJSON.

WIMA's param-search API returns film capacitors with: product_partnumber,
product_type (series), product_capacitance ("1.0 µF"/"470.0 pF"),
product_voltage ("400 VDC"/"305 VAC"), product_tolerance ("5%"),
product_dielectric (Polypropylene/Polyester/PPS), product_boxsize ("WxHxL" mm),
product_sizecode ("PCM 15"), product_group, product_webpdf (datasheet).

Emits one record per distinct part number, SI base units, in the CAS discriminator
shape {"capacitor": {...}}. Routes to staging/wima/capacitors.{main,incomplete}.ndjson.
No value fabricated; rows missing a schema-required field go to incomplete.
"""
import json, glob, os, re

SRC = "/tmp/wima"
OUT = "/home/alf/PSMA/TAS/staging/wima"
os.makedirs(OUT, exist_ok=True)

DIELECTRIC_TECH = {
    "Polypropylene": "film-polypropylene",
    "Polyester": "film-polyester",
    "PPS": "film-polyphenylene-sulfide",
    "Paper": "film-paper",
}

def num(s):
    m = re.search(r"[-+]?\d*\.?\d+", (s or "").replace(",", "."))
    return float(m.group()) if m else None

def cap_farads(s):
    v = num(s)
    if v is None:
        return None
    u = (s or "").lower()
    if "pf" in u:
        return v * 1e-12
    if "nf" in u:
        return v * 1e-9
    if "µf" in u or "uf" in u or "μf" in u:
        return v * 1e-6
    return None  # unknown unit -> treat as missing, don't guess

def dims_m(boxsize):
    """'5x11x18' (mm, W x H x L) -> (w,h,l) in metres, or None."""
    parts = re.findall(r"[\d.]+", boxsize or "")
    if len(parts) != 3:
        return None
    return tuple(round(float(p) * 1e-3, 9) for p in parts)

def status_load():
    return "production"

def convert(p):
    pn = (p.get("product_partnumber") or "").strip()
    if not pn:
        return None, ["partNumber"]
    diel = (p.get("product_dielectric") or "").strip()
    tech = DIELECTRIC_TECH.get(diel)
    C = cap_farads(p.get("product_capacitance"))
    V = num(p.get("product_voltage"))
    tol = num(p.get("product_tolerance"))  # percent
    group = (p.get("product_group") or "").strip()
    series = (p.get("product_type") or "").strip()
    is_smd = "smd" in group.lower()

    part = {"partNumber": pn}
    if tech:
        part["technology"] = tech
    if series:
        part["series"] = series
    sizecode = (p.get("product_sizecode") or "").strip()
    if sizecode:
        part["case"] = sizecode
    desc = " ".join(x for x in [group, series, p.get("product_capacitance", "").strip(),
                                p.get("product_voltage", "").strip()] if x)
    if desc:
        part["description"] = desc

    electrical = {}
    if C is not None:
        cap = {"nominal": C}
        if tol is not None:
            cap["minimum"] = round(C * (1 - tol / 100.0), 18)
            cap["maximum"] = round(C * (1 + tol / 100.0), 18)
        electrical["capacitance"] = cap
    if V is not None:
        electrical["ratedVoltage"] = V
    electrical["polarized"] = False  # film caps are non-polarized

    # mechanical
    shape = {"assembly": "SMT" if is_smd else "THT",
             "shapeType": "SMD Chip" if is_smd else "Box"}
    mechanical = {"shape": shape}
    d = dims_m(p.get("product_boxsize"))
    if d:
        w, h, l = d
        mechanical["dimensions"] = {"width": {"nominal": w}, "height": {"nominal": h},
                                    "length": {"nominal": l}}
        shape["volume"] = {"nominal": round(w * h * l, 18)}
    pcm = num(sizecode) if sizecode.upper().startswith("PCM") else None
    if pcm is not None:
        mechanical.setdefault("dimensions", {})["pitch"] = {"nominal": round(pcm * 1e-3, 9)}

    di = {"part": part, "electrical": electrical, "mechanical": mechanical}
    mi = {"name": "WIMA", "reference": pn, "status": status_load(), "datasheetInfo": di}
    if series:
        mi["family"] = series
    pdf = (p.get("product_webpdf") or "").strip()
    if pdf.startswith("http"):
        mi["datasheetUrl"] = pdf
    if desc:
        mi["description"] = desc
    rec = {"capacitor": {"manufacturerInfo": mi}}

    # schema-required: part.technology, electrical.capacitance, electrical.ratedVoltage,
    #                  mechanical.shape.{assembly,shapeType}
    missing = []
    if "technology" not in part:
        missing.append("technology")
    if "capacitance" not in electrical:
        missing.append("capacitance")
    if "ratedVoltage" not in electrical:
        missing.append("ratedVoltage")
    return rec, missing

def main():
    seen = set()
    main_recs, incomplete = [], []
    n = dup = 0
    for f in sorted(glob.glob(f"{SRC}/*.json")):
        for p in json.load(open(f)):
            n += 1
            pn = (p.get("product_partnumber") or "").strip()
            if pn in seen:
                dup += 1
                continue
            seen.add(pn)
            rec, missing = convert(p)
            if rec is None:
                continue
            if missing:
                rec["quarantineReason"] = ("incomplete WIMA parametric data; missing "
                                           "required field(s): " + ", ".join(missing) + " (2026-06-24)")
                incomplete.append(rec)
            else:
                main_recs.append(rec)
    with open(f"{OUT}/capacitors.main.ndjson", "w") as fo:
        for r in main_recs:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(f"{OUT}/capacitors.incomplete.ndjson", "w") as fo:
        for r in incomplete:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"rows_scanned": n, "dup_partnumbers_skipped": dup,
                      "main": len(main_recs), "incomplete": len(incomplete)}, indent=2))

if __name__ == "__main__":
    main()
