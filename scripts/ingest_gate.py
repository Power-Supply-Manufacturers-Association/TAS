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
  * Rule 2 does NOT require the URL to spell the part. ST "DM00..." links,
    Littelfuse asset ids, family PDFs and distributor mirrors print the part
    number inside the document while their URLs never do. Such a citation is
    accepted when a cached copy of THAT EXACT document is in the document-
    evidence store and its text prints the exact part number (see below). The
    store is consulted read-only and offline: no document on file means the old
    URL-only rule applies unchanged, never "assume it names the part".

THE DOCUMENT-EVIDENCE STORE (rule 2's second path; approved by Alf 2026-10-17)

  Location: <TAS>/staging/doc_evidence/  (DOC_EVIDENCE_DIR). staging/ is
  gitignored, so the store is NOT committed; it lives beside the campaigns'
  own document caches it is filled from.

  Key: evidence_key(url) = sha256 of the provenance sourceUrl (or the
  datasheetUrl) exactly as stored in the record, whitespace-stripped, UTF-8.
  Records carry no extra field -- the key is derived from sourceUrl, so the
  check is reproducible from the record plus the store.

  Layout, one entry per document:
      doc_evidence/<key[:2]>/<key>.json     metadata
      doc_evidence/<key[:2]>/<key>.txt.gz   the document's extracted text, gzip
  Metadata keys: url (must equal the cited URL byte for byte), evidenceKey,
  retrievedDate (when the document was fetched), textSha256 (sha256 of the
  UTF-8 text; re-checked on every read -- a mismatch RAISES DocEvidenceError),
  documentSha256 (sha256 of the original bytes when they were on file, else
  null), textMethod (how the text was extracted), registeredFrom (the cache
  file it came from), registeredDate.

  Entries are added only by scripts/register_doc_evidence.py, from documents a
  campaign already fetched. The gate never fetches.

  Match: the part number, normalised like _norm (alphanumerics, upper-case),
  must occur in the text as a whole token -- one optional punctuation/space
  character allowed between its characters, and no letter/digit (nor a
  "-letter/digit" suffix or prefix) touching either end. So IRF540 is NOT named
  by a document that prints only IRF540N, and GT400TH60N is not named by one
  that prints only VS-GT400TH60N. Identities shorter than
  MIN_TEXT_MATCH_LEN normalised characters never use this path, and a
  search-shaped URL never does (a results page echoes the query it was given).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
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
        # Signed as the datasheet prints them: a P-channel part's V_DS and I_D are
        # negative. check_channel_sign ties the sign to part.subType instead.
        Field("drainSourceVoltage", _di("electrical", "drainSourceVoltage"), positive=False, bound=1e5),
        Field("continuousDrainCurrent", _di("electrical", "continuousDrainCurrent"), positive=False),
        Field("totalGateCharge", _di("electrical", "totalGateCharge")),
        Field("inputCapacitance", _di("electrical", "inputCapacitance"), "F", bound=1.0),
        Field("outputCapacitance", _di("electrical", "outputCapacitance"), "F", bound=1.0),
        Field("powerDissipation", _di("electrical", "powerDissipation")),
    ]),
    Catalogue("diodes.ndjson", ["semiconductor", "diode"], "SAS", "diode.json", [
        Field("forwardVoltage", _di("electrical", "forwardVoltage")),
        Field("reverseVoltage", _di("electrical", "reverseVoltage")),
        Field("forwardCurrent", _di("electrical", "forwardCurrent")),
        # TVS/ESD pulse ratings (SAS-RFC 0003). Bounds are physical ceilings for a
        # single diode package, not typical values: the largest held surge rating
        # is a 20 kA 8/20 us PTVS, the largest TVS P_PPM tens of kW, and a mOhm/mA
        # or kA-written-as-A slip lands orders of magnitude past these.
        Field("peakPulseCurrent", _di("electrical", "peakPulseCurrent"), bound=1e5),
        Field("peakPulsePower", _di("electrical", "peakPulsePower"), bound=1e7),
        Field("clampingVoltage", _di("electrical", "clampingVoltage"), bound=1e5),
        Field("clampingCurrent", _di("electrical", "clampingCurrent"), bound=1e5),
    ]),
    # ---- catalogues added 2026-09-07 ---------------------------------------
    # The gate covered 7 of 15 catalogues, so 8 could not be guarded at all --
    # including connectors.ndjson, the LARGEST in the corpus at 392,308 rows. An
    # unknown catalogue makes IngestGate raise, which reads as "no gate" to a
    # caller that does not check, so this was a silent hole rather than a loud one.
    # Fields are the quantities whose UNITS can be checked and whose magnitude has
    # a physical ceiling; fields with no meaningful bound (enums, counts, ratios)
    # are deliberately absent rather than given an invented one.
    Catalogue("connectors.ndjson", ["connector"], "CONAS", "connector.json", [
        Field("ratedCurrentPerContact", _di("electrical", "ratedCurrentPerContact"),
              bound=1e4),
        Field("ratedVoltage", _di("electrical", "ratedVoltage"), bound=1e6),
        Field("contactResistance", _di("electrical", "contactResistance", "maximum"),
              "OHM", bound=1e6),
        Field("insulationResistance", _di("electrical", "insulationResistance"),
              "OHM", bound=1e16),
        # insulationPaths[] is an ARRAY of paths, each with its own rating -- the
        # 11.1 megavolt Weidmueller placeholder lived in here, not on the top level
        Field("ratedImpulseVoltage",
              _di("electrical", "insulationPaths", "*", "ratedImpulseVoltage"),
              bound=1e6),
        # familyRf (added 2026-10-17 with CONAS draft A): an rf connector may omit
        # `electrical`, so these three are the only numbers that can carry it. No
        # `unit`: rule 3 would read a series-wide 50 ohm as a minted constant.
        # Bounds are ceilings, not typical values -- live rf rows hold 42.5..100
        # ohm, 0.1..110 GHz and VSWR 1.0..2.6; 50 ohm written in mOhm is 5e4,
        # 6 GHz written in kHz-as-Hz is 6e12, a return loss in dB stored as VSWR
        # lands past 10.
        Field("characteristicImpedance", _di("familyDetails", "characteristicImpedance"),
              bound=1e4),
        Field("frequencyMaximum", _di("familyDetails", "frequencyRange", "maximum"),
              bound=1e12),
        Field("maxVswr", _di("familyDetails", "maxVswr"), bound=10.0),
    ]),
    Catalogue("igbts.ndjson", ["semiconductor", "igbt"], "SAS", "igbt.json", [
        Field("collectorEmitterVoltage", _di("electrical", "collectorEmitterVoltage"),
              bound=1e5),
        Field("continuousCollectorCurrent",
              _di("electrical", "continuousCollectorCurrent"), bound=1e5),
        Field("collectorEmitterSaturation",
              _di("electrical", "collectorEmitterSaturation"), bound=1e3),
        Field("powerDissipation", _di("thermal", "powerDissipation"), bound=1e7),
    ]),
    Catalogue("bjts.ndjson", ["semiconductor", "bjt"], "SAS", "bjt.json", [
        Field("collectorEmitterVoltage", _di("electrical", "collectorEmitterVoltage"),
              bound=1e5),
        Field("collectorCurrent", _di("electrical", "collectorCurrent"), bound=1e5),
        Field("transitionFrequency", _di("electrical", "transitionFrequency"),
              bound=1e13),
        Field("powerDissipation", _di("electrical", "powerDissipation"), bound=1e7),
    ]),
    Catalogue("controllers.ndjson", ["controller"], "CTAS", "controller.json", [
        Field("referenceVoltage", _di("electrical", "referenceVoltage", "nominal"),
              bound=1e4),
        Field("supplyVoltageMax", _di("electrical", "supplyVoltage", "maximum"),
              bound=1e4),
        Field("switchingFrequencyMax",
              _di("electrical", "switchingFrequency", "maximum"), bound=1e10),
    ]),
    Catalogue("analog_ics.ndjson", ["analog", "*"], "AAS", "*", [
        Field("gainBandwidthProduct", _di("electrical", "gainBandwidthProduct"),
              bound=1e12),
        Field("slewRate", _di("electrical", "slewRate"), bound=1e12),
        Field("inputOffsetVoltage", _di("electrical", "inputOffsetVoltage"), bound=1e2),
        Field("inputBiasCurrent", _di("electrical", "inputBiasCurrent"), bound=1e2),
    ]),
    Catalogue("timing_devices.ndjson", ["timeBase", "*"], "TDAS", "*", [
        # no description parser exists for hertz (RE_BY_UNIT covers F/H/OHM only),
        # so this field is bound-checked but not cross-read from the description
        Field("frequency", _di("electrical", "frequency"), bound=1e13),
        Field("loadCapacitance", _di("electrical", "loadCapacitance"), "F", bound=1e0),
        Field("supplyVoltageMax", _di("electrical", "supplyVoltage", "maximum"),
              bound=1e4),
    ]),
    # NOT given specs, deliberately: circuits.ndjson holds CIAS bricks and
    # converters.ndjson holds whole TAS documents. Neither is an orderable part, so
    # identity, citation and cohort rules do not apply to them, and inventing a spec
    # to make the count look complete would mean the gate reporting on something it
    # is not measuring. They stay outside the gate and are covered by
    # tests/test_data.py and scripts/validate_topology.py instead.
    # ---- catalogues added 2026-09-10 ---------------------------------------
    # Four families whose parts spent months in quarantine because no schema could
    # hold them. Their catalogues are new, so the gate had no spec and would have
    # refused them outright -- which is the correct failure, but it means the
    # migration could not be gated at all until these existed.
    Catalogue("connector_accessories.ndjson", ["connectorAccessory"], "CONAS",
              "connectorAccessory.json", [
        Field("ratedCurrentPerContact", _di("electrical", "ratedCurrentPerContact"),
              bound=1e4),
        Field("ratedVoltage", _di("electrical", "ratedVoltage"), bound=1e6),
        Field("contactResistance", _di("electrical", "contactResistance", "maximum"),
              "OHM", bound=1e6),
        Field("characteristicImpedance", _di("electrical", "characteristicImpedance"),
              "OHM", bound=1e4),
    ]),
    Catalogue("relays.ndjson", ["relay"], "EMAS", "relay.json", [
        # the ratings live on the shared contactSet, not on electrical directly
        Field("contactRatedCurrent", _di("electrical", "contacts", "ratedCurrent"),
              bound=1e5),
        Field("contactRatedVoltage", _di("electrical", "contacts", "ratedVoltage"),
              bound=1e6),
        Field("contactResistance",
              _di("electrical", "contacts", "contactResistance"), "OHM", bound=1e6),
        Field("operateTime", _di("electrical", "operateTime"), bound=1e3),
    ]),
    Catalogue("switches.ndjson", ["switch"], "EMAS", "switch.json", [
        Field("contactRatedCurrent", _di("electrical", "contacts", "ratedCurrent"),
              bound=1e5),
        Field("contactRatedVoltage", _di("electrical", "contacts", "ratedVoltage"),
              bound=1e6),
        Field("contactResistance",
              _di("electrical", "contacts", "contactResistance"), "OHM", bound=1e6),
    ]),
    Catalogue("potentiometers.ndjson", ["potentiometer"], "RAS", "potentiometer.json", [
        # totalResistance, NOT resistance: the potentiometer schema declares no bare
        # `resistance` property, which is the structural guard that stops a fixed
        # resistor validating here
        Field("totalResistance", _di("electrical", "totalResistance", "nominal"),
              "OHM", bound=1e15),
        Field("powerRating", _di("electrical", "powerRating"), bound=1e5),
        Field("maximumWorkingVoltage", _di("electrical", "maximumWorkingVoltage"),
              bound=1e5),
        Field("insulationResistance", _di("electrical", "insulationResistance"),
              "OHM", bound=1e16),
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

def unwrap(rec, disc, want_kind=False):
    """Peel the PEAS discriminator(s) off a catalogue row to reach the part body.

    A "*" segment means the sub-kind is not fixed for this catalogue: analog_ics
    wraps {"analog": {"operationalAmplifier": ...}} but also comparator, adc, dac,
    voltageReference; timing_devices wraps {"timeBase": {"oscillator": ...}} and
    also timer and latch. Naming one sub-kind would silently refuse every row of
    the others, so "*" takes the single sub-object that actually carries the part
    -- the one with a manufacturerInfo. If more than one candidate matches, the
    shape is not what this spec describes and unwrapping FAILS rather than picking
    one: guessing which sibling is the part is how a gate ends up measuring itself.
    """
    body, kind = rec, None
    for key in disc:
        if not isinstance(body, dict):
            return None
        if key == "*":
            cands = [(k, v) for k, v in body.items()
                     if isinstance(v, dict) and "manufacturerInfo" in v]
            if len(cands) != 1:
                return None
            kind, body = cands[0]
            continue
        if key not in body:
            return None
        body = body[key]
    if not isinstance(body, dict):
        return None
    return (body, kind) if want_kind else body


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
# known identities -- is a candidate already in the live catalogue/quarantine?
# ---------------------------------------------------------------------------
# IDENTITY IS (PART NUMBER, MANUFACTURER) (approved by Alf 2026-10-18). The same
# part number from another manufacturer is a second source and a separate
# record (onsemi MURS160 and Vishay MURS160; Infineon and Vishay IRF530N).
#
# Every pipeline join key drops whitespace, '-', '_' and '/'. That is a
# bijection for most vendors and a COLLISION for some: Eaton prints the decimal
# point of a standoff voltage as a hyphen, so 10BJ6-0A (6.0 V) and 10BJ60A
# (60 V) are two different parts under one key -- 121 such pairs are live in
# diodes.ndjson. Rule (approved by Alf 2026-10-17): only an exact,
# case-insensitive spelling match is a duplicate. A spelling that differs from
# a known one only by those characters is a SPELLING TWIN: neither admitted nor
# dropped as a duplicate, but held for review, admitted only once someone has
# checked that the vendor prints both spellings. Both apply only WITHIN one
# manufacturer: Diodes SMAJ12AQ and Bourns SMAJ12A-Q are two sources, not twins.
#
# A known entry WITHOUT a manufacturer name (a quarantined row that never had
# one; a denylist entry, which records only the id) cannot say whose part it is,
# so it is compared against every manufacturer -- the strict direction.
# (This is the catalogue-join key. _norm / _token_pattern above, which match a
# part number inside DOCUMENT TEXT, are a separate tolerance and unchanged.)

_SPELLING_PUNCT = re.compile(r"[\s\-_/]")
HELD_TWIN = "1-twin"      # Refusal.rule of a held spelling twin
ALREADY_KNOWN = "already in live catalogue or quarantine"

# Manufacturer names are compared case-insensitively after trimming and
# collapsing whitespace. Beyond that, ONLY the spellings below are treated as
# one company: each is a legal-suffix, abbreviation, rename or "Parent / brand"
# form of the canonical name, as printed in data/*.ndjson on 2026-10-18. A name
# not listed here is its own manufacturer -- nothing is merged by similarity.
# Keys and values are already in manufacturer_key form (lower-case).
MANUFACTURER_ALIASES = {
    # legal suffix / abbreviation / punctuation
    "alpha and omega": "alpha & omega semiconductor",
    "bourns inc.": "bourns",
    "diodes inc": "diodes incorporated",
    "diodes inc.": "diodes incorporated",
    "infineon technologies": "infineon",
    "keystone electronics": "keystone",
    "littelfuse inc.": "littelfuse",
    "micro commercial components (mcc)": "micro commercial components",
    "mcc (micro commercial components)": "micro commercial components",
    "microchip technology": "microchip",
    "microsemi corporation": "microsemi",
    "monolithic power systems (mps)": "monolithic power systems",
    "mps": "monolithic power systems",
    "murata electronics": "murata",
    "murata power solutions inc.": "murata power solutions",
    "navitas semiconductor, inc.": "navitas",
    "nexperia usa inc.": "nexperia",
    "nxp semiconductors": "nxp",
    "rohm semiconductor": "rohm",
    "samtec inc.": "samtec",
    "sei stackpole": "stackpole electronics",
    "st": "stmicroelectronics",
    "sullins connector solutions": "sullins",
    "ti": "texas instruments",
    "wurth elektronik": "würth elektronik",
    "ael crystals": "ael",
    "laird technologies": "laird",
    # renames of one legal entity
    "on semiconductor": "onsemi",
    "kyocera avx": "avx",
    # divisions and "Parent / brand" labels printed with the parent's name
    "eaton - electronics division": "eaton",
    "eaton electrical": "eaton",
    "eaton electronics": "eaton",
    "bussmann / eaton": "eaton",
    "panasonic electronic components": "panasonic",
    "panasonic industrial devices": "panasonic",
    "panasonic industry": "panasonic",
    "te connectivity / deutsch": "te connectivity",
    "te connectivity / holsworthy": "te connectivity",
    "te connectivity / p&b": "te connectivity",
    "te connectivity / raychem": "te connectivity",
    "te connectivity / schrack": "te connectivity",
    "te connectivity amp connectors": "te connectivity",
    "te connectivity aerospace, defense and marine": "te connectivity",
    "te connectivity potter & brumfield relays": "te connectivity",
    "vishay / barry industries": "vishay",
    "vishay / bc components": "vishay",
    "vishay / beyschlag": "vishay",
    "vishay / dale": "vishay",
    "vishay / draloric": "vishay",
    "vishay / electro-films": "vishay",
    "vishay / esta": "vishay",
    "vishay / huntington": "vishay",
    "vishay / mcb industrie": "vishay",
    "vishay / mills": "vishay",
    "vishay / sfernice": "vishay",
    "vishay / siliconix": "vishay",
    "vishay / spectrol": "vishay",
    "vishay / techno": "vishay",
    "vishay / thin film": "vishay",
    "vishay general semiconductor - diodes division": "vishay",
    "vishay semiconductors": "vishay",
    "vishay siliconix": "vishay",
    "renesas electronics": "renesas",
    "renesas / intersil": "renesas",
    "ixys integrated circuits": "ixys",
    "yageo vitrohm": "yageo",
    "yageo xsemi": "yageo",
    "samsung electro-mechanics": "samsung",
}


def manufacturer_key(name):
    """A manufacturer name as compared for identity: trimmed, whitespace
    collapsed, case-folded, then mapped through MANUFACTURER_ALIASES. None when
    the name is absent or blank -- never a placeholder string."""
    if not isinstance(name, str):
        return None
    k = " ".join(name.split()).casefold()
    if not k:
        return None
    return MANUFACTURER_ALIASES.get(k, k)


def spelling_key(ident):
    """The pipeline's join key: whitespace, '-', '_' and '/' dropped, upper-cased.
    Two identities with one key are the same part ONLY if exact_spelling agrees
    AND the manufacturer is the same."""
    return _SPELLING_PUNCT.sub("", ident or "").upper()


def exact_spelling(ident):
    """Identity as compared for duplicates: outer whitespace stripped, upper-cased."""
    return (ident or "").strip().upper()


def manufacturer_infos(node):
    """Every manufacturerInfo object of a record, outermost first, without
    descending into a body that has one (a magnetic's own manufacturerInfo, not
    its core's). Works for every wrap, including quarantine rows of any catalogue."""
    if isinstance(node, dict):
        mi = node.get("manufacturerInfo")
        if isinstance(mi, dict):
            yield mi
            return
        for v in node.values():
            yield from manufacturer_infos(v)
    elif isinstance(node, list):
        for v in node:
            yield from manufacturer_infos(v)


def _identity_fields(mi, fields):
    out = []
    for f in fields:
        if f == "partNumber":
            v = first(mi, ["datasheetInfo", "part", "partNumber"])
        elif f == "reference":
            v = mi.get("reference")
        else:
            raise ValueError("unknown identity field %r (partNumber | reference)" % f)
        if isinstance(v, str) and v.strip():
            out.append(v)
    return out


class KnownSpellings:
    """Every spelling seen under each (manufacturer, spelling_key), with where it was seen."""

    def __init__(self):
        # (manufacturer_key or None, spelling_key)
        #   -> {exact spelling: (where, as printed, manufacturer as printed or None)}
        self._by_key = {}

    def __len__(self):
        return len(self._by_key)

    def add(self, ident, manufacturer, where):
        """`manufacturer` is the name as printed; None/blank records an entry that
        names no manufacturer, which is then compared against every manufacturer."""
        k = spelling_key(ident)
        if k:
            m = manufacturer_key(manufacturer)
            printed = " ".join(manufacturer.split()) if m is not None else None
            self._by_key.setdefault((m, k), {}).setdefault(
                exact_spelling(ident), (where, ident, printed))

    def add_file(self, path, where, fields=("partNumber", "reference"), skip=None):
        """Index the identity (`fields`) and manufacturer name of every record of
        an NDJSON file (read-only). `skip(ident, manufacturer)` -> True leaves an
        identity out. A line that is not JSON raises -- an index that silently
        drops rows would admit their duplicates."""
        with open(path, "rb") as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError as e:
                    raise ValueError("%s:%d is not JSON (%s) -- cannot index the "
                                     "known identities" % (path, n, e))
                for mi in manufacturer_infos(rec):
                    mfr = mi.get("name")
                    for ident in _identity_fields(mi, fields):
                        if skip is None or not skip(ident, mfr):
                            self.add(ident, mfr, where)
        return self

    def _known(self, ident, manufacturer):
        m = manufacturer_key(manufacturer)
        if m is None:
            raise ValueError(
                "identity %r has no manufacturer name -- identity is (part number, "
                "manufacturer), so it cannot be compared" % (ident,))
        k = spelling_key(ident)
        merged = dict(self._by_key.get((None, k), {}))
        merged.update(self._by_key.get((m, k), {}))
        return merged

    def spellings(self, ident, manufacturer):
        """{exact spelling: (where, as printed, manufacturer)} under ident's key, for this
        manufacturer (plus entries that name no manufacturer)."""
        return self._known(ident, manufacturer)

    def verdict(self, ident, manufacturer):
        """None (unknown) | ("duplicate", where, printed, manufacturer)
        | ("twin", [(where, printed, manufacturer), ...])."""
        known = self._known(ident, manufacturer)
        if not known:
            return None
        hit = known.get(exact_spelling(ident))
        if hit:
            return ("duplicate",) + hit
        return ("twin", sorted(known.values()))

    def reason(self, ident, manufacturer):
        """The reject reason for `ident`, or None when it is new."""
        v = self.verdict(ident, manufacturer)
        if v is None:
            return None
        if v[0] == "duplicate":
            return ALREADY_KNOWN
        return twin_reason(v[1])


def known_label(entry):
    """'live 10BJ60A (Eaton)' from a (where, printed, manufacturer) entry."""
    where, printed, mfr = entry
    return "%s %s%s" % (where, printed, " (%s)" % mfr if mfr else " (no manufacturer named)")


def twin_reason(known):
    return ("spelling twin: differs from %s only by '-', '_', '/' or spaces, same "
            "manufacturer; admit only if the vendor prints both"
            % ", ".join(known_label(e) for e in known))


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


# A part number inside a SEARCH parameter is the query somebody typed, not
# evidence that the document names the part. Until 2026-09-23 the PART_SPECIFIC
# test ran against the whole URL, so
# `https://datasheetpdf.com/search?q=IKW75N65H5` counted as naming IKW75N65H5
# and 223 IGBTs were admitted on the strength of their own part number pasted
# into an aggregator's search box. Every one of those rows was later shown to
# cite no document at all.
#
# Only the search PARAMETER is removed, never the whole query: a per-SKU detail
# page is legitimately addressed by query (`?part_no=MPZ2012S101A`), and that
# URL does lead a human to this part.
SEARCH_PARAM_VALUE = re.compile(
    r"[?&](?:q|query|keyword|keywords|search|searchterm|term|text|filter)=[^&]*", re.I)


def _evidence_url(url):
    """The part of a URL that can testify the page names a part."""
    return SEARCH_PARAM_VALUE.sub("", url or "")


def classify_citation(url, ident, entry):
    """PART_SPECIFIC / DOCUMENT_NAMES_PART / SERIES_DATASHEET / TOOL_QUERIED /
    DOCUMENT_BY_ID / SEARCH / LANDING."""
    if ident and _norm(ident) and _norm(ident) in _norm(_evidence_url(url)):
        return "PART_SPECIFIC"
    if ident and stored_document_names_part(url, ident):
        return "DOCUMENT_NAMES_PART"
    if entry is not None and entry.get("verification") == SERIES_VERIFICATION:
        return "SERIES_DATASHEET"
    if _is_tool_citation(url, entry):
        return "TOOL_QUERIED"
    if DOCID_RE.search(url):
        return "DOCUMENT_BY_ID"
    if SEARCH_RE.search(url):
        return "SEARCH"
    return "LANDING"


# A parametric TOOL is not a document. KEMET K-SIM, TDK's pdc_api and Taiyo
# Yuden's TYCOMPAS answer a POST whose key IS the part number and return that
# part's own curve; TDK and Yuden key it by an internal numeric pid the printed
# order code never appears in. There is no per-part URL to cite and inventing
# one would be fabrication, so the only honest sourceUrl is the endpoint --
# which reads as a LANDING page to a rule that only looks at URL shape.
#
# Admitting them on the endpoint alone would be an assertion, so it is only half
# the check. The other half is _tool_answers_per_part below: a tool that was
# really queried per part returns DIFFERENT values per part, and a landing page
# copied onto N rows returns the same ones. That half runs in the batch pass and
# can fail, which is what makes this an exemption rather than a hole.
TOOL_API_RE = re.compile(
    r"^https?://(?:"
    r"ksim\d*\.kemet\.com/api/"
    r"|product\.tdk\.com/pdc_api/"
    r"|ds\.yuden\.co\.jp/TYCOMPAS/"
    r"|redexpert\.we-online\.com/(?:api|redexpert)/"
    r")", re.I)
TOOL_SOURCE = "manufacturerParametric"


def _is_tool_citation(url, entry):
    """The entry cites a parametric tool endpoint and says it read values there."""
    if entry is None or not TOOL_API_RE.match(url or ""):
        return False
    if entry.get("source") != TOOL_SOURCE:
        return False
    fields = entry.get("fields")
    return isinstance(fields, list) and bool(fields)


ACCEPTABLE_CITATIONS = {"PART_SPECIFIC", "DOCUMENT_NAMES_PART", "DOCUMENT_BY_ID",
                        "SERIES_DATASHEET", "TOOL_QUERIED"}
# The citation kinds that name THIS part (by URL, or by the stored document's
# text). A row holding one is not a family document passed off as per-part.
NAMES_PART_CITATIONS = {"PART_SPECIFIC", "DOCUMENT_NAMES_PART"}


# ---------------------------------------------------------------------------
# rule 2 -- the document-evidence store (layout in the module docstring)
# ---------------------------------------------------------------------------
# A URL that does not spell the part can still cite a document that prints it:
# ST "DM00..." links, Littelfuse asset ids, family PDFs, distributor mirrors.
# The URL-only test cannot see that, and refused verified work for it. The
# store holds the text of documents a campaign already fetched, keyed by the
# cited URL, so the gate can read the document instead of guessing from its
# address -- offline, deterministically, and only for documents on file.

DOC_EVIDENCE_DIR = REPO / "staging" / "doc_evidence"
MIN_TEXT_MATCH_LEN = 5    # normalised characters; shorter identities match noise


class DocEvidenceError(Exception):
    """A store entry exists but cannot be trusted (URL or text hash mismatch,
    unreadable file). Raised, never treated as 'not on file': a corrupt entry
    is a broken store, and silently refusing would hide it."""


def evidence_key(url):
    """The store key of a cited URL: sha256 of the URL exactly as recorded."""
    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()


class DocEvidenceStore:
    """Read side of staging/doc_evidence. The key set is listed once, at
    construction (plus whatever register() adds), so a sweep over a million
    rows costs a set lookup per citation, not a stat."""

    def __init__(self, root):
        self.root = Path(root)
        self._keys = set()
        self._texts = {}
        if self.root.is_dir():
            for sub in sorted(self.root.iterdir()):
                if sub.is_dir() and len(sub.name) == 2:
                    for f in sub.iterdir():
                        if f.name.endswith(".json"):
                            self._keys.add(f.name[:-5])

    def _paths(self, key):
        d = self.root / key[:2]
        return d / (key + ".json"), d / (key + ".txt.gz")

    def __contains__(self, url):
        return evidence_key(url) in self._keys

    def __len__(self):
        return len(self._keys)

    def text_for(self, url):
        """The stored text of the document at exactly `url`, or None when it is
        not on file. Raises DocEvidenceError on an entry that fails its checks."""
        url = (url or "").strip()
        key = evidence_key(url)
        if key not in self._keys:
            return None
        if key in self._texts:
            return self._texts[key]
        meta_p, text_p = self._paths(key)
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            raw = gzip.decompress(text_p.read_bytes())
        except (OSError, ValueError, EOFError) as e:
            raise DocEvidenceError("doc-evidence entry %s for %s is unreadable: %s"
                                   % (key, url, e)) from e
        if meta.get("url") != url:
            raise DocEvidenceError("doc-evidence entry %s records url %r, not the "
                                   "cited %r" % (key, meta.get("url"), url))
        if hashlib.sha256(raw).hexdigest() != meta.get("textSha256"):
            raise DocEvidenceError("doc-evidence entry %s (%s): stored text does not "
                                   "hash to its recorded textSha256" % (key, url))
        text = raw.decode("utf-8")
        if len(self._texts) > 256:
            self._texts.clear()
        self._texts[key] = text
        return text

    def register(self, url, text, retrieved_date, registered_from,
                 document_sha256=None, text_method=None, registered_date=None,
                 replace=False):
        """Write one entry. Returns 'added', 'unchanged' or 'replaced'. Refuses
        (DocEvidenceError) to overwrite different text unless replace=True."""
        url = (url or "").strip()
        if not url:
            raise DocEvidenceError("cannot register a document with no URL")
        if not isinstance(text, str) or not text.strip():
            raise DocEvidenceError("cannot register %s: the text is empty" % url)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(retrieved_date or "")):
            raise DocEvidenceError("cannot register %s: retrievedDate %r is not "
                                   "YYYY-MM-DD" % (url, retrieved_date))
        raw = text.encode("utf-8")
        key = evidence_key(url)
        meta_p, text_p = self._paths(key)
        sha = hashlib.sha256(raw).hexdigest()
        status = "added"
        if meta_p.exists():
            old = json.loads(meta_p.read_text(encoding="utf-8"))
            if old.get("textSha256") == sha and old.get("url") == url:
                self._keys.add(key)
                return "unchanged"
            if not replace:
                raise DocEvidenceError(
                    "%s is already on file (%s) with DIFFERENT text; pass "
                    "replace=True only if the stored copy is known wrong"
                    % (url, key))
            status = "replaced"
        meta = {"url": url, "evidenceKey": key, "retrievedDate": retrieved_date,
                "textSha256": sha, "documentSha256": document_sha256,
                "textMethod": text_method, "registeredFrom": str(registered_from),
                "registeredDate": registered_date}
        meta_p.parent.mkdir(parents=True, exist_ok=True)
        # mtime=0: the same text always gzips to the same bytes
        tmp = text_p.with_suffix(".gz.tmp")
        tmp.write_bytes(gzip.compress(raw, mtime=0))
        os.replace(tmp, text_p)
        tmp = meta_p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n",
                       encoding="utf-8")
        os.replace(tmp, meta_p)
        self._keys.add(key)
        self._texts.pop(key, None)
        return status


_EVIDENCE_STORE = None


def evidence_store():
    global _EVIDENCE_STORE
    if _EVIDENCE_STORE is None:
        _EVIDENCE_STORE = DocEvidenceStore(DOC_EVIDENCE_DIR)
    return _EVIDENCE_STORE


def set_evidence_store(store):
    """Swap the store the gate reads (the selftest uses a temporary one).
    Returns the previous store so the caller can put it back."""
    global _EVIDENCE_STORE
    prev, _EVIDENCE_STORE = _EVIDENCE_STORE, store
    return prev


_ALNUM = "0-9A-Za-z"


def _token_pattern(ident):
    """Regex for `ident` as a whole token in free text, or None if too short.

    Characters are compared after _norm (so punctuation in the record and in the
    document need not agree: CM150EXS24S == CM150EXS-24S); one optional
    non-alphanumeric character may sit between any two of them; and neither end
    may touch a letter/digit or a "-letter/digit" continuation, which is what
    keeps IRF540 from being "named" by IRF540N and GT400TH60N by VS-GT400TH60N.
    """
    n = _norm(ident)
    if len(n) < MIN_TEXT_MATCH_LEN:
        return None
    body = ("[^%s\\n]?" % _ALNUM).join(re.escape(c) for c in n)
    return re.compile(r"(?<![%s])(?<![%s]-)%s(?![%s])(?!-[%s])"
                      % (_ALNUM, _ALNUM, body, _ALNUM, _ALNUM), re.I)


def text_names_part(text, ident):
    pat = _token_pattern(ident)
    return bool(pat and text and pat.search(text))


def stored_document_names_part(url, ident):
    """True only when the store holds the document at exactly `url` AND its text
    prints `ident` as a whole token. A search-shaped URL is never consulted: a
    results page echoes the query it was given, which is not a document naming
    the part (see SEARCH_PARAM_VALUE)."""
    if not url or SEARCH_RE.search(url):
        return False
    store = evidence_store()
    if url.strip() not in store:
        return False
    return text_names_part(store.text_for(url), ident)



def _values_at(body, fields):
    """Resolve a provenance entry's dotted `fields` against the record.

    Paths are relative to manufacturerInfo.datasheetInfo, which is where every
    entry in this corpus writes them ("electrical.impedancePoints"). A path that
    does not resolve contributes a distinct marker rather than being skipped --
    silently dropping it would make two rows look identical because neither has
    the field, which is the opposite of what the caller is testing.
    """
    ds = {}
    if isinstance(body, dict):
        ds = (body.get("manufacturerInfo") or {}).get("datasheetInfo") or {}
    out = []
    for f in fields:
        node = ds
        for seg in str(f).split("."):
            if isinstance(node, list):
                node = node[0] if node else None
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(seg)
        out.append(("<unresolved:%s>" % f) if node is None else node)
    return out


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
                    "names the part (a URL that does not spell the part counts "
                    "only when the document's text is in the doc-evidence store "
                    "and prints it: scripts/register_doc_evidence.py)"
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


CHANNEL_SIGNED = ("drainSourceVoltage", "continuousDrainCurrent", "pulsedDrainCurrent")


def check_channel_sign(body):
    """MOSFET ratings are stored signed as the datasheet prints them: negative on a
    P-channel part, positive on an N-channel one. A sign that disagrees with
    part.subType is a mapping bug (or the wrong channel), so it is refused."""
    out = []
    di = (body.get("manufacturerInfo") or {}).get("datasheetInfo") or {}
    sub = (di.get("part") or {}).get("subType")
    want = {"pChannel": -1, "nChannel": 1}.get(sub)
    if want is None:
        return out
    el = di.get("electrical") or {}
    for k in CHANNEL_SIGNED:
        v = el.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0 and (v > 0) != (want > 0):
            out.append(Refusal(5, identity_of(body) or "<no identity>",
                               "%s = %r on a %s part; ratings are stored signed as the "
                               "datasheet prints them (negative for P-channel, positive "
                               "for N-channel)" % (k, v, sub)))
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
                      "TDAS", "EMAS"):
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


def validator_for(cat, kind=None):
    """A validator for this catalogue, or for one sub-kind of it.

    schema_file "*" means the sub-kind names its own schema: an analog_ics row
    wrapping {"analog": {"comparator": ...}} validates against AAS comparator.json,
    not against AAS.json, because AAS.json describes the WRAPPER and the gate holds
    the unwrapped body. A missing sub-schema is a refusal, never a skip -- TDAS
    ships oscillator.json but no timer.json, so timer rows cannot be checked and
    therefore cannot be admitted."""
    global _REGISTRY
    from jsonschema import Draft202012Validator
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    schema_file = cat.schema_file
    if schema_file == "*":
        if not kind:
            return None
        schema_file = kind + ".json"
    schema_path = PROTEUS / cat.repo / "schemas" / schema_file
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
                 outlier_overrides=(), median_outliers=False, known=None):
        """`known`: an optional KnownSpellings of the live catalogue and the
        quarantine. When given, an exact spelling match from the same
        manufacturer is refused as a duplicate (rule 1) and a same-manufacturer
        spelling twin is held (rule HELD_TWIN). The same part number from another
        manufacturer is a second source and passes."""
        self.known = known
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
        self._per_kind = self.cat.schema_file == "*"
        self._validator = (None if (not validate or self._per_kind)
                           else validator_for(self.cat))
        self._kind_validators = {}
        self._validate = validate
        self.rows = []          # (identity, manufacturer, series, description, body)
        self.cites = []         # (has_part_specific, {url accepted only by doc-id})
        self.values = []        # {field key: scalar}
        self.accepted = 0

    # -- per record ---------------------------------------------------------
    def offer(self, rec):
        """Refusals for this row alone. The row is remembered for the batch pass
        only when it passes: a refused row never enters, so it must not colour
        the cohort statistics of the ones that do."""
        body, kind = unwrap(rec, self.cat.disc, want_kind=True)
        validator = self._validator
        if self._validate and self._per_kind and body is not None:
            if kind not in self._kind_validators:
                try:
                    self._kind_validators[kind] = validator_for(self.cat, kind)
                except IngestRefused as e:
                    self._kind_validators[kind] = e
            validator = self._kind_validators[kind]
            if isinstance(validator, IngestRefused):
                return [Refusal(6, identity_of(body) or "<no identity>", str(validator))]
        if body is None:
            return [Refusal(0, "<unwrappable>",
                            "row is not wrapped in its discriminator %s -- every "
                            "catalogue record is {\"%s\": ...}"
                            % ("/".join(self.cat.disc), self.cat.disc[0]))]
        refusals = []
        refusals += check_identity(body)
        if self.known is not None and identity_of(body):
            mfr = (body.get("manufacturerInfo") or {}).get("name")
            if manufacturer_key(mfr) is None:
                refusals.append(Refusal(1, identity_of(body),
                                        "no manufacturerInfo.name -- identity is (part "
                                        "number, manufacturer), so a row naming no "
                                        "manufacturer cannot be checked for duplicates"))
            else:
                v = self.known.verdict(identity_of(body), mfr)
                if v is not None and v[0] == "duplicate":
                    refusals.append(Refusal(1, identity_of(body), "%s (%s)"
                                            % (ALREADY_KNOWN, known_label(v[1:]))))
                elif v is not None:
                    refusals.append(Refusal(HELD_TWIN, identity_of(body),
                                            twin_reason(v[1])))
        refusals += check_citation(body)
        refusals += check_units(body, self.cat.fields)
        if self.cat.disc == ["semiconductor", "mosfet"]:
            refusals += check_channel_sign(body)
        refusals += check_seed_identity(body)
        if validator is not None:
            refusals += self._check_schema(body, validator)
        if refusals:
            return refusals
        self.rows.append((identity_of(body), manufacturer_of(body), series_of(body),
                          description_of(body), body))
        self.values.append({f.key: first(body, f.segs) for f in self.cat.fields})
        kinds = [(u, classify_citation(u, identity_of(body), e))
                 for u, e in _citations(body)]
        self.cites.append((any(k in NAMES_PART_CITATIONS for _, k in kinds),
                           {u for u, k in kinds if k == "DOCUMENT_BY_ID"}))
        self.accepted += 1
        return []

    def _check_schema(self, body, validator=None):
        validator = validator or self._validator
        ident = identity_of(body) or "<no identity>"
        errs = sorted(validator.iter_errors(body), key=lambda e: e.path)
        if errs:
            e = errs[0]
            return [Refusal(6, ident, "does not validate against %s/%s: %s @ %s"
                            % (self.cat.repo, self.cat.schema_file, e.message,
                               list(e.absolute_path)))]
        pruned = prune_empty(body)
        errs = sorted(validator.iter_errors(pruned), key=lambda e: e.path)
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
                + self._tool_answers_per_part()
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


    # rule 2, cross-row: the other half of the TOOL_QUERIED exemption
    def _tool_answers_per_part(self):
        """TOOL_QUERIED lets a row cite a bare tool endpoint, on the strength of
        a claim the URL itself cannot carry: that the tool was asked about THIS
        part. That claim is checkable against the cohort. A tool queried per part
        returns per-part values; one response pasted across a cohort does not.

        So: if every row citing one tool endpoint holds identical values at the
        very fields it says it read there, the exemption is refused for all of
        them. Without this pass, TOOL_QUERIED would be an assertion dressed as a
        check -- the exact failure that let a landing page stand in for 134,949
        rows in the first place.
        """
        cohorts = {}
        for i, (ident, _mfr, _series, _desc, body) in enumerate(self.rows):
            for url, entry in _citations(body):
                if not _is_tool_citation(url, entry):
                    continue
                cohorts.setdefault(url, []).append(
                    (ident, _values_at(body, entry.get("fields") or [])))
        out = []
        for url, members in cohorts.items():
            if len(members) < self.min_cohort:
                continue
            distinct = set()
            for _ident, vals in members:
                try:
                    distinct.add(json.dumps(vals, sort_keys=True, default=str))
                except (TypeError, ValueError):
                    distinct.add(repr(vals))
                if len(distinct) > 1:
                    break
            if len(distinct) > 1:
                continue
            for ident, _vals in members:
                out.append(Refusal(
                    2, ident,
                    "%d rows cite tool endpoint %s and every one holds the SAME "
                    "values at the fields it claims to have read there. A tool "
                    "queried per part answers per part -- identical values mean "
                    "one response was copied across the cohort, so the endpoint "
                    "is standing in as a landing page and cannot be cited as a "
                    "per-part source" % (len(members), url)))
        return out

    # rule 1, cross-row
    def _dup_identities(self):
        """Within the batch, identity is (part number, manufacturer) as for the
        live catalogue: an exact (case-insensitive) repeat from one manufacturer
        is a duplicate, a punctuation-only variant from one manufacturer is a held
        spelling twin, and the same number from two manufacturers is two parts."""
        seen = KnownSpellings()
        out = []
        for ident, _mfr, _series, _desc, body in self.rows:
            name = (body.get("manufacturerInfo") or {}).get("name")
            if manufacturer_key(name) is None:
                out.append(Refusal(1, ident,
                                   "no manufacturerInfo.name -- identity is (part "
                                   "number, manufacturer), so a row naming no "
                                   "manufacturer cannot be checked for duplicates"))
                continue
            v = seen.verdict(ident, name)
            if v is not None and v[0] == "duplicate":
                out.append(Refusal(1, ident,
                                   "duplicate identity within manufacturer %r -- two "
                                   "rows cannot be the same part (%s)"
                                   % (name, known_label(v[1:]))))
            elif v is not None:
                out.append(Refusal(HELD_TWIN, ident, twin_reason(v[1])))
            seen.add(ident, name, "earlier batch row")
        return out

    # rule 2, cross-row: the same document standing in for many different parts
    IDENTITY_FIELDS = ("partnumber", "reference")

    def _names_part_via_fields(self, row_index, url):
        """True when the provenance entry citing `url` claims it supplied the identity."""
        body = self.rows[row_index][4]
        for entry in provenance_of(body):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("sourceUrl") or "") != url:
                continue
            fields = entry.get("fields")
            if not isinstance(fields, list):
                continue
            for f in fields:
                leaf = str(f).rsplit(".", 1)[-1].lower()
                if leaf in self.IDENTITY_FIELDS:
                    return True
        return False

    def _entry_says_series(self, row_index, url):
        """True when the provenance entry citing `url` is stamped seriesConfirmed.

        That stamp is the schema's word for "this document covers the part's
        SERIES but does not print its individual order code" -- which is the
        honest description of a family datasheet, and precisely what this rule
        asks for. Honouring it is the difference between a gate and a wall.
        """
        body = self.rows[row_index][4]
        for entry in provenance_of(body):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("sourceUrl") or "") != url:
                continue
            if entry.get("verification") == SERIES_VERIFICATION:
                return True
        return False

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
                # A FAMILY DATASHEET THAT PRINTS A PARTS GRID DOES NAME THE PART.
                # This rule reads URL shape plus reuse count, so it cannot tell a
                # Vishay inductor datasheet -- whose STANDARD ELECTRICAL
                # SPECIFICATIONS table has a PART NUMBER column -- from a landing
                # page naming nobody. Both are one document cited by many rows.
                #
                # The record can say which, and falsifiably: if the provenance
                # entry citing THIS url lists partNumber or reference among the
                # `fields` it took from that document, it is asserting the
                # document supplied this part's IDENTITY. A landing page cannot
                # supply an identity; a parts grid can. That claim is checkable
                # against the document, which is exactly what a stamp should be.
                #
                # Without this, the only escape offered was
                # verification=seriesConfirmed -- and the schema defines that as
                # "covers this part's SERIES but does not print this individual
                # order code", which for 1,183 rows read straight out of a
                # printed PART NUMBER column is simply false. A gate whose only
                # green path is a false statement teaches importers to lie.
                if self._names_part_via_fields(i, url):
                    continue
                # THE ESCAPE HATCH THIS RULE ADVERTISES MUST ACTUALLY WORK.
                # The refusal text says "or stamp verification=seriesConfirmed",
                # but until 2026-09-11 nothing here read the stamp -- the rule saw
                # only URL shape and a reuse count. A row that did exactly what it
                # was told was refused anyway, which is worse than no escape hatch:
                # it teaches that complying is pointless. Found on 99 Vishay
                # magnetics that had been restamped on the rule's own instruction.
                if self._entry_says_series(i, url):
                    continue
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
            idx_of = {}
            for i, (ident, mfr, _s, desc, _b) in enumerate(self.rows):
                v = self.values[i].get(f.key)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
                    cohorts.setdefault((mfr, float(v)), []).append((ident, desc))
                    idx_of.setdefault((mfr, float(v)), []).append(i)
            for (mfr, value), members in cohorts.items():
                if len(members) < self.min_cohort:
                    continue
                members_idx = idx_of[(mfr, value)]
                contradicted, mismatched, agreeing = [], [], 0
                for i2, (ident, desc) in enumerate(members):
                    stated = parse_quantity(desc, f.unit)
                    if stated:
                        if any(abs(s - value) <= TOLERANCE * max(s, value)
                               for s in stated):
                            agreeing += 1
                            continue
                        # A QUANTITY THE RECORD ALREADY EXPLAINS IS NOT A
                        # CONTRADICTION. An RF connector's description carries
                        # its characteristic impedance -- "…RTK 031 50 Ohm A
                        # Code" -- and this rule read that 50 as the part's own
                        # statement of contactResistance, refusing 16 Amphenol
                        # rows whose 0.024 ohm is correct and whose 50 ohm is
                        # stored, correctly, in familyDetails.characteristicImpedance.
                        #
                        # So: an ohm figure in the description that EQUALS some
                        # other stored field of that same record is accounted
                        # for, and says nothing about this field. The test is
                        # against the row's own values, not a vendor allowlist,
                        # so it cannot excuse a number the record does not hold.
                        if self._explained_elsewhere(members_idx[i2], stated,
                                                     f.key):
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


    def _explained_elsewhere(self, row_index, stated, field_key):
        """True when a quantity in the description is some OTHER field's value.

        A description that quotes a number the record already stores under a
        different name is describing that other field, not contradicting this
        one. Checked against the row's own stored values, so it can never
        excuse a figure the record does not actually hold.
        """
        body = self.rows[row_index][4]
        mine = self.values[row_index].get(field_key)

        def leaves(node, depth=0):
            if depth > 8:
                return
            if isinstance(node, dict):
                for v in node.values():
                    yield from leaves(v, depth + 1)
            elif isinstance(node, list):
                for v in node[:64]:
                    yield from leaves(v, depth + 1)
            elif isinstance(node, (int, float)) and not isinstance(node, bool):
                yield node

        # The record's OWN numbers, wherever they sit -- characteristicImpedance
        # lives under familyDetails, not among the tracked fields, which is why
        # checking only those missed it.
        for v in leaves(body):
            if mine is not None and v == mine:
                continue          # this field itself explains nothing
            for st in stated:
                if v and abs(st - v) <= TOLERANCE * max(abs(st), abs(v)):
                    return True
        return False

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
    # Every counter-check runs against a TEMPORARY evidence store, so the
    # verdicts depend on nothing but this file: the real staging/doc_evidence
    # is never read here, and the doc-evidence cases register what they need.
    import tempfile
    tmp = tempfile.TemporaryDirectory(prefix="gate_selftest_evidence_")
    prev = set_evidence_store(DocEvidenceStore(tmp.name))
    try:
        return _selftest()
    finally:
        set_evidence_store(prev)
        tmp.cleanup()


def _selftest():
    mag = lambda: IngestGate("magnetics.ndjson", validate=False)      # noqa: E731
    mos = lambda: IngestGate("mosfets.ndjson", validate=False)        # noqa: E731
    cap = lambda: IngestGate("capacitors.ndjson", validate=False)     # noqa: E731
    con = lambda: IngestGate("connectors.ndjson", validate=False)     # noqa: E731
    igb = lambda: IngestGate("igbts.ndjson", validate=False)          # noqa: E731
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

    # -- rule 2: a family datasheet that PRINTS the part number -------------
    # Added 2026-09-11. The reuse rule reads URL shape and a reuse count, so it
    # cannot distinguish a Vishay inductor datasheet whose STANDARD ELECTRICAL
    # SPECIFICATIONS table has a PART NUMBER column from a landing page naming
    # nobody. Both are one document cited by many rows. The record settles it by
    # listing partNumber among the `fields` it took from that document -- a
    # landing page cannot supply an identity, a parts grid can.
    #
    # BOTH cases are asserted, and the second is the one that matters: without
    # it, a blanket exemption would pass this pair identically to a working rule.
    def _shared_pdf(pn, fields, verification="valuesReadFromSource"):
        return {"magnetic": {"manufacturerInfo": {
            "name": "Vishay", "reference": pn,
            "datasheetInfo": {
                "part": {"partNumber": pn, "series": "IHLP-1008"},
                "electrical": [{"inductance": {"nominal": 1e-6}}],
                "provenance": [{
                    "source": "manufacturerDatasheet",
                    "sourceName": "Vishay IHLP-1008 datasheet",
                    "sourceUrl": "https://www.vishay.com/docs/34609/ihlp1008.pdf",
                    "retrievedDate": "2026-09-11",
                    "verification": verification,
                    "verificationDate": "2026-09-11",
                    "fields": fields}]}}}}

    names_part = [_shared_pdf("IHLP1008ABER%dR0M11" % i,
                              ["part.partNumber", "electrical.inductance"])
                  for i in range(MIN_COHORT + 2)]
    results.append(_run("2f  family PDF whose grid prints the part number",
                        "ACCEPTED", mag, names_part))

    silent = [_shared_pdf("IHLP1008ABEZ%dR0M11" % i, ["electrical.inductance"])
              for i in range(MIN_COHORT + 2)]
    results.append(_run("2g  same PDF, provenance does NOT claim the identity",
                        "REFUSED", mag, silent))

    # The escape hatch this rule advertises must actually work. Until 2026-09-11
    # the refusal said "or stamp verification=seriesConfirmed" and nothing read
    # the stamp, so a row that complied was refused anyway.
    stamped = [_shared_pdf("IHSM3825ER%dR0L" % i, ["electrical.inductance"],
                           verification="seriesConfirmed")
               for i in range(MIN_COHORT + 2)]
    results.append(_run("2h  family PDF, entry stamped seriesConfirmed as instructed",
                        "ACCEPTED", mag, stamped))

    # -- rule 2: a parametric TOOL, which has no per-part URL ----------------
    # Added 2026-09-20. K-SIM/TDK/Yuden answer a POST keyed on the part number
    # and return that part's own curve; there is no document to cite and no
    # per-part URL to point at. The endpoint reads as LANDING to a shape test,
    # so 17,718 honestly-sourced rows were refused with no truthful way out:
    # inventing a part-specific URL is fabrication and downgrading to
    # inferredNotVerified is a false weakening of a claim that is simply true.
    #
    # BOTH fixtures matter, and the second is the one that keeps this honest.
    # A blanket endpoint exemption would pass this pair identically to a working
    # rule, which is precisely how the landing-page defect survived for so long.
    def _ksim(pn, esr):
        return {"capacitor": {"manufacturerInfo": {
            "name": "KEMET", "reference": pn,
            "datasheetInfo": {
                "part": {"partNumber": pn, "series": "C0603"},
                "electrical": {"capacitance": {"nominal": 1e-07},
                               "esrPoints": {"xData": [100.0, 1000.0],
                                             "yData": esr}},
                "provenance": [{
                    "source": "manufacturerParametric",
                    "sourceName": "KEMET K-SIM 3 simulation, Impedance & ESR chart",
                    "sourceUrl": "https://ksim3.kemet.com/api/csv/",
                    "retrievedDate": "2026-09-20",
                    "verification": "valuesReadFromSource",
                    "verificationDate": "2026-09-20",
                    "fields": ["electrical.esrPoints"]}]}}}}

    per_part = [_ksim("C0603C%03dK5RACTU" % (100 + i), [12.0 + i, 1.2 + i * 0.1])
                for i in range(MIN_COHORT + 2)]
    results.append(_run("2i  tool endpoint, every row a different measured curve",
                        "ACCEPTED", cap, per_part))

    pasted = [_ksim("C0603C%03dK5RACTU" % (200 + i), [12.0, 1.2])
              for i in range(MIN_COHORT + 2)]
    results.append(_run("2j  same endpoint, ONE curve copied onto the whole cohort",
                        "REFUSED", cap, pasted))

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

    # -- rule 2: a part number in a search box is not a citation --------------
    # Added 2026-09-23. classify_citation tested the identity against the WHOLE
    # url, so an aggregator search whose query string was the part number came
    # back PART_SPECIFIC. 223 IGBTs cited datasheetpdf.com/search?q=<their own
    # part number> and were admitted on it; none of them cites a document.
    #
    # The second fixture is the one that keeps this honest: a per-SKU page
    # addressed BY QUERY is a real citation and must stay admitted, so the fix
    # strips the search PARAMETER rather than the query string.
    def _cite(pn, url):
        return {"semiconductor": {"igbt": {"manufacturerInfo": {
            "name": "Infineon", "reference": pn, "datasheetUrl": url,
            "datasheetInfo": {
                "part": {"partNumber": pn, "series": "IKW"},
                "electrical": {"collectorEmitterVoltage": 650.0},
                "provenance": [{"source": "scrape",
                                "sourceName": "third-party datasheet aggregator",
                                "sourceUrl": url,
                                "verification": "valuesReadFromSource",
                                "verificationDate": "2026-09-23",
                                "fields": ["electrical.collectorEmitterVoltage"]}]}}}}}

    searchbox = [_cite("IKW%02dN65H5" % i,
                       "https://datasheetpdf.com/search?q=IKW%02dN65H5" % i)
                 for i in range(MIN_COHORT + 2)]
    results.append(_run("2k  a search box whose query IS the part number",
                        "REFUSED", igb, searchbox))

    persku = [_cite("MPZ2012S%03dA" % (100 + i),
                    "https://product.tdk.com/en/search/x/info?part_no=MPZ2012S%03dA"
                    % (100 + i))
              for i in range(MIN_COHORT + 2)]
    results.append(_run("2l  a per-SKU page addressed by ?part_no= is a real citation",
                        "ACCEPTED", igb, persku))

    # -- rule 2: the cited document's STORED TEXT prints the part --------------
    # Added 2026-10-17 (approved by Alf). A distributor mirror, an ST DM00 link,
    # a Littelfuse asset id or a family PDF prints the part number inside the
    # document while its URL never does. Rule 2 read only the URL and refused
    # verified corrections for it (73 IGBT ops, 118 tracing ops, 64 Bourns TVS
    # rows stamped seriesConfirmed to get past it). Now a cached copy of that
    # exact document, in the doc-evidence store, can testify -- and every one of
    # the refusal fixtures below is what keeps that from becoming a blanket pass.
    store = evidence_store()
    mirror = ("https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/404/"
              "6th_Gen_NX-Series_Choppers_PB_11-14-12.pdf")
    store.register(mirror, "6th Gen NX-Series Choppers\n"
                   "  CM150EXS-24S   150 A  1200 V\n  CM200EXS-24S   200 A  1200 V\n"
                   "  Tjmax = 175 \u00b0C for higher operating temperature\n",
                   "2026-10-17", "selftest")
    other = "https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/405/NX_B.pdf"
    store.register(other, "NX-Series dual\n  CM300DY-24S  300 A  1200 V\n",
                   "2026-10-17", "selftest")

    def _mirror(pn, url, sub="igbt", verification="valuesReadFromSource"):
        return {"semiconductor": {sub: {"manufacturerInfo": {
            "name": "Powerex Inc.", "reference": pn,
            "datasheetInfo": {
                "part": {"partNumber": pn},
                "thermal": {"junctionTemperatureMax": 175},
                "provenance": [{"source": "manufacturerDatasheet",
                                "sourceName": "datasheet (distributor copy)",
                                "sourceUrl": url, "retrievedDate": "2026-10-17",
                                "verification": verification,
                                "verificationDate": "2026-10-17",
                                "fields": ["thermal.junctionTemperatureMax"]}]}}}}}

    results.append(_run("2m  URL lacks the part, STORED document text prints it",
                        "ACCEPTED", igb, [_mirror("CM150EXS-24S", mirror)]))
    results.append(_run("2n  stored document text does NOT print the part",
                        "REFUSED", igb, [_mirror("CM600EXS-24S", mirror)]))
    results.append(_run("2o  same URL shape, document NOT in the store",
                        "REFUSED", igb, [_mirror(
                            "CM150EXS-24S",
                            "https://mm.digikey.com/Volume0/opasdata/d220001/"
                            "medias/docus/999/not_on_file.pdf")]))
    results.append(_run("2p  stored text names a DIFFERENT document's part",
                        "REFUSED", igb, [_mirror("CM150EXS-24S", other)]))

    # Prefix collisions: the normaliser strips punctuation, so a bare substring
    # test would let IRF540N "name" IRF540 and VS-GT400TH60N "name" GT400TH60N.
    coll = "https://example.org/assets/a7f3e9/family_sheet.pdf"
    store.register(coll, "Ordering: IRF540N, IRF540NS (D2PAK). Also VS-GT400TH60N.\n",
                   "2026-10-17", "selftest")
    results.append(_run("2q  IRF540 vs a document that prints only IRF540N",
                        "REFUSED", mos, [_mirror("IRF540", coll, sub="mosfet")]))
    results.append(_run("2r  GT400TH60N vs a document that prints only VS-GT400TH60N",
                        "REFUSED", igb, [_mirror("GT400TH60N", coll)]))
    results.append(_run("2s  IRF540N itself, printed in the same document",
                        "ACCEPTED", mos, [_mirror("IRF540N", coll, sub="mosfet")]))

    # A results page echoes its query, so a stored search page is never evidence.
    srch = "https://www.alldatasheet.com/view.jsp?Searchword=x&q=CM150EXS-24S"
    store.register(srch, "Results for CM150EXS-24S\n", "2026-10-17", "selftest")
    results.append(_run("2t  stored SEARCH page whose text echoes the part",
                        "REFUSED", igb, [_mirror("CM150EXS-24S", srch)]))

    # The cross-row half: the same family PDF on many rows (2g's refusal) is no
    # longer "a document standing in for parts it does not name" once its
    # stored text prints every one of them.
    vis = "https://www.vishay.com/docs/34609/ihlp1008.pdf"
    grid = ["IHLP1008ABEZ%dR0M11" % i for i in range(MIN_COHORT + 2)]
    store.register(vis, "STANDARD ELECTRICAL SPECIFICATIONS\nPART NUMBER\n"
                   + "\n".join(grid) + "\n", "2026-10-17", "selftest")
    results.append(_run("2u  family PDF on %d rows, stored text prints each part"
                        % len(grid), "ACCEPTED", mag,
                        [_shared_pdf(pn, ["electrical.inductance"]) for pn in grid]))

    # A tampered entry must not read as "not on file": it raises.
    key = evidence_key(other)
    (store.root / key[:2] / (key + ".txt.gz")).write_bytes(
        gzip.compress(b"NX-Series dual\n  CM600EXS-24S\n"))
    store._texts.pop(key, None)
    try:
        store.text_for(other)
        ok = False
    except DocEvidenceError:
        ok = True
    print("%-4s %-9s (expected %-9s) %s" % ("PASS" if ok else "FAIL",
          "RAISED" if ok else "READ", "RAISED", "2v  tampered store entry"))
    results.append(ok)

    # -- rule 3: a quantity the record already explains -----------------------
    # Added 2026-09-21. An RF connector's description carries its characteristic
    # impedance -- "...RTK 031 50 Ohm A Code" -- and rule 3 read that 50 as the
    # part's own statement of contactResistance, refusing 16 Amphenol rows whose
    # 0.024 ohm is correct and whose 50 ohm is stored, correctly, in
    # familyDetails.characteristicImpedance.
    #
    # BOTH fixtures matter and the second is the one that keeps the rule alive:
    # a description quoting a number the record does NOT hold anywhere is still
    # a contradiction, and must still refuse.
    def _rf(pn, impedance):
        d = {"connector": {"manufacturerInfo": {
            "name": "Amphenol RF", "reference": pn,
            "datasheetInfo": {
                "part": {"partNumber": pn,
                         "description": "Mini-FAKRA Sealed Crimp Jack RTK 031 "
                                        "50 Ohm A Code"},
                "electrical": {"contactResistance": {"maximum": 0.024}},
                "provenance": [{"source": "manufacturerDatasheet",
                                "sourceName": "Amphenol RF series spec",
                                "sourceUrl": "https://www.amphenolrf.com/docs/automate.pdf",
                                "verification": "seriesConfirmed",
                                "verificationDate": "2026-09-21",
                                "fields": ["electrical.contactResistance"]}]}}}}
        if impedance is not None:
            d["connector"]["manufacturerInfo"]["datasheetInfo"]["familyDetails"] = {
                "characteristicImpedance": impedance}
        return d

    explained = [_rf("FM1CN%dSJ-C30E0" % i, 50.0) for i in range(MIN_COHORT + 2)]
    results.append(_run("3d  50 ohm in the text IS the stored characteristic impedance",
                        "ACCEPTED", con, explained))

    unexplained = [_rf("FM9CN%dSJ-C30E0" % i, None) for i in range(MIN_COHORT + 2)]
    results.append(_run("3e  same text, but the record holds no 50 anywhere",
                        "REFUSED", con, unexplained))

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

    # 5b-5d: MOSFET ratings carry the datasheet's sign, tied to the channel.
    def _ch(pn, sub, vds, i_d):
        r = _mos(pn, drainSourceVoltage=vds, continuousDrainCurrent=i_d)
        r["semiconductor"]["mosfet"]["manufacturerInfo"]["datasheetInfo"]["part"]["subType"] = sub
        return r
    nocheck = lambda: IngestGate("mosfets.ndjson", validate=False)
    results.append(_run("5b  P-channel -30 V / -5 A, signed as printed", "ACCEPTED",
                        nocheck, [_ch("TESTP1", "pChannel", -30.0, -5.0)]))
    results.append(_run("5c  P-channel stored +30 V", "REFUSED",
                        nocheck, [_ch("TESTP2", "pChannel", 30.0, -5.0)]))
    results.append(_run("5d  N-channel stored -60 V", "REFUSED",
                        nocheck, [_ch("TESTN1", "nChannel", -60.0, 4.0)]))

    # 5e-5f: TVS clamping test current. A 20 kA 8/20 us PTVS is real; the same
    # rating written in mA (2e7) is not a part.
    def _tvs(pn, i_c, i_pp):
        return {"semiconductor": {"diode": {"manufacturerInfo": {
            "name": "Test", "reference": pn,
            "datasheetUrl": "https://example.com/datasheet/%s.pdf" % pn,
            "datasheetInfo": {"part": {"partNumber": pn, "subType": "tvs"},
                              "electrical": {"standoffVoltage": 22,
                                             "clampingVoltage": 40,
                                             "clampingCurrent": i_c,
                                             "clampingWaveform": "8/20",
                                             "peakPulseCurrent": i_pp,
                                             "peakPulseCurrentWaveform": "8/20"}}}}}}
    tvscheck = lambda: IngestGate("diodes.ndjson", validate=False)
    results.append(_run("5e  TVS clampingCurrent 20 kA (PTVS20-class, real)", "ACCEPTED",
                        tvscheck, [_tvs("TESTTVS1", 2.0e4, 2.0e4)]))
    results.append(_run("5f  TVS clampingCurrent 2e7 A (20 kA written in mA)", "REFUSED",
                        tvscheck, [_tvs("TESTTVS2", 2.0e7, 2.0e4)]))

    # rf connectors: familyRf impedance, band and VSWR are bounded (2026-10-17)
    def _rfband(pn, **fd):
        fam = {"family": "rf", "characteristicImpedance": 50.0,
               "frequencyRange": {"maximum": 6.0e9}, "maxVswr": 1.3}
        fam.update(fd)
        return {"connector": {"manufacturerInfo": {
            "name": "Test", "reference": pn,
            "datasheetUrl": "https://example.com/datasheet/%s.pdf" % pn,
            "datasheetInfo": {"part": {"partNumber": pn},
                              "mechanical": {"positions": 1},
                              "familyDetails": fam}}}}
    results.append(_run("5g  rf 50 ohm / 6 GHz / VSWR 1.3, no electrical", "ACCEPTED",
                        con, [_rfband("TESTRF1")]))
    results.append(_run("5h  rf impedance 5e4 (50 ohm written in mOhm)", "REFUSED",
                        con, [_rfband("TESTRF2", characteristicImpedance=5.0e4)]))
    results.append(_run("5i  rf frequencyRange.maximum 6e12 (6 GHz in kHz-as-Hz)", "REFUSED",
                        con, [_rfband("TESTRF3", frequencyRange={"maximum": 6.0e12})]))
    results.append(_run("5j  rf maxVswr 20 (a return loss in dB)", "REFUSED",
                        con, [_rfband("TESTRF4", maxVswr=20.0)]))

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

    # -- rule 1, against the live catalogue: hyphen-aware duplicates ----------
    # Eaton 10BJ60A (60 V) is live; 10BJ6-0A is the 6.0 V part, a different
    # part under the same hyphen-blind key. The known index is read from temp
    # NDJSON files so add_file's regex scan is exercised too.
    import tempfile
    def _by(pn, mfr):
        r = _mos(pn)
        r["semiconductor"]["mosfet"]["manufacturerInfo"]["name"] = mfr
        return r

    with tempfile.TemporaryDirectory(prefix="gate_selftest_known_") as kd:
        live_f, quar_f = Path(kd, "diodes.ndjson"), Path(kd, "quarantine.ndjson")
        live_f.write_text("".join(json.dumps(r) + "\n" for r in (
            _mos("10BJ60A"), _mos("SMBJ5.0A"), _by("SMAJ12AQ", "Diodes Inc."),
            _by("MURS160", "Vishay / Dale"))))
        quar_f.write_text(json.dumps(_mos("10BJ7-5A")) + "\n")
        known = KnownSpellings()
        known.add_file(live_f, "live")
        known.add_file(quar_f, "quarantined")
    kgate = lambda: IngestGate("mosfets.ndjson", validate=False, known=known)  # noqa: E731

    def _known_case(name, expect, pn, rec=None):
        refusals = kgate().offer(rec or _mos(pn))
        got = ("HELD" if any(r.rule == HELD_TWIN for r in refusals)
               else "REFUSED" if refusals else "ACCEPTED")
        ok = got == expect
        print("%-4s %-9s (expected %-9s) %s" % ("PASS" if ok else "FAIL", got, expect, name))
        for r in refusals[:2]:
            print("        %s" % r)
        return ok

    results.append(_known_case("1e  exact live duplicate 10BJ60A", "REFUSED", "10BJ60A"))
    results.append(_known_case("1f  exact duplicate, other case: 10bj60a", "REFUSED", "10bj60a"))
    results.append(_known_case("1g  exact quarantined duplicate 10BJ7-5A", "REFUSED", "10BJ7-5A"))
    results.append(_known_case("1h  hyphen twin 10BJ6-0A of live 10BJ60A", "HELD", "10BJ6-0A"))
    results.append(_known_case("1i  hyphen-less twin 10BJ75A of quarantined 10BJ7-5A",
                               "HELD", "10BJ75A"))
    results.append(_known_case("1j  slash/space twin 'SMBJ5.0 A' of SMBJ5.0A", "HELD",
                               "SMBJ5.0 A"))
    results.append(_known_case("1k  unrelated part 10BJ33A", "ACCEPTED", "10BJ33A"))
    results.append(_known_case("1l  a dot is identity: SMBJ50A is not a twin of SMBJ5.0A",
                               "ACCEPTED", "SMBJ50A"))
    # identity is (part number, manufacturer): Alf 2026-10-18
    results.append(_known_case("1m  same pn, other manufacturer: Bourns SMAJ12AQ is a second "
                               "source of live Diodes SMAJ12AQ", "ACCEPTED", "SMAJ12AQ",
                               _by("SMAJ12AQ", "Bourns")))
    results.append(_known_case("1n  same pn, same manufacturer spelled 'Diodes Incorporated'",
                               "REFUSED", "SMAJ12AQ", _by("SMAJ12AQ", "diodes incorporated ")))
    results.append(_known_case("1o  twin, same manufacturer: Diodes SMAJ12A-Q", "HELD",
                               "SMAJ12A-Q", _by("SMAJ12A-Q", "Diodes Inc")))
    results.append(_known_case("1p  twin, other manufacturer: Bourns SMAJ12A-Q", "ACCEPTED",
                               "SMAJ12A-Q", _by("SMAJ12A-Q", "Bourns")))
    results.append(_known_case("1q  alias: Vishay MURS160 duplicates live 'Vishay / Dale'",
                               "REFUSED", "MURS160", _by("MURS160", "Vishay")))
    results.append(_known_case("1r  same pn, other manufacturer: onsemi MURS160", "ACCEPTED",
                               "MURS160", _by("MURS160", "onsemi")))
    nameless = _mos("10BJ33A")
    del nameless["semiconductor"]["mosfet"]["manufacturerInfo"]["name"]
    results.append(_known_case("1s  no manufacturer name: cannot be dedup-checked", "REFUSED",
                               "10BJ33A", nameless))
    # an entry naming no manufacturer (the denylist) is compared against every one
    wild = KnownSpellings()
    wild.add("IRF530N", None, "denylisted")
    ok = (wild.verdict("IRF530N", "Infineon")[0] == "duplicate"
          and wild.verdict("IRF-530N", "Vishay")[0] == "twin"
          and wild.verdict("IRF530", "Vishay") is None)
    print("%-4s %s" % ("PASS" if ok else "FAIL",
                       "1t  a known entry naming no manufacturer matches every manufacturer"))
    results.append(ok)
    # within one batch
    plain = lambda: IngestGate("mosfets.ndjson", validate=False)  # noqa: E731
    results.append(_run("1u  batch: same pn from two manufacturers", "ACCEPTED", plain,
                        [_by("MURS160", "onsemi"), _by("MURS160", "Vishay")]))
    results.append(_run("1v  batch: same pn, same manufacturer (alias + case)", "REFUSED", plain,
                        [_by("MURS160", "Vishay"), _by("murs160", "Vishay Semiconductors")]))
    results.append(_run("1w  batch: twin from one manufacturer", "REFUSED", plain,
                        [_by("SMAJ12AQ", "Diodes Inc."), _by("SMAJ12A-Q", "Diodes Inc.")]))
    results.append(_run("1x  batch: twin from two manufacturers", "ACCEPTED", plain,
                        [_by("SMAJ12AQ", "Diodes Inc."), _by("SMAJ12A-Q", "Bourns")]))

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
