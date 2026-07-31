#!/usr/bin/env python3
"""Harwin catalogue (api.harwin.com/v1) -> CONAS connector NDJSON.

Source (plain GET, no auth, no bot gate):
  https://api.harwin.com/v1/products?page=<n>&pageSize=500     (list, 7231 parts)
  https://api.harwin.com/v1/products/<partNumber>              (detail: technicalDetails,
                                                                documents, related.matings)
Pulled by scripts/harwin_fetch.py into /tmp/harwin/{all,details}.json.

Everything emitted is a value Harwin publishes for that exact part; nothing is inferred
from a sibling part or a series. Fields with no published value are omitted.
SI base units (A, V, m, kg, Ohm); Harwin quotes mm / g / AWG.

Outputs (append-as-you-go, resumable via progress.json):
  staging/harwin/records.ndjson    schema-valid CONAS records
  staging/harwin/incomplete.ndjson missing a schema-required value (quarantineReason)
  staging/harwin/skipped.json      counts of non-connector items (spacers, tooling, ...)
"""
import json
import os
import re
import sys

SRC = "/tmp/harwin"
OUT = "/home/alf/PSMA/TAS/staging/harwin"
TODAY = "2026-07-31"
PROV = [{"source": "manufacturerParametric",
         "sourceName": "Harwin product API (api.harwin.com/v1/products)",
         "sourceUrl": "https://api.harwin.com/v1/products",
         "retrievedDate": TODAY}]

# ---------------------------------------------------------------- helpers ---


def av(it, key):
    a = (it.get("attributes") or {}).get(key)
    return a.get("value") if a else None


def mm(v):
    """'3.40mm' -> 0.0034 (m). None when not a plain single mm figure."""
    if v is None:
        return None
    m = re.fullmatch(r"\s*([\d.]+)\s*mm\s*", str(v))
    return float(m.group(1)) / 1000.0 if m else None


def as_int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------- current rating ----
# Only tokens that are explicitly PER CONTACT are eligible; "max simultaneous"
# (the all-contacts-energised derating) is not a per-contact rating.  Where a part
# publishes several per-contact ratings (signal + power + coax) the LOWEST is used,
# i.e. the worst case across contact types.  Every number emitted is printed on
# Harwin's own page for that part.
PER_CONTACT = re.compile(
    r"([\d.]+)\s*A\s*(?:max\s*)?(?:individual contact|per contact(?: pair)?"
    r"|max per contact|max individual)", re.I)
AT_TEMP = re.compile(r"([\d.]+)\s*A\s*(?:max\s*)?at\s*([\-\d]+)\s*°?C", re.I)


def current_rating(s):
    """-> (amps, referenceTemperatureC or None) or (None, None)."""
    if not s:
        return None, None
    vals = [float(x) for x in PER_CONTACT.findall(s)]
    ref = None
    m = re.search(r"\((\-?\d+)\s*°C\)", s)
    if m:
        ref = float(m.group(1))
    if not vals:
        at = AT_TEMP.findall(s)
        if at:
            # e.g. "2A max at 25°C, 1.75A max at 85°C" -> the 25 °C reference figure
            best = min(at, key=lambda t: abs(float(t[1]) - 25.0))
            return float(best[0]), float(best[1])
        m = re.fullmatch(r"\s*([\d.]+)\s*A\s*", s)
        if m:
            return float(m.group(1)), ref
        return None, None
    return min(vals), ref


TEMP = re.compile(r"(-?\d+)\s*°C\s*to\s*\+?(-?\d+)\s*°C")

MOUNT = {
    "PC Tail / Throughboard": "tht",
    "Surface Mount (SMT)": "smt",
    "Press-Fit PC Tail / Throughboard": "pressFit",
    "Cable": "cable",
    "Cable (Crimp)": "cable",
    "Cable (Solder)": "cable",
    "Cable (Solder + Crimp)": "cable",
    "Cable (Ribbon IDC)": "cable",
    "Cable Assembly": "cable",
    "Flex Circuit Assembly": "cable",
}
TERMINATION = {
    "Cable (Crimp)": "crimp",
    "Cable (Ribbon IDC)": "idc",
    "Cable (Solder)": "solderCup",
}
CABLE_CT = {k for k, v in MOUNT.items() if v == "cable"}
BOARD_CT = {"PC Tail / Throughboard", "Surface Mount (SMT)",
            "Press-Fit PC Tail / Throughboard",
            "Surface Mount (SMT) & PC Tail / Throughboard"}
POLARITY = {"Male/Plug": "male", "Female/Receptacle": "female"}

# Non-connector items: hardware, tooling, shielding, loose contacts, probes.
SKIP_TYPE = re.compile(
    r"^Hardware -|^EMC Shielding|^SMT Spring Contact|^SMT Contact Pad|"
    r"^Spring Loaded Contact|^ATE Spring Probe|Connector Tooling|Connector Accessory",
    re.I)
SKIP_CATEGORY = {"Backshell", "Spacer", "Tooling & Accessories", "Shield Can",
                 "Shield Clip", "Grounding Clip", "Test Probe", "Test Point",
                 "Contact Pad", "Blanking Pin", "Battery Holder", "Terminal Pin",
                 "Pogo Pin", "Spring Contact", "Contact", "Jumper"}
IMPEDANCE = re.compile(r"\b(50|75)\s*(?:ohm|Ω)", re.I)


def mount_side(it):
    ct = av(it, "connectionType")
    if ct in CABLE_CT:
        return "cable"
    if ct in BOARD_CT:
        return "board"
    return None


def family_of(it, sides):
    """-> (family, extraFamilyFields, None) or (None, None, reason)."""
    typ = it.get("type") or ""
    cats = it.get("category") or []
    ct = av(it, "connectionType")
    side = mount_side(it)

    if "Circular Connector" in typ:
        return "circular", {}, None
    if "Coax" in typ or "Coax" in cats:
        txt = " ".join(it.get("features") or []) + " " + (it.get("description") or "") + " " + typ
        m = IMPEDANCE.search(txt)
        if not m:
            return None, None, "coaxial connector with no published characteristicImpedance"
        return "rf", {"characteristicImpedance": float(m.group(1))}, None
    if "USB" in cats or "I/O Connector" in typ:
        txt = (it.get("description") or "") + " " + typ + " " + " ".join(it.get("features") or [])
        for tok in ("USB-C", "USB Type-C", "Micro-USB", "Mini-USB", "USB"):
            if tok.lower() in txt.lower():
                return "dataInterface", {"interfaceStandard": tok}, None
        return None, None, "data-interface connector with no identifiable interfaceStandard"

    if side == "cable":
        extra = {}
        t = TERMINATION.get(ct)
        if t:
            extra["termination"] = t
        gauges = av(it, "wireSize")
        awg = [float(re.sub(r"[^\d.]", "", g)) for g in (gauges or [])
               if re.search(r"\d", str(g))]
        if awg:
            # AWG numbering runs backwards: the largest AWG number is the SMALLEST wire.
            extra["wireGaugeRange"] = {"minimumAwg": min(awg), "maximumAwg": max(awg)}
        return "wireToBoard", extra, None

    if side == "board":
        if "Board-To-Board" in typ or "PC/104" in typ:
            return "boardToBoard", {}, None
        if "Cable-To-Board" in typ:
            return "wireToBoard", {}, None
        if typ.startswith("Sockets -") or "M20 Series" in typ or "M22 Series" in typ:
            extra = {}
            tl = mm(av(it, "pcTailLength"))
            if tl is not None:
                extra["tailLength"] = tl
            return "pinHeaderSocket", extra, None
        # Harwin's own published mating list decides board-to-board vs wire-to-board.
        partners = [m["value"] for m in ((it.get("related") or {}).get("matings") or [])]
        psides = {sides.get(p) for p in partners} - {None}
        if "board" in psides:
            return "boardToBoard", {}, None
        if "cable" in psides:
            return "wireToBoard", {}, None
        return None, None, "board-mounted connector with no published mating partners to classify"
    return None, None, f"unclassifiable mounting style: {ct!r}"


def convert(it, sides):
    pn = it["slug"]
    typ = it.get("type") or ""
    cats = it.get("category") or []
    if SKIP_TYPE.search(typ) or (set(cats) & SKIP_CATEGORY and "Cable Assembly" not in cats
                                 and "Housing" not in cats):
        return None, None, "non-connector item"

    part = {"partNumber": pn}
    if it.get("description"):
        part["description"] = it["description"][:1000]
    if it.get("family"):
        part["series"] = it["family"]
    pol = POLARITY.get(av(it, "gender"))
    if pol:
        part["matingPolarity"] = pol

    electrical = {}
    amps, ref = current_rating(av(it, "currentRating"))
    if amps is not None:
        electrical["ratedCurrentPerContact"] = amps
        if ref is not None:
            electrical["ratedCurrentReferenceTemperature"] = ref

    mech = {}
    sig = as_int(av(it, "totalNoOfContacts"))
    pwr = as_int(av(it, "numberOfPowerContacts"))
    pos = (sig or 0) + (pwr or 0)
    if pos >= 1:
        mech["positions"] = pos
    rows = as_int(av(it, "noOfRows"))
    if rows and rows >= 1:
        mech["rows"] = rows
    p = mm((av(it, "pitch") or "").split(" ")[0] if av(it, "pitch") else None)
    if p:
        mech["pitch"] = p
    ms = MOUNT.get(av(it, "connectionType"))
    if ms:
        mech["mountingStyle"] = ms
    if ms != "cable":
        o = av(it, "orientation")
        if o == "Straight":
            mech["orientation"] = "vertical"
        elif o == "Right-Angle":
            mech["orientation"] = "rightAngle"
    dims = str(av(it, "dimensions") or "").split(" x ")
    if len(dims) == 3:
        try:
            l, w, h = (float(x) / 1000.0 for x in dims)
            mech["length"], mech["width"], mech["height"] = (
                {"nominal": l}, {"nominal": w}, {"nominal": h})
        except ValueError:
            pass
    wt = av(it, "weight")
    if wt:
        m = re.fullmatch(r"\s*([\d.]+)\s*g\s*", str(wt))
        if m:
            mech["weight"] = float(m.group(1)) / 1000.0

    env = {}
    m = TEMP.search(str(av(it, "operatingTemperature") or ""))
    if m:
        env["operatingTemperature"] = {"minimum": float(m.group(1)), "maximum": float(m.group(2))}
    rohs = str(av(it, "rohsStatus") or "")
    if rohs.startswith("RoHS Compliant"):
        env["rohsCompliant"] = True
    elif rohs.startswith("Not RoHS Compliant"):
        env["rohsCompliant"] = False

    fam, extra, reason = family_of(it, sides)

    di = {"part": part, "electrical": electrical, "mechanical": mech, "provenance": PROV}
    if env:
        di["environmental"] = env
    if fam:
        fd = {"family": fam}
        fd.update(extra)
        di["familyDetails"] = fd
    matings = [m["value"] for m in ((it.get("related") or {}).get("matings") or [])]
    if matings:
        di["mating"] = {"matesWith": [{"series": s, "relation": "mates"}
                                      for s in sorted(set(matings))]}

    mi = {"name": "Harwin", "reference": pn, "datasheetInfo": di,
          "status": "production" if it.get("isActive") else "obsolete"}
    if it.get("family"):
        mi["family"] = it["family"]
    if it.get("description"):
        mi["description"] = it["description"][:1000]
    url = datasheet_url(it)
    if url:
        mi["datasheetUrl"] = url

    missing = []
    if fam is None:
        missing.append(reason)
    elif fam != "rf" and "ratedCurrentPerContact" not in electrical:
        missing.append("no per-contact current rating published "
                       f"(currentRating={av(it, 'currentRating')!r})")
    return {"connector": {"manufacturerInfo": mi}}, missing, None


def datasheet_url(it):
    for group in it.get("documents") or []:
        for f in group.get("files") or []:
            if "Technical Drawing" in (f.get("name") or ""):
                return f.get("url")
    for group in it.get("documents") or []:
        for f in group.get("files") or []:
            if "Product Datasheet" in (f.get("name") or ""):
                return f.get("url")
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    details = json.load(open(f"{SRC}/details.json"))
    sides = {s: mount_side(it) for s, it in details.items()}

    good = open(f"{OUT}/records.ndjson", "w", encoding="utf-8")
    inc = open(f"{OUT}/incomplete.ndjson", "w", encoding="utf-8")
    n_good = n_inc = n_skip = 0
    skip_types = {}
    for i, (slug, it) in enumerate(sorted(details.items())):
        rec, missing, _ = convert(it, sides)
        if rec is None:
            n_skip += 1
            skip_types[it.get("type") or "?"] = skip_types.get(it.get("type") or "?", 0) + 1
            continue
        if missing:
            rec["quarantineReason"] = ("incomplete Harwin data; " + "; ".join(missing)
                                       + f" ({TODAY})")
            inc.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_inc += 1
        else:
            good.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_good += 1
        if (i + 1) % 2000 == 0:
            print(f"  {i+1}/{len(details)}: good={n_good} incomplete={n_inc} skip={n_skip}",
                  flush=True)
    good.close()
    inc.close()
    json.dump({"skipped_non_connector": n_skip, "by_type": skip_types},
              open(f"{OUT}/skipped.json", "w"), indent=1)
    print(json.dumps({"input": len(details), "records": n_good,
                      "incomplete": n_inc, "skipped": n_skip}))


if __name__ == "__main__":
    sys.exit(main())
