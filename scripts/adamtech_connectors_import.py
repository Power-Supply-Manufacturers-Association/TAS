#!/usr/bin/env python3
"""Adam Tech leaf-category listing rows (/tmp/adamtech/rows2.jsonl) -> CONAS connector NDJSON.

Source: GET https://app.adam-tech.com/products/index/category:<id>[/page:<n>]
(leaf ids discovered by scripts/adamtech_probe.py; see scripts/adamtech_crawl_leaves.py).

Two hard limits of the Adam Tech web catalogue, both handled by omission, never by guessing:
  * 39 % of listing rows are ORDERING TEMPLATES ("2PH1-XX-TA", XX = position count).
    A template is not an orderable part number and its position count is not published,
    so templates are dropped (counted in skipped.json), not expanded.
  * Adam Tech publishes a current rating for only ~360 of its 4 680 concrete parts.
    CONAS requires electrical.ratedCurrentPerContact for every family except rf, so the
    rest go to incomplete.ndjson with that reason rather than getting an invented value.
"""
import json
import os
import re
from html import unescape

SRC = "/tmp/adamtech/rows2.jsonl"
OUT = "/home/alf/PSMA/TAS/staging/adamtech"
TODAY = "2026-07-31"
PROV = [{"source": "manufacturerParametric",
         "sourceName": "Adam Tech product listings (app.adam-tech.com/products/index/category:<id>)",
         "sourceUrl": "https://app.adam-tech.com/products",
         "retrievedDate": TODAY}]

# leaf category -> (CONAS family, interfaceStandard for dataInterface)
CATEGORY_FAMILY = {
    "Radio Frequency Connectors": ("rf", None),
    "RF Cables": ("rf", None),
    "FAKRA Connectors": ("rf", None),
    "FAKRA Cables": ("rf", None),
    "Mini FAKRA Connectors and Cable Assemblies": ("rf", None),
    "D-Sub Connectors": ("dataInterface", "D-Sub"),
    "Modular Jacks - Single Port": ("dataInterface", "RJ45"),
    "Modular Jacks - Multi Port": ("dataInterface", "RJ45"),
    "Modular Jacks with Integrated Magnetics": ("dataInterface", "RJ45"),
    "Modular Jacks - Keystone Style": ("dataInterface", "RJ45"),
    "Modular Jacks with Wire Leads": ("dataInterface", "RJ45"),
    "Modular Jacks with USB": ("dataInterface", "RJ45"),
    "Modular Plugs": ("dataInterface", "RJ45"),
    "RJ45 Couplers & Adapters": ("dataInterface", "RJ45"),
    "RJ45 Cable Assemblies (Ethernet)": ("dataInterface", "RJ45"),
    "High Speed Jacks - Capable of 1GB-10GB": ("dataInterface", "RJ45"),
    "High Speed Modular Twisted-Pair Data (HT-X Series)": ("dataInterface", "RJ45"),
    "USB": ("dataInterface", "USB"),
    "Additional USB": ("dataInterface", "USB"),
    "USB Cable Assemblies": ("dataInterface", "USB"),
    "HSecurLinC USB Latching": ("dataInterface", "USB"),
    "HDMI Connectors": ("dataInterface", "HDMI"),
    "HDMI Cable Assemblies": ("dataInterface", "HDMI"),
    "DisplayPort": ("dataInterface", "DisplayPort"),
    "DisplayPort Cables": ("dataInterface", "DisplayPort"),
    "DVI Connectors": ("dataInterface", "DVI"),
    "SATA Connectors": ("dataInterface", "SATA"),
    "SFP": ("dataInterface", "SFP"),
    "QSFP": ("dataInterface", "QSFP"),
    "Fiber Optic Adapters": ("dataInterface", "Fiber Optic"),
    "Audio Jacks & Plugs": ("dataInterface", "Audio Jack"),
    "Pin Headers": ("pinHeaderSocket", None),
    "Receptacle Strips": ("pinHeaderSocket", None),
    "Shrouded Headers": ("pinHeaderSocket", None),
    "Shrouded Headers with Latches": ("pinHeaderSocket", None),
    "Chip Carrier Sockets": ("pinHeaderSocket", None),
    "Board to Board & High Speed Board to Board": ("boardToBoard", None),
    "DIN 41612 Connectors": ("boardToBoard", None),
    "Card Edge Connectors": ("cardEdge", None),
    "Memory Connectors & Sockets": ("cardEdge", None),
    "Miniature Disconnecting Headers": ("wireToBoard", None),
    "Miniature Disconnecting Housings": ("wireToBoard", None),
    "Disconnecting Wire to Boards Headers": ("wireToBoard", None),
    "Disconnecting Wire to Board Housings": ("wireToBoard", None),
    "Wire to Board Power Headers": ("wireToBoard", None),
    "Wire to Board Power Housings": ("wireToBoard", None),
    "IDC Connectors": ("wireToBoard", None),
    "Wire to Wire Power Housings": ("wireToWire", None),
    "Secur-Seal Wire-to-Wire Connectors": ("wireToWire", None),
    "Terminal Blocks": ("terminalBlock", None),
    "Euro Blocks": ("terminalBlock", None),
    "DIN Rail Terminal Blocks & Accessories": ("terminalBlock", None),
    "Flex Circuit Connectors": ("fpcFfc", None),
    "ZIF Flex Circuit Connectors (Zero Insertion Force)": ("fpcFfc", None),
    "Non-ZIF Flex Circuit Connectors": ("fpcFfc", None),
    "Flat Flex (FFC) Cables": ("fpcFfc", None),
    "Circular Industrial Connectors - Waterproof, Ruggedized & High Current": ("circular", None),
    "Circular Styles": ("circular", None),
    "Circular DIN Connectors": ("circular", None),
    "Mini DIN Connectors": ("circular", None),
    "M8 & M12 Styles": ("circular", None),
    "Push Pull Connectors": ("circular", None),
    "Sensor Cables": ("circular", None),
    "AC Inlets & Outlets": ("acInlet", None),
    "DC Power Jacks & Plugs": ("power", None),
    "Energy Storage Connectors": ("power", None),
    "High Current Terminals (HCT Series)": ("power", None),
    "Waterproof I/O, Audio & DC Power": ("power", None),
}
# Categories that are not connectors at all (converters, switches, heat sinks, ...).
NOT_CONNECTORS = re.compile(
    r"Converter|Power Supplies|Heat Sink|Power Line Filter|Fuse|Switch|Antenna|"
    r"Electric Vehicle|Crimp Terminal|Shunt|Cable Accessories|Terminals & Stampings|"
    r"Spring Loaded|Spring Connectors|Standoff|Tool", re.I)

CURRENT_KEYS = ("Current Rating", "Current Rating Per Pin", "Current (Amps)", "Amp", "Rating")
VOLTAGE_KEYS = ("Voltage Rating", "Voltage")
MOUNT = {"Through Hole": "tht", "Thru-Hole": "tht", "Thru Hole": "tht", "THT": "tht",
         "Surface Mount": "smt", "SMT": "smt", "Press Fit": "pressFit", "Panel Mount": "panel"}
ORIENT = {"Vertical": "vertical", "Straight": "vertical", "Right Angle": "rightAngle",
          "Right-Angle": "rightAngle", "Horizontal": "horizontal"}


def num(v, unit):
    if not v:
        return None
    m = re.search(r"([\d.]+)\s*" + unit, str(v), re.I)
    return float(m.group(1)) if m else None


def pitch_m(v):
    """'.100\" [2.54mm]' / '2.00mm' / '0.100\"' -> metres, from the mm figure Adam Tech prints."""
    if not v:
        return None
    m = re.search(r"([\d.]+)\s*mm", str(v))
    if m:
        return float(m.group(1)) / 1000.0
    m = re.search(r"([\d.]+)\s*\"", str(v))
    return float(m.group(1)) * 0.0254 if m else None


def first(attrs, keys):
    for k in keys:
        if attrs.get(k):
            return attrs[k]
    return None


def convert(row):
    cat = unescape(row["category"])
    a = {unescape(k): unescape(v) for k, v in row["attrs"].items()}
    pn = row["partNumber"]
    if "XX" in pn:
        return None, None, "ordering template, not an orderable part number"
    if NOT_CONNECTORS.search(cat):
        return None, None, f"not a connector category: {cat}"
    fam_iface = CATEGORY_FAMILY.get(cat)

    part = {"partNumber": pn}
    desc = ", ".join(x for x in (a.get("Type"), a.get("Product Type"), a.get("Style"),
                                 a.get("Description")) if x)
    if desc:
        part["description"] = desc[:1000]
    g = (a.get("Gender") or "").lower()
    if g in ("male", "plug", "male/plug"):
        part["matingPolarity"] = "male"
    elif g in ("female", "receptacle", "jack", "female/receptacle"):
        part["matingPolarity"] = "female"

    electrical = {}
    cur = num(first(a, CURRENT_KEYS), r"(?:A\b|Amp)")
    if cur is not None:
        electrical["ratedCurrentPerContact"] = cur
    vol = num(first(a, VOLTAGE_KEYS), r"V")
    if vol is not None:
        electrical["ratedVoltage"] = vol

    mech = {}
    pos = a.get("Number of Positions/Contacts") or a.get("Positions") or a.get("Pin Count")
    if pos:
        m = re.fullmatch(r"\s*(\d+)\s*", str(pos))
        if m and int(m.group(1)) >= 1:
            mech["positions"] = int(m.group(1))
    rws = a.get("No. of Rows") or a.get("Rows")
    if rws:
        m = re.fullmatch(r"\s*(\d+)\s*", str(rws))
        if m and int(m.group(1)) >= 1:
            mech["rows"] = int(m.group(1))
    p = pitch_m(a.get("Pitch") or a.get("Centerline"))
    if p:
        mech["pitch"] = p
    o = ORIENT.get((a.get("Mounting Orientation") or a.get("Orientation") or "").strip())
    if o:
        mech["orientation"] = o
    ms = MOUNT.get((a.get("Mounting") or a.get("Termination") or "").strip())
    if ms:
        mech["mountingStyle"] = ms

    fd = None
    if fam_iface:
        fam, iface = fam_iface
        fd = {"family": fam}
        if fam == "dataInterface":
            fd["interfaceStandard"] = iface
            sh = (a.get("Shielding") or "").lower()
            if sh in ("shielded", "yes"):
                fd["shielded"] = True
            elif sh in ("unshielded", "no"):
                fd["shielded"] = False
        elif fam == "rf":
            imp = num(a.get("Impedence") or a.get("Impedance"), r"ohm")
            if imp is None:
                return ({"connector": {"manufacturerInfo": {}}}, None,
                        "rf connector with no published characteristicImpedance")
            fd["characteristicImpedance"] = imp
            if a.get("Type"):
                fd["interface"] = a["Type"][:60]
    else:
        fam = None

    di = {"part": part, "electrical": electrical, "mechanical": mech, "provenance": PROV}
    if fd:
        di["familyDetails"] = fd

    mi = {"name": "Adam Tech", "reference": pn, "status": "production", "datasheetInfo": di}
    if desc:
        mi["description"] = desc[:1000]
    if row.get("datasheet"):
        mi["datasheetUrl"] = row["datasheet"]

    missing = []
    if fam is None:
        missing.append(f"no CONAS family mapped for Adam Tech category {cat!r}")
    elif fam != "rf" and "ratedCurrentPerContact" not in electrical:
        missing.append("Adam Tech publishes no current rating for this part")
    return {"connector": {"manufacturerInfo": mi}}, missing, None


def main():
    os.makedirs(OUT, exist_ok=True)
    seen = set()
    skipped = {}
    n = n_good = n_inc = n_skip = 0
    with open(f"{OUT}/records.ndjson", "w", encoding="utf-8") as good, \
            open(f"{OUT}/incomplete.ndjson", "w", encoding="utf-8") as inc:
        for line in open(SRC, encoding="utf-8"):
            row = json.loads(line)
            if row["partNumber"] in seen:
                continue
            seen.add(row["partNumber"])
            n += 1
            rec, missing, reason = convert(row)
            if reason and rec is None:
                n_skip += 1
                skipped[reason.split(":")[0]] = skipped.get(reason.split(":")[0], 0) + 1
                continue
            if reason:                       # rf without impedance -> incomplete, keep data
                missing = [reason]
            if missing:
                rec["quarantineReason"] = ("incomplete Adam Tech data; " + "; ".join(missing)
                                           + f" ({TODAY})")
                inc.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_inc += 1
            else:
                good.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_good += 1
    json.dump({"skipped": n_skip, "reasons": skipped}, open(f"{OUT}/skipped.json", "w"), indent=1)
    print(json.dumps({"rows": n, "records": n_good, "incomplete": n_inc, "skipped": n_skip}))


if __name__ == "__main__":
    main()
