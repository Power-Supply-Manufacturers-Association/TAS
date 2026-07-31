#!/usr/bin/env python3
"""Convert the Amphenol RF parametric dump (scripts/amphenol_rf_pull.py) -> CONAS connector NDJSON.

Only the RF-connector and RF-adapter branches are converted; cable assemblies, terminators,
attenuators, tools and accessories are not connectors and are skipped. Every value is copied
straight from the vendor API -- nothing is estimated, interpolated or defaulted. SI base units.

    python3 scripts/amphenol_rf_import.py <raw_dir> <out_dir>
"""
import glob, json, os, re, sys

RAW = sys.argv[1] if len(sys.argv) > 1 else "/tmp/amphenol_rf"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/home/alf/PSMA/TAS/staging/amphenol"
RETRIEVED = "2026-07-30"
BASE = "https://www.amphenolrf.com"

os.makedirs(OUT, exist_ok=True)


def num(v):
    if v is None:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", str(v).replace(",", ""))
    return float(m.group()) if m else None


GENDER = {"jack": "female", "plug": "male", "push-on plug": "male"}

# productCategory -> familyRf.interface (only when the category names ONE interface)
def interface_of(cat):
    if not cat:
        return None
    if cat.endswith(" Connectors"):
        return cat[:-len(" Connectors")].strip() or None
    m = re.match(r"^(.+?) to (.+?) (Adapters|Tee Adapters)$", cat)
    if m and m.group(1).strip() == m.group(2).strip():
        return m.group(1).strip()
    return None


def body_style(d):
    term = (d.get("termination_style") or "").lower()
    mount = (d.get("mounting_feature") or "").lower()
    orient = (d.get("orientation") or "").lower()
    if "end launch" in term:
        return "endLaunch"
    if "bulkhead" in mount:
        return "bulkhead"
    if orient == "right angle":
        return "rightAngle"
    if orient == "straight":
        return "straight"
    return None


def mounting_style(d):
    term = (d.get("termination_style") or "").lower()
    mount = (d.get("mounting_feature") or "").lower()
    ct = (d.get("contact_termination") or "").lower()
    if "through hole" in term:
        return "tht"
    if "surface mount" in term:
        return "smt"
    if ct == "press-fit" or "press-fit" in mount:
        return "pressFit"
    if "bulkhead" in mount or "flange" in mount or "thread-in" in mount:
        return "panel"
    if term.startswith("cable"):
        return "cable"
    return None


def temp_c(v):
    """'-65 °C' -> -65.0"""
    if not v:
        return None
    return num(v)


def convert(r):
    """-> (record, reject_reason)"""
    d = r.get("additionalDictionary") or {}
    pn = (r.get("partNumber") or "").strip()
    if not pn:
        return None, "no partNumber"

    imp = num(d.get("impedance"))
    if imp is None or imp <= 0:
        return None, ("characteristicImpedance not published (impedance=%r)"
                      % (d.get("impedance") or ""))

    fam = {"family": "rf", "characteristicImpedance": imp}
    iface = interface_of(r.get("productCategory"))
    if iface:
        fam["interface"] = iface
    fmax = num(d.get("frequency_max"))
    if fmax and fmax > 0:
        fam["frequencyRange"] = {"maximum": fmax * 1e9}      # catalogue value is GHz
    vswr = num(d.get("vswr"))
    if vswr and vswr >= 1:
        fam["maxVswr"] = vswr
    bs = body_style(d)
    if bs:
        fam["bodyStyle"] = bs

    part = {"partNumber": pn}
    pol = GENDER.get((d.get("gender") or "").strip().lower())
    if pol:
        part["matingPolarity"] = pol
    desc = (r.get("description") or "").strip()
    if desc:
        part["description"] = desc[:1000]

    mech = {}
    cyc = num(d.get("mating_cycles_min"))
    if cyc is not None and cyc >= 0:
        mech["matingCycles"] = int(cyc)
    grams = num(d.get("unit_weight_grams"))
    if grams is not None and grams > 0:
        mech["weight"] = grams / 1000.0                       # g -> kg
    ms = mounting_style(d)
    if ms:
        mech["mountingStyle"] = ms
    ports = num(d.get("ports"))
    if ports is not None and ports >= 1:
        mech["positions"] = int(ports)

    env = {}
    tmin, tmax = temp_c(d.get("temp_min")), temp_c(d.get("temp_max"))
    if tmin is not None and tmax is not None and tmin < tmax:
        env["operatingTemperature"] = {"minimum": tmin, "maximum": tmax}
    ip = (d.get("ip_rating") or "").strip()
    if re.fullmatch(r"IP[0-9XK]{2,4}", ip, flags=re.I):
        env["ipRating"] = ip.upper()

    di = {"part": part, "electrical": {}, "mechanical": mech, "familyDetails": fam}
    if env:
        di["environmental"] = env
    prov = {"source": "manufacturerParametric",
            "sourceName": "Amphenol RF parametric search API "
                          "(POST amphenolrf.com/api/search/parametric)",
            "retrievedDate": RETRIEVED}
    if r.get("pageUrl"):
        prov["sourceUrl"] = BASE + r["pageUrl"]
    di["provenance"] = [prov]

    mi = {"name": "Amphenol RF", "reference": pn, "datasheetInfo": di}
    if r.get("productCategory"):
        mi["family"] = r["productCategory"]
    if desc:
        mi["description"] = desc[:1000]
    cd = r.get("customerDrawing") or {}
    if cd.get("assetUrl") and not cd.get("assetIsGated"):
        mi["datasheetUrl"] = BASE + cd["assetUrl"]
    return {"connector": {"manufacturerInfo": mi}}, None


def main():
    recs = []
    for f in sorted(glob.glob(os.path.join(RAW, "page_*.json"))):
        recs += json.load(open(f))
    keep = [r for r in recs
            if any(s in (r.get("categoryPageUrl") or "")
                   for s in ("/rf-connectors/", "/rf-adapters/"))]
    good, bad = [], []
    seen = set()
    for r in keep:
        pn = (r.get("partNumber") or "").strip()
        if pn in seen:
            continue
        seen.add(pn)
        rec, why = convert(r)
        if rec is None:
            bad.append({"partNumber": pn, "reason": why,
                        "productCategory": r.get("productCategory")})
        else:
            good.append(rec)
    with open(os.path.join(OUT, "rf.records.ndjson"), "w") as fo:
        for g in good:
            fo.write(json.dumps(g, ensure_ascii=False) + "\n")
    with open(os.path.join(OUT, "rf.incomplete.ndjson"), "w") as fo:
        for b in bad:
            fo.write(json.dumps(b, ensure_ascii=False) + "\n")
    print(json.dumps({"raw": len(recs), "connector_or_adapter": len(keep),
                      "converted": len(good), "incomplete": len(bad)}, indent=1))


if __name__ == "__main__":
    main()
