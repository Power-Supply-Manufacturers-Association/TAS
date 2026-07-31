#!/usr/bin/env python3
"""Convert the Amphenol CS / ICC GraphQL dump (scripts/amphenol_cs_pull.py) -> CONAS NDJSON.

    python3 scripts/amphenol_cs_import.py vocab  <products.ndjson>     # observed taxonomy
    python3 scripts/amphenol_cs_import.py build  <products.ndjson> <out_dir>

No value is estimated, inferred from a sibling part, or defaulted: every emitted number is a
unit conversion of a string the vendor published for that exact part. Parts whose category
cannot be mapped to a CONAS family, or that lack a schema-required field (per-contact current
outside the rf family, interfaceStandard on dataInterface, characteristicImpedance on rf) are
written to cs.incomplete.ndjson with the reason instead of being invented.
"""
import collections, html, json, os, re, sys

RETRIEVED = "2026-07-30"
SITE = "https://www.amphenol-cs.com"
MEDIA = SITE + "/media/wysiwyg/files/"


def txt(v):
    if v is None:
        return ""
    return html.unescape(str(v)).replace(" ", " ").strip()


def strip_tags(v):
    return re.sub(r"<[^>]+>", "", txt(v)).strip()


def _f(s):
    m = re.search(r"[-+]?\d*\.?\d+", s.replace(",", ""))
    return float(m.group()) if m else None


def length_m(v):
    """'1.27mm (0.050in)' / '0.80mm' / '2.54 mm' -> metres. None if not a length."""
    s = txt(v)
    if not s or s.upper() in ("N/A", "NA", "NONE"):
        return None
    m = re.search(r"([\d.]+)\s*mm", s, re.I)
    if m:
        return float(m.group(1)) / 1000.0
    m = re.search(r"([\d.]+)\s*(?:in|inch|\")", s, re.I)
    if m:
        return float(m.group(1)) * 0.0254
    return None


def current_a(v):
    """'1A' / '1.5 A' / '500mA' -> amps."""
    s = txt(v)
    if not s or s.upper().startswith("N/A"):
        return None
    m = re.search(r"([\d.]+)\s*mA\b", s, re.I)
    if m:
        return float(m.group(1)) / 1000.0
    m = re.search(r"([\d.]+)\s*A\b", s, re.I)
    if m:
        return float(m.group(1))
    return None


def voltage_v(v):
    """'125V AC' / '250 V AC/DC' -> volts."""
    s = txt(v)
    if not s or s.upper().startswith("N/A"):
        return None
    m = re.search(r"([\d.]+)\s*V\b", s, re.I)
    return float(m.group(1)) if m else None


def integer(v, lo=1, hi=100000):
    s = txt(v)
    if not s or s.upper().startswith("N/A"):
        return None
    m = re.fullmatch(r"\s*(\d+)\s*", s)
    if not m:
        return None
    n = int(m.group(1))
    return n if lo <= n <= hi else None


def temp_range(v):
    """'-55°C to +125°C' -> (min, max)."""
    s = txt(v)
    if not s:
        return None
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", s)
    if len(nums) < 2:
        return None
    a, b = float(nums[0]), float(nums[1])
    return (a, b) if a < b else None


def ohm(v):
    """'20m&#8486; max (Initial), 50m&#8486; max (After Test)' -> 0.02 (the first/initial max)."""
    s = txt(v)
    m = re.search(r"([\d.]+)\s*(m|k|M)?Ω", s)
    if not m:
        return None
    x = float(m.group(1))
    return {"m": x * 1e-3, "k": x * 1e3, "M": x * 1e6, None: x}[m.group(2)]


POLARITY = {"receptacle": "female", "socket": "female", "jack": "female",
            "header": "male", "plug": "male", "pin": "male", "tab": "male",
            "hermaphroditic": "hermaphroditic"}
ORIENTATION = {"vertical": "vertical", "right angle": "rightAngle",
               "horizontal": "horizontal", "mezzanine": "mezzanine"}
MOUNTING = {"surface mount": "smt", "smt": "smt", "through hole": "tht", "thru hole": "tht",
            "tht": "tht", "press-fit": "pressFit", "press fit": "pressFit",
            "pin in paste": "tht", "panel mount": "panel", "cable": "cable"}
WIRE_TERM = {"crimp": "crimp", "idc": "idc", "insulation displacement": "idc",
             "poke-in": "poke-in", "poke in": "poke-in", "solder cup": "solderCup"}

# Standardised data interfaces only -- a proprietary series never gets an interfaceStandard.
INTERFACES = [
    ("usb type c", "USB-C"), ("usb type-c", "USB-C"), ("type c", "USB-C"),
    ("usb 3.2", "USB 3.2"), ("usb 3.1", "USB 3.1"), ("usb 3.0", "USB 3.0"),
    ("usb 2.0", "USB 2.0"), ("usb", "USB"),
    ("rj45", "RJ45"), ("rj-45", "RJ45"), ("rj11", "RJ11"),
    ("hdmi", "HDMI"), ("displayport", "DisplayPort"),
    ("qsfp-dd", "QSFP-DD"), ("qsfp", "QSFP"), ("osfp", "OSFP"), ("sfp+", "SFP+"),
    ("sfp", "SFP"), ("mini-sas hd", "Mini-SAS HD"), ("minisas", "Mini-SAS"),
    ("slimsas", "SlimSAS"), ("mini-sas", "Mini-SAS"),
    ("pci express", "PCI Express"), ("pcie", "PCI Express"), ("m.2", "M.2"),
    ("sata", "SATA"), ("sas", "SAS"),
    ("so-dimm", "SO-DIMM"), ("sodimm", "SO-DIMM"), ("ddr5", "DDR5"), ("ddr4", "DDR4"),
    ("ddr3", "DDR3"), ("ddr2", "DDR2"), ("dimm", "DIMM"),
    ("d-sub", "D-Sub"), ("dsub", "D-Sub"), ("micro-d", "Micro-D"),
    ("smart card", "Smart Card"), ("sim", "SIM"), ("microsd", "microSD"), ("sd card", "SD Card"),
    ("ix industrial", "ix Industrial"), ("single pair ethernet", "Single Pair Ethernet"),
    ("m8", "M8"), ("m12", "M12"),
]


def interface_of(*fields):
    blob = " ".join(strip_tags(f) for f in fields).lower()
    for key, std in INTERFACES:
        if key in blob:
            return std
    return None


def map_family(a):
    """(family, extra, None) or (None, None, reason). Driven only by the vendor taxonomy."""
    l2 = strip_tags(a.get("be_l2_cat")).lower()
    l3 = strip_tags(a.get("be_l3_cat")).lower()
    l4 = strip_tags(a.get("be_l4_cat")).lower()
    pt = strip_tags(a.get("product_type")).lower()
    blob = " ".join((l2, l3, l4, pt))

    def has(*xs):
        return any(x in blob for x in xs)

    # cable assemblies / transceivers are not connectors -- judged on the PER-PART
    # product_type (and the l3 bucket), never on a series name that merely mentions cables.
    if ("cable assembl" in pt or "dac" in pt or "aoc" in pt or "transceiver" in pt
            or "cable assembl" in l3 or "active optical" in l3):
        return None, None, "cable assembly / transceiver, not a connector"
    if has("fiber optic", "optical transceiver", "active optical"):
        return None, None, "fiber-optic part (no electrical connector family in CONAS)"
    if has("backshell", "hardware", "tooling", "tool kit", "accessor", "jackscrew", "cover only"):
        return None, None, "accessory / hardware, not a connector"

    if "busbar" in l2 or "busbar" in l3:
        return "busbar", {}, None
    if ("card edge" in blob or "memory module" in blob or "dimm" in blob
            or "memory and media" in blob):
        return "cardEdge", {}, None
    if "flex" in l2 or "ffc" in blob or "fpc" in blob:
        return "fpcFfc", {}, None
    if "board to board" in l2 or "mezzanine" in l2 or "backplane" in l2:
        extra = {}
        if "mezzanine" in blob:
            extra["mezzanine"] = True
        return "boardToBoard", extra, None
    if "wire to wire" in l2 or "cable to cable" in l2 or "wire-to-wire" in blob:
        extra = {}
        t = WIRE_TERM.get(strip_tags(a.get("termination_style")).lower())
        if t:
            extra["termination"] = t
        return "wireToWire", extra, None
    if ("wire to board" in l2 or "cable to board" in l2 or "wire-to-board" in blob
            or "cable-to-board" in blob):
        extra = {}
        t = WIRE_TERM.get(strip_tags(a.get("termination_style")).lower())
        if t:
            extra["termination"] = t
        return "wireToBoard", extra, None
    if "terminal block" in blob:
        return "terminalBlock", {}, None
    if "circular" in blob or " m12" in (" " + blob) or " m8" in (" " + blob):
        return "circular", {}, None
    if "power" in l2:
        return "power", {}, None
    if ("input output" in l2 or "i/o" in l2 or "input/output" in l2
            or re.search(r"\bio\b", l2)):
        std = interface_of(a.get("be_l4_cat"), a.get("product_type"), a.get("product_series"),
                           a.get("name"), a.get("short_description"))
        if not std:
            return None, None, ("input/output part with no standardised interface "
                                "(proprietary series; interfaceStandard must not be invented)")
        extra = {"interfaceStandard": std}
        return "dataInterface", extra, None
    return None, None, f"unmapped Amphenol CS category: {l2!r}/{l3!r}/{l4!r}/{pt!r}"


STATUS = {"active": "production", "obsolete": "obsolete", "eol": "obsolete",
          "end of life": "obsolete", "not recommended for new designs": "nrnd",
          "nrnd": "nrnd", "discontinued": "obsolete"}


def convert(rec):
    a = rec.get("attrs") or {}
    pn = txt(rec.get("display_pn")) or txt(a.get("display_pn")) or txt(rec.get("sku"))
    if not pn:
        return None, "no part number"

    family, extra, why = map_family(a)
    cur = current_a(a.get("current_rating_percntct")) or current_a(a.get("current_rating"))
    if cur is None:
        cur = current_a(a.get("current_rating_signal")) or current_a(a.get("current_rating_power"))
    blocking = []
    if family is None:
        blocking.append(why)
    if cur is None and family != "rf":
        blocking.append("no per-contact current rating published (CONAS requires it)")
    if blocking:
        return None, " + ".join(blocking)

    part = {"partNumber": pn}
    pol = POLARITY.get(strip_tags(a.get("gender")).lower())
    if pol:
        part["matingPolarity"] = pol
    desc = strip_tags(a.get("short_description")) or strip_tags(rec.get("name"))
    if desc:
        part["description"] = desc[:1000]
    series = strip_tags(a.get("product_series")) or strip_tags(rec.get("name"))
    if series:
        part["series"] = series

    electrical = {}
    if cur is not None:
        electrical["ratedCurrentPerContact"] = cur
    volt = voltage_v(a.get("voltage_rating"))
    if volt is not None and volt > 0:
        electrical["ratedVoltage"] = volt
    # Amphenol's resistance strings carry a recurring vendor case-typo ("30M\u2126" for a
    # contact, "5000m\u2126" for an insulator). The correct reading cannot be recovered
    # without inventing it, so an out-of-range value is DROPPED (and logged by the caller
    # via SUSPECT), never rescaled and never published.
    suspect = []
    r = ohm(a.get("resistance_contact"))
    if r is not None:
        if 1e-6 <= r <= 1.0:
            electrical["contactResistance"] = {"maximum": r}
        else:
            suspect.append(("contactResistance", txt(a.get("resistance_contact")), r))
    ri = ohm(a.get("resistance_insulation"))
    if ri is not None:
        if ri >= 1e6:
            electrical["insulationResistance"] = ri
        else:
            suspect.append(("insulationResistance", txt(a.get("resistance_insulation")), ri))

    mech = {}
    pos = integer(a.get("number_of_contacts"))
    if pos:
        mech["positions"] = pos
    rows = integer(a.get("number_of_rows"), 1, 64)
    if rows:
        mech["rows"] = rows
    pitch = length_m(a.get("pitch"))
    if pitch:
        mech["pitch"] = pitch
    rp = length_m(a.get("row_to_row_spacing"))
    if rp:
        mech["rowPitch"] = rp
    o = ORIENTATION.get(strip_tags(a.get("orientation")).lower())
    if o:
        mech["orientation"] = o
    ms = MOUNTING.get(strip_tags(a.get("termination_style")).lower())
    if ms:
        mech["mountingStyle"] = ms
    cyc = integer(a.get("durability_mate_cycles"), 0, 1000000)
    if cyc is not None:
        mech["matingCycles"] = cyc

    env = {}
    tr = temp_range(a.get("operating_temperature_range"))
    if tr:
        env["operatingTemperature"] = {"minimum": tr[0], "maximum": tr[1]}
    ip = strip_tags(a.get("ip_rating")).upper()
    if re.fullmatch(r"IP[0-9XK]{2,4}", ip):
        env["ipRating"] = ip
    sp = strip_tags(a.get("solder_process")).lower()
    if "reflow" in sp:
        env["solderProcess"] = "reflow"
    elif "wave" in sp:
        env["solderProcess"] = "wave"
    elif "hand" in sp:
        env["solderProcess"] = "handSolder"
    rohs = strip_tags(a.get("eu_rohs_y")).lower()
    if rohs in ("yes", "no"):
        env["rohsCompliant"] = rohs == "yes"
    reach = strip_tags(a.get("reach")).lower()
    if reach in ("yes", "no"):
        env["reachCompliant"] = reach == "yes"

    fam = {"family": family}
    fam.update(extra or {})
    if family == "boardToBoard":
        sh = length_m(a.get("stack_height")) or length_m(a.get("height_mated"))
        if sh:
            fam["stackHeight"] = sh
    shielded = strip_tags(a.get("shield")).strip().lower()
    if family in ("dataInterface", "circular") and shielded in ("yes", "no"):
        fam["shielded"] = shielded == "yes"
    if family == "circular":
        ss = strip_tags(a.get("shell_size")).strip()
        if ss and ss.upper() not in ("N/A", "NA"):
            fam["shellSize"] = ss

    di = {"part": part, "electrical": electrical, "mechanical": mech, "familyDetails": fam}
    if env:
        di["environmental"] = env
    mw = txt(a.get("mates_with"))
    if mw:
        di["mating"] = {"matesWith": [{"series": s.strip(), "relation": "mates"}
                                      for s in re.split(r"[|,;]", mw) if s.strip()][:8]}
    prov = {"source": "manufacturerParametric",
            "sourceName": "Amphenol CS Magento GraphQL catalogue "
                          "(amphenol-cs.com/graphql, products.custom_attributesV2)",
            "retrievedDate": RETRIEVED}
    if txt(rec.get("url_key")):
        prov["sourceUrl"] = f"{SITE}/{txt(rec['url_key'])}.html"
    di["provenance"] = [prov]

    mi = {"name": "Amphenol CS", "reference": pn, "datasheetInfo": di}
    st = STATUS.get(txt(rec.get("part_status")).lower())
    if st:
        mi["status"] = st
    if series:
        mi["family"] = series
    if desc:
        mi["description"] = desc[:1000]
    ds = txt(a.get("datasheet"))
    if ";" in ds:
        path = ds.split(";", 1)[1].strip()
        if path and not path.startswith("http"):
            mi["datasheetUrl"] = MEDIA + path
    return {"connector": {"manufacturerInfo": mi}, "_suspect": suspect}, None


def cmd_vocab(src):
    c = collections.Counter()
    n = 0
    for line in open(src):
        r = json.loads(line)
        a = r["attrs"]
        n += 1
        c[(strip_tags(a.get("be_l2_cat")), strip_tags(a.get("be_l3_cat")),
           strip_tags(a.get("product_type")))] += 1
    print(f"records {n}, distinct L2/L3/type {len(c)}")
    for k, v in c.most_common(80):
        print(f"  {v:6d}  {k}")


def cmd_build(src, outdir):
    os.makedirs(outdir, exist_ok=True)
    good = open(os.path.join(outdir, "cs.records.ndjson"), "w")
    bad = open(os.path.join(outdir, "cs.incomplete.ndjson"), "w")
    sus = open(os.path.join(outdir, "cs.suspect_fields.ndjson"), "w")
    nsus = 0
    seen = set()
    n = ng = nb = 0
    reasons = collections.Counter()
    fams = collections.Counter()
    for line in open(src):
        rec = json.loads(line)
        n += 1
        pn = txt(rec.get("display_pn")) or txt(rec.get("sku"))
        if pn in seen:
            continue
        seen.add(pn)
        out, why = convert(rec)
        if out is None:
            nb += 1
            reasons[why[:150]] += 1
            bad.write(json.dumps({"partNumber": pn, "reason": why,
                                  "source": "amphenolCS"}, ensure_ascii=False) + "\n")
        else:
            ng += 1
            for field, rawv, parsed in out.pop("_suspect", []):
                nsus += 1
                sus.write(json.dumps({"partNumber": pn, "field": field, "raw": rawv,
                                      "parsedOhm": parsed,
                                      "action": "field omitted (implausible vendor value)"},
                                     ensure_ascii=False) + "\n")
            fams[out["connector"]["manufacturerInfo"]["datasheetInfo"]
                 ["familyDetails"]["family"]] += 1
            good.write(json.dumps(out, ensure_ascii=False) + "\n")
    good.close(); bad.close(); sus.close()
    print(json.dumps({"rows": n, "converted": ng, "incomplete": nb,
                      "suspect_fields_dropped": nsus,
                      "families": fams.most_common(),
                      "top_reasons": reasons.most_common(10)}, indent=1)[:3000])


if __name__ == "__main__":
    if sys.argv[1] == "vocab":
        cmd_vocab(sys.argv[2])
    else:
        cmd_build(sys.argv[2], sys.argv[3])
