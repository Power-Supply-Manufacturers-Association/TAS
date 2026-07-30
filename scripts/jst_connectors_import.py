#!/usr/bin/env python3
"""JST (J.S.T. Mfg. Co., Ltd.) global product catalogue -> CONAS connector NDJSON.

SOURCE (verified 2026-07-31, plain curl/urllib, no bot gate):

  series index : https://www.jst-mfg.com/product/index.php?type=5&lang=en
                 -> alphabetical list of every product series, links carry ?series=<id>
  series page  : https://www.jst-mfg.com/product/index.php?series=<id>&lang=en
                 -> "Product Profile" + "Specification" key/value tables (series-level
                    ratings) and a "3D/2D Data" table whose "Product" column is the list
                    of orderable part numbers in that series.

  Send a normal desktop User-Agent. `&lang=en` switches the page to English; without it
  the page is Japanese. www.jst.com (JST Sales America) is Cloudflare-Turnstile gated
  and cannot be automated - its wp-json REST API is open but exposes only part-number
  titles with empty ACF, i.e. no parametric data.

NO FABRICATION. Every number emitted is printed verbatim by JST and only unit-converted
to SI; nothing is interpolated, averaged or assumed. Two ambiguity rules:

  * Electrical scalars. JST frequently prints several ratings for one series, one per
    wire gauge / circuit count / circuit role ("12.25 A (3 circuits, AWG #14) | 2.5 A
    (40 circuits, AWG #28)"), and the same part number can be listed by several series
    with different ratings (B4B-PH-K-S is 2 A under PH, 1 A under KR/KRD). The page never
    says which figure belongs to an individual part number, so the LOWEST published
    figure is taken - CONAS defines the scalar electrical.* fields as "the
    standalone/worst-case figures", and operatingTemperature collapses to the narrowest
    published range. Every such record carries an extra provenance entry naming the
    affected field and quoting the published alternatives, so it can be filtered or
    re-derived later.
  * Non-scalar per-part descriptors (positions, rows, mounting style, mating
    orientation, pitch, family) have no worst case. They are emitted only when the
    series publishes a single unambiguous value, and for a part listed by several series
    only when every series agrees; otherwise the field is omitted (or, when the field is
    schema-required, the part is routed to rejected.ndjson with the reason).

Writes ONLY to staging/jst/ - never to data/connectors.ndjson.
"""
import argparse
import collections
import glob
import gzip
import html
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
BASE = "https://www.jst-mfg.com/product/index.php"
INDEX_URL = BASE + "?type=5&lang=en"
SERIES_URL = BASE + "?series={sid}&lang=en"
RETRIEVED = "2026-07-31"
MANUFACTURER = "JST"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.environ.get("JST_CACHE", "/tmp/jst/html")
OUT = os.path.join(REPO, "staging", "jst")


# --------------------------------------------------------------------------- fetch
def http_get(url, tries=4):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                b = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    b = gzip.decompress(b)
            return b
        except Exception as e:                                    # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def fetch_all():
    os.makedirs(CACHE, exist_ok=True)
    idx = http_get(INDEX_URL).decode("utf-8", "replace")
    pairs = re.findall(r"index\.php\?series=(\d+)'>(.*?)</a>", idx)
    series = {}
    for sid, name in pairs:
        series.setdefault(int(sid), re.sub(r"<[^>]+>", "", name).strip())
    json.dump(series, open(os.path.join(CACHE, "_index.json"), "w"), ensure_ascii=False)
    print(f"series index: {len(series)} series")

    def one(sid):
        p = os.path.join(CACHE, f"{sid}.html")
        if os.path.exists(p) and os.path.getsize(p) > 2000:
            return "cached"
        open(p, "wb").write(http_get(SERIES_URL.format(sid=sid)))
        return "fetched"

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(one, sorted(series)))
    print(f"cached {len(glob.glob(CACHE + '/*.html'))} series pages in {CACHE}")


# --------------------------------------------------------------------------- parse
def clean(s):
    s = re.sub(r"<br\s*/?>", " | ", s, flags=re.I)
    s = re.sub(r"<sup>(.*?)</sup>", r"^\1", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def kvtable(body):
    kv = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) == 2:
            k = clean(tds[0]).replace(" | ", " ")
            v = clean(tds[1])
            if k:
                kv[k] = v
    return kv


def parse_series(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    sid = int(os.path.basename(path).split(".")[0])
    d = {"seriesId": sid, "url": SERIES_URL.format(sid=sid)}
    m = (re.search(r"<div class='title_bar_e'[^>]*>(.*?)</div>", raw, re.S)
         or re.search(r"<div class='title_bar'[^>]*>(.*?)</div>", raw, re.S))
    d["title"] = clean(m.group(1)) if m else None
    m = re.search(r"<div class='marginLeft20 marginTop10 marginBottom25'>(.*?)</div>", raw, re.S)
    d["description"] = clean(m.group(1)) if m else None
    chunks = re.split(r"<div class='itemBar'>(.*?)</div>", raw)
    secs = collections.defaultdict(list)
    for i in range(1, len(chunks), 2):
        secs[clean(chunks[i])].append(chunks[i + 1])
    for name, key in (("Product Profile", "profile"), ("Specification", "spec")):
        kv = {}
        for body in secs.get(name, []):
            kv.update(kvtable(body))
        d[key] = kv
    prods, hdr = [], None
    i = raw.find("id='nomen_list'")
    if i != -1:
        m = re.search(r"<table.*?</table>", raw[i:], re.S)
        if m:
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(), re.S):
                cells = [clean(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
                if hdr is None:
                    hdr = cells
                    continue
                if len(cells) >= 2 and cells[0].isdigit() and cells[1]:
                    prods.append(cells[1])
    d["products"] = prods
    m = re.search(r"href='(\.\./product/pdf/[^']+)'", raw)
    d["catalogPdf"] = ("https://www.jst-mfg.com" + m.group(1)[2:]) if m else None
    return d


# ------------------------------------------------------------------- value parsing
def _num(s):
    return float(s.replace(",", ""))


def parse_current(v):
    """'2 A AC/DC (AWG #24)' -> (2.0, False).

    JST often publishes several ratings for one series, one per wire gauge / circuit
    count / circuit role ("12.25 A (3 circuits, AWG #14) | 2.5 A (40 circuits, AWG
    #28)"). Nothing on the page says which one applies to an individual part number, so
    we take the LOWEST published figure - CONAS defines the scalar electrical.* fields as
    "the standalone/worst-case figures". Every number emitted is printed verbatim by JST;
    nothing is interpolated. The flag marks records whose value came from such a set so
    the provenance trail can say so.
    """
    if not v:
        return None, False
    vals = set()
    for num, unit in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(mA|A)(?![A-Za-z])", v):
        vals.add(_num(num) / 1000.0 if unit == "mA" else _num(num))
    if not vals:
        return None, False
    return min(vals), len(vals) > 1


def parse_voltage(v):
    """-> (volts, was_worst_case_of_several). Same worst-case rule as parse_current."""
    if not v:
        return None, False
    vals = set()
    for num, unit in re.findall(r"(?<![\w.])([\d,]+(?:\.\d+)?)\s*(kV|V)(?![A-Za-z])", v):
        vals.add(_num(num) * (1000.0 if unit == "kV" else 1.0))
    if not vals:
        return None, False
    return min(vals), len(vals) > 1


def parse_temp(v):
    if not v:
        return None
    m = re.search(r"([-+]?\d+)\s*℃\s*to\s*([-+]?\d+)\s*℃", v)
    if not m:
        return None
    lo, hi = float(m.group(1)), float(m.group(2))
    return {"minimum": lo, "maximum": hi} if lo < hi else None


def parse_pitch(v):
    """'2 mm' -> 0.002 m ; multi-pitch series -> None."""
    if not v:
        return None
    m = re.fullmatch(r"([\d.]+)\s*mm", v.strip())
    return float(m.group(1)) / 1000.0 if m else None


def parse_insulation_resistance(v):
    if not v:
        return None
    m = re.fullmatch(r"([\d,]+)\s*MΩ\s*min\.?", v.strip())
    return _num(m.group(1)) * 1e6 if m else None


def parse_withstanding(v):
    if not v:
        return None
    vals = {_num(x) for x in re.findall(r"applying\s+([\d,]+)\s*V", v)}
    return vals.pop() if len(vals) == 1 else None


def parse_circuits(v):
    """positions only when the series publishes exactly ONE circuit count."""
    if not v:
        return None
    toks = [t.strip() for t in v.replace("|", ",").split(",") if t.strip()]
    if len(toks) == 1 and toks[0].isdigit():
        n = int(toks[0])
        return n if n >= 1 else None
    return None


ROWS = {"Single-row": 1, "Dual-row": 2, "Dual-row staggered": 2, "3-row": 3,
        "3-row staggered": 3, "4-row": 4, "6-row": 6}
MOUNT = {"Through-hole": "tht", "SMT": "smt", "Press-fit": "pressFit",
         "Through-hole reflow": "tht"}
ORIENT = {"Top entry": "vertical", "Side entry": "rightAngle"}


def parse_wire_gauge(v):
    """'AWG # 28 , # 26 , # 24 | 0.08 mm^2 to 0.22 mm^2' -> gauge range dict."""
    if not v or "(" in v:                 # per-circuit qualifiers -> ambiguous
        return None
    out = {}
    awgs = [float(x) for x in re.findall(r"#\s*(\d+)", v)]
    if awgs:
        out["minimumAwg"] = min(awgs)
        out["maximumAwg"] = max(awgs)
    areas = [float(x) for x in re.findall(r"([\d.]+)\s*mm\^2", v)]
    if len(areas) == 2 and areas[0] < areas[1]:
        out["minimumArea"] = areas[0] * 1e-6
        out["maximumArea"] = areas[1] * 1e-6
    elif len(areas) == 1:
        out["minimumArea"] = out["maximumArea"] = areas[0] * 1e-6
    return out or None


# ------------------------------------------------------------------ family mapping
NON_CONNECTOR_CATEGORIES = {
    "Solderless Terminals", "Chain Terminals/Splices", "Solderless Splices",
    "Shunt Wires", "DIN type Solderless Terminals/Splices", "Application Tools/Machines",
}

# Interface / card series -> published standard (taken from the series description text).
INTERFACE_STANDARD = {
    51: "D-Sub", 52: "D-Sub", 53: "D-Sub", 55: "D-Sub",
    171: "Modular jack", 172: "RJ45", 741: "RJ45",
    256: "USB", 257: "USB Mini-B", 553: "Micro USB", 673: "Micro USB",
    602: "USB 3.0 Type-A", 693: "USB-C", 766: "USB-C", 771: "USB-C", 838: "USB-C",
    564: "3.5 mm phone jack",
    30: "CompactFlash", 31: "CompactFlash", 32: "CompactFlash", 804: "CompactFlash",
    223: "SIM", 675: "SIM", 240: "PC Card",
    582: "microSD", 630: "microSD", 631: "microSD", 721: "microSD",
    660: "CFast", 718: "CFexpress Type B",
}
# Series the category alone cannot classify (proprietary interface bodies, battery
# contacts, pressure contacts, automotive squib/wire-to-device, ...) -> not staged.
FORCE_UNMAPPED = {506, 642, 663, 170, 194, 590, 695, 767}
HEADER_SERIES = {5, 8, 15, 146, 163, 249, 267}


def map_family(s):
    """-> (family, extra_props, None) or (None, None, reason)."""
    sid = s["seriesId"]
    cat = s["profile"].get("Category", "")
    sub = s["profile"].get("Sub category", "")
    wiring = s["profile"].get("Wiring type", "")
    title = s["title"] or ""

    if cat in NON_CONNECTOR_CATEGORIES:
        return None, None, f"not a connector (JST category '{cat}')"
    if sid in FORCE_UNMAPPED:
        return None, None, f"no CONAS family fits this series ('{title}')"

    term_from_wiring = {"Crimp style": "crimp", "IDC style": "idc"}.get(wiring)

    if cat in ("Crimp Style Connectors (Wire-to-Board type)",
               "Crimp Style Connectors (Wire-to-Board Board-in type)"):
        return "wireToBoard", {"termination": "crimp"}, None
    if cat in ("Insulation Displacement Connectors (Wire-to-Board type)",
               "Insulation Displacement Connectors (Wire-to-Board Board-in type)",
               "Ribbon Cable Connectors"):
        return "wireToBoard", {"termination": "idc"}, None
    if cat == "Wire-to-Wire Connectors":
        extra = {"termination": term_from_wiring} if term_from_wiring else {}
        return "wireToWire", extra, None
    if cat == "Board-to-Board Connectors":
        return "boardToBoard", {}, None
    if cat == "FFC/FPC Connectors":
        return "fpcFfc", {}, None
    if cat == "Card Edge Connectors":
        return "cardEdge", {}, None
    if cat in ("Interface Connection Connectors", "Card Connectors"):
        std = INTERFACE_STANDARD.get(sid)
        if not std:
            return None, None, "dataInterface series without a published interface standard"
        return "dataInterface", {"interfaceStandard": std}, None
    if cat == "Other Connectors (Including headers)":
        if sid in HEADER_SERIES:
            return "pinHeaderSocket", {}, None
        return None, None, f"unclassifiable 'Other Connectors' series ('{title}')"
    if cat == "Spring Type":
        if sid == 534:                     # J-FAT: spring-cage terminal block
            return "terminalBlock", {"clampType": "springCage"}, None
        if sid == 786:                     # VH quick-connect: poke-in wire-to-board
            return "wireToBoard", {"termination": "poke-in"}, None
        return None, None, f"unclassifiable 'Spring Type' series ('{title}')"
    if cat in ("Automotive Connectors", "Compatible with Glow Wire Test"):
        if sub == "Wire-to-Board":
            return "wireToBoard", {"termination": "crimp"} if term_from_wiring != "idc" else {"termination": "idc"}, None
        if sub == "Wire-to-Wire":
            extra = {"termination": term_from_wiring} if term_from_wiring else {}
            return "wireToWire", extra, None
        if sub == "Board-to-Board":
            return "boardToBoard", {}, None
        if sub == "FFC/FPC":
            return "fpcFfc", {}, None
        if sub == "Card-to-Board":
            return "cardEdge", {}, None
        return None, None, f"automotive series with ambiguous/unmapped sub category '{sub or '-'}'"
    if cat == "Coaxial Connectors":
        return None, None, "rf family requires characteristicImpedance; JST does not publish it"
    return None, None, f"unmapped JST category '{cat}'"


# ------------------------------------------------------------------ record building
def build_datasheet_info(s, family, extra):
    """Series-level datasheetInfo body (everything except part.partNumber)."""
    spec, prof = s["spec"], s["profile"]

    electrical = {}
    worst = []          # (field, verbatim published string)
    cur, cur_multi = parse_current(spec.get("Current rating"))
    if cur is not None and cur > 0:
        electrical["ratedCurrentPerContact"] = cur
        if cur_multi:
            worst.append(("electrical.ratedCurrentPerContact", spec["Current rating"]))
    volt, volt_multi = parse_voltage(spec.get("Voltage rating"))
    if volt is not None and volt > 0:
        electrical["ratedVoltage"] = volt
        if volt_multi:
            worst.append(("electrical.ratedVoltage", spec["Voltage rating"]))
    ins = parse_insulation_resistance(spec.get("Insulation resistance"))
    if ins is not None:
        electrical["insulationResistance"] = ins
    dwv = parse_withstanding(spec.get("Withstanding voltage"))
    if dwv is not None:
        electrical["dielectricWithstandingVoltage"] = dwv

    mechanical = {}
    pitch = parse_pitch(spec.get("Pitch"))
    if pitch is not None:
        mechanical["pitch"] = pitch
    pos = parse_circuits(spec.get("Circuit"))
    if pos is not None:
        mechanical["positions"] = pos
    rows = ROWS.get((prof.get("Array (Insertion part)") or "").strip())
    if rows is not None:
        mechanical["rows"] = rows
    mount = MOUNT.get((prof.get("PC board mounting") or "").strip())
    if mount is not None:
        mechanical["mountingStyle"] = mount
    orient = ORIENT.get((prof.get("Mating direction") or "").strip())
    if orient is not None:
        mechanical["orientation"] = orient

    environmental = {}
    tr = parse_temp(spec.get("Temperature range"))
    if tr:
        environmental["operatingTemperature"] = tr
    wp = (prof.get("Waterproof") or "").strip()
    if wp:
        environmental["sealed"] = True
        if re.fullmatch(r"IPX?\d[A-Z0-9]*", wp):
            environmental["ipRating"] = wp
    if (prof.get("PC board mounting") or "").strip() == "Through-hole reflow":
        environmental["solderProcess"] = "throughHoleReflow"

    fd = {"family": family}
    fd.update({k: v for k, v in (extra or {}).items() if v is not None})
    if family in ("wireToBoard", "wireToWire", "power", "terminalBlock"):
        wg = parse_wire_gauge(spec.get("Conductor size"))
        if wg:
            fd["wireGaugeRange"] = wg

    di = {"electrical": electrical, "mechanical": mechanical, "familyDetails": fd}
    if environmental:
        di["environmental"] = environmental
    return di, worst


def series_codes(title):
    """Series code tokens published in the series title ('SR/SZ connector' -> SR, SZ)."""
    head = re.split(r"\bconnector\b|\bheader\b|\bterminal\b|\(", title or "", 1)[0]
    return [t for t in re.split(r"[\s/,]+", head.strip()) if t]


def pick_primary(pn, cs):
    """Home series of a part listed by several series: the one whose published series
    code appears as a token of the part number (e.g. B2B-XH-A -> 'XH connector', not the
    'NR connector' series that also lists it). Falls back to the lowest series id."""
    for c, d in sorted(cs, key=lambda c: c[0]["seriesId"]):
        for code in series_codes(c["title"]):
            if re.search(r"(^|[^A-Za-z0-9])" + re.escape(code) + r"([^A-Za-z0-9]|$)", pn):
                return (c, d)
    return min(cs, key=lambda c: c[0]["seriesId"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="download/refresh the HTML cache")
    args = ap.parse_args()
    if args.fetch:
        fetch_all()

    os.makedirs(OUT, exist_ok=True)
    paths = sorted(glob.glob(CACHE + "/*.html"),
                   key=lambda p: int(os.path.basename(p).split(".")[0]))
    if not paths:
        raise SystemExit(f"no cached series pages in {CACHE}; run with --fetch")
    series = [parse_series(p) for p in paths]

    stats = collections.Counter()
    fam_count = collections.Counter()
    worst_case_fields = collections.Counter()
    multi_source_worst = collections.Counter()
    # part number -> list of candidate (series, datasheetInfo)
    cands = collections.defaultdict(list)
    rejects = []

    for s in series:
        family, extra, reason = map_family(s)
        if family is None:
            stats["series_unmapped"] += 1
            for pn in s["products"]:
                rejects.append((pn, s, None, reason))
            continue
        stats["series_mapped"] += 1
        di, worst = build_datasheet_info(s, family, extra)
        s["_worst"] = worst
        if "ratedCurrentPerContact" not in di["electrical"]:
            stats["series_no_current"] += 1
            for pn in s["products"]:
                rejects.append((pn, s, di, "JST publishes no single per-contact current "
                                           "rating for this series (CONAS requires "
                                           "electrical.ratedCurrentPerContact)"))
            continue
        for pn in s["products"]:
            cands[pn].append((s, di))

    records, rejected = [], []
    seen_reject = set()

    for pn, cs in sorted(cands.items()):
        # merge: keep only values on which every source series agrees
        primary = pick_primary(pn, cs)
        if len(cs) == 1:
            di = json.loads(json.dumps(primary[1]))
        else:
            # A part number listed by several series (e.g. a PH header that JST also
            # lists under the compatible KR/KRD series, where it is rated 1 A instead of
            # 2 A). Electrical scalars collapse to the WORST published case and the
            # temperature range to the narrowest published range - both are figures JST
            # prints for this exact part number. Mechanical/environmental descriptors
            # have no worst case, so they survive only when every series agrees.
            di = {"electrical": {}, "mechanical": {}}
            cross_worst = []
            for block in ("electrical", "mechanical", "environmental"):
                keys = set()
                for _, d in cs:
                    keys |= set(d.get(block, {}).keys())
                merged = {}
                for k in keys:
                    raw = [d.get(block, {}).get(k) for _, d in cs]
                    present = [x for x in raw if x is not None]
                    if not present:
                        continue
                    vals = {json.dumps(x, sort_keys=True) for x in present}
                    if len(vals) == 1:
                        merged[k] = present[0]
                        continue
                    if block == "electrical":
                        merged[k] = min(present)          # worst case
                        multi_source_worst[k] += 1
                        cross_worst.append(block + "." + k)
                    elif k == "operatingTemperature":
                        merged[k] = {"minimum": max(x["minimum"] for x in present),
                                     "maximum": min(x["maximum"] for x in present)}
                        multi_source_worst[k] += 1
                        cross_worst.append(block + "." + k)
                if merged:
                    di[block] = merged
                elif block in ("electrical", "mechanical"):
                    di[block] = {}
            fams = {d["familyDetails"]["family"] for _, d in cs}
            if len(fams) != 1:
                rejected.append({"partNumber": pn, "reason":
                                 "listed in several JST series with conflicting "
                                 "connector families: " + ", ".join(sorted(fams))})
                continue
            fd = {"family": fams.pop()}
            fkeys = set()
            for _, d in cs:
                fkeys |= set(d["familyDetails"].keys())
            for k in fkeys - {"family"}:
                vals = [json.dumps(d["familyDetails"].get(k), sort_keys=True) for _, d in cs]
                if len(set(vals)) == 1 and vals[0] != "null":
                    fd[k] = json.loads(vals[0])
            if fd["family"] == "dataInterface" and "interfaceStandard" not in fd:
                rejected.append({"partNumber": pn, "reason":
                                 "listed in several JST series with conflicting "
                                 "interfaceStandard; no unambiguous value"})
                continue
            di["familyDetails"] = fd
        s = primary[0]
        di["part"] = {"partNumber": pn, "series": s["title"]}
        if s["description"]:
            di["part"]["description"] = s["description"][:1000]
        cross_worst = locals().get("cross_worst", []) if len(cs) > 1 else []
        prov = [{
            "source": "manufacturerParametric",
            "sourceName": "JST global product catalogue (jst-mfg.com)",
            "sourceUrl": c["url"],
            "retrievedDate": RETRIEVED,
        } for c, _ in sorted(cs, key=lambda c: c[0]["seriesId"])]
        for c, _ in sorted(cs, key=lambda c: c[0]["seriesId"]):
            for field, verbatim in c.get("_worst", []):
                if field.split(".")[-1] not in di.get(field.split(".")[0], {}):
                    continue
                prov.append({
                    "source": "manufacturerParametric",
                    "sourceName": ("JST global product catalogue (jst-mfg.com): worst "
                                   "(lowest) of the several ratings JST publishes for "
                                   "this series - verbatim \"%s\"" % verbatim)[:900],
                    "sourceUrl": c["url"],
                    "retrievedDate": RETRIEVED,
                    "fields": [field],
                })
                worst_case_fields[field] += 1
        if cross_worst:
            prov.append({
                "source": "manufacturerParametric",
                "sourceName": ("JST global product catalogue (jst-mfg.com): this part "
                               "number is listed by %d JST series whose published ratings "
                               "differ; the worst (lowest/narrowest) published figure was "
                               "taken" % len(cs)),
                "sourceUrl": None,
                "retrievedDate": RETRIEVED,
                "fields": sorted(set(cross_worst)),
            })
        di["provenance"] = prov

        mi = {"name": MANUFACTURER, "reference": pn, "status": "production",
              "family": s["title"], "datasheetInfo": di}
        if s["description"]:
            mi["description"] = s["description"][:1000]
        if s["catalogPdf"]:
            mi["datasheetUrl"] = s["catalogPdf"]
        records.append({"connector": {"manufacturerInfo": mi}})
        fam_count[di["familyDetails"]["family"]] += 1
        seen_reject.add(pn)

    staged = {r["connector"]["manufacturerInfo"]["reference"] for r in records}
    seen = set()
    for pn, s, di, reason in rejects:
        if pn in staged or pn in seen:
            continue
        seen.add(pn)
        rejected.append({"partNumber": pn, "series": s["title"],
                         "sourceUrl": s["url"], "reason": reason})

    with open(os.path.join(OUT, "records.ndjson"), "w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(OUT, "rejected.ndjson"), "w") as f:
        for r in rejected:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(json.dumps({
        "series_pages": len(series),
        "series_mapped": stats["series_mapped"],
        "series_unmapped": stats["series_unmapped"],
        "series_without_single_current_rating": stats["series_no_current"],
        "unique_part_numbers_seen": len(cands) + len({p for p, *_ in rejects}),
        "staged": len(records),
        "rejected": len(rejected),
        "families": dict(fam_count.most_common()),
        "worst_case_derived_fields": dict(worst_case_fields.most_common()),
        "worst_case_across_multiple_series": dict(multi_source_worst.most_common()),
    }, indent=2))


if __name__ == "__main__":
    main()
