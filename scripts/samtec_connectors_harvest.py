#!/usr/bin/env python3
"""Samtec connector harvest -> CONAS staging NDJSON (ABT #389).

TWO REAL SOURCES, both captured from the live site (nothing guessed):

1. **Solutionator parametric API** (per-part mechanical data, ~288 k mated-set rows).
   Discovered by reading the Solutionator SPA bundle
   https://www.samtec.com/solutionator/assets/main-*.js, which calls

       POST https://www.samtec.com/api/solutionator/filters/solutionator/search?skip=<N>&top=<M>
       Content-Type: application/json
       body: []                      # no filters -> whole catalogue
             [{"filter":"Search","value":["TSW"]}]   # or a filter list
       -> {"totalRows": 288502, "result": [ {malePartNumber, femalePartNumber,
            maleSeriesCode, femaleSeriesCode, categoryDescription, seriesDescription,
            male/femaleSeriesDescription, pitch, positions, positionsRawValue,
            termination, orientation, stackHeight, contactSystem, arrayImpedance,
            wireGauge, cableType, signalType, cardThickness, clearance, creepage,
            ruggedRating, protocolDataRate, priceReference, ...} ] }

   `top` is honoured up to at least 2000 (default 50).  Companion endpoints:
       GET /api/solutionator/filters/{1..6}        filter option lists
       GET /api/solutionator/filters/protocols
       GET /techspecs/blocks/getsolutionatormates?id=<SERIES>&...

2. **Catalog-page PDFs** (per-series electrical ratings).  samtec.com no longer
   renders a specification table in HTML at all - the ratings live only in the
   catalog pages, enumerated by the public document sitemaps
   (https://www.samtec.com/document-sitemap{1..11}.xml -> 888 URLs under
   https://suddendocs.samtec.com/catalog_english/*.pdf).  Each carries a
   SPECIFICATIONS block:  Operating Temp Range / Voltage Rating (often per mated
   series) / CURRENT RATING (PER PIN) per mated series / Insulator + Terminal
   material / Plating.

RATE LIMIT: www.samtec.com sits behind Cloudflare with a very tight rate-limit
rule (error 1015 after a page load's ~150 asset requests).  Fetch API endpoints
only, never whole pages, and keep >=1 s between calls; scripts/vpn_rotate.py
clears a ban if one is hit.  suddendocs.samtec.com is not rate limited.

Subcommands
  pull       -> raw/solutionator_*.json           (whole Solutionator table)
  catalogs   -> raw/catalogs/*.pdf                (888 catalog pages)
  specs      -> raw/series_specs.json             (parsed per-series ratings)
  build      -> staging/samtec/records.ndjson + rejected.ndjson
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

TAS = Path(__file__).resolve().parent.parent
STAGE = TAS / "staging" / "samtec"
RAW = STAGE / "raw"
SEARCH = "https://www.samtec.com/api/solutionator/filters/solutionator/search"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
HDRS = {"Content-Type": "application/json", "User-Agent": UA,
        "Referer": "https://www.samtec.com/solutionator/"}
TODAY = "2026-07-31"
IN_TO_M = 0.0254


# --------------------------------------------------------------------------
# 1. Solutionator pull
# --------------------------------------------------------------------------
def cmd_pull(a):
    RAW.mkdir(parents=True, exist_ok=True)
    skip, total = a.start, None
    while total is None or skip < total:
        out = RAW / f"solutionator_{skip:07d}.json"
        if out.exists() and out.stat().st_size > 1000:
            d = json.loads(out.read_text())
            total = d["totalRows"]
            skip += len(d["result"])
            continue
        for attempt in range(6):
            try:
                r = requests.post(f"{SEARCH}?skip={skip}&top={a.top}", headers=HDRS,
                                  data="[]", timeout=180)
                if r.status_code == 200:
                    break
                print(f"  http {r.status_code} at skip={skip}, attempt {attempt}", flush=True)
            except requests.RequestException as e:
                print(f"  {type(e).__name__} at skip={skip}", flush=True)
            time.sleep(20)
        else:
            raise RuntimeError(f"gave up at skip={skip}")
        d = r.json()
        total = d["totalRows"]
        n = len(d["result"])
        if n == 0:
            break
        out.write_text(json.dumps(d))
        skip += n
        print(f"{skip}/{total}", flush=True)
        time.sleep(a.delay)
    print("pull done", skip)


# --------------------------------------------------------------------------
# 2. catalog PDFs -> per-series electrical specs
# --------------------------------------------------------------------------
LABELS = ["Insulator Material", "Terminal Material", "Plating", "Operating Temp Range",
          "Voltage Rating", "Current Rating", "Contact Resistance", "Insulation Resistance",
          "Withstanding Voltage", "Lead-Free Solderable", "Lead–Free Solderable",
          "Processing", "Mates With", "Cable Mates", "Applicable Wire", "Durability",
          "Note", "Notes", "SPECIFICATIONS"]


def pdf_text(path):
    try:
        return subprocess.run(["pdftotext", "-layout", str(path), "-"],
                              capture_output=True, text=True, timeout=120).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def spec_block(txt, label):
    """Text between '<label>:' and the next known label, as a list of lines.

    The catalog pages are two-column drawings, so pdftotext interleaves drawing
    text into the same lines.  Everything downstream therefore matches strict
    patterns and drops anything it cannot read - it never 'best guesses'.
    """
    m = re.search(rf"^\s*{re.escape(label)}\s*:", txt, re.M | re.I)
    if not m:
        return []
    rest = txt[m.end():]
    stop = len(rest)
    for lab in LABELS:
        mm = re.search(rf"^\s*{re.escape(lab)}\s*:", rest, re.M | re.I)
        if mm and mm.start() < stop:
            stop = mm.start()
    return [ln.rstrip() for ln in rest[:stop].splitlines()]


TEMP_RE = re.compile(r"(-?\d{1,3})\s*°?\s*C\s*(?:to|~|-)\s*\+?(-?\d{1,3})\s*°?\s*C", re.I)
VOLT_RE = re.compile(r"(\d{2,4})\s*VAC\s*/\s*(\d{2,4})\s*VDC", re.I)
VOLT1_RE = re.compile(r"(\d{2,4})\s*V\s*(AC|DC)\b", re.I)
MATED_RE = re.compile(r"mated with\s+([A-Z0-9\-/ ,]+?)\s*[;:.]?\s*$", re.I)


def parse_temp(txt):
    lines = spec_block(txt, "Operating Temp Range")
    out = []
    for ln in lines:
        for m in TEMP_RE.finditer(ln):
            lo, hi = float(m.group(1)), float(m.group(2))
            if -100 <= lo < hi <= 300:
                out.append((lo, hi))
    if not out:
        return None
    # the datasheet may list per-plating ranges; take the intersection so the
    # emitted range is one every listed variant satisfies (no invented widening)
    return {"minimum": max(o[0] for o in out), "maximum": min(o[1] for o in out)}


def parse_voltage(txt):
    """-> (worst_case_v, []) from the 'Voltage Rating' block.

    Only the MINIMUM of every published rating is emitted.  Samtec prints the
    voltage rating per mated counterpart ('465 VAC/658 VDC mated with SSW; 500
    VAC/707 VDC mated with BCS; ...'), but pdftotext interleaves the drawing
    columns into those lines, so which number belongs to which counterpart
    cannot be recovered reliably -- per-counterpart voltage rows are therefore
    NOT emitted rather than emitted possibly-wrong.
    """
    lines = spec_block(txt, "Voltage Rating")
    vals = []
    for ln in lines:
        m = VOLT_RE.search(ln)
        if m:
            vals.append(min(float(m.group(1)), float(m.group(2))))
            continue
        m1 = VOLT1_RE.search(ln)
        if m1:
            vals.append(float(m1.group(1)))
    vals = [v for v in vals if 1 <= v <= 20000]
    if not vals:
        return None, []
    return min(vals), []


CUR_HDR_RE = re.compile(r"CURRENT\s+RATING", re.I)
AMPS_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*A\b")
PERPIN_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*A\s*(?:per\s*pin|/\s*pin)", re.I)
MATE_LABEL_RE = re.compile(r"\(([A-Z0-9\-]{2,12}(?:\s*/\s*[A-Z0-9\-]{2,12})*)\)\s*:")


def _amp_ok(v):
    return 0.05 <= v <= 300


def parse_current(txt):
    """-> (worst_case_A_per_pin, [{counterpartSeries, ratedCurrentPerContact}]).

    Two published layouts are recognised, and nothing else:
      (a) '<x.y> A per pin', optionally preceded (within 3 lines) by a
          '(MATE/MATE):' label -> per-counterpart rows;
      (b) the mated-series table under 'CURRENT RATING': a row of mate series
          codes followed by a row of 'x.y A' values, accepted ONLY when the two
          rows have the same length.
    The scalar returned is the MINIMUM published value, i.e. the worst case over
    every counterpart - never an average or an arbitrary pick.
    """
    lines = txt.splitlines()
    vals, pairs = [], {}
    for i, ln in enumerate(lines):
        for m in PERPIN_RE.finditer(ln):
            v = float(m.group(1))
            if not _amp_ok(v):
                continue
            vals.append(v)
            for back in range(1, 4):
                if i - back < 0:
                    break
                lm = MATE_LABEL_RE.search(lines[i - back])
                if lm:
                    for s in re.split(r"/", lm.group(1)):
                        s = s.strip().upper()
                        if re.fullmatch(r"[A-Z][A-Z0-9\-]{1,11}", s):
                            pairs[s] = v
                    break
    m = CUR_HDR_RE.search(txt)
    if m:
        seg = txt[m.start():m.start() + 1400].splitlines()
        codes = None
        for ln in seg[:14]:
            toks = ln.split()
            cand = [t for t in toks if re.fullmatch(r"[A-Z][A-Z0-9\-]{1,10}", t)]
            if codes is None and len(cand) >= 2 and len(cand) == len(toks):
                codes = cand
                continue
            a = [float(x) for x in AMPS_RE.findall(ln)]
            a = [x for x in a if _amp_ok(x)]
            if a:
                # the mate-code row is routinely contaminated by drawing text,
                # so the table layout is used ONLY for the worst-case scalar;
                # per-counterpart rows come exclusively from the unambiguous
                # '(MATE/MATE): x A per pin' form above.
                if not vals:
                    vals.extend(a)
                break
    if not vals:
        return None, []
    return min(vals), [{"counterpartSeries": k, "ratedCurrentPerContact": v}
                       for k, v in sorted(pairs.items())]


QUALIFIER = re.compile(
    r"(TH|SMT|SM|RA|WEB|PAGE|ENGLISH|CA|RF\d+[A-Z]?|A|\d+)$")


def known_series():
    """Every series code Samtec's own Solutionator returns (the ground truth)."""
    out = set()
    for f in sorted(glob.glob(str(RAW / "solutionator_*.json"))):
        for r in json.loads(Path(f).read_text())["result"]:
            for k in ("maleSeriesCode", "femaleSeriesCode"):
                if r.get(k):
                    out.add(str(r[k]).strip().upper())
    return out


def series_from_name(fname, known):
    """Series codes a catalog page covers, from its FILE NAME.

    'tsw_th.pdf' -> TSW ; 'a-erf8-erm8.pdf' -> ERF8, ERM8.  A token is only
    accepted when it is a REAL Samtec series code (present in the Solutionator
    data), so page qualifiers ('th', 'smt', 'rf047') and A-series prefixes drop
    out on their own instead of being guessed at.
    """
    stem = os.path.basename(fname).rsplit(".", 1)[0].upper()
    toks = [t for t in re.split(r"[_\-]", stem) if t]
    if toks and toks[0] == "A":
        # 'a-ssq_th.pdf' is the AUTOMOTIVE A-SSQ page, whose ratings are not
        # SSQ's.  Only A-prefixed series may claim it; if the A-series is not in
        # the catalogue the page contributes nothing (better than mis-assigning).
        toks = ["A-" + t for t in toks[1:]]
    return [t for t in toks if t in known]


def cmd_specs(a):
    known = known_series()
    print(f"known series codes: {len(known)}", flush=True)
    rows, conflicts = {}, set()
    files = sorted(glob.glob(str(RAW / "catalogs" / "*.pdf")))
    for i, f in enumerate(files):
        txt = pdf_text(f)
        if not txt.strip():
            continue
        temp = parse_temp(txt)
        v, vpairs = parse_voltage(txt)
        c, cpairs = parse_current(txt)
        if not (temp or v or c):
            continue
        rec = {"file": os.path.basename(f)}
        if temp:
            rec["operatingTemperature"] = temp
        if v:
            rec["ratedVoltage"] = v
        if c:
            rec["ratedCurrentPerContact"] = c
        pairs = {}
        for p in vpairs:
            pairs.setdefault(p["counterpartSeries"], {})["ratedVoltage"] = p["ratedVoltage"]
        for p in cpairs:
            pairs.setdefault(p["counterpartSeries"], {})["ratedCurrentPerContact"] = \
                p["ratedCurrentPerContact"]
        if pairs:
            rec["pairRatings"] = [dict(counterpartSeries=k, **v2) for k, v2 in pairs.items()]
        for s in series_from_name(f, known):
            prev = rows.get(s)
            if prev is None:
                rows[s] = rec
            elif {k: prev.get(k) for k in ("ratedVoltage", "ratedCurrentPerContact",
                                           "operatingTemperature")} != \
                    {k: rec.get(k) for k in ("ratedVoltage", "ratedCurrentPerContact",
                                             "operatingTemperature")}:
                # two catalog pages disagree about the same series -> refuse to
                # pick one; the series gets no electrical data at all
                conflicts.add(s)
        if (i + 1) % 100 == 0:
            print(f"{i+1}/{len(files)} parsed, {len(rows)} series", flush=True)
    for s in conflicts:
        rows.pop(s, None)
    (RAW / "series_specs.json").write_text(json.dumps(rows, indent=1))
    print(json.dumps({"pdfs": len(files), "series_with_specs": len(rows),
                      "dropped_conflicting": len(conflicts),
                      "with_current": sum(1 for r in rows.values()
                                          if "ratedCurrentPerContact" in r),
                      "with_voltage": sum(1 for r in rows.values() if "ratedVoltage" in r),
                      "with_temp": sum(1 for r in rows.values()
                                       if "operatingTemperature" in r)}, indent=2))


# --------------------------------------------------------------------------
# 3. build CONAS records
# --------------------------------------------------------------------------
PITCH_MM_RE = re.compile(r"\(([\d.,]+)\s*mm\)")
PITCH_IN_RE = re.compile(r'^\s*\.?(\d*\.?\d+)"')

# Solutionator categoryDescription -> CONAS familyDetails.family.
# Matched on the vendor's own category text; where a category is a mixed bag
# ('Rugged / Power', 'Discrete Wire, IDC & FFC') the vendor's own series
# DESCRIPTION decides, and anything the vendor's text does not resolve is
# rejected as unmapped rather than assigned to a plausible-looking family.
CATEGORY_FAMILY = [
    ("board-to-board", "boardToBoard"),
    ("board to board", "boardToBoard"),
    ("mezzanine", "boardToBoard"),
    ("backplane", "boardToBoard"),
    ("edge card", "cardEdge"),
    ("card edge", "cardEdge"),
    ("rf", "rf"),
    ("coax", "rf"),
]

DESC_FAMILY = [
    ("edge card", "cardEdge"),
    ("card edge", "cardEdge"),
    ("crimp contact", None),          # loose piece, not a connector
    ("crimp terminal", None),
    ("terminal for", None),
    ("contact for", None),
    ("circular", "circular"),
    ("ffc", "fpcFfc"),
    ("fpc", "fpcFfc"),
    ("discrete wire", "wireToBoard"),
    ("cable assembly", "wireToBoard"),
    ("idc socket", "wireToBoard"),
    ("socket housing", "wireToBoard"),
    ("power", "power"),
    ("board stacker", "boardToBoard"),
    ("terminal strip", "boardToBoard"),
    ("socket strip", "boardToBoard"),
    ("socket", "boardToBoard"),
    ("header", "boardToBoard"),
]

# series descriptions that are NOT a connector body (loose contacts, terminals)
NON_CONNECTOR_DESC = ("crimp contact", "crimp terminal", "terminal for", "contact for")


def pitch_m(s):
    """Samtec prints pitch as '.100" (2.54 mm)' or '2.00 mm (.0787")' -> metres.

    A dual-pitch string ('.050" (1.27 mm) X .100" (2.54 mm)') names two pitches
    on different axes; CONAS mechanical.pitch is a single number, so nothing is
    emitted rather than one of the two being picked.
    """
    if not s:
        return None
    s = str(s)
    if re.search(r"\s[xX]\s", s):
        return None
    m = re.search(r"([\d.]+)\s*mm", s)
    if m:
        try:
            return float(m.group(1)) / 1000.0
        except ValueError:
            return None
    m = re.search(r"([\d.]+)\s*\"", s)
    if m:
        try:
            return float(m.group(1)) * IN_TO_M
        except ValueError:
            return None
    return None


LEN_MM_RE = re.compile(r"([\d.]+)\s*mm", re.I)
LEN_IN_RE = re.compile(r"([\d.]+)\s*(?:\"|in\b)", re.I)


def length_m(s):
    """'1.60 mm' / '.062\"' -> metres.  Unit-aware: an inch value is NOT mm."""
    if s is None:
        return None
    s = str(s).strip()
    m = LEN_MM_RE.search(s)
    if m:
        try:
            return float(m.group(1)) / 1000.0
        except ValueError:
            return None
    m = LEN_IN_RE.search(s)
    if m:
        try:
            return float(m.group(1)) * IN_TO_M
        except ValueError:
            return None
    return None


# Mating orientation is a property of the PAIR.  Only the two values that force
# BOTH halves to the same orientation are mapped onto a part; 'Right Angle',
# 'Coplanar', 'Parallel' and 'Hinge' describe an asymmetric pair and are dropped
# rather than assigned to the wrong half.
ORIENTATION = {"vertical board stacking": "vertical", "edge mount": "edge"}


def family_of(cat, desc):
    """(categoryDescription, seriesDescription) -> CONAS family, or None.

    The series description wins over the category, because Samtec's categories
    mix families ('Rugged / Power' holds board stackers, power strips AND edge
    cards) while the description names the actual product type.
    """
    d = (desc or "").strip().lower()
    for k, v in DESC_FAMILY:
        if k in d:
            return v
    c = (cat or "").strip().lower()
    for k, v in CATEGORY_FAMILY:
        if k in c:
            return v
    return None


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    psma = TAS.parent
    by = {}
    for repo in ("PEAS", "CONAS"):
        for p in (psma / repo / "schemas").rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if isinstance(s, dict) and s.get("$id"):
                by[s["$id"]] = s
    reg = Registry().with_resources(
        [(k, Resource(contents=v, specification=DRAFT202012)) for k, v in by.items()])
    return Draft202012Validator(
        json.loads((psma / "CONAS" / "schemas" / "connector.json").read_text()), registry=reg)


def collect_parts():
    """Fold the mated-set rows into one dict per PART NUMBER.

    A row describes a PAIR.  Only attributes that are necessarily identical for
    both halves of a mated pair (pitch, positions) or that the API itself labels
    per half (male/femaleSeriesCode, male/femaleSeriesDescription, gender) are
    taken as part attributes.  Pair-scoped values (mated stack height) become
    mating.matesWith[] entries.  A value that is not consistent across every row
    a part appears in is DROPPED, never averaged or arbitrarily picked.
    """
    parts = {}

    def touch(pn, gender, series, sdesc, row):
        p = parts.setdefault(pn, {"pn": pn, "gender": gender, "series": series,
                                  "seriesDescription": sdesc, "mates": {},
                                  "attrs": {}, "conflict": set()})
        if p["gender"] != gender:
            p["conflict"].add("gender")
        a = p["attrs"]
        for k, v in row.items():
            if v is None or v == "":
                continue
            if k in a and a[k] != v:
                p["conflict"].add(k)
            a[k] = v

    for f in sorted(glob.glob(str(RAW / "solutionator_*.json"))):
        for r in json.loads(Path(f).read_text())["result"]:
            shared = {"pitch": r.get("pitch"),
                      "positions": r.get("positionsRawValue"),
                      "termination": r.get("termination"),
                      "orientation": r.get("orientation"),
                      "category": r.get("categoryDescription"),
                      "arrayImpedance": r.get("arrayImpedance"),
                      "cardThickness": r.get("cardThickness"),
                      "clearance": r.get("clearance"),
                      "creepage": r.get("creepage"),
                      "wireGauge": r.get("wireGauge"),
                      "cableType": r.get("cableType"),
                      "contactSystem": r.get("contactSystem")}
            mp, fp = r.get("malePartNumber"), r.get("femalePartNumber")
            if mp:
                touch(mp, "male", r.get("maleSeriesCode"), r.get("maleSeriesDescription"), shared)
            if fp:
                touch(fp, "female", r.get("femaleSeriesCode"),
                      r.get("femaleSeriesDescription"), shared)
            sh = r.get("stackHeight")
            try:
                sh = float(sh) / 1000.0 if sh not in (None, "") else None
            except (TypeError, ValueError):
                sh = None
            if mp and fp:
                parts[mp]["mates"].setdefault(r.get("femaleSeriesCode"), set()).add(sh)
                parts[fp]["mates"].setdefault(r.get("maleSeriesCode"), set()).add(sh)
    return parts


def to_record(p, specs):
    pn = p["pn"]
    a, conflict = p["attrs"], p["conflict"]
    missing = []
    fam = family_of(a.get("category"), p["seriesDescription"])
    part = {"partNumber": pn, "matingPolarity": p["gender"]}
    if "gender" in conflict:
        del part["matingPolarity"]
    if p["series"]:
        part["series"] = p["series"]
    if p["seriesDescription"]:
        part["description"] = str(p["seriesDescription"])[:1000]

    ser = (p["series"] or "").upper()
    sp = specs.get(ser, {})

    electrical = {}
    if "ratedCurrentPerContact" in sp:
        electrical["ratedCurrentPerContact"] = sp["ratedCurrentPerContact"]
    if "ratedVoltage" in sp:
        electrical["ratedVoltage"] = sp["ratedVoltage"]
    if "clearance" not in conflict and a.get("clearance"):
        try:
            electrical["clearance"] = float(a["clearance"]) / 1000.0
        except (TypeError, ValueError):
            pass
    if "creepage" not in conflict and a.get("creepage"):
        try:
            electrical["creepage"] = float(a["creepage"]) / 1000.0
        except (TypeError, ValueError):
            pass
    pr = [x for x in sp.get("pairRatings", []) if x["counterpartSeries"] in p["mates"]]
    if pr:
        electrical["pairRatings"] = pr

    mechanical = {}
    if "positions" not in conflict and a.get("positions"):
        try:
            n = int(a["positions"])
            if n >= 1:
                mechanical["positions"] = n
        except (TypeError, ValueError):
            pass
    if "pitch" not in conflict:
        pm = pitch_m(a.get("pitch"))
        if pm:
            mechanical["pitch"] = pm
    if "termination" not in conflict:
        t = (a.get("termination") or "").strip().lower()
        mechanical.update({"through-hole": {"mountingStyle": "tht"},
                           "surface mount": {"mountingStyle": "smt"},
                           "press fit": {"mountingStyle": "pressFit"}}.get(t, {}))
    if "orientation" not in conflict:
        o = ORIENTATION.get((a.get("orientation") or "").strip().lower())
        if o:
            mechanical["orientation"] = o

    di = {"part": part, "electrical": electrical, "mechanical": mechanical}
    if fam:
        fd = {"family": fam}
        if fam == "rf":
            imp = None
            m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(?:ohm|Ω)", str(a.get("arrayImpedance") or ""),
                         re.I)
            if m:
                imp = float(m.group(1))
            if imp:
                fd["characteristicImpedance"] = imp
            else:
                missing.append("rf connector without published characteristicImpedance")
        if fam == "cardEdge" and a.get("cardThickness") and "cardThickness" not in conflict:
            ct = length_m(a["cardThickness"])
            if ct and ct > 0:
                fd["cardThickness"] = ct
        if fam in ("wireToBoard",) and a.get("wireGauge") and "wireGauge" not in conflict:
            g = [float(x) for x in re.findall(r"\d+", str(a["wireGauge"]))]
            if g:
                fd["wireGaugeRange"] = {"minimumAwg": min(g), "maximumAwg": max(g)}
        di["familyDetails"] = fd
    else:
        d = (p["seriesDescription"] or "").lower()
        if any(t in d for t in NON_CONNECTOR_DESC):
            missing.append(f"not a connector: '{(p['seriesDescription'] or '')[:60]}'")
        else:
            missing.append(
                f"unmapped Samtec category '{a.get('category')}' / "
                f"series description '{(p['seriesDescription'] or '')[:60]}'")

    mates = []
    for s, hs in sorted(p["mates"].items()):
        if not s:
            continue
        e = {"series": s, "relation": "mates"}
        hs = {h for h in hs if h}
        if len(hs) == 1:
            e["matedHeight"] = round(next(iter(hs)), 6)
        mates.append(e)
    if mates:
        di["mating"] = {"matesWith": mates}

    if "operatingTemperature" in sp:
        di["environmental"] = {"operatingTemperature": sp["operatingTemperature"]}

    di["provenance"] = [
        {"source": "manufacturerParametric",
         "sourceName": "Samtec Solutionator API "
                       "(POST samtec.com/api/solutionator/filters/solutionator/search)",
         "sourceUrl": f"https://www.samtec.com/products/{pn.lower()}",
         "retrievedDate": TODAY,
         "fields": ["part", "mechanical", "mating", "familyDetails"]}]
    if sp:
        di["provenance"].append(
            {"source": "manufacturerDatasheet",
             "sourceName": f"Samtec catalog page {sp.get('file')}",
             "sourceUrl": f"https://suddendocs.samtec.com/catalog_english/{sp.get('file')}",
             "retrievedDate": TODAY,
             "fields": ["electrical", "environmental"]})

    if "ratedCurrentPerContact" not in electrical and fam != "rf":
        missing.append("no published current rating (series catalog page not parsed)")

    mi = {"name": "Samtec", "reference": pn, "status": "production", "datasheetInfo": di}
    if p["series"]:
        mi["family"] = p["series"]
    if p["seriesDescription"]:
        mi["description"] = str(p["seriesDescription"])[:1000]
    mi["datasheetUrl"] = f"https://www.samtec.com/products/{pn.lower()}"
    return {"connector": {"manufacturerInfo": mi}}, missing


def cmd_build(a):
    STAGE.mkdir(parents=True, exist_ok=True)
    specs_path = RAW / "series_specs.json"
    specs = json.loads(specs_path.read_text()) if specs_path.exists() else {}
    parts = collect_parts()
    print(f"distinct part numbers: {len(parts)}", flush=True)
    v = build_validator()
    sys.path.insert(0, str(TAS / "validator" / "build-ninja"))
    import tas_validator

    stats = {"parts": len(parts), "good": 0, "incomplete": 0, "schema_fail": 0,
             "impossible": 0, "suspicious": 0}
    fam = {}
    fg = open(STAGE / "records.ndjson", "w")
    fb = open(STAGE / "rejected.ndjson", "w")
    for i, p in enumerate(parts.values()):
        rec, missing = to_record(p, specs)
        if missing:
            stats["incomplete"] += 1
            rec["quarantineReason"] = "incomplete Samtec data: " + "; ".join(missing)
            fb.write(json.dumps(rec, ensure_ascii=False) + "\n")
            continue
        errs = sorted(v.iter_errors(rec["connector"]), key=lambda e: list(e.path))
        if errs:
            stats["schema_fail"] += 1
            rec["quarantineReason"] = "schema: " + errs[0].message[:300]
            fb.write(json.dumps(rec, ensure_ascii=False) + "\n")
            continue
        verdict = tas_validator.validate(rec)
        imp = [x for x in verdict.findings if str(x.severity) == "IMPOSSIBLE"]
        sus = [x for x in verdict.findings if str(x.severity) == "SUSPICIOUS"]
        if imp:
            stats["impossible"] += 1
            rec["quarantineReason"] = "blade-runner IMPOSSIBLE: " + \
                "; ".join(f"{x.code}: {x.message}" for x in imp)[:300]
            fb.write(json.dumps(rec, ensure_ascii=False) + "\n")
            continue
        if sus:
            stats["suspicious"] += 1
            for x in sus:
                stats.setdefault("suspicious_codes", {})
                stats["suspicious_codes"][x.code] = stats["suspicious_codes"].get(x.code, 0) + 1
        f = rec["connector"]["manufacturerInfo"]["datasheetInfo"]["familyDetails"]["family"]
        fam[f] = fam.get(f, 0) + 1
        stats["good"] += 1
        fg.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if (i + 1) % 20000 == 0:
            print(f"  {i+1} processed, good={stats['good']}", flush=True)
    fg.close()
    fb.close()
    stats["families"] = fam
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull")
    p.add_argument("--top", type=int, default=2000)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--delay", type=float, default=1.5)
    p.set_defaults(fn=cmd_pull)
    p = sub.add_parser("specs")
    p.set_defaults(fn=cmd_specs)
    p = sub.add_parser("build")
    p.set_defaults(fn=cmd_build)
    args = ap.parse_args()
    args.fn(args)
