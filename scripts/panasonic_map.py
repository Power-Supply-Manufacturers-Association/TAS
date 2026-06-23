#!/usr/bin/env python3
"""Map scraped Panasonic raw rows -> TAS component records (CAS/RAS/MAS shapes).

Pure mapping only (no validation/IO). `map_record(raw)` returns
(record_dict_or_None, status, reason) where status in {"ok","quarantine","skip"}.

Units are read from each column header (the value in parentheses) and converted to
SI — never assumed. Missing values ("-", "", "N/A") become None and absent fields
are simply omitted (schemas are additionalProperties:false, so we emit only known
keys). Required-field gaps are reported as "quarantine", never faked.
"""
import re

MANUF = "Panasonic"

# ---- value / unit parsing -------------------------------------------------

def num(s):
    """Parse a single numeric value; return None if missing/non-numeric."""
    if s is None: return None
    s = str(s).strip()
    if s in ("", "-", "—", "–", "N/A", "n/a", "TBD", "*"): return None
    s = s.replace("±", "").replace("+/-", "").replace("+/−", "")
    s = s.replace(",", "").replace(" ", "")
    s = s.replace("approx.", "").replace("max.", "").replace("min.", "").replace("typ.", "")
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s)
    return float(m.group()) if m else None

def parse_range(s):
    """'185 - 225' or '-55 - 125' -> (lo, hi); single -> (v, v); None -> (None,None)."""
    if s is None: return (None, None)
    s = str(s).strip()
    if s in ("", "-", "—", "–", "N/A"): return (None, None)
    m = re.match(r"^\s*([-+]?\d*\.?\d+)\s*(?:[-~]|to)\s*([-+]?\d*\.?\d+)\s*$", s)
    if m: return (float(m.group(1)), float(m.group(2)))
    v = num(s)
    return (v, v)

# unit token (from header parenthesis) -> SI multiplier, per quantity family
_CAP = {"f":1, "mf":1e-3, "µf":1e-6, "uf":1e-6, "nf":1e-9, "pf":1e-12}
_RES = {"ω":1, "ohm":1, "ohms":1, "mω":1e-3, "mohm":1e-3, "kω":1e3, "kohm":1e3,
        "µω":1e-6, "uω":1e-6}
_CUR = {"a":1, "arms":1, "ma":1e-3, "marms":1e-3, "µa":1e-6, "ua":1e-6}
_VOLT = {"v":1, "mv":1e-3, "kv":1e3, "vrms":1, "vdc":1, "vac":1}
_LEN = {"mm":1e-3, "cm":1e-2, "m":1, "µm":1e-6, "um":1e-6, "inch":0.0254, "in":0.0254}
_IND = {"h":1, "mh":1e-3, "µh":1e-6, "uh":1e-6, "nh":1e-9, "ph":1e-12}
_FREQ = {"hz":1, "khz":1e3, "mhz":1e6, "ghz":1e9}
_ENERGY = {"j":1, "mj":1e-3}

def unit_from_header(h):
    """Last parenthesised token of a header, lowercased (e.g. '... (µF)' -> 'µf')."""
    paren = re.findall(r"\(([^()]*)\)", h)
    if not paren: return ""
    tok = paren[-1].strip().lower()
    # strip noise like 'max.', 'rms' descriptors handled per-table
    return tok

def convert(value, header, table):
    if value is None: return None
    u = unit_from_header(header)
    # try direct, then strip trailing punctuation
    for key in (u, u.rstrip("."), u.replace("µ", "u")):
        if key in table: return value * table[key]
    return None  # unknown unit -> signal caller (don't guess)

# ---- generic column finder ------------------------------------------------

def find_header(specs, includes, excludes=()):
    """First spec header containing all `includes` substrings and no `excludes`."""
    incs = [i.lower() for i in includes]; exs = [e.lower() for e in excludes]
    for h in specs:
        hl = h.lower()
        if all(i in hl for i in incs) and not any(e in hl for e in exs):
            return h
    return None

def get_si(specs, includes, table, excludes=(), agg=None):
    """Find header(s), parse value(s), convert via unit table. agg='min'/'max'/'list'."""
    if agg == "list":
        out = []
        for h in specs:
            hl = h.lower()
            if all(i.lower() in hl for i in includes) and not any(e.lower() in hl for e in excludes):
                v = convert(num(specs[h]), h, table)
                if v is not None: out.append(v)
        return out
    h = find_header(specs, includes, excludes)
    if not h: return None
    return convert(num(specs[h]), h, table)

def parse_dims_pair(s):
    """'7.0×7.5' or '4.5 x 3.2 (EIA:1812)' -> (a, b) in mm, plus EIA code."""
    eia = None
    m = re.search(r"EIA[:\s]*([0-9]+)", s or "", re.I)
    if m: eia = m.group(1)
    nums = re.findall(r"\d*\.?\d+", re.split(r"\(", s or "")[0].replace("×", "x").replace("X", "x"))
    a = float(nums[0]) if len(nums) >= 1 else None
    b = float(nums[1]) if len(nums) >= 2 else None
    return a, b, eia

def temp_range(specs):
    h = find_header(specs, ["temperature"], excludes=["coefficient", "freq", "tcr"])
    if not h: return None
    lo, hi = parse_range(specs[h])
    if lo is None and hi is None: return None
    out = {}
    if lo is not None: out["minimum"] = lo
    if hi is not None and hi != lo: out["maximum"] = hi
    return out or None

def dwt(nominal=None, minimum=None, maximum=None):
    o = {}
    if nominal is not None: o["nominal"] = nominal
    if minimum is not None: o["minimum"] = minimum
    if maximum is not None: o["maximum"] = maximum
    return o or None

# ---- per-category configuration -------------------------------------------

CAP_TECH = {
    "sp-cap": "aluminum-electrolytic-polymer",
    "os-con": "aluminum-electrolytic-polymer",
    "poscap": "tantalum-polymer",
    "hybrid-aluminum": "aluminum-hybrid-polymer",
    "aluminum-cap-smd": "aluminum-electrolytic-wet",
    "aluminum-cap-lead": "aluminum-electrolytic-wet",
    "film-cap-electroequip": None,          # decide from series
    "automotive-film-cap": "film-polypropylene",
}
CAP_ASSEMBLY_DEFAULT = {
    "sp-cap": "SMT", "os-con": "SMT", "poscap": "SMT", "hybrid-aluminum": "SMT",
    "aluminum-cap-smd": "SMT", "aluminum-cap-lead": "THT",
    "film-cap-electroequip": "THT", "automotive-film-cap": "THT",
}

def film_technology(series):
    s = (series or "").lower()
    if "polyester" in s or "pet" in s or "mylar" in s: return "film-polyester"
    if "pps" in s or "phenylene" in s: return "film-polyphenylene-sulfide"
    if "paper" in s: return "film-paper"
    return "film-polypropylene"  # PP is Panasonic's dominant electronic-equipment film

def infer_assembly(specs, default):
    blob = " ".join(f"{k} {v}" for k, v in specs.items()).lower()
    if "snap" in blob: return "Snap-In"
    if "screw" in blob: return "Screw Type"
    if any(t in blob for t in ("smd", "v-chip", "vchip", "chip", "reflow", "surface")):
        return "SMT"
    if "lead" in blob or "radial" in blob: return "THT"
    return default

RES_TECH_DEFAULT = {
    "current-sensing-chip-resistors": "currentSenseShunt",
    "anti-sulfurated-chip-resistors": "thickFilm",
    "general-purpose-chip-resistors": "thickFilm",
    "high-temperature-chip-resistors": "thickFilm",
    "high-precision-chip-resistors": "thinFilm",
    "small-and-high-power-chip-resistors": "thickFilm",
    "resistor-network-array": "thickFilm",
}

def res_technology(series, cat):
    s = (series or "").lower()
    if "current sens" in s: return "currentSenseShunt"
    if "thin film" in s: return "thinFilm"
    if "metal foil" in s: return "metalFoil"
    if "metal film" in s: return "metalFilm"
    if "metal oxide" in s: return "metalOxide"
    if "wirewound" in s or "wire wound" in s: return "wirewound"
    if "thick film" in s: return "thickFilm"
    return RES_TECH_DEFAULT.get(cat, "thickFilm")

# magnetic subtype per category
MAG_SUBTYPE = {
    "automotive-inductors": "inductor",
    "inductors-for-consumer": "inductor",
    "voltage-stepup-coils": "inductor",
    "multilayer-inductors": "inductor",
    "choke-coils": "inductor",
    "chip-inductors": "inductor",
    "noise-filters": "commonModeChoke",
}

# ---- record builders ------------------------------------------------------

def _part_number(raw):
    url = raw.get("part_url") or ""
    seg = url.rstrip("/").split("/")[-1]
    if seg.lower() == "cad":
        seg = url.rstrip("/").split("/")[-2]
    if seg and re.match(r"^[A-Za-z0-9._-]+$", seg):
        return seg
    txt = (raw.get("part") or "").replace("Discontinued products", "").strip()
    return txt

def _manuf_block(raw, pn, family=None):
    mi = {"name": MANUF, "reference": pn,
          "status": "obsolete" if raw.get("eol") else "production"}
    if raw.get("datasheet"): mi["datasheetUrl"] = raw["datasheet"]
    if family: mi["family"] = family
    return mi

def map_capacitor(raw):
    specs = raw["specs"]; pn = _part_number(raw); cat = raw["category"]
    series = specs.get("Series/Type")
    tech = CAP_TECH[cat] or film_technology(series)
    cap = get_si(specs, ["capacitance"], _CAP, excludes=["tolerance", "drift"])
    volt = get_si(specs, ["voltage"], _VOLT, excludes=["allowable", "surge"])
    if cap is None or volt is None:
        return None, "quarantine", f"missing capacitance({cap}) or ratedVoltage({volt})"
    elec = {"capacitance": dwt(nominal=cap), "ratedVoltage": volt}
    esr = get_si(specs, ["esr"], _RES) or get_si(specs, ["equivalent series resistance"], _RES) \
          or get_si(specs, ["e.s.r"], _RES)
    if esr is not None: elec["esr"] = esr
    esrf = get_si(specs, ["esr", "frequency"], _FREQ)
    if esrf is not None: elec["esrFrequency"] = esrf
    leak = get_si(specs, ["leakage"], _CUR)
    if leak is not None: elec["leakageCurrent"] = leak
    ripple = get_si(specs, ["ripple", "current"], _CUR, excludes=["temp", "freq"])
    if ripple is not None:
        elec["rippleCurrent"] = ripple
        rf = get_si(specs, ["ripple", "freq"], _FREQ)
        if rf is not None: elec["rippleCurrentFrequency"] = rf
        rt_h = find_header(specs, ["ripple", "temp"])
        if rt_h and num(specs[rt_h]) is not None: elec["rippleCurrentTemperature"] = num(specs[rt_h])
    df_h = find_header(specs, ["dissipation"])
    if df_h and num(specs[df_h]) is not None: elec["dissipationFactor"] = num(specs[df_h])
    # mechanical
    dims = {}
    for field, inc, exc in [("length", ["length"], []), ("width", ["width"], []),
                            ("height", ["height"], []), ("diameter", ["diameter"], [])]:
        v = get_si(specs, inc, _LEN, excludes=exc)
        if v is not None: dims[field] = dwt(nominal=v)
    pitch = get_si(specs, ["pitch"], _LEN)
    if pitch is not None: dims["pitch"] = dwt(nominal=pitch)
    assembly = infer_assembly(specs, CAP_ASSEMBLY_DEFAULT[cat])
    body = specs.get("Body shape") or specs.get("Body Type") or ""
    shape_type = body.strip() or ("Chip" if assembly == "SMT" else "Cylindrical")
    mech = {"shape": {"assembly": assembly, "shapeType": shape_type}}
    if dims: mech["dimensions"] = dims
    part = {"partNumber": pn, "technology": tech}
    if series: part["series"] = series
    size = specs.get("Size Code")
    if size: part["case"] = size
    di = {"part": part, "electrical": elec, "mechanical": mech}
    th = temp_range(specs)
    if th: di["thermal"] = {"operatingTemperature": th}
    rec = {"capacitor": {"manufacturerInfo": {**_manuf_block(raw, pn, series),
                                              "datasheetInfo": di}}}
    return rec, "ok", None

def map_resistor(raw):
    specs = raw["specs"]; pn = _part_number(raw); cat = raw["category"]
    series = specs.get("Series/Type")
    res = get_si(specs, ["resistance", "value"], _RES) or get_si(specs, ["resistance"], _RES,
                 excludes=["tolerance", "temperature"])
    tol_h = find_header(specs, ["tolerance"])
    tol = num(specs[tol_h]) / 100.0 if tol_h and num(specs[tol_h]) is not None else None
    pw = get_si(specs, ["power", "rating"], {"w":1, "mw":1e-3}) or \
         (num(specs[find_header(specs, ["power"])]) if find_header(specs, ["power"]) else None)
    if res is None or tol is None or pw is None:
        return None, "quarantine", f"missing resistance({res})/tolerance({tol})/power({pw})"
    elec = {"resistance": dwt(nominal=res), "tolerance": tol, "powerRating": pw}
    tcr_h = find_header(specs, ["t.c.r"]) or find_header(specs, ["tcr"]) \
            or find_header(specs, ["temperature", "coefficient"])
    if tcr_h and num(specs[tcr_h]) is not None: elec["temperatureCoefficient"] = num(specs[tcr_h])
    part = {"partNumber": pn, "technology": res_technology(series, cat)}
    if series: part["series"] = series
    size_h = find_header(specs, ["chip size"]) or find_header(specs, ["size"])
    mech = {}  # RAS resistor mechanical uses flat length/width (no 'dimensions' wrapper)
    if size_h:
        a, b, eia = parse_dims_pair(specs[size_h])
        if eia: part["case"] = eia
        if a is not None: mech["length"] = dwt(nominal=a*1e-3)
        if b is not None: mech["width"] = dwt(nominal=b*1e-3)
    extras = []
    for k in ("Number of Elements (piece)", "Type of Circuit"):
        if specs.get(k): extras.append(f"{k.split('(')[0].strip()}: {specs[k]}")
    if extras: part["matchcodeDescription"] = "; ".join(extras)
    di = {"part": part, "electrical": elec}
    if mech: di["mechanical"] = mech
    th = temp_range(specs)
    if th: di["thermal"] = {"operatingTemperature": th}
    rec = {"resistor": {"manufacturerInfo": {**_manuf_block(raw, pn, series),
                                             "datasheetInfo": di}}}
    return rec, "ok", None

def map_varistor(raw):
    specs = raw["specs"]; pn = _part_number(raw); series = specs.get("Series/Type")
    vv_n = get_si(specs, ["varistor voltage"], _VOLT, excludes=["range"])
    vv_lo, vv_hi = parse_range(specs.get(find_header(specs, ["varistor voltage", "range"]) or "", ""))
    vv = dwt(nominal=vv_n, minimum=vv_lo if vv_n is None else None,
             maximum=vv_hi if vv_n is None else None)
    if vv_n is None and vv_lo is not None:
        vv = dwt(nominal=(vv_lo + vv_hi) / 2.0 if vv_hi else vv_lo, minimum=vv_lo, maximum=vv_hi)
    peak = get_si(specs, ["peak current"], _CUR) or get_si(specs, ["maximum peak current"], _CUR)
    clamp = get_si(specs, ["clamping"], _VOLT)
    elec = {}
    if vv: elec["varistorVoltage"] = vv
    if clamp is not None: elec["clampingVoltage"] = clamp
    if peak is not None: elec["peakSurgeCurrent"] = peak
    energy = get_si(specs, ["energy"], _ENERGY)
    if energy is not None: elec["energyAbsorption"] = energy
    mac = get_si(specs, ["allowable voltage", "ac"], _VOLT) or get_si(specs, ["allowable voltage", "acrms"], _VOLT)
    if mac is not None: elec["maxContinuousAcVoltage"] = mac
    mdc = get_si(specs, ["allowable voltage", "dc"], _VOLT)
    if mdc is not None: elec["maxContinuousDcVoltage"] = mdc
    elif find_header(specs, ["max", "allowable voltage"]):
        v = get_si(specs, ["allowable voltage"], _VOLT)
        if v is not None: elec.setdefault("maxContinuousDcVoltage", v)
    cap = get_si(specs, ["capacitance"], _CAP, excludes=["max"]) or get_si(specs, ["capacitance"], _CAP)
    if cap is not None: elec["capacitance"] = cap
    wave_h = find_header(specs, ["8/20"]) or find_header(specs, ["waveform"])
    if wave_h or peak is not None: elec["surgeWaveform"] = "8/20"
    # technology
    tech = "multiLayer" if "chip" in raw["category"] else "metalOxide"
    part = {"partNumber": pn, "technology": tech}
    if series: part["series"] = series
    missing = [f for f in ("varistorVoltage", "clampingVoltage", "peakSurgeCurrent") if f not in elec]
    di = {"part": part, "electrical": elec}
    rec = {"varistor": {"manufacturerInfo": {**_manuf_block(raw, pn, series),
                                             "datasheetInfo": di}}}
    if missing:
        return rec, "quarantine", "missing required: " + ",".join(missing)
    return rec, "ok", None

def map_magnetic(raw):
    specs = raw["specs"]; pn = _part_number(raw); cat = raw["category"]
    series = specs.get("Series/Type")
    subtype = MAG_SUBTYPE.get(cat, "inductor")
    if cat == "noise-filters":
        s = (series or "").lower()
        subtype = "commonModeChoke" if ("common" in s or "choke" in s or "filter" in s) else "inductor"
    e = {"subtype": subtype}
    dcr_h = find_header(specs, ["dc resistance", "max"]) or find_header(specs, ["dc resistance"])
    if subtype == "commonModeChoke":
        # CMC variant: dcResistances (array of dimensionWithTolerance), ratedVoltageDC, no scalar dcResistance
        rc = get_si(specs, ["rated current"], _CUR, agg="list")
        if rc: e["ratedCurrents"] = rc
        rv = get_si(specs, ["rated voltage", "dc"], _VOLT) or get_si(specs, ["rated voltage"], _VOLT)
        if rv is not None: e["ratedVoltageDC"] = rv
        if dcr_h:
            v = convert(num(specs[dcr_h]), dcr_h, _RES)
            if v is not None:
                e["dcResistances"] = [dwt(maximum=v) if "max" in dcr_h.lower() else dwt(nominal=v)]
        srf = get_si(specs, ["resonant"], _FREQ) or get_si(specs, ["srf"], _FREQ)
        if srf is not None: e["selfResonantFrequency"] = srf
    else:
        # inductance: prefer no-load / nominal column
        ind = get_si(specs, ["inductance", "no load"], _IND) or get_si(specs, ["inductance", "l0"], _IND) \
              or get_si(specs, ["inductance"], _IND, excludes=["tolerance", "leakage"])
        if ind is not None: e["inductance"] = dwt(nominal=ind)
        if dcr_h:  # header carries mΩ or Ω
            v = convert(num(specs[dcr_h]), dcr_h, _RES)
            if v is not None:
                e["dcResistance"] = dwt(maximum=v) if "max" in dcr_h.lower() else dwt(nominal=v)
        rc = get_si(specs, ["rated current"], _CUR, agg="list") or get_si(specs, ["idc"], _CUR, agg="list")
        if rc: e["ratedCurrents"] = rc
        sat_h = find_header(specs, ["rated current", "ΔL"]) or find_header(specs, ["rated current", "-30%"])
        if sat_h:
            v = convert(num(specs[sat_h]), sat_h, _CUR)
            if v is not None: e["saturationCurrentPeak"] = v
        srf = get_si(specs, ["resonant"], _FREQ) or get_si(specs, ["srf"], _FREQ)
        if srf is not None: e["selfResonantFrequency"] = srf
    part = {"partNumber": pn}
    if series: part["description"] = series
    sz_h = find_header(specs, ["size"])
    mech = {}
    if sz_h:
        val = (specs[sz_h] or "").strip()
        if "inch" in sz_h.lower() or re.match(r"^\d{3,4}[A-Za-z]?$", val):
            # EIA case code (e.g. '0805'), not a physical dimension
            if val: part["caseCode"] = val
        else:
            a, b, eia = parse_dims_pair(val)
            if eia: part["caseCode"] = eia
            if a is not None: mech["length"] = dwt(nominal=a*1e-3)
            if b is not None: mech["width"] = dwt(nominal=b*1e-3)
    ht = get_si(specs, ["height"], _LEN)
    if ht is not None: mech["height"] = dwt(nominal=ht)
    wt_h = find_header(specs, ["weight"])
    if wt_h and num(specs[wt_h]) is not None: mech["weight"] = dwt(nominal=num(specs[wt_h])*1e-3)  # g->kg
    blob = " ".join(specs.values()).lower()
    if "shielded" in blob: part["shielded"] = True
    di = {"part": part, "electrical": [e]}
    if mech: di["mechanical"] = mech
    th = {}
    hh = find_header(specs, ["heat resistance"]) or find_header(specs, ["temperature"])
    if hh and num(specs[hh]) is not None:
        lo, hi = parse_range(specs[hh])
        ot = {}
        if lo is not None and hi is not None and lo != hi:
            ot = {"minimum": lo, "maximum": hi}
        elif hi is not None:
            ot = {"maximum": hi}
        if ot: th["operatingTemperature"] = ot
    if th: di["thermal"] = th
    rec = {"magnetic": {"manufacturerInfo": {**_manuf_block(raw, pn, series),
                                             "datasheetInfo": di}}}
    return rec, "ok", None

MAPPERS = {"capacitor": map_capacitor, "resistor": map_resistor,
           "varistor": map_varistor, "magnetic": map_magnetic}

def map_record(raw):
    pn = _part_number(raw)
    if not pn:
        return None, "skip", "no part number"
    return MAPPERS[raw["type"]](raw)
