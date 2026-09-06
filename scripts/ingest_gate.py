#!/usr/bin/env python3
"""INGEST GATE: the check an importer must pass BEFORE a row reaches a catalogue.

    from ingest_gate import IngestGate, IngestRefused
    gate = IngestGate("magnetics.ndjson")
    for rec in candidates:
        gate.admit(rec)          # raises IngestRefused on the first bad row
    gate.close()                 # raises on the BATCH-level rules
    # only now write the rows

CLI:
    python3 scripts/ingest_gate.py --catalogue magnetics.ndjson FILE.ndjson
    python3 scripts/ingest_gate.py --selftest        # the counter-checks

WHY THIS EXISTS

Every large defect cohort found in the 2026-08/09 audits came from ONE IMPORTER,
not from many independently bad rows:

  * 549 TDK chip beads minted an identical 1e-09 H
  * 404 Wuerth rows took a hardcoded family default
  * 716 onsemi diodes were all typed "rectifier"
  * 535 thermistors and 535 Vishay capacitors got a grid-row DESCRIPTION as
    their partNumber
  * 6,082 Vishay tantalums recorded in their OWN provenance that the identity
    was synthesized
  * 22,269 TE electromechanical parts were imported as connectors
  * 134,949 rows cite a landing page that names no part

Repairing output is losing: the campaign that writes the rows is faster than the
campaign that fixes them. So the gate sits at the INPUT, and an importer that
cannot satisfy it must abort, not degrade.

THE SEVEN RULES (each one is a defect that actually shipped)

 1. IDENTITY IS REAL -- not a grid-row description. Refuses an identity longer
    than 40 characters, one that duplicates another row of the same
    manufacturer+series, and one that is its own series followed by prose
    (the series-prefix test).
 2. THE CITATION NAMES THE PART -- a search endpoint or a landing page reused
    across rows is not evidence. A row with no part-specific citation may still
    enter, but ONLY stamped verification=inferredNotVerified/notAttempted; it
    may never enter carrying a stronger claim.
 3. NO MINTED CONSTANT -- a value byte-identical across the batch in a field the
    vendor publishes per part, WHERE THE PARTS' OWN DESCRIPTIONS REFUTE IT.
 4. NO ARITHMETIC LADDER -- a numeric field that is an exact affine function of
    a part index, on TWO OR MORE unrelated fields at once.
 5. UNITS ARE SI AND PHYSICALLY POSSIBLE -- no negative capacitance/resistance/
    inductance, and no magnitude a component of that kind cannot have. The
    "1000x off the field median" form of this test is available (median_outliers
    / --median-outliers) but is OFF by default: see the comment in __init__ --
    a real catalogue batch spans six decades and the test refused 54 genuine
    capacitors out of 300.
 6. IT VALIDATES against its family schema, with required-but-vacuous ({} / [])
    counted as MISSING (Draft 2020-12 happily accepts an empty object).
 7. NO TWO-SEED EXPANSION -- a MOSFET row whose powerDissipation is exactly
    0.3 x Vds x Id, corroborated by the derived constants the same generator
    writes beside it. Unlike rule 4 this is an identity WITHIN ONE ROW, so it
    does not need a cohort, a shared stem or a contiguous run -- which is
    precisely how it catches the generated families rule 4 cannot see.

WHAT EACH RULE DELIBERATELY DOES *NOT* DO -- a gate that refuses everything is
as useless as one that refuses nothing, so every rule carries its counter-example:

  * Rule 1 does NOT gate on whitespace or parentheses. Ohmite prints
    "HS50 R3 F", Molex uses parenthetical packaging codes, Murata ships
    "#A914BYW-150M=P3". Those are legitimate part numbers.
  * Rule 3 does NOT refuse a shared value on its own. Wuerth really does sell
    a lot of 10 uH inductors and 51 TDK parts genuinely share 1 nH. The
    DESCRIPTION CONTRADICTION is what makes a shared value a minted one.
  * Rule 7 does NOT fire on the identity alone. 0.3 x 60 V x 4 A = 72.0 W is
    exactly what the live catalogue stores for onsemi's NDT3055 -- a real
    SOT-223 part whose dissipation column was mis-mapped on import. Two of the
    derived constants must corroborate before a row is called generated.
  * Rule 4 does NOT refuse a single-field ladder. Real vendor numbering encodes
    a quantity in the part number -- IPP60R080P7 states its 80 mOhm Rds(on),
    744043100 states its 10 uH -- so ONE field tracking a numeric run is
    expected. Several unrelated fields tracking the same run is a generator.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parents[1]
PROTEUS = REPO.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The cohort/description machinery is shared with the standing guard so the two
# cannot drift apart: the gate stops a minted constant at the door, the guard
# re-checks the corpus nightly, and both must agree on what "refuted" means.
from check_no_constant_cohorts import (  # noqa: E402
    IMPEDANCE_FLOOR,
    RE_IMPEDANCE_CONTEXT,
    TOLERANCE,
    first,
    parse_quantity,
    walk,
)
# Rule 7's arithmetic is defined ONCE, in the standing guard, and imported here
# so the gate at the door and the nightly sweep cannot drift apart -- same
# constants, same tolerance, same corroboration bar.
from check_no_fabricated_parts import seed_expanded_mosfet  # noqa: E402

MIN_COHORT = 5            # rows sharing a value before rule 3 looks at it
MIN_LADDER = 5            # rows before rule 4 will fit a line
MAX_IDENTITY_LEN = 40     # characters; longer is a description, not a part number
OUTLIER_RATIO = 1.0e3     # rule 5: x this far from the field median needs an override


class IngestRefused(Exception):
    """Raised when a candidate row (or the batch) is refused. Never caught by an
    importer: the row does not enter, and the import stops."""


class Refusal:
    __slots__ = ("rule", "part", "why")

    def __init__(self, rule, part, why):
        self.rule, self.part, self.why = rule, part, why

    def __str__(self):
        return "[rule %s] %s: %s" % (self.rule, self.part, self.why)

    __repr__ = __str__


# ---------------------------------------------------------------------------
# catalogue table: how to unwrap a row, which schema owns it, which numeric
# fields the vendor publishes PER PART (so a constant or a ladder in one is a
# statement about the importer, not about the parts)
# ---------------------------------------------------------------------------
# Paths below start at the UNWRAPPED body (i.e. after the discriminator chain).
# "*" walks a list. `unit` is set only where a description can state the same
# quantity in text -- that is what rule 3 needs to refute a value.

def _di(*rest):
    return ["manufacturerInfo", "datasheetInfo"] + list(rest)


class Field:
    """One numeric field the vendor publishes PER PART.

    `unit` is set only where a part description can state the same quantity in
    words -- that is what rule 3 needs in order to REFUTE a value. `bound` is the
    largest magnitude that quantity can physically take in a component catalogue
    (a 3000 F supercapacitor exists; a 1e6 F one does not), and it is the only
    magnitude test that runs by default: see the note on `median_outliers`."""

    __slots__ = ("key", "unit", "segs", "positive", "bound")

    def __init__(self, key, segs, unit=None, positive=True, bound=None):
        self.key, self.segs, self.unit = key, segs, unit
        self.positive, self.bound = positive, bound


class Catalogue:
    __slots__ = ("name", "disc", "repo", "schema_file", "fields")

    def __init__(self, name, disc, repo, schema_file, fields):
        self.name, self.disc, self.repo = name, disc, repo
        self.schema_file, self.fields = schema_file, fields


_CATALOGUES = [
    Catalogue("magnetics.ndjson", ["magnetic"], "MAS", "magnetic.json", [
        Field("inductance", _di("electrical", "*", "inductance", "nominal"), "H",
              bound=1e4),
        Field("dcResistance", _di("electrical", "*", "dcResistance", "maximum"), "OHM",
              bound=1e9),
        Field("saturationCurrentPeak", _di("electrical", "*", "saturationCurrentPeak"),
              bound=1e5),
        Field("ratedCurrent", _di("electrical", "*", "ratedCurrents", "*"), bound=1e5),
        Field("selfResonantFrequency", _di("electrical", "*", "selfResonantFrequency"),
              bound=1e13),
        Field("impedance",
              _di("electrical", "*", "impedancePoints", "*", "impedance", "magnitude"),
              "OHM"),
    ]),
    Catalogue("capacitors.ndjson", ["capacitor"], "CAS", "capacitor.json", [
        Field("capacitance", _di("electrical", "capacitance", "nominal"), "F",
              bound=1e5),
        Field("ratedVoltage", _di("electrical", "ratedVoltage"), bound=1e6),
        Field("esr", _di("electrical", "esr"), "OHM"),
        Field("rippleCurrent", _di("electrical", "rippleCurrent")),
    ]),
    Catalogue("resistors.ndjson", ["resistor"], "RAS", "resistor.json", [
        Field("resistance", _di("electrical", "resistance", "nominal"), "OHM",
              bound=1e15),
        Field("powerRating", _di("electrical", "powerRating")),
        Field("maxVoltage", _di("electrical", "maxVoltage")),
    ]),
    Catalogue("varistors.ndjson", ["varistor"], "RAS", "varistor.json", [
        Field("varistorVoltage", _di("electrical", "varistorVoltage", "nominal")),
        Field("clampingVoltage", _di("electrical", "clampingVoltage")),
        Field("peakSurgeCurrent", _di("electrical", "peakSurgeCurrent")),
    ]),
    Catalogue("thermistors.ndjson", ["thermistor"], "RAS", "thermistor.json", [
        Field("resistanceAt25C", _di("electrical", "resistanceAt25C", "nominal"), "OHM"),
        Field("bConstant", _di("electrical", "bConstant")),
    ]),
    Catalogue("mosfets.ndjson", ["semiconductor", "mosfet"], "SAS", "mosfet.json", [
        Field("onResistance", _di("electrical", "onResistance"), "OHM", bound=1e6),
        Field("drainSourceVoltage", _di("electrical", "drainSourceVoltage"), bound=1e5),
        Field("continuousDrainCurrent", _di("electrical", "continuousDrainCurrent")),
        Field("totalGateCharge", _di("electrical", "totalGateCharge")),
        Field("inputCapacitance", _di("electrical", "inputCapacitance"), "F", bound=1.0),
        Field("outputCapacitance", _di("electrical", "outputCapacitance"), "F", bound=1.0),
        Field("powerDissipation", _di("electrical", "powerDissipation")),
    ]),
    Catalogue("diodes.ndjson", ["semiconductor", "diode"], "SAS", "diode.json", [
        Field("forwardVoltage", _di("electrical", "forwardVoltage")),
        Field("reverseVoltage", _di("electrical", "reverseVoltage")),
        Field("forwardCurrent", _di("electrical", "forwardCurrent")),
    ]),
]

CATALOGUES = {c.name: c for c in _CATALOGUES}

# Rule 4's ONE sanctioned single-field ladder. Real vendor numbering prints
# Rds(on) in the order code (IPP60R080P7 = 80 mOhm), so onResistance tracking a
# numeric run of the part number is EXPECTED. It is exempt from the count of
# laddered fields; it is not exempt from any other rule.
LADDER_EXEMPT = {"onResistance"}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def unwrap(rec, disc):
    body = rec
    for key in disc:
        if not isinstance(body, dict) or key not in body:
            return None
        body = body[key]
    return body if isinstance(body, dict) else None


def identity_of(body):
    """The row's identity: partNumber first, reference as the fallback.

    Both are optional and neither is universal, so a gate keyed on one of them
    silently passes half the corpus -- which is exactly how ABT #256 and the 448
    TDK magnetics got in. A row with NEITHER is a refusal, never a skip."""
    mi = body.get("manufacturerInfo", {}) if isinstance(body, dict) else {}
    pn = first(mi, ["datasheetInfo", "part", "partNumber"])
    if isinstance(pn, str) and pn.strip():
        return pn.strip()
    ref = mi.get("reference")
    if isinstance(ref, str) and ref.strip():
        return ref.strip()
    return None


def series_of(body):
    mi = body.get("manufacturerInfo", {}) if isinstance(body, dict) else {}
    for path in (["datasheetInfo", "part", "series"], ["family"]):
        v = first(mi, path)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def manufacturer_of(body):
    mi = body.get("manufacturerInfo", {}) if isinstance(body, dict) else {}
    return (mi.get("name") or "?").strip()


DESC_PATHS = (
    ["datasheetInfo", "part", "description"],
    ["datasheetInfo", "part", "matchcodeDescription"],
    ["datasheetInfo", "part", "matchCode"],
)


def description_of(body):
    mi = body.get("manufacturerInfo", {}) if isinstance(body, dict) else {}
    out = []
    for p in DESC_PATHS:
        v = first(mi, p)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return " ".join(out)


def provenance_of(body):
    mi = body.get("manufacturerInfo", {}) if isinstance(body, dict) else {}
    entries = first(mi, ["datasheetInfo", "provenance"]) or []
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _norm(s):
    """Alphanumerics only, upper-cased, percent-decoding first.

    A vendor's per-SKU URL escapes the punctuation of the part it names --
    Murata's "#A914BYW-150M=P3" arrives as "%23A914BYW-150M%3DP3" -- and
    normalising WITHOUT decoding leaves the "23"/"3D" of the escapes inside the
    string, breaking the match on exactly the parts whose identity is unusual."""
    return re.sub(r"[^0-9A-Za-z]", "", unquote(s or "")).upper()


# ---------------------------------------------------------------------------
# rule 1 -- identity is real
# ---------------------------------------------------------------------------
# A description is prose; a part number is a code. The signals below are the
# ones that separate the two WITHOUT catching a real part number. Bare
# whitespace is NOT one of them (Ohmite "HS50 R3 F"), nor is a parenthesis
# (Molex packaging codes), nor is "=" (Murata "#A914BYW-150M=P3").

_LOWER_WORD = re.compile(r"[a-z]{3,}")
_PROSE_WORD = re.compile(r"\b(series|for|with|and|type|tol|max|min|nom|profile|"
                         r"tolerance|refrigerator|sensor)\b", re.I)
_LIST_SEP = re.compile(r",\s*\S")


def _looks_like_prose(text):
    """Reasons `text` reads as a grid-row description rather than a code."""
    why = []
    if _LOWER_WORD.search(text):
        why.append("contains a lower-case word")
    if _LIST_SEP.search(text):
        why.append("comma-separated list")
    if "%" in text:
        why.append("states a percentage")
    if _PROSE_WORD.search(text):
        why.append("contains an English descriptor word")
    if len(text.split()) >= 5:
        why.append("%d whitespace-separated tokens" % len(text.split()))
    return why


def check_identity(body):
    """Rule 1, per record. Cross-row duplicates are handled by the batch pass."""
    out = []
    ident = identity_of(body)
    if not ident:
        return [Refusal(1, "<no identity>",
                        "row carries neither datasheetInfo.part.partNumber nor "
                        "manufacturerInfo.reference -- a row with no identity is a "
                        "refusal, never a skip")]
    if len(ident) > MAX_IDENTITY_LEN:
        out.append(Refusal(1, ident,
                           "identity is %d characters (>%d): that is a grid-row "
                           "description, not a part number"
                           % (len(ident), MAX_IDENTITY_LEN)))
    series = series_of(body)
    if series and ident.startswith(series) and len(ident) > len(series):
        remainder = ident[len(series):]
        why = _looks_like_prose(remainder)
        if why:
            out.append(Refusal(1, ident,
                               "series-prefix test: identity is its own series %r "
                               "followed by %r, which reads as prose (%s)"
                               % (series, remainder, "; ".join(why))))
    elif not series and (" " in ident or "\t" in ident):
        why = _looks_like_prose(ident)
        if why:
            out.append(Refusal(1, ident,
                               "identity contains whitespace and reads as prose (%s)"
                               % "; ".join(why)))
    return out


# ---------------------------------------------------------------------------
# rule 2 -- the citation names the part
# ---------------------------------------------------------------------------
# 134,949 rows cite a landing page that names no part. A URL is evidence only if
# it can lead a human to THIS part: it spells the part out, it addresses a
# document by id, or the entry itself says the document covers this series.

SEARCH_RE = re.compile(
    r"(?:[?&](?:q|query|keyword|keywords|search|searchterm|term|text|filter)=)"
    r"|/search(?:/|\?|$)|/find(?:/|\?|$)|/parametric|/results?(?:/|\?|$)"
    r"|selectionmodel|/productsearch", re.I)
DOCID_RE = re.compile(
    r"/(?:docs?|documents?|datasheets?|pdfs?|download|getfile|media)/[^/?#]*\d{3,}",
    re.I)

# The two stamps that are honest about an un-cited row. ABSENT is not one of
# them: absent means UNSTATED, and an unstated claim is how 6,082 synthesized
# Vishay identities passed for sourced data.
WEAK_VERIFICATION = {"inferredNotVerified", "notAttempted"}
SERIES_VERIFICATION = "seriesConfirmed"


def _citations(body):
    mi = body.get("manufacturerInfo", {}) if isinstance(body, dict) else {}
    urls = []
    ds = mi.get("datasheetUrl")
    if isinstance(ds, str) and ds.strip():
        urls.append((ds.strip(), None))
    for e in provenance_of(body):
        u = e.get("sourceUrl")
        if isinstance(u, str) and u.strip():
            urls.append((u.strip(), e))
    return urls


def classify_citation(url, ident, entry):
    """PART_SPECIFIC / DOCUMENT_BY_ID / SERIES_DATASHEET / SEARCH / LANDING."""
    if ident and _norm(ident) and _norm(ident) in _norm(url):
        return "PART_SPECIFIC"
    if entry is not None and entry.get("verification") == SERIES_VERIFICATION:
        return "SERIES_DATASHEET"
    if DOCID_RE.search(url):
        return "DOCUMENT_BY_ID"
    if SEARCH_RE.search(url):
        return "SEARCH"
    return "LANDING"


ACCEPTABLE_CITATIONS = {"PART_SPECIFIC", "DOCUMENT_BY_ID", "SERIES_DATASHEET"}


def check_citation(body):
    ident = identity_of(body) or "<no identity>"
    kinds = [classify_citation(u, identity_of(body), e) for u, e in _citations(body)]
    if any(k in ACCEPTABLE_CITATIONS for k in kinds):
        return []
    # No citation names the part. The row may still enter -- most of a catalogue
    # is parametric data -- but only while SAYING SO.
    stamps = [e.get("verification") for e in provenance_of(body)]
    if stamps and all(s in WEAK_VERIFICATION for s in stamps):
        return []
    if not stamps:
        return [Refusal(2, ident,
                        "no citation names this part (%s) and no provenance entry "
                        "carries a verification stamp. An un-cited row may enter "
                        "only stamped %s -- absent means UNSTATED, which is a "
                        "stronger claim than the evidence supports"
                        % (kinds or "no sourceUrl at all",
                           "/".join(sorted(WEAK_VERIFICATION))))]
    # `None` here is an entry with NO verification key: unstated, which is a
    # stronger claim than the evidence supports, not a weaker one.
    strong = sorted({("<absent>" if s is None else str(s))
                     for s in stamps if s not in WEAK_VERIFICATION})
    return [Refusal(2, ident,
                    "no citation names this part (%s) but provenance claims "
                    "verification=%s. Downgrade to %s or cite a document that "
                    "names the part"
                    % (kinds, ",".join(str(s) for s in strong),
                       "/".join(sorted(WEAK_VERIFICATION))))]


# ---------------------------------------------------------------------------
# rule 5 -- SI and physically possible (per record; the median test is batch)
# ---------------------------------------------------------------------------

def check_units(body, fields):
    out = []
    ident = identity_of(body) or "<no identity>"
    for f in fields:
        for v in walk(body, f.segs):
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            if f.bound is not None and abs(v) > f.bound:
                out.append(Refusal(5, ident,
                                   "%s = %r exceeds the largest magnitude this "
                                   "quantity can physically take (%g). A value in "
                                   "the wrong SI unit lands here: mOhm written as "
                                   "Ohm, uH as H, uF as F"
                                   % (f.key, v, f.bound)))
            if f.positive and v < 0:
                out.append(Refusal(5, ident,
                                   "%s = %r is negative; there is no negative "
                                   "capacitance, resistance or inductance -- this is "
                                   "a unit/sign bug in the extractor, not a part"
                                   % (f.key, v)))
    return out


# ---------------------------------------------------------------------------
# rule 7 -- no two-seed expansion (per record)
# ---------------------------------------------------------------------------

def _electrical_of(body):
    """The record's own electrical dict; the first variant when it is a list."""
    e = ((body.get("manufacturerInfo") or {}).get("datasheetInfo") or {}).get("electrical")
    if isinstance(e, list):
        e = e[0] if e else {}
    return e if isinstance(e, dict) else {}


def check_seed_identity(body):
    why = seed_expanded_mosfet(_electrical_of(body))
    if not why:
        return []
    return [Refusal(7, identity_of(body) or "<no identity>",
                    "row was expanded from two seeds, not read from a datasheet: "
                    + why)]


def _median(vals):
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


# ---------------------------------------------------------------------------
# rule 6 -- schema validity, with required-but-vacuous counted as missing
# ---------------------------------------------------------------------------

# An empty ARRAY is sometimes a positive statement -- MAS `gapping: []` says the
# core is UNGAPPED, which is a fact about the part, not a hole where a value
# should be. An empty OBJECT never says anything. So the pruner treats the two
# differently, and the keys where [] is an assertion are listed, not guessed.
EMPTY_LIST_IS_A_STATEMENT = {"gapping"}


def prune_empty(node):
    """A copy with every vacuous object/array removed.

    Draft 2020-12 accepts `{"electrical": {}}` for a required `electrical`: the
    key is present, so `required` is satisfied and NOTHING INSIDE IS CHECKED.
    Validating the PRUNED copy is what turns "present but vacuous" back into
    "missing", which is what it means."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            p = prune_empty(v)
            if isinstance(p, dict) and not p:
                continue
            if isinstance(p, list) and not p and k not in EMPTY_LIST_IS_A_STATEMENT:
                continue
            out[k] = p
        return out
    if isinstance(node, list):
        out = []
        for v in node:
            p = prune_empty(v)
            if isinstance(p, (dict, list)) and not p:
                continue
            out.append(p)
        return out
    return node


def _build_registry():
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    by_id, by_path = {}, {}
    for repo_name in ("PEAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "CONAS", "AAS",
                      "TDAS"):
        d = PROTEUS / repo_name / "schemas"
        if not d.is_dir():
            continue
        for p in d.rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            p = p.resolve()
            by_path[p] = s
            if s.get("$id"):
                by_id[s["$id"]] = s
    META = {"$schema", "$id", "title", "description", "$comment"}
    for sid, s in list(by_id.items()):
        if set(s) - META != {"$ref"}:
            continue
        path = next((p for p, v in by_path.items() if v is s), None)
        if path is None:
            continue
        tgt = by_path.get((path.parent / s["$ref"]).resolve())
        if tgt is None:
            continue
        inl = {k: v for k, v in tgt.items() if k not in ("$id", "$schema")}
        inl["$id"] = sid
        inl["$schema"] = s.get("$schema",
                               "https://json-schema.org/draft/2020-12/schema")
        by_id[sid] = inl
    return Registry().with_resources(
        [(sid, Resource(contents=s, specification=DRAFT202012))
         for sid, s in by_id.items()])


_REGISTRY = None


def validator_for(cat):
    global _REGISTRY
    from jsonschema import Draft202012Validator
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    schema_path = PROTEUS / cat.repo / "schemas" / cat.schema_file
    if not schema_path.is_file():
        raise IngestRefused(
            "cannot validate %s: %s is missing. The gate refuses to report a pass "
            "on a schema it never loaded -- check out the sibling repos."
            % (cat.name, schema_path))
    return Draft202012Validator(json.loads(schema_path.read_text()),
                                registry=_REGISTRY)


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

class IngestGate:
    """Per-record rules on offer(); cohort rules on close()."""

    def __init__(self, catalogue, validate=True, min_cohort=MIN_COHORT,
                 outlier_overrides=(), median_outliers=False):
        if catalogue not in CATALOGUES:
            raise IngestRefused(
                "unknown catalogue %r -- the gate refuses to pass rows into a file "
                "it has no field spec for. Add one to CATALOGUES." % catalogue)
        self.cat = CATALOGUES[catalogue]
        self.min_cohort = min_cohort
        self.overrides = set(outlier_overrides)
        # OFF by default, and this is the one rule that could not be made
        # precise enough to run unattended. "> 1e3 x the field median" is exactly
        # the signature of a unit slip -- and also of an ordinary catalogue: a
        # capacitor batch spans 100 pF to 100 uF (six decades), a resistor batch
        # 5 mOhm to 10 MOhm (nine). Run over 300 real capacitor rows it refused
        # 54 of them, every one a genuine part. The magnitude test that DOES run
        # by default is Field.bound, which is physics, not statistics. Turn this
        # on for a batch you know to be one decade wide (one series, one size).
        self.median_outliers = median_outliers
        self._validator = validator_for(self.cat) if validate else None
        self.rows = []          # (identity, manufacturer, series, description, body)
        self.cites = []         # (has_part_specific, {url accepted only by doc-id})
        self.values = []        # {field key: scalar}
        self.accepted = 0

    # -- per record ---------------------------------------------------------
    def offer(self, rec):
        """Refusals for this row alone. The row is remembered for the batch pass
        only when it passes: a refused row never enters, so it must not colour
        the cohort statistics of the ones that do."""
        body = unwrap(rec, self.cat.disc)
        if body is None:
            return [Refusal(0, "<unwrappable>",
                            "row is not wrapped in its discriminator %s -- every "
                            "catalogue record is {\"%s\": ...}"
                            % ("/".join(self.cat.disc), self.cat.disc[0]))]
        refusals = []
        refusals += check_identity(body)
        refusals += check_citation(body)
        refusals += check_units(body, self.cat.fields)
        refusals += check_seed_identity(body)
        if self._validator is not None:
            refusals += self._check_schema(body)
        if refusals:
            return refusals
        self.rows.append((identity_of(body), manufacturer_of(body), series_of(body),
                          description_of(body), body))
        self.values.append({f.key: first(body, f.segs) for f in self.cat.fields})
        kinds = [(u, classify_citation(u, identity_of(body), e))
                 for u, e in _citations(body)]
        self.cites.append((any(k == "PART_SPECIFIC" for _, k in kinds),
                           {u for u, k in kinds if k == "DOCUMENT_BY_ID"}))
        self.accepted += 1
        return []

    def _check_schema(self, body):
        ident = identity_of(body) or "<no identity>"
        errs = sorted(self._validator.iter_errors(body), key=lambda e: e.path)
        if errs:
            e = errs[0]
            return [Refusal(6, ident, "does not validate against %s/%s: %s @ %s"
                            % (self.cat.repo, self.cat.schema_file, e.message,
                               list(e.absolute_path)))]
        pruned = prune_empty(body)
        errs = sorted(self._validator.iter_errors(pruned), key=lambda e: e.path)
        if errs:
            e = errs[0]
            return [Refusal(6, ident,
                            "a required field is present but VACUOUS ({} or []). "
                            "Draft 2020-12 accepts that; the gate does not -- an "
                            "empty object is a missing value wearing a key: %s @ %s"
                            % (e.message, list(e.absolute_path)))]
        return []

    def admit(self, rec):
        """Admit a row or ABORT. No degraded row, no default, no silent skip."""
        refusals = self.offer(rec)
        if refusals:
            raise IngestRefused(
                "INGEST REFUSED (%d reason(s)) -- the row does not enter and the "
                "import stops:\n  %s"
                % (len(refusals), "\n  ".join(str(r) for r in refusals)))
        return True

    # -- batch --------------------------------------------------------------
    def batch_refusals(self):
        return (self._dup_identities() + self._reused_documents()
                + self._minted_constants() + self._ladders()
                + self._outliers())   # _outliers is opt-in

    def close(self):
        refusals = self.batch_refusals()
        if refusals:
            raise IngestRefused(
                "INGEST BATCH REFUSED (%d finding(s)) over %d candidate row(s) -- "
                "nothing is written:\n  %s"
                % (len(refusals), len(self.rows),
                   "\n  ".join(str(r) for r in refusals)))
        return True

    # rule 1, cross-row
    def _dup_identities(self):
        seen = {}
        out = []
        for ident, mfr, series, _desc, _body in self.rows:
            key = (mfr, series, ident)
            if key in seen:
                out.append(Refusal(1, ident,
                                   "duplicate identity within manufacturer %r series "
                                   "%r -- two rows cannot be the same part"
                                   % (mfr, series)))
            seen[key] = True
        return out

    # rule 2, cross-row: the same document standing in for many different parts
    def _reused_documents(self):
        """A document-by-id URL is accepted per row because it addresses a real
        document. Handed to N different parts, it is a FAMILY datasheet being
        passed off as a per-part citation -- the shape of the 134,949 rows that
        cite a page naming no part. The escape hatch is to say so: stamp the
        entry verification=seriesConfirmed, which is the schema's own word for
        "this document covers the part's series but does not print its code"."""
        counts = {}
        for i, (part_specific, docids) in enumerate(self.cites):
            if part_specific:
                continue
            for u in docids:
                counts.setdefault(u, []).append(i)
        out = []
        for url, idx in counts.items():
            if len(idx) < self.min_cohort:
                continue
            for i in idx:
                out.append(Refusal(2, self.rows[i][0],
                                   "its only citation %s is handed to %d different "
                                   "parts in this batch and names none of them. "
                                   "Cite the part's own document, or stamp "
                                   "verification=%s"
                                   % (url, len(idx), SERIES_VERIFICATION)))
        return out

    # rule 3
    def _minted_constants(self):
        out = []
        for f in self.cat.fields:
            if f.unit is None:
                continue
            cohorts = {}
            for i, (ident, mfr, _s, desc, _b) in enumerate(self.rows):
                v = self.values[i].get(f.key)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
                    cohorts.setdefault((mfr, float(v)), []).append((ident, desc))
            for (mfr, value), members in cohorts.items():
                if len(members) < self.min_cohort:
                    continue
                contradicted, mismatched, agreeing = [], [], 0
                for ident, desc in members:
                    stated = parse_quantity(desc, f.unit)
                    if stated:
                        if any(abs(s - value) <= TOLERANCE * max(s, value)
                               for s in stated):
                            agreeing += 1
                            continue
                        contradicted.append((ident, stated))
                    elif f.unit == "H" and RE_IMPEDANCE_CONTEXT.search(desc or ""):
                        ohms = [z for z in parse_quantity(desc, "OHM")
                                if z >= IMPEDANCE_FLOOR]
                        if ohms:
                            mismatched.append((ident, ohms))
                # An impedance-defined part has no inductance to publish, so a
                # shared one was minted whatever the rest of the cohort looks
                # like -- and ABT #1090's fabricated rows sat in the SAME 1e-05
                # cohort as Wuerth's real 10 uH parts, so a majority test here
                # would have missed the very defect this rule exists for.
                for ident, ohms in mismatched:
                    out.append(Refusal(3, ident,
                                       "%s = %g minted on an impedance-defined part "
                                       "(its own description quotes %s ohm and no "
                                       "henries) and shared byte-identically by %d "
                                       "rows of this batch. Omit the field."
                                       % (f.key, value,
                                          "/".join("%g" % z for z in ohms),
                                          len(members))))
                # A minted constant contradicts its WHOLE cohort; a corrupt
                # description contradicts one row of a sound one.
                evidenced = len(contradicted) + agreeing
                if (len(contradicted) >= self.min_cohort
                        and len(contradicted) * 2 >= evidenced):
                    for ident, stated in contradicted:
                        out.append(Refusal(3, ident,
                                           "%s = %g shared byte-identically by %d "
                                           "rows while this part's own description "
                                           "states %s"
                                           % (f.key, value, len(members),
                                              "/".join("%g" % s for s in stated))))
        return out

    # rule 4
    def _index_sources(self):
        """Candidate "part index" series: every numeric run of the identity,
        counted from the START and from the END (a vendor's variable-length code
        puts the meaningful run at either), plus the row order itself."""
        n = len(self.rows)
        runs = [re.findall(r"\d+", ident or "") for ident, *_ in self.rows]
        sources = {"rowOrder": list(range(n))}
        if runs and all(runs):
            depth = min(len(r) for r in runs)
            for pos in range(depth):
                sources["numericRun[%d]" % pos] = [int(r[pos]) for r in runs]
                sources["numericRun[-%d]" % (pos + 1)] = [
                    int(r[-(pos + 1)]) for r in runs]
        return sources

    @staticmethod
    def _is_affine(xs, ys):
        """True when y = a*x + b holds EXACTLY (to float noise) with a != 0."""
        pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
        if len(pts) < MIN_LADDER or len({x for x, _ in pts}) < 4:
            return None
        (x0, y0), (x1, y1) = pts[0], next(
            ((x, y) for x, y in pts[1:] if x != pts[0][0]), (None, None))
        if x1 is None or x1 == x0:
            return None
        a = (y1 - y0) / (x1 - x0)
        if a == 0:
            return None
        b = y0 - a * x0
        scale = max(abs(y) for _, y in pts) or 1.0
        for x, y in pts:
            if abs(a * x + b - y) > 1e-9 * scale:
                return None
        return a, b

    def _ladders(self):
        out = []
        if len(self.rows) < MIN_LADDER:
            return out
        for label, xs in self._index_sources().items():
            hits = {}
            for f in self.cat.fields:
                ys = [v.get(f.key) for v in self.values]
                ys = [y if isinstance(y, (int, float)) and not isinstance(y, bool)
                      else None for y in ys]
                if sum(y is not None for y in ys) < MIN_LADDER:
                    continue
                if len({y for y in ys if y is not None}) < 4:
                    continue
                fit = self._is_affine(xs, ys)
                if fit:
                    hits[f.key] = fit
            counted = [k for k in hits if k not in LADDER_EXEMPT]
            # ONE field tracking a numeric run is how real vendors number parts
            # (IPP60R080P7 states its 80 mOhm). Two or more unrelated fields
            # marching off the SAME index is a generator loop, not a catalogue.
            if len(counted) >= 2:
                for ident, *_ in self.rows[:1]:
                    pass
                out.append(Refusal(4, self.rows[0][0],
                                   "arithmetic ladder: %d unrelated fields (%s) are "
                                   "each an exact affine function of %s across %d "
                                   "rows (%s). Real numbering encodes ONE quantity; "
                                   "several at once is a generator formula"
                                   % (len(counted), ", ".join(sorted(counted)), label,
                                      len(self.rows),
                                      "; ".join("%s = %g*i + %g" % (k, hits[k][0],
                                                                    hits[k][1])
                                                for k in sorted(counted)))))
        return out

    # rule 5, batch half: an implausible magnitude relative to the field itself
    def _outliers(self):
        out = []
        if not self.median_outliers:
            return out
        for f in self.cat.fields:
            if f.key in self.overrides:
                continue
            vals = [(i, v[f.key]) for i, v in enumerate(self.values)
                    if isinstance(v.get(f.key), (int, float))
                    and not isinstance(v.get(f.key), bool) and v[f.key]]
            if len(vals) < MIN_COHORT:
                continue
            med = _median([abs(v) for _, v in vals])
            if not med:
                continue
            for i, v in vals:
                ratio = abs(v) / med
                if ratio > OUTLIER_RATIO or ratio < 1.0 / OUTLIER_RATIO:
                    out.append(Refusal(5, self.rows[i][0],
                                       "%s = %g is %.3gx the batch median %g for "
                                       "this field -- a unit slip (mOhm read as Ohm, "
                                       "uH as H) looks exactly like this. Pass "
                                       "outlier_overrides={'%s'} if it is real"
                                       % (f.key, v, ratio, med, f.key)))
        return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def check_file(path, catalogue, validate=True, limit=None, median_outliers=False):
    gate = IngestGate(catalogue, validate=validate, median_outliers=median_outliers)
    refusals = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            if limit and lineno > limit:
                break
            try:
                rec = json.loads(line)
            except ValueError as exc:
                refusals.append(Refusal(0, "line %d" % lineno, "unparseable: %s" % exc))
                continue
            for r in gate.offer(rec):
                refusals.append(Refusal(r.rule, "line %d %s" % (lineno, r.part), r.why))
    refusals += gate.batch_refusals()
    return gate, refusals


# --------------------------- counter-checks --------------------------------
# Every rule is fed a REAL historical defect and its legitimate look-alike. A
# gate that refuses everything is as useless as one that refuses nothing, so
# both halves are asserted.

def _mag(pn, desc=None, ind=None, url=None, prov=None, family=None, extra=None):
    e = {"subtype": "inductor"}
    if ind is not None:
        e["inductance"] = {"nominal": ind}
    if extra:
        e.update(extra)
    part = {"partNumber": pn}
    if desc:
        part["description"] = desc
    di = {"part": part, "electrical": [e]}
    if prov is not None:
        di["provenance"] = prov
    mi = {"name": "Test", "reference": pn, "datasheetInfo": di}
    if family:
        mi["family"] = family
    mi["datasheetUrl"] = url if url is not None else (
        "https://example.com/datasheet/%s.pdf" % pn)
    return {"magnetic": {"manufacturerInfo": mi}}


def _mos(pn, **elec):
    return {"semiconductor": {"mosfet": {"manufacturerInfo": {
        "name": "Test", "reference": pn,
        "datasheetUrl": "https://example.com/datasheet/%s.pdf" % pn,
        "datasheetInfo": {"part": {"partNumber": pn}, "electrical": elec}}}}}


def _run(name, expect, gate_factory, records):
    gate = gate_factory()
    refusals = []
    for rec in records:
        refusals += gate.offer(rec)
    refusals += gate.batch_refusals()
    got = "REFUSED" if refusals else "ACCEPTED"
    ok = (got == expect)
    print("%-4s %-9s (expected %-9s) %s" % ("PASS" if ok else "FAIL", got, expect, name))
    if refusals:
        for r in refusals[:2]:
            print("        %s" % r)
    return ok


def selftest():
    mag = lambda: IngestGate("magnetics.ndjson", validate=False)      # noqa: E731
    mos = lambda: IngestGate("mosfets.ndjson", validate=False)        # noqa: E731
    results = []

    # -- rule 1: identity -----------------------------------------------------
    therm = {"thermistor": {"manufacturerInfo": {
        "name": "Vishay",
        "reference": "NTCACAP Series for Refrigerator (R25=10KOhm, tol=2%)",
        "datasheetUrl": "https://www.vishay.com/docs/29048/ntcacap.pdf",
        "datasheetInfo": {"part": {
            "partNumber": "NTCACAP Series for Refrigerator (R25=10KOhm, tol=2%)",
            "series": "NTCACAP Series for Refrigerator"}}}}}
    results.append(_run("1a  thermistor description as partNumber", "REFUSED",
                        lambda: IngestGate("thermistors.ndjson", validate=False),
                        [therm]))

    vishay = {"capacitor": {"manufacturerInfo": {
        "name": "Vishay",
        "datasheetUrl": "https://www.vishay.com/docs/42001/134d.pdf",
        "datasheetInfo": {"part": {
            "partNumber": "134D, 134J, 134L, 134P, 134S 470uF 75V Low Profile",
            "series": "134D"}}}}}
    results.append(_run("1b  Vishay capacitor description as partNumber", "REFUSED",
                        lambda: IngestGate("capacitors.ndjson", validate=False),
                        [vishay]))

    ohmite = {"resistor": {"manufacturerInfo": {
        "name": "Ohmite",
        "datasheetUrl": "https://www.ohmite.com/docs/1234/hs.pdf",
        "datasheetInfo": {"part": {"partNumber": "HS50 R3 F", "series": "HS50"}}}}}
    results.append(_run("1c  Ohmite 'HS50 R3 F' (legitimate, spaces)", "ACCEPTED",
                        lambda: IngestGate("resistors.ndjson", validate=False),
                        [ohmite]))

    murata = {"capacitor": {"manufacturerInfo": {
        "name": "Murata",
        "datasheetUrl": "https://www.murata.com/products/productdetail?partno="
                        "%23A914BYW-150M%3DP3",
        "datasheetInfo": {"part": {"partNumber": "#A914BYW-150M=P3",
                                   "series": "A914"}}}}}
    results.append(_run("1d  Murata '#A914BYW-150M=P3' (legitimate, '=' and '#')",
                        "ACCEPTED",
                        lambda: IngestGate("capacitors.ndjson", validate=False),
                        [murata]))

    # -- rule 2: citation -----------------------------------------------------
    sullins = {"connectorless": None}
    sullins = _mag("SFH11-PBPC-D13-ST-BK", desc="Header 26 pos",
                   url="https://www.sullinscorp.com/catalogs/SPB_Board_Stacking.pdf",
                   prov=[{"source": "scrape", "verification": "partNamed",
                          "sourceUrl": "https://www.sullinscorp.com/products/"}])
    results.append(_run("2a  Sullins landing page, claims verification=partNamed",
                        "REFUSED", mag, [sullins]))

    sullins_honest = _mag(
        "SFH11-PBPC-D13-ST-BK", desc="Header 26 pos",
        url="https://www.sullinscorp.com/catalogs/SPB_Board_Stacking.pdf",
        prov=[{"source": "scrape", "verification": "inferredNotVerified",
               "sourceUrl": "https://www.sullinscorp.com/products/"}])
    results.append(_run("2b  same row stamped inferredNotVerified", "ACCEPTED",
                        mag, [sullins_honest]))

    # -- rule 3: minted constant ---------------------------------------------
    beads = [_mag("MPZ2012S%03dA" % (100 + 11 * i),
                  desc="Chip Bead %d Ohm @ 100MHz" % (120 + 40 * i),
                  ind=1e-09,
                  url="https://product.tdk.com/en/search/x/info?part_no="
                      "MPZ2012S%03dA" % (100 + 11 * i))
             for i in range(6)]
    results.append(_run("3a  6 TDK beads all carrying 1e-09 H, descriptions in ohms",
                        "REFUSED", mag, beads))

    mlg = [_mag("MLG1005S1N0%sT000" % c, desc="MLG 1nH Chip Inductor 0402",
                ind=1e-09,
                url="https://product.tdk.com/en/search/x/info?part_no="
                    "MLG1005S1N0%sT000" % c)
           for c in "BCDEFG"]
    results.append(_run("3b  6 genuine 1 nH MLG inductors (same value, agreeing text)",
                        "ACCEPTED", mag, mlg))

    we = [_mag("7443430%02d" % i, desc="WE-HCI 10uH Power Inductor", ind=1e-05,
               url="https://www.we-online.com/components/products/datasheet/"
                   "7443430%02d.pdf" % i)
          for i in range(6)]
    results.append(_run("3c  6 genuine Wuerth 10 uH inductors sharing the value",
                        "ACCEPTED", mag, we))

    # -- rule 4: arithmetic ladder -------------------------------------------
    ladder = [_mos("FDMU81%03d" % (100 + 50 * i),
                   onResistance=0.001 + 0.0005 * (100 + 50 * i),
                   drainSourceVoltage=20.0 + 1.0 * (100 + 50 * i),
                   totalGateCharge=1e-09 * (100 + 50 * i),
                   continuousDrainCurrent=5.0 + 0.25 * (100 + 50 * i))
              for i in range(6)]
    results.append(_run("4a  FDMU81000-style ladder across 4 unrelated fields",
                        "REFUSED", mos, ladder))

    codes = [80, 99, 125, 190, 280, 400]
    rdson = [_mos("IPP60R%03dP7" % c, onResistance=c / 1000.0,
                  drainSourceVoltage=600.0,
                  continuousDrainCurrent=[24.0, 21.0, 16.0, 12.5, 9.0, 7.0][i],
                  totalGateCharge=[95e-9, 82e-9, 61e-9, 43e-9, 30e-9, 21e-9][i])
             for i, c in enumerate(codes)]
    results.append(_run("4b  IPP60R080P7-style onResistance-only ladder", "ACCEPTED",
                        mos, rdson))

    # -- rule 7: two-seed expansion ------------------------------------------
    # 7a is the real FDMU8100L row as it stood in data/mosfets.ndjson before it
    # was deleted on 2026-09-06 (recovered from the git-LFS object of 4f7de90).
    # onsemi has no FDMU prefix, the record claims TI's "NexFET" trademark, and
    # 2400 W = 0.3 x 100 V x 80 A exactly.
    fdmu = _mos("FDMU8100L", drainSourceVoltage=100, gateSourceVoltageMax=20,
                continuousDrainCurrent=80, continuousDrainCurrentAt100C=52.0,
                powerDissipation=2400.0, onResistance=0.013, onResistanceVgs=10,
                onResistanceId=40.0, inputCapacitance=5.2e-08,
                outputCapacitance=1.04e-08,
                reverseTransferCapacitance=1.0400000000000001e-09,
                capacitanceMeasurementVds=50.0, totalGateCharge=4e-06,
                bodyDiodeForwardVoltage=0.85, bodyDiodeContinuousCurrent=80)
    results.append(_run("7a  the deleted FDMU8100L row (Pd = 0.3 x Vds x Id exactly)",
                        "REFUSED", mos, [fdmu]))

    # 7b is a REAL part whose dissipation sits NEAR the identity and must pass:
    # onsemi NDT014, 60 V / 2.7 A / 43 W, against 0.3 x 60 x 2.7 = 48.6 (11.5%
    # off). "Near" is not the signature; exact-in-double is.
    ndt014 = _mos("NDT014", drainSourceVoltage=60.0, continuousDrainCurrent=2.7,
                  powerDissipation=43.0, onResistance=0.2, onResistanceVgs=10,
                  totalGateCharge=5e-09)
    results.append(_run("7b  onsemi NDT014, Pd 11.5% off the identity", "ACCEPTED",
                        mos, [ndt014]))

    # 7c is the identity landing on a real part by COINCIDENCE, with none of the
    # derived constants beside it: onsemi NDT3055, 0.3 x 60 x 4 = 72.0 exactly.
    # It is a mis-mapped dissipation column on a part onsemi ships, and the gate
    # must not call it generated.
    ndt3055 = _mos("NDT3055", drainSourceVoltage=60.0, continuousDrainCurrent=4.0,
                   powerDissipation=72.0, onResistance=0.1, onResistanceVgs=10,
                   totalGateCharge=9e-09)
    results.append(_run("7c  onsemi NDT3055, identity by coincidence, no derived "
                        "constants", "ACCEPTED", mos, [ndt3055]))

    # -- rule 5: units --------------------------------------------------------
    negcap = {"capacitor": {"manufacturerInfo": {
        "name": "Test",
        "datasheetUrl": "https://example.com/datasheet/TESTCAP1.pdf",
        "datasheetInfo": {"part": {"partNumber": "TESTCAP1"},
                          "electrical": {"capacitance": {"nominal": -5e-14},
                                         "ratedVoltage": 50}}}}}
    results.append(_run("5a  capacitance = -5e-14 F", "REFUSED",
                        lambda: IngestGate("capacitors.ndjson", validate=False),
                        [negcap]))

    # -- rule 6: schema, with vacuous-required counted as missing -------------
    vacuous = {"magnetic": {"manufacturerInfo": {
        "name": "Test", "reference": "VAC-1",
        "datasheetUrl": "https://example.com/datasheet/VAC-1.pdf",
        "datasheetInfo": {"part": {"partNumber": "VAC-1"}, "electrical": [{}]}}}}
    results.append(_run("6a  required `electrical` present but vacuous ([{}])",
                        "REFUSED", lambda: IngestGate("magnetics.ndjson"), [vacuous]))

    # 6c exercises the PRUNE branch on its own. MAS/CAS/RAS turned out to be
    # strict enough that every emptiable required node they have already fails
    # the plain validator (searched exhaustively over a real record of each), so
    # nothing in those three catalogues reaches this branch today -- it is the
    # net for the next schema that lists a required object with no required
    # contents, where Draft 2020-12 would accept `{}` and mean nothing by it.
    from jsonschema import Draft202012Validator
    toy = Draft202012Validator({"type": "object", "required": ["electrical"],
                                "properties": {"electrical": {"type": "object"}}})

    def _toy_gate():
        gg = IngestGate("magnetics.ndjson", validate=False)
        gg._validator = toy
        return gg

    toy_row = {"magnetic": {"electrical": {}, "manufacturerInfo": {
        "name": "Test", "reference": "TOY-1",
        "datasheetUrl": "https://example.com/datasheet/TOY-1.pdf",
        "datasheetInfo": {"part": {"partNumber": "TOY-1"}}}}}
    results.append(_run("6c  vacuous-required prune branch (toy schema)", "REFUSED",
                        _toy_gate, [toy_row]))

    real = _mag("744314101", desc="WE-HCI 1.0uH Power Inductor", ind=1e-06,
                url="https://www.we-online.com/components/products/datasheet/"
                    "744314101.pdf")
    results.append(_run("6b  a complete, cited, schema-valid magnetic", "ACCEPTED",
                        lambda: IngestGate("magnetics.ndjson"), [real]))

    print("\n%d/%d counter-checks behaved as specified" % (sum(results), len(results)))
    return 0 if all(results) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--catalogue", help="e.g. magnetics.ndjson")
    ap.add_argument("--no-schema", action="store_true",
                    help="skip rule 6 (for a source that is not yet schema-shaped)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--median-outliers", action="store_true",
                    help="also flag a value >1e3x the batch median for its field. "
                         "Only meaningful on a batch known to span ONE decade: a "
                         "real capacitor catalogue spans six")
    ap.add_argument("--selftest", action="store_true",
                    help="run the counter-checks: one real defect and one "
                         "legitimate look-alike per rule")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.files or not args.catalogue:
        ap.error("give --catalogue and at least one file, or --selftest")
    rc = 0
    for path in args.files:
        gate, refusals = check_file(path, args.catalogue,
                                    validate=not args.no_schema, limit=args.limit,
                                    median_outliers=args.median_outliers)
        print("%s: %d row(s) admitted, %d refusal(s)"
              % (path.name, gate.accepted, len(refusals)))
        for r in refusals[:40]:
            print("  %s" % r)
        if len(refusals) > 40:
            print("  ... and %d more" % (len(refusals) - 40))
        if refusals:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
