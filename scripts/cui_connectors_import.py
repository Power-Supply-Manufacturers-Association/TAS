#!/usr/bin/env python3
"""CUI Devices / Same Sky interconnect rows (/tmp/cui/rows.jsonl) -> CONAS connector NDJSON.

Source: GET https://www.sameskydevices.com/catalog/interconnect/<group>/<leaf>
(server-rendered parametric table; see scripts/cui_fetch.py).  Every value emitted is a
cell of that table for that exact part; empty cells ("--") are omitted.  SI base units
(the vendor labels its columns mm / A / V / AWG / °C, which is what makes this safe).
"""
import json
import os
import re

SRC = "/tmp/cui/rows.jsonl"
OUT = "/home/alf/PSMA/TAS/staging/cui"
TODAY = "2026-07-31"
PROV = [{"source": "manufacturerParametric",
         "sourceName": "CUI Devices / Same Sky catalogue (sameskydevices.com/catalog/interconnect)",
         "sourceUrl": "https://www.sameskydevices.com/catalog/interconnect",
         "retrievedDate": TODAY}]

LEAF_FAMILY = {
    "ac-power-cords": "wireToWire",
    "circular-cable-assemblies": "circular",
    "usb-cables": "dataInterface",
    "audio-connectors": "dataInterface",
    "circular-connectors": "circular",
    "dc-power-connectors": "power",
    "din-connectors": "circular",
    "hdmi-connectors": "dataInterface",
    "memory-card-connectors": "dataInterface",
    "modular-connectors": "dataInterface",
    "rca-connectors": "dataInterface",
    "rectangular-connectors": "pinHeaderSocket",
    "terminal-blocks": "terminalBlock",
    "usb-connectors": "dataInterface",
}
SKIP_LEAF = {"pcb-pins": "single PCB terminal pin, not a connector",
             "pogo-pins": "spring-loaded contact, no CONAS connector family"}

MOUNT = {"Through Hole": "tht", "Surface Mount": "smt", "Mid Mount SMT": "smt",
         "Surface Mount, Anchor Pins": "smt", "Panel Mount": "panel",
         "Free Hanging": "cable", "Cable": "cable", "Cable - Modular": "cable",
         "Cable - Overmold": "cable", "Press Fit": "pressFit"}
ORIENT = {"Vertical": "vertical", "Straight": "vertical", "Horizontal": "horizontal",
          "Right Angled": "rightAngle", "Right Angle": "rightAngle"}
POLARITY = {"Jack": "female", "Receptacle": "female", "Female Pin Header": "female",
            "Socket": "female", "Plug": "male", "Male Pin Header": "male", "Pin": "male"}
CURRENT_KEYS = ("Current Rating (A)", "IEC Current Rating (A)", "UL Current Rating (A)")
VOLTAGE_KEYS = ("Voltage Rating (Vdc)", "Voltage Rating (Vac)", "IEC Voltage Rating (Vdc)",
                "UL Voltage Rating (Vdc)", "Voltage Rating")
POSITION_KEYS = ("Number of Positions", "Number of Contacts", "Number of Pins",
                 "Number of Conductors", "Connector 1 Positions")


def f(v):
    if not v or v in ("--", "-"):
        return None
    m = re.match(r"\s*([\d.]+)", str(v))
    return float(m.group(1)) if m else None


def lowest(a, keys):
    """Worst case across the ratings the vendor publishes (IEC vs UL, ac vs dc)."""
    vals = [f(a.get(k)) for k in keys]
    vals = [v for v in vals if v is not None and v > 0]
    return min(vals) if vals else None


def convert(row):
    leaf = row["catalogPath"].split("/")[-1]
    if leaf in SKIP_LEAF:
        return None, None, SKIP_LEAF[leaf]
    a = {k: v for k, v in row["attrs"].items() if v not in ("--", "-")}
    fam = LEAF_FAMILY.get(leaf)
    pn = row["partNumber"]

    part = {"partNumber": pn}
    desc = ", ".join(x for x in (a.get("Connector Type"), a.get("Connector Style"),
                                 a.get("Terminal Block Style"), a.get("Cable Style")) if x)
    if desc:
        part["description"] = desc[:1000]
    if a.get("Series"):
        part["series"] = a["Series"]
    pol = POLARITY.get(a.get("Connector Type"))
    if pol:
        part["matingPolarity"] = pol

    electrical = {}
    cur = lowest(a, CURRENT_KEYS)
    if cur is not None:
        electrical["ratedCurrentPerContact"] = cur
    vol = lowest(a, VOLTAGE_KEYS)
    if vol is not None:
        electrical["ratedVoltage"] = vol

    mech = {}
    for k in POSITION_KEYS:
        n = f(a.get(k))
        if n and n >= 1:
            mech["positions"] = int(n)
            break
    if "positions" not in mech:
        m = re.match(r"(\d+)P(\d+)C", a.get("Number of Positions/Contacts", ""))
        if m:
            mech["positions"] = int(m.group(2))
    n = f(a.get("Number of Rows"))
    if n and n >= 1:
        mech["rows"] = int(n)
    p = f(a.get("Pitch (mm)"))
    if p:
        mech["pitch"] = p / 1000.0
    rp = f(a.get("Row Spacing (mm)"))
    if rp:
        mech["rowPitch"] = rp / 1000.0
    ms = MOUNT.get(a.get("Mounting Style") or a.get("Connector 1 Mounting Style"))
    if ms:
        mech["mountingStyle"] = ms
    o = ORIENT.get(a.get("Orientation") or a.get("Connector 1 Orientation"))
    if o:
        mech["orientation"] = o
    for src, dst in (("Length (mm)", "length"), ("Width (mm)", "width"),
                     ("Height (mm)", "height")):
        d = f(a.get(src))
        if d:
            mech[dst] = {"nominal": d / 1000.0}
    mc = f(a.get("Mating Cycles"))
    if mc:
        mech["matingCycles"] = int(mc)

    env = {}
    m = re.match(r"\s*(-?\d+)\s*~\s*\+?(-?\d+)", a.get("Operating Temp Range (°C)", ""))
    if m:
        env["operatingTemperature"] = {"minimum": float(m.group(1)), "maximum": float(m.group(2))}

    shielded = {"Yes": True, "No": False}.get(a.get("Shielding"))
    fd = None
    if fam:
        fd = {"family": fam}
        if fam == "dataInterface":
            std = (a.get("USB Standard") or a.get("HDMI Type") or a.get("Compatible Card")
                   or a.get("Number of Positions/Contacts") or a.get("Audio Standard (mm)")
                   or a.get("DIN Standard"))
            if leaf == "usb-connectors" and a.get("USB Type"):
                std = "USB " + a["USB Type"]
            elif leaf == "usb-cables" and a.get("USB Standard"):
                std = "USB " + a["USB Standard"]
            elif leaf == "rca-connectors":
                std = "RCA"
            elif leaf == "hdmi-connectors" and a.get("HDMI Type"):
                std = "HDMI " + a["HDMI Type"]
            elif leaf == "audio-connectors" and a.get("Audio Standard (mm)"):
                std = a["Audio Standard (mm)"] + " mm audio jack"
            if not std:
                return {"connector": {"manufacturerInfo": {}}}, None, \
                    "data-interface connector with no published interface standard"
            fd["interfaceStandard"] = str(std)[:80]
            n = f(a.get("Number of Ports"))
            if n and n >= 1:
                fd["ports"] = int(n)
            if shielded is not None:
                fd["shielded"] = shielded
            im = a.get("Integrated Magnetics")
            if im in ("Yes", "No"):
                fd["integratedMagnetics"] = im == "Yes"
            led = a.get("LED")
            if led in ("Yes", "No"):
                fd["integratedLeds"] = led == "Yes"
        elif fam == "circular":
            if a.get("Connector Size"):
                fd["shellSize"] = a["Connector Size"][:40]
            if a.get("Coding") or a.get("Connector 1 Coding"):
                fd["coding"] = (a.get("Coding") or a["Connector 1 Coding"])[:20]
            if shielded is not None:
                fd["shielded"] = shielded
        elif fam == "terminalBlock":
            n = f(a.get("Number of Levels"))
            if n and n >= 1:
                fd["levels"] = int(n)
            if a.get("Terminal Block Style") == "Pluggable":
                fd["pluggable"] = True
            wg = a.get("Wire Gauge (AWG)")
            if wg:
                nums = [float(x) for x in re.findall(r"\d+", wg)]
                if len(nums) == 2:
                    fd["wireGaugeRange"] = {"minimumAwg": min(nums), "maximumAwg": max(nums)}
                elif len(nums) == 1:
                    fd["wireGaugeRange"] = {"minimumAwg": nums[0], "maximumAwg": nums[0]}

    di = {"part": part, "electrical": electrical, "mechanical": mech, "provenance": PROV}
    if env:
        di["environmental"] = env
    if fd:
        di["familyDetails"] = fd

    mi = {"name": "CUI Devices", "reference": pn, "status": "production", "datasheetInfo": di}
    if desc:
        mi["description"] = desc[:1000]
    if row.get("datasheet"):
        url = row["datasheet"]
        mi["datasheetUrl"] = url if url.startswith("http") else "https://www.sameskydevices.com" + url

    missing = []
    if fam is None:
        missing.append(f"no CONAS family mapped for CUI catalogue leaf {leaf!r}")
    elif fam != "rf" and "ratedCurrentPerContact" not in electrical:
        missing.append("no current rating published in the CUI parametric table")
    return {"connector": {"manufacturerInfo": mi}}, missing, None


def main():
    os.makedirs(OUT, exist_ok=True)
    skipped = {}
    n = n_good = n_inc = n_skip = 0
    with open(f"{OUT}/records.ndjson", "w", encoding="utf-8") as good, \
            open(f"{OUT}/incomplete.ndjson", "w", encoding="utf-8") as inc:
        for line in open(SRC, encoding="utf-8"):
            row = json.loads(line)
            n += 1
            rec, missing, reason = convert(row)
            if rec is None:
                n_skip += 1
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            if reason:
                missing = [reason]
            if missing:
                rec["quarantineReason"] = ("incomplete CUI data; " + "; ".join(missing)
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
