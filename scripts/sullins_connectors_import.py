#!/usr/bin/env python3
"""Sullins parametric grid rows (/tmp/sullins/rows.jsonl) -> CONAS connector NDJSON.

Source: POST https://www.sullinscorp.com/products/?raw  body `filters=category:<id>&more=<offset>`
(see scripts/sullins_fetch.py).  Every value emitted is printed in Sullins' own parametric
table for that exact part; absent cells are omitted, never filled in.  SI base units.

Outputs (staging/sullins/):
  records.ndjson     schema-valid CONAS records
  incomplete.ndjson  missing a schema-required value (quarantineReason)
  skipped.json       counts of non-connector items (tools, keys, card guides, ...)
"""
import json
import os
import re

SRC = "/tmp/sullins/rows.jsonl"
OUT = "/home/alf/PSMA/TAS/staging/sullins"
TODAY = "2026-07-31"
PROV = [{"source": "manufacturerParametric",
         "sourceName": "Sullins product grid (sullinscorp.com/products/?raw)",
         "sourceUrl": "https://www.sullinscorp.com/products/",
         "retrievedDate": TODAY}]

# (category, Type) -> CONAS family.  None = not a connector in CONAS terms.
FAMILY = {
    ("Headers", "Pin Header"): "pinHeaderSocket",
    ("Headers", "Box Header"): "pinHeaderSocket",
    ("Headers", "SIP"): "pinHeaderSocket",
    ("Headers", "SMD"): "pinHeaderSocket",
    ("Headers", "IDC"): "wireToBoard",
    ("Headers", "Wafer"): "wireToBoard",
    ("Headers", "Wafer Housing"): "wireToBoard",
    ("Card Edge", None): "cardEdge",
}
SKIP_TYPES = {"Crimp Terminal", "Crimp Tool", "Polarizing Key", "Card Guide", "Hood",
              "Jumper", "Axial", "TO", "MELF1", "Clear"}
POLARITY = {"Male": "male", "Female": "female", "Dual Female": "female"}
ORIENT = {"Straight": "vertical", "Right Angle": "rightAngle", "Straddle": "edge"}
MOUNT = {"Thru Hole": "tht", "Surface Mount": "smt", "Press Fit": "pressFit",
         "Wire Wrap": "tht", "IDC": "cable", "IDC, Thru Hole": "tht"}
LOCK = {"Metal Board Lock": "boardLock", "Threaded": "screwLock",
        "Side Hole, Threaded": "screwLock"}


def mm(v):
    if not v:
        return None
    m = re.fullmatch(r"\s*([\d.]+)\s*mm\s*", str(v))
    return float(m.group(1)) / 1000.0 if m else None


def amps(v):
    if not v:
        return None
    m = re.match(r"\s*([\d.]+)\s*A\b", str(v))
    return float(m.group(1)) if m else None


def volts(v):
    if not v:
        return None
    m = re.match(r"\s*([\d.]+)\s*V", str(v))
    return float(m.group(1)) if m else None


TEMP = re.compile(r"(-?\d+)\s*to\s*\+?(-?\d+)\s*°C")


def family_of(cat, typ):
    if cat == "Card Edge":
        return "cardEdge"
    return FAMILY.get((cat, typ))


def convert(row):
    a = row["attrs"]
    cat, typ = row["category"], a.get("Type")
    if typ in SKIP_TYPES or cat in ("Test Sockets", "Accessories"):
        return None, None
    fam = family_of(cat, typ)

    part = {"partNumber": row["partNumber"]}
    pol = POLARITY.get(a.get("Gender"))
    if pol:
        part["matingPolarity"] = pol
    desc = ", ".join(x for x in (typ, a.get("Package"), a.get("Features")) if x)
    if desc:
        part["description"] = desc[:1000]

    electrical = {}
    cur = amps(a.get("Current Rating"))
    if cur is not None:
        electrical["ratedCurrentPerContact"] = cur
    v = volts(a.get("Operating Voltage"))
    if v is not None:
        electrical["ratedVoltage"] = v

    mech = {}
    pc = a.get("Positions/Contacts")
    if pc and "/" in pc:
        try:                                  # "36/72" = 36 positions per row / 72 contacts
            mech["positions"] = int(pc.split("/")[1])
        except ValueError:
            pass
    try:
        rows = int(a.get("Rows", ""))
        if rows >= 1:
            mech["rows"] = rows
    except ValueError:
        pass
    p = mm(a.get("Pitch [mm]"))
    if p:
        mech["pitch"] = p
    for src, dst in (("Height [mm]", "height"), ("Width [mm]", "width")):
        d = mm(a.get(src))
        if d:
            mech[dst] = {"nominal": d}
    o = ORIENT.get(a.get("Orientation"))
    if o:
        mech["orientation"] = o
    ms = MOUNT.get(a.get("Termination Method"))
    if ms:
        mech["mountingStyle"] = ms
    lk = LOCK.get(a.get("Mounting Feature"))
    if lk:
        mech["locking"] = lk

    env = {}
    m = TEMP.search(a.get("Operating Temperature") or "")
    if m:
        env["operatingTemperature"] = {"minimum": float(m.group(1)), "maximum": float(m.group(2))}

    fd = None
    if fam == "pinHeaderSocket":
        fd = {"family": fam}
        tl = mm(a.get("Tail Length [mm]"))
        if tl:
            fd["tailLength"] = tl
    elif fam == "wireToBoard":
        fd = {"family": fam}
        if a.get("Termination Method", "").startswith("IDC"):
            fd["termination"] = "idc"
    elif fam == "cardEdge":
        fd = {"family": fam}
        ct = mm(a.get("Mating Thickness [mm]"))
        if ct:
            fd["cardThickness"] = ct
    elif fam:
        fd = {"family": fam}

    di = {"part": part, "electrical": electrical, "mechanical": mech, "provenance": PROV}
    if env:
        di["environmental"] = env
    if fd:
        di["familyDetails"] = fd

    mi = {"name": "Sullins Connector Solutions", "reference": row["partNumber"],
          "status": "production", "datasheetInfo": di}
    if desc:
        mi["description"] = desc[:1000]
    if row.get("drawing"):
        mi["datasheetUrl"] = row["drawing"]

    missing = []
    if fam is None:
        missing.append(f"no CONAS family for Sullins category/type {cat!r}/{typ!r}")
    elif "ratedCurrentPerContact" not in electrical:
        missing.append(f"no current rating published (Current Rating={a.get('Current Rating')!r})")
    return {"connector": {"manufacturerInfo": mi}}, missing


def main():
    os.makedirs(OUT, exist_ok=True)
    seen = set()
    skipped = {}
    n = n_good = n_inc = n_skip = n_dup = 0
    with open(f"{OUT}/records.ndjson", "w", encoding="utf-8") as good, \
            open(f"{OUT}/incomplete.ndjson", "w", encoding="utf-8") as inc:
        for line in open(SRC, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            n += 1
            pn = row["partNumber"]
            if pn in seen:
                n_dup += 1
                continue
            seen.add(pn)
            rec, missing = convert(row)
            if rec is None:
                n_skip += 1
                k = f"{row['category']}/{row['attrs'].get('Type')}"
                skipped[k] = skipped.get(k, 0) + 1
                continue
            if missing:
                rec["quarantineReason"] = ("incomplete Sullins data; " + "; ".join(missing)
                                           + f" ({TODAY})")
                inc.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_inc += 1
            else:
                good.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_good += 1
            if n % 20000 == 0:
                print(f"  {n}: good={n_good} inc={n_inc} skip={n_skip} dup={n_dup}", flush=True)
    json.dump({"skipped_non_connector": n_skip, "by_type": skipped},
              open(f"{OUT}/skipped.json", "w"), indent=1)
    print(json.dumps({"rows": n, "unique": len(seen), "records": n_good,
                      "incomplete": n_inc, "skipped": n_skip, "dup": n_dup}))


if __name__ == "__main__":
    main()
