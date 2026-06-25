#!/usr/bin/env python3
"""Convert a Hirose (HRS) parametric-catalog CSV export -> CONAS connector NDJSON.

Source: Hirose product CSV (e.g. product_20260625175506.csv). Columns are SI-ish strings
("0.5 mm", "5.0 A", "AC 48.0 V", "40.0 mOhm Max.", "6.0 GHz", "-40 C"). Row 0 of the CSV
(after the header) is a human-readable label row and is skipped.

Maps to the CONAS connector discriminator {"connector": {...}}, all values in SI base units.
No fabrication: a part missing a schema-required field (a mappable family, rf characteristic
impedance, or ratedCurrentPerContact) is routed to staging/hirose/connectors.incomplete.ndjson
with a quarantineReason. Relaxed CONAS requires partNumber + familyDetails.family +
electrical.ratedCurrentPerContact.

Field map: code=HRS order code, name=Part No (the orderable partNumber).
"""
import csv, datetime, json, os, re, sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/hirose.csv"
OUT = "/home/alf/PSMA/TAS/staging/hirose"
os.makedirs(OUT, exist_ok=True)
TODAY = datetime.date.today().isoformat()


def num(s):
    if not s:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", s.replace(",", "."))
    return float(m.group()) if m else None


def metres(s):
    v = num(s)
    if v is None:
        return None
    return v * 1e-3 if "mm" in (s or "").lower() else v


def ohms(s):
    v = num(s)
    if v is None:
        return None
    sl = (s or "").lower()
    if "mΩ" in (s or "") or "mohm" in sl or "m ω" in sl:
        return v * 1e-3
    if "kΩ" in (s or "") or "kohm" in sl:
        return v * 1e3
    if "mΩ" in (s or "") or "megohm" in sl:  # rare
        return v
    return v


def hz(s):
    v = num(s)
    if v is None:
        return None
    sl = (s or "").lower()
    if "ghz" in sl:
        return v * 1e9
    if "mhz" in sl:
        return v * 1e6
    if "khz" in sl:
        return v * 1e3
    return v


# ---- family classification ------------------------------------------------
RF_RE = re.compile(
    r"(U\.FL|X\.FL|H\.FL|G\.FL|D\.FL|W\.FL|R\.FL|N\.FL|AMMC|MMCX|MCX|SMA|SMB|SMPM|SMP3|SMP|"
    r"BNC|RP-SMA|MS-?15[0-9]|MS-?14[0-9]|1\.0mm|1\.85mm|2\.4mm|2\.92mm|PL7|TNC|\bN\b)",
    re.I,
)
DATA_RE = re.compile(r"(USB|RJ-?45|RJ-?11|HDMI|DisplayPort|Ethernet|Type-C)", re.I)


def classify(row):
    """Return base family key (before required-field gating)."""
    g, s = row["genericType"], row["series_name"]
    if DATA_RE.search(g) or DATA_RE.search(s):
        return "dataInterface"
    if RF_RE.search(g) or RF_RE.search(s) or num(row["characteristicImpedance"]) is not None:
        return "rf"
    wtm = (row["wireTerminationMethod"] or "").strip().lower()
    if wtm == "screw":
        return "terminalBlock"
    if wtm in ("crimping", "soldering", "idc") or row["applicableWireType"].strip() \
            or row["applicableWireSizeAwgMax"].strip():
        return "wireToBoard"
    return "boardToBoard"


WIRE_TERM = {"crimping": "crimp", "idc": "idc", "soldering": "solderCup"}

STATUS = {
    "Sales Products": "production",
    "Scheduled to be discontinued": "nrnd",
    "Not recommemded for new design": "nrnd",  # (Hirose's spelling)
}


def polarity(row):
    ct = (row["connectorType"] or "").strip().lower()
    if ct in ("receptacle", "socket", "jack"):
        return "female"
    if ct in ("plug", "header"):
        return "male"
    return None


def data_interface_std(row):
    g = (row["genericType"] or "").strip()
    if g:
        # Hirose writes "USB Type-C" / "MicroUSB" / "RJ-45"
        return {"USB Type-C": "USB-C", "MicroUSB": "Micro-USB"}.get(g, g)
    return None


def convert(row):
    pn = (row["name"] or "").strip()  # Part No.
    if not pn:
        return None, ["partNumber"]
    fam = classify(row)
    cur = num(row["ratedCurrent"])
    vdc = num(row["ratedVoltageDc"])
    vac = num(row["ratedVoltageAc"])
    volt = max(v for v in (vdc, vac) if v is not None) if (vdc or vac) else None

    part = {"partNumber": pn}
    pol = polarity(row)
    if pol:
        part["matingPolarity"] = pol
    if row["series_name"].strip():
        part["series"] = row["series_name"].strip()

    e = {}
    if cur is not None:
        e["ratedCurrentPerContact"] = cur
    if volt is not None:
        e["ratedVoltage"] = volt
    if (v := ohms(row["contactResistance"])) is not None:
        e["contactResistance"] = {"maximum": round(v, 9)}
    if (v := num(row["withstandingVoltage"])) is not None:
        e["dielectricWithstandingVoltage"] = v

    mech = {}
    if (v := num(row["numberOfPositions"])) and v >= 1:
        mech["positions"] = int(v)
    if (v := num(row["numberOfRow"])) and v >= 1:
        mech["rows"] = int(v)
    if (v := metres(row["contactPitch"])) is not None and v > 0:
        mech["pitch"] = round(v, 9)
    ms = (row["mountingStyle"] or "").strip().upper()
    if ms == "SMT":
        mech["mountingStyle"] = "smt"
    elif ms in ("THT", "DIP"):
        mech["mountingStyle"] = "tht"
    elif "PRESS" in ms:
        mech["mountingStyle"] = "pressFit"
    if (v := num(row["matingUnmatingCycles"])) is not None and v >= 0:
        mech["matingCycles"] = int(v)

    di = {"part": part, "electrical": e, "mechanical": mech}

    env = {}
    tmin, tmax = num(row["operatingTemperatureMin"]), num(row["operatingTemperatureMax"])
    if tmin is not None and tmax is not None:
        env["operatingTemperature"] = {"minimum": tmin, "maximum": tmax}
    if env:
        di["environmental"] = env

    extra_missing = None
    if fam == "rf":
        imp = num(row["characteristicImpedance"])
        if imp is not None and imp > 0:
            fd = {"family": "rf", "characteristicImpedance": imp}
            iface = (row["genericType"] or row["series_name"] or "").strip()
            if iface:
                fd["interface"] = iface[:40]
            if (f := hz(row["frequencyMax"])) is not None and f > 0:
                fd["frequencyRange"] = {"maximum": f}
            if (v := num(row["vSWR"])) is not None and v >= 1:
                fd["maxVswr"] = v
            di["familyDetails"] = fd
        else:
            fam = None
            extra_missing = "rf connector without characteristicImpedance"
    elif fam == "dataInterface":
        std = data_interface_std(row)
        if std:
            di["familyDetails"] = {"family": "dataInterface", "interfaceStandard": std}
        else:
            fam = None
            extra_missing = "dataInterface without interfaceStandard"
    elif fam == "terminalBlock":
        di["familyDetails"] = {"family": "terminalBlock", "clampType": "screw"}
    elif fam == "wireToBoard":
        fd = {"family": "wireToBoard"}
        t = WIRE_TERM.get((row["wireTerminationMethod"] or "").strip().lower())
        if t:
            fd["termination"] = t
        di["familyDetails"] = fd
    elif fam == "boardToBoard":
        di["familyDetails"] = {"family": "boardToBoard"}

    di["provenance"] = [{
        "source": "manufacturerParametric",
        "sourceName": "Hirose product CSV",
        "retrievedDate": TODAY,
    }]

    mi = {"name": "Hirose Electric", "reference": pn,
          "status": STATUS.get(row["salesStatus"].strip(), "production"),
          "datasheetInfo": di}
    if row["code"].strip():
        mi["orderCode"] = row["code"].strip()

    rec = {"connector": {"manufacturerInfo": mi}}
    missing = []
    if cur is None:
        missing.append("ratedCurrentPerContact")
    if fam is None:
        missing.append(extra_missing)
    return rec, missing


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))[1:]  # drop label row
    mains, inc, seen = [], [], set()
    for row in rows:
        pn = (row["name"] or "").strip()
        if not pn or pn in seen:
            continue
        seen.add(pn)
        rec, missing = convert(row)
        if rec is None:
            continue
        if missing:
            rec["quarantineReason"] = (
                "incomplete Hirose connector; missing: " + "; ".join(missing) + f" ({TODAY})"
            )
            inc.append(rec)
        else:
            mains.append(rec)
    for nm, recs in [("connectors.main", mains), ("connectors.incomplete", inc)]:
        with open(f"{OUT}/{nm}.ndjson", "w") as fo:
            for r in recs:
                fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"rows": len(rows), "unique": len(seen),
                      "main": len(mains), "incomplete": len(inc)}))


if __name__ == "__main__":
    main()
