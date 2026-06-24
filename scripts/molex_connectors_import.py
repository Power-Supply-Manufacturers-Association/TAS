#!/usr/bin/env python3
"""Convert Molex search API docs (/tmp/molex/page_*.json) -> CONAS connector NDJSON.

Source: https://search.molex.com/api/search/products-per-category (Solr). One doc per
orderable part. Maps to the CONAS connector discriminator {"connector": {...}}, SI units.
Routes to staging/molex/connectors.{main,incomplete}.ndjson. No fabrication: parts missing a
schema-required field (matingPolarity / ratedCurrentPerContact / ratedVoltage / a mappable
family / rf-impedance) go to incomplete with a reason.
"""
import glob, json, os, re

SRC = "/tmp/molex"
OUT = "/home/alf/PSMA/TAS/staging/molex"
os.makedirs(OUT, exist_ok=True)

def first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v

def num(v):
    v = first(v)
    if v is None:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", str(v))
    return float(m.group()) if m else None

# ---- matingPolarity (gender -> derive from contact/component type) ----------
GENDER = {"male": "male", "female": "female", "hermaphroditic": "hermaphroditic",
          "unisex": "hermaphroditic", "genderless": "genderless"}
def mating_polarity(d):
    g = (first(d.get("physical.gender")) or "").strip().lower()
    if g in GENDER:
        return GENDER[g]
    ct = (first(d.get("general.contactType")) or "").strip().lower()
    if ct in ("pin", "tab", "blade"):
        return "male"
    if ct in ("socket", "receptacle"):
        return "female"
    comp = " ".join(str(x) for x in (d.get("general.componentType") or [])).lower()
    if any(w in comp for w in ("header", "plug", "pin")):
        return "male"
    if any(w in comp for w in ("receptacle", "socket", "jack")):
        return "female"
    return None

# ---- family mapping by taxonomy leaf ---------------------------------------
INTERFACE_TOKENS = ["USB4", "USB-C", "USB", "HDMI", "DisplayPort", "Thunderbolt", "QSFP-DD",
                    "QSFP", "OSFP", "SFP+", "SFP", "PCI Express", "PCIe", "SATA", "SAS",
                    "Ethernet", "RJ45", "RJ-45", "FAKRA", "Mini-SAS", "Micro-USB", "Type-C",
                    "M.2", "SO-DIMM", "DIMM", "D-Sub", "FireWire", "CXP"]
def interface_standard(d):
    text = (first(d.get("general.webDescription")) or "") + " " + (d.get("general.productName") or "")
    for tok in INTERFACE_TOKENS:
        if tok.lower() in text.lower():
            return tok
    return (d.get("general.productName") or "").strip() or None

def map_family(d):
    """Return (family, extra_dict) or (None, reason)."""
    tax = first(d.get("general.taxonomyPathValues")) or ""
    leaf = tax.split(">")[-1].strip().lower()
    full = tax.lower()
    def has(*xs): return any(x in leaf for x in xs)
    # accessories / non-connector items -> not mappable
    if has("backshell", "accessor", "contact", "cage", "gland", "tool", "cover"):
        return None, "non-connector accessory (no mappable connector family)"
    if has("rf", "coax"):
        imp = num(d.get("electrical.impedance_int")) or num(d.get("electrical.impedance"))
        if imp is None:
            return None, "rf connector without characteristicImpedance"
        return "rf", {"characteristicImpedance": imp}
    if has("ffc", "fpc"):
        return "fpcFfc", {}
    if has("card edge", "memory module"):
        return "cardEdge", {}
    if has("circular"):
        return "circular", {}
    if has("terminal block", "din rail", "barrier"):
        return "terminalBlock", {}
    if has("board-to-board", "backplane", "mezzanine"):
        return "boardToBoard", {}
    if has("header", "receptacle", "socket"):
        return "pinHeaderSocket", {}
    if has("housing", "crimp", "quick disconnect", "ring", "spade", "splice", "terminal"):
        return "wireToBoard", {}
    if has("d-sub", "i/o", "modular", "sim", "high-speed"):
        std = interface_standard(d)
        if not std:
            return None, "dataInterface connector without interfaceStandard"
        return "dataInterface", {"interfaceStandard": std}
    if has("power", "heavy-duty"):
        return "power", {}
    # fall through by path keywords
    if "board-to-board" in full or "backplane" in full:
        return "boardToBoard", {}
    if "pcb" in full:
        return "pinHeaderSocket", {}
    return None, f"unmapped connector category: {tax}"

STATUS = {"active": "production", "obsolete": "obsolete",
          "not recommended": "nrnd", "end of life": "obsolete"}

def temp_range(d):
    t = first(d.get("physical.temperatureRangeOperating")) or ""
    m = re.findall(r"[-+]?\d+", t)
    if len(m) >= 2:
        return {"minimum": float(m[0]), "maximum": float(m[1])}
    return None

def convert(d):
    pn = (first(d.get("materialMaster.productNumberDisplay")) or d.get("id") or "").strip()
    if not pn:
        return None, ["partNumber"]
    pol = mating_polarity(d)
    cur = num(d.get("electrical.currentMaximumPerContact_int")) or num(d.get("electrical.currentMaximumPerContact"))
    volt = num(d.get("electrical.voltageMaximum_int")) or num(d.get("electrical.voltageMaximum"))
    family, extra = map_family(d)

    # CONAS (relaxed 2026-06-24): required = partNumber + family + ratedCurrentPerContact.
    # matingPolarity and ratedVoltage are optional (emitted when available).
    missing = []
    if cur is None:
        missing.append("ratedCurrentPerContact")
    if family is None:
        missing.append(extra)  # reason string

    part = {"partNumber": pn}
    if pol:
        part["matingPolarity"] = pol
    desc = first(d.get("general.webDescription"))
    if desc:
        part["description"] = desc[:1000]
    series = d.get("general.productName")
    if series:
        part["series"] = series

    electrical = {}
    if cur is not None:
        electrical["ratedCurrentPerContact"] = cur
    if volt is not None:
        electrical["ratedVoltage"] = volt

    mechanical = {}
    pos = num(d.get("physical.circuitsMaximum_int"))
    if pos is not None and pos >= 1:
        mechanical["positions"] = int(pos)

    di = {"part": part, "electrical": electrical, "mechanical": mechanical}
    if family is not None:
        fd = {"family": family}
        fd.update(extra)
        di["familyDetails"] = fd
    tr = temp_range(d)
    if tr:
        di["environmental"] = {"operatingTemperature": tr}

    status = STATUS.get((first(d.get("materialMaster.productStatus")) or "active").lower(), "production")
    mi = {"name": "Molex", "reference": pn, "status": status, "datasheetInfo": di}
    if series:
        mi["family"] = series
    if desc:
        mi["description"] = desc[:1000]
    pdf = first(d.get("engineeringDocs.salesDrawingPdf"))
    if pdf and str(pdf).startswith("http"):
        mi["datasheetUrl"] = pdf
    rec = {"connector": {"manufacturerInfo": mi}}
    return rec, missing

def main():
    seen = set()
    main_recs, incomplete = [], []
    n = dup = 0
    for f in sorted(glob.glob(f"{SRC}/page_*.json")):
        for d in json.load(open(f))["product"]["response"]["docs"]:
            n += 1
            pn = (first(d.get("materialMaster.productNumberDisplay")) or d.get("id") or "").strip()
            if pn in seen:
                dup += 1
                continue
            seen.add(pn)
            rec, missing = convert(d)
            if rec is None:
                continue
            if missing:
                rec["quarantineReason"] = "incomplete Molex data; missing: " + "; ".join(map(str, missing)) + " (2026-06-24)"
                incomplete.append(rec)
            else:
                main_recs.append(rec)
    for name, recs in [("connectors.main", main_recs), ("connectors.incomplete", incomplete)]:
        with open(f"{OUT}/{name}.ndjson", "w") as fo:
            for r in recs:
                fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"rows": n, "dup_skipped": dup, "main": len(main_recs),
                      "incomplete": len(incomplete)}, indent=2))

if __name__ == "__main__":
    main()
