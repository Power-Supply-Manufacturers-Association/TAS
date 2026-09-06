#!/usr/bin/env python3
"""GUARD: the six per-catalogue audits, as arithmetic instead of tokens.

    python3 scripts/catalogue_audit.py [--data DIR] [--file F ...] [--json PATH]
                                       [--only CHECK ...] [--limit N] [--selftest]

Exit 0 = clean, exit 1 = findings, exit 2 = the audit could not run.

WHY THIS EXISTS
---------------
On 2026-09-05 roughly 900,000 model tokens were spent having agents read
catalogues and count things. Every defect they found was reachable by
arithmetic: an identity that is a series name with a parametric grid row glued
to it, a voltage 1e3 out from its own field's median, a cohort of 16,707 rows
that all carry the same number, a citation that points at a search box. The
expensive judgement -- is this rule measuring the parts, or measuring my parser?
-- was spent once, when the rules below were written and counter-checked. Running
them is free, so they should run nightly and block on what is NEW.

READ-ONLY. This never writes to data/. It opens each live catalogue once,
streams it a line at a time, and holds only summaries.

THE SIX CHECKS, AND WHY EACH IS SHAPED THE WAY IT IS
----------------------------------------------------
1. IDENTITY SANITY -- three signals, because no single one survives contact with
   real vendor part numbers.
     (a) SERIES-PREFIX. The identity starts with the row's own `series`
         verbatim, is longer than it, and the remainder carries a space, comma,
         paren or percent. That is the exact shape of the bug: the importer
         concatenated the series with the parametric grid's row label. It is
         what makes "NTCACAP Series for Refrigerator (R25=10KOhm, tol=2%)" a
         finding while leaving a genuine suffixed order code alone.
     (b) LENGTH > 40. Swept over 934,343 live rows with zero false positives.
     (c) CROSS-ROW. Two rows in one manufacturer+series group carrying the
         byte-identical identity. One of them is not the part it claims to be.
   Deliberately NOT gated on: bare whitespace (Ohmite ships "HS50 R3 F"),
   parens or commas (Molex packaging codes -- 18,350 legitimate connector hits),
   punctuation (Murata genuinely ships "#A914BYW-150M=P3"), or an "xxx"
   substring ("SCPX-AGA-XXX-010-C" is vendor wildcard notation). Each of those
   buries ~618 real defects under noise, which is the same as not having a check.

2. UNIT PLAUSIBILITY -- per catalogue, per numeric field: count / min / p1 /
   median / p99 / max / distinct, then flag
     - extremes more than 1e3 from the field median,
     - clustering at an exact 1e3 / 1e6 / 1e9 ratio (a unit-conversion bug does
       not scatter, it lands on a decade),
     - a field spanning more than 9 orders of magnitude,
     - the physically impossible: negative resistance / capacitance /
       inductance, efficiency > 1, temperature < -273.15 K, duty > 1.
   Fields are read INSIDE ARRAY ELEMENTS. The 11.1 MV connector hid at
   electrical.insulationPaths[0].ratedImpulseVoltage and a flat scan missed it,
   so list indices are collapsed to `[]` and the elements walked.

3. COHORT UNIFORMITY -- per (catalogue, provenance[0].sourceName,
   retrievedDate) cohort of >= 100 rows, the share of the most common value in
   each numeric field.
   CRITICAL, and the reason this check was nearly worthless: a shared VALUE and
   shared NULLNESS are not the same finding. An agent reported 16,707 rows
   "carrying electrolytic lifetime defaults on ceramics"; the fields were all
   null, so "100 % share the most common value" was both true and empty. Nulls
   and absences are excluded from uniformity here and reported separately as
   UNIFORMLY_ABSENT, which is a coverage fact, not a defect.
   A cohort-uniform field is only interesting if the CATALOGUE is not uniform in
   it -- a field every part shares is a constant, not a minted one -- so the
   same value's share across the whole catalogue must be below --catalogue-share.

4. CITATION SHAPE -- per catalogue: provenance entries whose sourceUrl is a
   search or query endpoint; one literal URL shared by more than 100 rows; and
   per row, whether ANY entry is part-specific (its URL contains the row's own
   identity). Rows where nothing is part-specific have no evidence attached to
   THEM, only to their neighbourhood.
   Excluded as legitimate, because they are genuinely per-part or genuinely
   cover the series: a vendor per-SKU detail page carrying the part in the query
   (Murata productdetail?partno=, TDK part_no=, Yuden or/detail?pn=), a
   document-by-id endpoint (TE DocumentDelivery), and a family datasheet
   document whose URL names the series.

5. VERIFICATION CONSISTENCY -- an entry with `verification` and no
   `verificationDate`; `retracted: true` with no `retractionReason`;
   `verification: disproven` that was never retracted; and
   `verification: valuesReadFromSource` whose sourceUrl is a search page or a
   bare landing page -- nobody read a value off a search box.

6. FIELD COVERAGE -- per catalogue, per field, the population rate; and
   schema-REQUIRED keys present but vacuous (`{}` / `[]` / `""`). A vacuous
   required object validates under Draft 2020-12 and carries nothing, which is
   the failure mode a required-field check is supposed to catch and does not.

OUTPUT
------
Findings print in the house shape --  `line N: <identity> -- <why>` -- so
integrity_scan.py parses them like every other guard. Checks that can fire on
six-figure row counts (2 extremes, 3, 4, 6) emit ONE aggregate finding per
(catalogue, field, reason) with a representative line, because a nightly diff of
134,949 individual lines is not a report. Per-row findings are reserved for the
checks that are countable by hand: identity, impossible values, verification.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DATA = REPO / "data"

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Check 7's arithmetic is defined ONCE, in the standing guard, and imported here
# and by the ingest gate, so the three cannot drift apart.
from check_no_fabricated_parts import seed_expanded_mosfet  # noqa: E402

CHECKS = ("identity", "units", "cohort", "citation", "verification", "coverage",
          "generator")

# ---------------------------------------------------------------------------
# catalogue -> (discriminator path, sibling repo, schema file)
# Same map tests/test_data.py validates against; kept here so the audit and the
# validator disagree loudly rather than quietly.
# ---------------------------------------------------------------------------
CATALOGUES = {
    "mosfets.ndjson":       (["semiconductor", "mosfet"], "SAS", "mosfet.json"),
    "diodes.ndjson":        (["semiconductor", "diode"],  "SAS", "diode.json"),
    "igbts.ndjson":         (["semiconductor", "igbt"],   "SAS", "igbt.json"),
    "bjts.ndjson":          (["semiconductor", "bjt"],    "SAS", "bjt.json"),
    "modules.ndjson":       (["semiconductor", "module"], "SAS", "module.json"),
    "capacitors.ndjson":    (["capacitor"],               "CAS", "capacitor.json"),
    "resistors.ndjson":     (["resistor"],                "RAS", "resistor.json"),
    "varistors.ndjson":     (["varistor"],                "RAS", "varistor.json"),
    "thermistors.ndjson":   (["thermistor"],              "RAS", "thermistor.json"),
    "magnetics.ndjson":     (["magnetic"],                "MAS", "magnetic.json"),
    "controllers.ndjson":   (["controller"],              "CTAS", "controller.json"),
    "analog_ics.ndjson":    (["analog"],                  "AAS", "AAS.json"),
    "connectors.ndjson":    (["connector"],               "CONAS", "connector.json"),
    "timing_devices.ndjson": (["timeBase"],               "TDAS", "tdas.json"),
    "circuits.ndjson":      ([],                          "CIAS", "CIAS.json"),
    "converters.ndjson":    ([],                          "TAS",  "TAS.json"),
}

# ---------------------------------------------------------------------------
# 1. identity
# ---------------------------------------------------------------------------
IDENT_MAX_LEN = 40
# The grid-row residue. Two conditions, and BOTH are load-bearing:
#   * it starts with whitespace -- the importer glued a description onto the
#     series with a space. Without this, every order code that merely BEGINS
#     with its series code is a finding: 'CX' + '90MW9-24P(002)' and 'FF1' +
#     '(100)-240X240T0800' are single order codes, not series plus junk, and
#     they alone contributed 7,295 false positives.
#   * what remains carries a space, comma, paren or percent -- so a genuine
#     packaging suffix ('... TR', '... T/R') is not mistaken for a grid row.
GRID_RESIDUE_HEAD = re.compile(r"^\s")
GRID_RESIDUE = re.compile(r"[ ,()%]")

# ---------------------------------------------------------------------------
# 2. units
# ---------------------------------------------------------------------------
EXTREME_RATIO = 1e3          # distance from the field median that is a finding
SPAN_DECADES = 9             # max/min beyond this many decades is a finding
RESERVOIR = 20000            # per-field sample for quantiles; seeded, so stable
EXTREME_KEEP = 40            # rows retained at each tail, with line numbers
DISTINCT_CAP = 50000

# Distributor commerce data legitimately spans decades -- a 4,000-piece MOQ
# against a median of 1, a $380 part against a median of $0.22 -- and is not a
# unit error. It is excluded from the unit checks rather than left to bury them.
UNITS_EXCLUDE = re.compile(r"(?:^|\.)distributorsInfo\b", re.I)

# The rules match the QUANTITY NAME, anchored at the end -- never a substring.
# Substring matching produced 89,044 false positives in one run and buried the
# real ones: `impedance.phase` is legitimately negative and matched "impedance";
# `inductancePoints[].temperature = -40` matched "inductance"; a resistor's
# `temperatureCoefficient = -500 ppm/K` matched "temperature" and was reported as
# below absolute zero; and a P-channel MOSFET's `onResistanceVgs = -10 V` -- the
# CONDITION at which R_DS(on) is quoted, not a resistance -- matched "resistance".
# Anchoring at the end keeps onResistance and dcResistance while dropping every
# one of those.
def _ends(*words):
    return re.compile(r"(?:^|[a-z0-9])(?:" + "|".join(words) + r")$", re.I)


IMPOSSIBLE = (
    (_ends("resistance", "capacitance", "inductance", "impedance"),
     lambda v: v < 0, "negative"),
    (_ends("efficiency"), lambda v: v > 1.0, "efficiency > 1"),
    (_ends("temperature"), lambda v: v < -273.15, "below absolute zero"),
    (_ends("dutycycle", "duty"), lambda v: v > 1.0, "duty > 1"),
)

# A bound on a quantity is the quantity: capacitance.nominal is a capacitance.
BOUND_LEAVES = {"nominal", "minimum", "maximum", "typical", "typ", "value"}

# ---------------------------------------------------------------------------
# 3. cohort
# ---------------------------------------------------------------------------
MIN_COHORT = 100
UNIFORM_SHARE = 0.98
CATALOGUE_SHARE = 0.90       # above this the value is a catalogue constant
COHORT_FIELD_CAP = 4096      # distinct values held per cohort field

# ---------------------------------------------------------------------------
# 4. citation
# ---------------------------------------------------------------------------
SEARCH_URL = re.compile(
    r"(?:/search\b|/search/|searchresult|/results?\b|/find\b"
    r"|[?&](?:q|query|keyword|kw|search|searchterm|term|text|filter)=)", re.I)
# A per-SKU detail page: the part is IN the query. Murata productdetail?partno=,
# TDK info?part_no=, Taiyo Yuden or/detail?pn=, plus the generic spellings.
PER_SKU_URL = re.compile(
    r"[?&](?:partno|part_no|partnumber|part_number|pn|sku|orderc(?:ode)?|"
    r"article|articlenumber)=[^&\s]+", re.I)
# A document fetched by id is part-specific even though the id is not the part:
# TE's DocumentDelivery serves one drawing per DOC number.
DOC_BY_ID_URL = re.compile(r"documentdelivery|/lit/ds/|datasheet.*?[?&]id=", re.I)
DOCUMENT_URL = re.compile(r"\.(?:pdf|PDF)(?:$|[?#])")
SHARED_URL_ROWS = 100
NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
SCHEME_HOST = re.compile(r"^[a-z]+://[^/]*", re.I)


def url_path(url: str) -> str:
    """Path + query only. Matching the search shape against the whole URL made
    every document on search.kemet.com a "search endpoint" -- the host is a
    brand name, the path is the evidence."""
    return SCHEME_HOST.sub("", url or "", count=1)

# ---------------------------------------------------------------------------
# 5. verification
# ---------------------------------------------------------------------------
LANDING_PAGE_EXT = re.compile(r"\.(?:pdf|html?|aspx|jsp|php|json|xml)(?:$|[?#])", re.I)

VACUOUS = ({}, [], "")


def norm(s: str) -> str:
    return NON_ALNUM.sub("", s or "").lower()


# ---------------------------------------------------------------------------
# record navigation
# ---------------------------------------------------------------------------
def unwrap(rec, disc):
    """Return the component object inside its discriminator wrapper.

    The map is authoritative, but a record whose wrapper does not match the map
    is not silently skipped -- it falls back to a bounded search for the single
    object that carries manufacturerInfo, and if there is none the row simply
    has no identity and the identity/citation checks pass over it."""
    node = rec
    for seg in disc:
        if not isinstance(node, dict) or seg not in node:
            node = None
            break
        node = node[seg]
    if isinstance(node, dict) and "manufacturerInfo" in node:
        return node
    # fallback: breadth-limited hunt
    stack = [(rec, 0)]
    while stack:
        cur, d = stack.pop()
        if isinstance(cur, dict):
            if "manufacturerInfo" in cur:
                return cur
            if d < 3:
                stack.extend((v, d + 1) for v in cur.values() if isinstance(v, dict))
    return node if isinstance(node, dict) else None


def identity_of(comp):
    """(identity, series, manufacturer) as the catalogue itself states them."""
    if not isinstance(comp, dict):
        return None, None, None
    mi = comp.get("manufacturerInfo")
    if not isinstance(mi, dict):
        return None, None, None
    di = mi.get("datasheetInfo") if isinstance(mi.get("datasheetInfo"), dict) else {}
    part = di.get("part") if isinstance(di.get("part"), dict) else {}
    ident = part.get("partNumber") or mi.get("reference")
    series = part.get("series") or mi.get("family")
    mfr = mi.get("name")
    def s(x):
        return x if isinstance(x, str) and x.strip() else None
    return s(ident), s(series), s(mfr)


def provenance_entries(obj, depth=0, out=None):
    """Every provenance list anywhere in the record. Provenance sits under
    datasheetInfo for most catalogues and deeper inside CIAS bricks, so it is
    collected by walking rather than by a fixed path."""
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(obj, dict):
        p = obj.get("provenance")
        if isinstance(p, list):
            out.extend(e for e in p if isinstance(e, dict))
        for k, v in obj.items():
            if k != "provenance" and isinstance(v, (dict, list)):
                provenance_entries(v, depth + 1, out)
    elif isinstance(obj, list):
        for v in obj[:64]:
            if isinstance(v, (dict, list)):
                provenance_entries(v, depth + 1, out)
    return out


def numeric_leaves(obj, path="", depth=0, budget=None):
    """Yield (dotted-path-with-[]-for-lists, value) for every numeric leaf.

    List indices collapse to `[]` so electrical.insulationPaths[0].x and
    ...[7].x are ONE field with one median -- which is the only way the 11.1 MV
    outlier is visible as an outlier."""
    if budget is None:
        budget = [200000]
    if depth > 12 or budget[0] <= 0:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            budget[0] -= 1
            if budget[0] <= 0:
                return
            yield from numeric_leaves(v, f"{path}.{k}" if path else k, depth + 1, budget)
    elif isinstance(obj, list):
        for v in obj:
            budget[0] -= 1
            if budget[0] <= 0:
                return
            yield from numeric_leaves(v, f"{path}[]", depth + 1, budget)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        if math.isfinite(obj):
            yield path, float(obj)


def all_leaves(obj, path="", depth=0, budget=None):
    """Every leaf and every container, for coverage and vacuity."""
    if budget is None:
        budget = [200000]
    if depth > 12 or budget[0] <= 0:
        return
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            budget[0] -= 1
            if budget[0] <= 0:
                return
            yield from all_leaves(v, f"{path}.{k}" if path else k, depth + 1, budget)
    elif isinstance(obj, list):
        for v in obj:
            budget[0] -= 1
            if budget[0] <= 0:
                return
            yield from all_leaves(v, f"{path}[]", depth + 1, budget)


# ---------------------------------------------------------------------------
# per-field numeric accumulator
# ---------------------------------------------------------------------------
class FieldStats:
    __slots__ = ("n", "lo", "hi", "sample", "seen", "distinct", "top", "bot", "rng")

    def __init__(self, seed):
        self.n = 0
        self.lo = math.inf
        self.hi = -math.inf
        self.sample = []
        self.seen = 0
        self.distinct = set()
        self.top = []          # [(value, line, ident)] biggest |v|
        self.bot = []
        self.rng = random.Random(seed)

    def add(self, v, line, ident):
        self.n += 1
        if v < self.lo:
            self.lo = v
        if v > self.hi:
            self.hi = v
        if len(self.distinct) < DISTINCT_CAP:
            self.distinct.add(v)
        if len(self.sample) < RESERVOIR:
            self.sample.append(v)
        else:
            j = self.rng.randrange(self.seen + 1)
            if j < RESERVOIR:
                self.sample[j] = v
        self.seen += 1
        self.top.append((v, line, ident))
        if len(self.top) > EXTREME_KEEP * 4:
            self.top.sort(key=lambda t: -abs(t[0]))
            del self.top[EXTREME_KEEP:]
        self.bot.append((v, line, ident))
        if len(self.bot) > EXTREME_KEEP * 4:
            self.bot.sort(key=lambda t: abs(t[0]))
            del self.bot[EXTREME_KEEP:]

    def finish(self):
        self.top.sort(key=lambda t: -abs(t[0]))
        del self.top[EXTREME_KEEP:]
        self.bot.sort(key=lambda t: abs(t[0]))
        del self.bot[EXTREME_KEEP:]
        self.sample.sort()

    def q(self, p):
        if not self.sample:
            return None
        i = min(len(self.sample) - 1, max(0, int(round(p * (len(self.sample) - 1)))))
        return self.sample[i]

    def summary(self):
        return {"count": self.n, "min": self.lo, "p1": self.q(0.01),
                "median": self.q(0.5), "p99": self.q(0.99), "max": self.hi,
                "distinct": len(self.distinct) if len(self.distinct) < DISTINCT_CAP
                else f">={DISTINCT_CAP}"}


# ---------------------------------------------------------------------------
# schema required-key vocabulary (check 6)
# ---------------------------------------------------------------------------
def required_keys(repo_root: Path, repo: str, fname: str) -> set:
    """Union of every `required` array in the family schema. Names, not paths --
    the point is to know which KEYS a validator insists on, so a vacuous one can
    be called out wherever it appears."""
    p = repo_root / repo / "schemas" / fname
    if not p.exists():
        return set()
    try:
        doc = json.loads(p.read_text())
    except Exception:
        return set()
    out = set()
    stack = [doc]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            r = cur.get("required")
            if isinstance(r, list):
                out.update(x for x in r if isinstance(x, str))
            stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            stack.extend(v for v in cur if isinstance(v, (dict, list)))
    return out


# ---------------------------------------------------------------------------
# the audit
# ---------------------------------------------------------------------------
class Finding(dict):
    pass


def F(check, line, ident, why):
    return Finding(check=check, line=line, id=ident or "(no identity)", why=why)


def audit_file(path: Path, checks, limit=None, sibling_root: Path = None,
               min_cohort=MIN_COHORT):
    name = path.name
    disc, repo, schema_file = CATALOGUES.get(name, ([], None, None))
    req = required_keys(sibling_root, repo, schema_file) if (
        "coverage" in checks and sibling_root and repo) else set()

    findings = []
    rows = 0
    parse_errors = 0

    # 1
    ident_seen = {}                      # (mfr, series, ident) -> first line
    # 2
    stats = defaultdict(lambda: FieldStats(0xC0FFEE))
    impossible = []
    # 3
    cohort_rows = Counter()
    cohort_vals = defaultdict(lambda: defaultdict(Counter))   # cohort -> field -> Counter
    cohort_present = defaultdict(Counter)                     # cohort -> field -> n non-null
    cohort_line = {}
    # 4
    url_rows = Counter()
    url_line = {}
    search_urls = Counter()
    no_part_specific = Counter()         # sourceName -> rows
    no_part_specific_line = {}
    no_part_specific_total = 0
    rows_with_prov = 0
    # 6
    field_present = Counter()
    vacuous = Counter()
    vacuous_line = {}

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            if limit and rows >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception as e:
                parse_errors += 1
                findings.append(F("parse", lineno, None, f"UNPARSEABLE_LINE {e}"))
                continue
            rows += 1
            comp = unwrap(rec, disc)
            ident, series, mfr = identity_of(comp)

            # ---- 1 identity ------------------------------------------------
            if "identity" in checks and ident:
                if series and ident.startswith(series) and len(ident) > len(series):
                    rest = ident[len(series):]
                    if GRID_RESIDUE_HEAD.match(rest) and GRID_RESIDUE.search(rest.strip()):
                        findings.append(F(
                            "identity", lineno, ident,
                            f"IDENTITY_SERIES_PREFIX series={series!r} + grid residue "
                            f"{rest.strip()!r}"))
                if len(ident) > IDENT_MAX_LEN:
                    findings.append(F("identity", lineno, ident,
                                      f"IDENTITY_TOO_LONG {len(ident)} chars "
                                      f"(> {IDENT_MAX_LEN})"))
                key = (mfr or "", series or "", ident)
                prev = ident_seen.get(key)
                if prev is None:
                    ident_seen[key] = lineno
                else:
                    findings.append(F(
                        "identity", lineno, ident,
                        f"IDENTITY_DUPLICATE_IN_SERIES manufacturer={mfr!r} "
                        f"series={series!r} first seen line {prev}"))

            # ---- 2 units ---------------------------------------------------
            if "units" in checks:
                for fpath, v in numeric_leaves(rec):
                    if UNITS_EXCLUDE.search(fpath):
                        continue
                    stats[fpath].add(v, lineno, ident)
                    segs = [x.replace("[]", "") for x in fpath.split(".")]
                    qty = segs[-1]
                    if qty in BOUND_LEAVES and len(segs) > 1:
                        qty = segs[-2]
                    for rx, pred, why in IMPOSSIBLE:
                        if rx.search(qty) and pred(v):
                            impossible.append(F(
                                "units", lineno, ident,
                                f"IMPOSSIBLE_VALUE {fpath} = {v!r} ({why})"))
                            break

            # ---- 7 generator -----------------------------------------------
            # An arithmetic identity WITHIN one row: powerDissipation exactly
            # 0.3 x Vds x Id, with the derived constants the same expansion
            # writes beside it. Needs no cohort, so it sees the generated
            # families the cohort check cannot -- see the long note in
            # check_no_fabricated_parts.py for the calibration and its one
            # measured coincidence (onsemi NDT3055).
            if "generator" in checks and isinstance(comp, dict):
                elec = ((comp.get("manufacturerInfo") or {}).get("datasheetInfo")
                        or {}).get("electrical")
                if isinstance(elec, list):
                    elec = elec[0] if elec else {}
                if isinstance(elec, dict):
                    why = seed_expanded_mosfet(elec)
                    if why:
                        findings.append(F("generator", lineno, ident,
                                          f"GENERATOR_SEED_EXPANSION {why}"))

            # ---- 3/4/5 provenance-driven -----------------------------------
            prov = provenance_entries(rec) if (
                {"cohort", "citation", "verification"} & set(checks)) else []

            if "cohort" in checks:
                p0 = prov[0] if prov else {}
                ckey = (p0.get("sourceName") or "(none)",
                        p0.get("retrievedDate") or "(none)")
                cohort_rows[ckey] += 1
                cohort_line.setdefault(ckey, lineno)
                cv = cohort_vals[ckey]
                cp = cohort_present[ckey]
                for fpath, v in numeric_leaves(rec):
                    cp[fpath] += 1
                    c = cv[fpath]
                    if len(c) < COHORT_FIELD_CAP or v in c:
                        c[v] += 1

            if "citation" in checks and prov:
                rows_with_prov += 1
                idn = norm(ident) if ident else ""
                sern = norm(series) if series else ""
                part_specific = False
                for e in prov:
                    url = e.get("sourceUrl")
                    if not isinstance(url, str) or not url:
                        continue
                    url_rows[url] += 1
                    url_line.setdefault(url, lineno)
                    if (SEARCH_URL.search(url_path(url))
                            and not PER_SKU_URL.search(url)):
                        search_urls[url] += 1
                    if part_specific:
                        continue
                    un = norm(url)
                    if idn and len(idn) >= 4 and idn in un:
                        part_specific = True
                    elif PER_SKU_URL.search(url):
                        part_specific = True          # per-SKU detail page
                    elif DOC_BY_ID_URL.search(url):
                        part_specific = True          # document fetched by id
                    elif (sern and len(sern) >= 4 and sern in un
                          and DOCUMENT_URL.search(url)):
                        part_specific = True          # family datasheet
                if not part_specific:
                    no_part_specific_total += 1
                    sn = (prov[0].get("sourceName") or "(none)")[:80]
                    no_part_specific[sn] += 1
                    no_part_specific_line.setdefault(sn, lineno)

            if "verification" in checks:
                for e in prov:
                    ver = e.get("verification")
                    url = e.get("sourceUrl") if isinstance(e.get("sourceUrl"), str) else ""
                    if ver and not e.get("verificationDate"):
                        findings.append(F("verification", lineno, ident,
                                          f"VERIFICATION_WITHOUT_DATE verification={ver!r}"))
                    if e.get("retracted") is True and not (
                            e.get("retractionReason") or e.get("reason")):
                        findings.append(F("verification", lineno, ident,
                                          "RETRACTED_WITHOUT_REASON"))
                    if ver == "disproven" and e.get("retracted") is not True:
                        findings.append(F("verification", lineno, ident,
                                          "DISPROVEN_NOT_RETRACTED"))
                    if ver == "valuesReadFromSource" and url:
                        bare = (not LANDING_PAGE_EXT.search(url)
                                and "?" not in url
                                and len([s for s in url.split("/")[3:] if s]) <= 2)
                        if (SEARCH_URL.search(url_path(url))
                                and not PER_SKU_URL.search(url)):
                            findings.append(F(
                                "verification", lineno, ident,
                                f"READ_FROM_SEARCH_PAGE {url[:120]}"))
                        elif bare:
                            findings.append(F(
                                "verification", lineno, ident,
                                f"READ_FROM_LANDING_PAGE {url[:120]}"))

            # ---- 6 coverage ------------------------------------------------
            if "coverage" in checks:
                for fpath, v in all_leaves(rec):
                    if not fpath:
                        continue
                    field_present[fpath] += 1
                    if v in VACUOUS and isinstance(v, (dict, list, str)):
                        leaf = fpath.split(".")[-1].replace("[]", "")
                        if leaf in req:
                            vacuous[fpath] += 1
                            vacuous_line.setdefault(fpath, (lineno, ident))

    # ---- 2 post-pass ------------------------------------------------------
    field_summaries = {}
    if "units" in checks:
        findings.extend(impossible)
        for fpath, st in sorted(stats.items()):
            st.finish()
            field_summaries[fpath] = st.summary()
            med = st.q(0.5)
            if med is None or st.n < 20:
                continue
            amed = abs(med)
            if amed > 0:
                for v, ln, idt in st.top:
                    if abs(v) > amed * EXTREME_RATIO:
                        findings.append(F(
                            "units", ln, idt,
                            f"UNIT_EXTREME {fpath} = {v!r} is "
                            f"{abs(v)/amed:.3g}x the field median {med!r} "
                            f"(n={st.n})"))
                        break
                for v, ln, idt in st.bot:
                    if v != 0 and abs(v) * EXTREME_RATIO < amed:
                        findings.append(F(
                            "units", ln, idt,
                            f"UNIT_EXTREME_LOW {fpath} = {v!r} is "
                            f"{amed/abs(v):.3g}x below the field median {med!r} "
                            f"(n={st.n})"))
                        break
            nz = [abs(v) for v in st.sample if v != 0]
            if nz:
                span = math.log10(max(nz)) - math.log10(min(nz))
                if span > SPAN_DECADES:
                    # A span is a property of the FIELD, not of one row, so it
                    # is reported against line 1 with the field as its identity.
                    findings.append(F(
                        "units", 1, fpath,
                        f"UNIT_SPAN {fpath} spans {span:.1f} orders of magnitude "
                        f"({min(nz):g} .. {max(nz):g}, n={st.n})"))
            # decade clustering: a unit-conversion bug lands ON a decade
            if nz and amed > 0:
                dec = Counter(int(round(math.log10(abs(v) / amed))) for v in nz
                              if abs(math.log10(abs(v) / amed)
                                     - round(math.log10(abs(v) / amed))) < 1e-6)
                for d in (3, 6, 9, -3, -6, -9):
                    if dec.get(d, 0) >= 5:
                        findings.append(F(
                            "units", 1, fpath,
                            f"UNIT_DECADE_CLUSTER {fpath}: {dec[d]} sampled values "
                            f"sit at exactly 1e{d} times the field median {med!r}"))

    # ---- 3 post-pass ------------------------------------------------------
    cohort_report = []
    if "cohort" in checks:
        # catalogue-wide count of a given (field, value), summed across cohorts
        def catalogue_count(fpath, val):
            return sum(cohort_vals[c][fpath].get(val, 0) for c in cohort_vals)
        cat_field_n = Counter()
        for c in cohort_present:
            for fpath, n in cohort_present[c].items():
                cat_field_n[fpath] += n
        for ckey, nrows in sorted(cohort_rows.items(), key=lambda kv: -kv[1]):
            if nrows < min_cohort:
                continue
            for fpath, counter in cohort_vals[ckey].items():
                present = cohort_present[ckey][fpath]
                absent = nrows - present
                if present >= min_cohort:
                    val, cnt = counter.most_common(1)[0]
                    share = cnt / present
                    if share >= UNIFORM_SHARE:
                        cat_share = (catalogue_count(fpath, val)
                                     / max(1, cat_field_n[fpath]))
                        if cat_share < CATALOGUE_SHARE:
                            findings.append(F(
                                "cohort", cohort_line[ckey], f"{ckey[0]} @ {ckey[1]}",
                                f"COHORT_UNIFORM_VALUE {fpath} = {val!r} on "
                                f"{cnt}/{present} populated rows "
                                f"({share:.1%}); catalogue-wide share of that "
                                f"value is only {cat_share:.1%}"))
                if nrows >= min_cohort and present == 0:
                    continue
                if nrows >= min_cohort and absent / nrows >= UNIFORM_SHARE and present:
                    cohort_report.append({
                        "cohort": list(ckey), "field": fpath, "rows": nrows,
                        "populated": present,
                        "signal": "UNIFORMLY_ABSENT"})

    # ---- 4 post-pass ------------------------------------------------------
    citation_report = {}
    if "citation" in checks:
        citation_report = {
            "rows_with_provenance": rows_with_prov,
            "search_shaped_entries": int(sum(search_urls.values())),
            "distinct_search_urls": len(search_urls),
            "rows_without_part_specific_evidence": no_part_specific_total,
        }
        for url, n in search_urls.most_common(50):
            findings.append(F("citation", url_line.get(url, 1), url[:100],
                              f"CITATION_SEARCH_URL cited by {n} provenance entries"))
        for url, n in url_rows.most_common(200):
            if n > SHARED_URL_ROWS and not SEARCH_URL.search(url_path(url)):
                findings.append(F("citation", url_line.get(url, 1), url[:100],
                                  f"CITATION_SHARED_URL one literal URL on {n} "
                                  f"provenance entries"))
        for sn, n in no_part_specific.most_common(50):
            findings.append(F("citation", no_part_specific_line[sn], sn,
                              f"CITATION_NO_PART_SPECIFIC_EVIDENCE {n} rows whose "
                              f"provenance never names the part"))

    # ---- 6 post-pass ------------------------------------------------------
    coverage = {}
    if "coverage" in checks:
        coverage = {f: {"rows": n, "rate": round(n / rows, 6) if rows else 0}
                    for f, n in field_present.most_common(400)}
        for fpath, n in vacuous.most_common():
            ln, idt = vacuous_line[fpath]
            findings.append(F("coverage", ln, idt,
                              f"VACUOUS_REQUIRED_FIELD {fpath} is empty on {n} rows "
                              f"(schema requires the key; empty validates and "
                              f"carries nothing)"))

    return {
        "file": name, "rows": rows, "parse_errors": parse_errors,
        "findings": findings,
        "fields": field_summaries,
        "cohorts": {"total": len(cohort_rows),
                    "large": sum(1 for v in cohort_rows.values() if v >= min_cohort),
                    "uniformly_absent": cohort_report[:200]},
        "citation": citation_report,
        "coverage": coverage,
    }


# ---------------------------------------------------------------------------
# self-test: every check must be SHOWN to fire, and shown to stay quiet
# ---------------------------------------------------------------------------
def selftest(tmpdir: Path) -> int:
    """A check that has never been shown to fire is not a check.

    Each case is a pair: a row that MUST produce the finding, and a look-alike
    built from a real catalogue string that MUST NOT."""
    import tempfile

    def cap(rec):
        return {"capacitor": rec}

    def mi(part, prov=None, extra=None, mfr="ACME"):
        d = {"part": part}
        if prov is not None:
            d["provenance"] = prov
        if extra:
            d.update(extra)
        return {"manufacturerInfo": {"name": mfr, "datasheetInfo": d}}

    cases = [
        # 1a series-prefix
        ("identity", "IDENTITY_SERIES_PREFIX",
         cap(mi({"partNumber": "NTCACAP Series for Refrigerator (R25=10KOhm, tol=2%)",
                 "series": "NTCACAP Series for Refrigerator"})),
         cap(mi({"partNumber": "NTCACAP0503FH103", "series": "NTCACAP"}))),
        # 1a look-alike that cost 7,059 false positives before the residue had
        # to start with whitespace: a Cinch order code whose series is the two
        # letters it opens with.
        ("identity", "IDENTITY_SERIES_PREFIX",
         cap(mi({"partNumber": "109D 82uF 50V Axial", "series": "109D"})),
         cap(mi({"partNumber": "CX90MW9-24P(002)", "series": "CX"}))),
        # 1b length
        ("identity", "IDENTITY_TOO_LONG",
         cap(mi({"partNumber": "X" * 41})),
         cap(mi({"partNumber": "SCPX-AGA-XXX-010-C"}))),
        # 1c cross-row duplicate is a two-row case, handled separately below
        # 2 impossible
        ("units", "IMPOSSIBLE_VALUE",
         cap(mi({"partNumber": "P1"}, extra={"electrical": {"capacitance":
                                                            {"nominal": -1e-6}}})),
         cap(mi({"partNumber": "P2"}, extra={"electrical": {"capacitance":
                                                            {"nominal": 1e-6}}}))),
        # 2 look-alikes for the anchored quantity names. Each of these was a
        # real false positive from substring matching, and each must stay quiet.
        ("units", "IMPOSSIBLE_VALUE",
         cap(mi({"partNumber": "N1"}, extra={"electrical": {"onResistance": -0.045}})),
         cap(mi({"partNumber": "N2"}, extra={"electrical": {"onResistanceVgs": -10.0}}))),
        ("units", "IMPOSSIBLE_VALUE",
         cap(mi({"partNumber": "N3"}, extra={"electrical": {"impedance": -50.0}})),
         cap(mi({"partNumber": "N4"}, extra={"electrical": {"impedancePoints": [
             {"impedance": {"phase": -0.18, "magnitude": 180.0}}]}}))),
        ("units", "IMPOSSIBLE_VALUE",
         cap(mi({"partNumber": "N5"}, extra={"thermal": {"operatingTemperature":
                                                         {"minimum": -300.0}}})),
         cap(mi({"partNumber": "N6"}, extra={"thermal": {"temperatureCoefficient":
                                                         {"nominal": -750.0}}}))),
        # 5 verification
        ("verification", "DISPROVEN_NOT_RETRACTED",
         cap(mi({"partNumber": "P3"}, prov=[{"source": "manual",
                                             "verification": "disproven",
                                             "verificationDate": "2026-01-01"}])),
         cap(mi({"partNumber": "P4"}, prov=[{"source": "manual",
                                             "verification": "disproven",
                                             "verificationDate": "2026-01-01",
                                             "retracted": True,
                                             "retractionReason": "wrong die"}]))),
        ("verification", "VERIFICATION_WITHOUT_DATE",
         cap(mi({"partNumber": "P5"}, prov=[{"source": "manual",
                                             "verification": "partNamed"}])),
         cap(mi({"partNumber": "P6"}, prov=[{"source": "manual",
                                             "verification": "partNamed",
                                             "verificationDate": "2026-01-01"}]))),
        ("verification", "RETRACTED_WITHOUT_REASON",
         cap(mi({"partNumber": "P7"}, prov=[{"source": "manual", "retracted": True}])),
         cap(mi({"partNumber": "P8"}, prov=[{"source": "manual", "retracted": True,
                                             "retractionReason": "superseded"}]))),
        ("verification", "READ_FROM_SEARCH_PAGE",
         cap(mi({"partNumber": "P9"}, prov=[{
             "source": "scrape", "verification": "valuesReadFromSource",
             "verificationDate": "2026-01-01",
             "sourceUrl": "https://example.com/search?q=P9"}])),
         cap(mi({"partNumber": "GRM155R71C104KA88D"}, prov=[{
             "source": "scrape", "verification": "valuesReadFromSource",
             "verificationDate": "2026-01-01",
             "sourceUrl": "https://www.murata.com/productdetail?partno=GRM155R71C104KA88D"}]))),
    ]

    ok, bad = 0, []
    tmp = Path(tempfile.mkdtemp(dir=str(tmpdir)))
    for check, token, fires, quiet in cases:
        for rec, want in ((fires, True), (quiet, False)):
            p = tmp / "capacitors.ndjson"
            p.write_text(json.dumps(rec) + "\n")
            res = audit_file(p, (check,), sibling_root=REPO.parent)
            got = any(token in f["why"] for f in res["findings"])
            if got == want:
                ok += 1
            else:
                bad.append(f"{check}/{token}: expected fire={want}, got {got}")

    # 1c cross-row duplicate: needs two rows, and the look-alike is the same
    # part number under a DIFFERENT series, which is a different part.
    p = tmp / "capacitors.ndjson"
    p.write_text("\n".join(json.dumps(cap(mi({"partNumber": "CGA2B2X7R", "series": "CGA"})))
                           for _ in range(2)) + "\n")
    res = audit_file(p, ("identity",), sibling_root=REPO.parent)
    (ok := ok + 1) if any("IDENTITY_DUPLICATE_IN_SERIES" in f["why"]
                          for f in res["findings"]) else bad.append(
        "identity/IDENTITY_DUPLICATE_IN_SERIES: expected fire=True, got False")
    p.write_text(json.dumps(cap(mi({"partNumber": "CGA2B2X7R", "series": "CGA"}))) + "\n"
                 + json.dumps(cap(mi({"partNumber": "CGA2B2X7R", "series": "CGB"}))) + "\n")
    res = audit_file(p, ("identity",), sibling_root=REPO.parent)
    (ok := ok + 1) if not any("IDENTITY_DUPLICATE_IN_SERIES" in f["why"]
                              for f in res["findings"]) else bad.append(
        "identity/IDENTITY_DUPLICATE_IN_SERIES: expected fire=False, got True")

    # 2 the array-element outlier: 11.1 MV hidden at [0].ratedImpulseVoltage.
    rows = [{"connector": mi({"partNumber": f"C{i}"},
                             extra={"electrical": {"insulationPaths": [
                                 {"ratedImpulseVoltage": 2500.0}]}})}
            for i in range(60)]
    rows[7]["connector"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
        "insulationPaths"][0]["ratedImpulseVoltage"] = 11.1e6
    p2 = tmp / "connectors.ndjson"
    p2.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    res = audit_file(p2, ("units",), sibling_root=REPO.parent)
    (ok := ok + 1) if any("UNIT_EXTREME" in f["why"] and "insulationPaths" in f["why"]
                          for f in res["findings"]) else bad.append(
        "units/UNIT_EXTREME nested-array: expected fire=True, got False")
    rows[7]["connector"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
        "insulationPaths"][0]["ratedImpulseVoltage"] = 4000.0
    p2.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    res = audit_file(p2, ("units",), sibling_root=REPO.parent)
    (ok := ok + 1) if not any("UNIT_EXTREME" in f["why"]
                              for f in res["findings"]) else bad.append(
        "units/UNIT_EXTREME nested-array: expected fire=False, got True")

    # 3 cohort uniformity: a minted value fires; the SAME shape with the field
    # null everywhere must NOT (that is uniform absence, not a minted value).
    def coh(v, i, src="S"):
        e = {"electrical": {"loadLifeHours": v}} if v is not None else {}
        return {"capacitor": mi({"partNumber": f"K{i}-{src}"},
                                prov=[{"source": "scrape", "sourceName": src,
                                       "retrievedDate": "2026-01-01"}],
                                extra=e)}
    # one import cohort minted the same 2000 h on every row; a second, healthy
    # cohort of the same catalogue spreads over real values -- so 2000 h is not
    # a catalogue constant, it is this importer's default.
    rows = [coh(2000.0, i) for i in range(150)] + [coh(float(200 + i), i, "T")
                                                   for i in range(200)]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    res = audit_file(p, ("cohort",), sibling_root=REPO.parent, min_cohort=100)
    (ok := ok + 1) if any("COHORT_UNIFORM_VALUE" in f["why"]
                          for f in res["findings"]) else bad.append(
        "cohort/COHORT_UNIFORM_VALUE: expected fire=True, got False")
    rows = [coh(None, i) for i in range(150)] + [coh(float(200 + i), i, "T")
                                                 for i in range(200)]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    res = audit_file(p, ("cohort",), sibling_root=REPO.parent, min_cohort=100)
    (ok := ok + 1) if not any("COHORT_UNIFORM_VALUE" in f["why"]
                              for f in res["findings"]) else bad.append(
        "cohort/COHORT_UNIFORM_VALUE: fired on uniform ABSENCE, which is the bug "
        "this check was rewritten to avoid")

    # 4 citation: a search URL with no part-specific entry fires; the same row
    # citing a per-SKU detail page does not.
    rows = [{"capacitor": mi({"partNumber": "GRM155"}, prov=[{
        "source": "scrape", "sourceName": "S", "retrievedDate": "2026-01-01",
        "sourceUrl": "https://www.example.com/search?q=capacitor"}])}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    res = audit_file(p, ("citation",), sibling_root=REPO.parent)
    (ok := ok + 1) if any("CITATION_SEARCH_URL" in f["why"] for f in res["findings"]) \
        and any("NO_PART_SPECIFIC" in f["why"] for f in res["findings"]) \
        else bad.append("citation: expected search + no-evidence, got neither")
    rows = [{"capacitor": mi({"partNumber": "GRM155R71C104KA88D"}, prov=[{
        "source": "scrape", "sourceName": "S", "retrievedDate": "2026-01-01",
        "sourceUrl": "https://www.murata.com/en/productdetail?partno=GRM155R71C104KA88D"}])}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    res = audit_file(p, ("citation",), sibling_root=REPO.parent)
    (ok := ok + 1) if not res["findings"] else bad.append(
        f"citation: per-SKU detail page must be silent, got {res['findings']}")

    # 7 generator seed expansion: the deleted FDMU8100L row fires; two real
    # onsemi parts must not -- NDT014 sits 11.5% off the identity, NDT3055 lands
    # on it exactly by coincidence but carries none of the derived constants.
    def mos(pn, **e):
        return {"semiconductor": {"mosfet": {"manufacturerInfo": {
            "name": "onsemi", "reference": pn,
            "datasheetInfo": {"part": {"partNumber": pn}, "electrical": e}}}}}
    p.write_text(json.dumps(mos(
        "FDMU8100L", drainSourceVoltage=100, continuousDrainCurrent=80,
        continuousDrainCurrentAt100C=52.0, powerDissipation=2400.0,
        onResistance=0.013, onResistanceId=40.0, inputCapacitance=5.2e-08,
        outputCapacitance=1.04e-08,
        reverseTransferCapacitance=1.0400000000000001e-09,
        capacitanceMeasurementVds=50.0)) + "\n")
    res = audit_file(p, ("generator",), sibling_root=REPO.parent)
    (ok := ok + 1) if any("GENERATOR_SEED_EXPANSION" in f["why"]
                          for f in res["findings"]) else bad.append(
        "generator/GENERATOR_SEED_EXPANSION: expected fire=True, got False")
    p.write_text("\n".join(json.dumps(r) for r in [
        mos("NDT014", drainSourceVoltage=60.0, continuousDrainCurrent=2.7,
            powerDissipation=43.0, onResistance=0.2, totalGateCharge=5e-09),
        mos("NDT3055", drainSourceVoltage=60.0, continuousDrainCurrent=4.0,
            powerDissipation=72.0, onResistance=0.1, totalGateCharge=9e-09),
    ]) + "\n")
    res = audit_file(p, ("generator",), sibling_root=REPO.parent)
    (ok := ok + 1) if not res["findings"] else bad.append(
        f"generator: real onsemi NDT014/NDT3055 must be silent, got {res['findings']}")

    # 6 vacuous required key vs a populated one.
    rows = [{"capacitor": {"manufacturerInfo": {"name": "ACME",
                                                "datasheetInfo": {"part": {}}}}}]
    p.write_text(json.dumps(rows[0]) + "\n")
    res = audit_file(p, ("coverage",), sibling_root=REPO.parent)
    fired = any("VACUOUS_REQUIRED_FIELD" in f["why"] for f in res["findings"])
    (ok := ok + 1) if fired else bad.append(
        "coverage/VACUOUS_REQUIRED_FIELD: expected fire=True, got False "
        f"(required vocabulary empty? -> {sorted(required_keys(REPO.parent, 'CAS', 'capacitor.json'))[:5]})")
    p.write_text(json.dumps({"capacitor": {"manufacturerInfo": {
        "name": "ACME", "datasheetInfo": {"part": {"partNumber": "P"}}}}}) + "\n")
    res = audit_file(p, ("coverage",), sibling_root=REPO.parent)
    (ok := ok + 1) if not any("VACUOUS_REQUIRED_FIELD" in f["why"]
                              for f in res["findings"]) else bad.append(
        "coverage/VACUOUS_REQUIRED_FIELD: expected fire=False, got True")

    print(f"selftest: {ok} case(s) behaved as designed, {len(bad)} did not")
    for b in bad:
        print(f"  MISMATCH {b}")
    return 0 if not bad else 2


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--file", action="append", default=[],
                    help="audit only these catalogue file names")
    ap.add_argument("--only", action="append", default=[], choices=CHECKS,
                    help="run only these checks")
    ap.add_argument("--limit", type=int, help="stop after N rows per file (smoke run)")
    ap.add_argument("--json", type=Path, help="write the full machine-readable report")
    ap.add_argument("--max-print", type=int, default=400,
                    help="findings printed per check per file")
    ap.add_argument("--min-cohort", type=int, default=MIN_COHORT)
    ap.add_argument("--selftest", action="store_true",
                    help="run the fire / stay-quiet fixtures and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            return selftest(Path(td))

    checks = tuple(args.only) if args.only else CHECKS

    if not args.data.is_dir():
        print(f"FAIL: no data directory at {args.data}")
        return 2
    files = []
    for p in sorted(args.data.glob("*.ndjson")):
        n = p.name
        if "quarantine" in n or "denylist" in n:
            continue
        if args.file and n not in args.file:
            continue
        files.append(p)
    if not files:
        print("FAIL: no live catalogues matched -- a check that cannot run FAILS")
        return 2

    reports = []
    total = Counter()
    rows_total = 0
    for p in files:
        try:
            rep = audit_file(p, checks, limit=args.limit,
                             sibling_root=REPO.parent, min_cohort=args.min_cohort)
        except Exception as e:  # a guard that crashes is not a clean guard
            print(f"FAIL: {p.name} could not be audited: {type(e).__name__}: {e}")
            return 2
        reports.append(rep)
        rows_total += rep["rows"]
        by = Counter(f["check"] for f in rep["findings"])
        total.update(by)
        print(f"\n{p.name}: {rep['rows']:,} rows, {len(rep['findings']):,} finding(s)"
              + (f", {rep['parse_errors']} unparseable" if rep["parse_errors"] else ""))
        for chk in ("parse",) + CHECKS:
            fs = [f for f in rep["findings"] if f["check"] == chk]
            if not fs:
                continue
            print(f"  [{chk}] {len(fs):,}")
            for f in fs[:args.max_print]:
                print(f"  line {f['line']}: {f['id']} -- {f['why']}")
            if len(fs) > args.max_print:
                print(f"  ... and {len(fs) - args.max_print} more")

    n = sum(total.values())
    print("\n" + "=" * 72)
    print(f"{rows_total:,} live rows over {len(files)} catalogue(s); "
          f"per-check totals: "
          + ", ".join(f"{k}={v:,}" for k, v in sorted(total.items())) or "none")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(
            {"rows": rows_total, "checks": list(checks),
             "totals": dict(total), "catalogues": reports}, indent=1, default=str))
        print(f"report written to {args.json}")
    if n:
        print(f"FAIL: {n:,} catalogue-audit finding(s)")
        return 1
    print("OK: catalogue audit clean -- no findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
