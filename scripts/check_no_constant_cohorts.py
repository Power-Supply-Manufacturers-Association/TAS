#!/usr/bin/env python3
"""GUARD: fail if a cohort of parts shares a byte-identical value in a field the
vendor publishes PER PART, while the parts' own text says otherwise.

    python3 scripts/check_no_constant_cohorts.py [--data DIR] [--file F ...]
                                                 [--min-cohort N] [--list]

Exit 0 = clean, exit 1 = at least one minted constant found.

WHY THIS EXISTS (ABT #1090, and #286 / #431 before it)

An importer with no value for a field can do one of two things: leave the field
absent, or write a constant. Three separate importers picked the constant, and
each time the number was legal, plausible and invisible:

  ABT #1090  29 Wuerth EMI-suppression ferrites (WE-WAFB / WE-MLS / WE-PF) all
             carried inductance = 1e-05 H while their own descriptions read
             "Sleeve Choke, 130Ohm, 3A" -- impedance-defined parts that quote
             ohms and publish no inductance at all.
  ABT #286   18 of 20 Laird common-mode chokes carried the identical 10.000 uH
             across different sizes and current ratings.
  ABT #431   14 Bourns / J.W. Miller SRF chokes stored inductance exactly 1e-5 H;
             seven are contradicted by their own datasheet (0.55 / 1.10 / 2.20 /
             2.50 / 2.60 / 6.30 / 8.20 uH) and the rest belong to series that
             publish no inductance column.

None of it was reachable by the existing gates. The rows validate against MAS --
inductance is an optional, legal, correctly-typed field -- and Blade Runner sees
a physically ordinary 10 uH. Schema validity and physical plausibility are both
blind to a number that is merely NOT THE PART'S.

WHAT IT KEYS ON, AND WHY IT IS NARROW

A repeated value is not by itself evidence: whole series legitimately share an
inductance, a capacitance, a resistance. What is NOT legitimate is a repeated
value that the parts' own descriptions refute. So a finding needs BOTH:

  1. COHORT      the same manufacturer carries the byte-identical value on at
                 least --min-cohort parts in one field, and
  2. REFUTATION  the part's own description text (which the importer read, and
                 which the vendor wrote per part) contradicts it, either as

     CONTRADICTED       the description states a value of the SAME quantity that
                        differs from the stored one by more than 10 %
                        ("6.3uH" in the text, 1e-05 H in the field), or
     QUANTITY_MISMATCH  the description states NO value of that quantity, but
                        does state an impedance of >= 1 ohm IN AN IMPEDANCE
                        CONTEXT (at a frequency, or on a part its own text calls
                        a bead / ferrite / EMI suppressor / choke) -- an
                        impedance-defined part that has been given an inductance
                        it does not have. The context test is what separates it
                        from an ordinary inductor whose description happens to
                        quote a DC resistance in whole ohms.

Rule 2 is what keeps this from crying wolf, and it is also the honest limit of
the check: a minted constant on parts whose descriptions are silent cannot be
detected from the corpus alone and is not reported.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data"

MIN_COHORT = 5
TOLERANCE = 0.10          # a description value within 10 % is the same value
IMPEDANCE_FLOOR = 1.0     # ohms; below this a description figure is a DCR, not a |Z|

# ---------------------------------------------------------------------------
# field specs: which per-part published quantity to police, per catalogue
# ---------------------------------------------------------------------------
# path segments; "*" walks a list, and a missing segment simply yields nothing.
FIELDS = {
    "magnetics.ndjson": [
        ("inductance", "H",
         ["magnetic", "manufacturerInfo", "datasheetInfo", "electrical", "*",
          "inductance", "nominal"]),
    ],
    "capacitors.ndjson": [
        ("capacitance", "F",
         ["capacitor", "manufacturerInfo", "datasheetInfo", "electrical",
          "capacitance", "nominal"]),
    ],
    "resistors.ndjson": [
        ("resistance", "OHM",
         ["resistor", "manufacturerInfo", "datasheetInfo", "electrical",
          "resistance", "nominal"]),
    ],
}

# where each catalogue keeps the vendor's own per-part text
DESC_PATHS = [
    ["datasheetInfo", "part", "description"],
    ["datasheetInfo", "part", "matchcodeDescription"],
    ["datasheetInfo", "part", "matchCode"],
]

# A number must start a token, and must NOT be the mantissa/exponent of a
# scientific-notation figure: "1e+02uH" is 100 uH, and reading "02uH" out of it
# invents a 2 uH part that then "contradicts" a perfectly correct record.
NUM = r"(?<![\w.])(?<!e[+-])(?<!E[+-])(\d+(?:[.,]\d+)?)"

# For henries and farads the prefix letter is unambiguous whatever its case:
# there is no mega-henry in a passive catalogue, so M/m both mean milli.
# ...and the unit letter itself is written in either case by real vendors
# ("68Uh Shld 420mA" is a Coilcraft description in this corpus), so H/F match
# case-insensitively too. "2.37Ohms" cannot match: 'O' is not a prefix letter.
RE_H = re.compile(NUM + r"\s*([pPnNuUµμmM])?[hH](?![a-zA-Z])")
RE_F = re.compile(NUM + r"\s*([pPnNuUµμmM])?[fF](?![a-zA-Z])")
# For ohms it is NOT unambiguous -- vendor descriptions write "75 MOHM" for
# 75 milliohm and "1 MOHM" for 1 megohm, in the same corpus. Group 2 is captured
# so ambiguous prefixes can be DISCARDED rather than guessed (see parse_quantity).
RE_R = re.compile(NUM + r"\s*([mkKM])?\s*(?:Ohm|OHM|ohm|OHMS|ohms|Ω)")

RE_BY_UNIT = {"H": RE_H, "F": RE_F, "OHM": RE_R}
SI_HF = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "": 1.0}
SI_R = {"m": 1e-3, "": 1.0}          # k/K/M deliberately absent: ambiguous

# An ohm figure alone does NOT make a part impedance-defined: a 68 uH Coilcraft
# XFL3012 description reads "68Uh Shld 420mA 2.37Ohms", and that 2.37 ohm is its
# DC resistance. The figure only refutes an inductance when the part's own text
# says it is specified by impedance -- either by quoting a frequency with it, or
# by naming the part as a bead / EMI suppressor / ferrite / choke.
RE_IMPEDANCE_CONTEXT = re.compile(
    r"@\s*[\d.]|[\d.]\s*[kKmMgG]?Hz|bead|ferrite|choke|EMI|suppress",
    re.IGNORECASE)


def parse_quantity(text: str, unit: str):
    """Every UNAMBIGUOUS value of `unit` stated in `text`, in SI base units.

    An ambiguous ohm prefix (K/M, which vendors use for both kilo/milli and
    mega) yields nothing at all: a guard must not refute a record with a number
    it had to guess."""
    out = []
    for m in RE_BY_UNIT[unit].finditer(text or ""):
        try:
            v = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        pfx = (m.group(2) or "")
        if unit == "OHM":
            if pfx and pfx not in SI_R:
                continue
            out.append(v * SI_R.get(pfx, 1.0))
        else:
            out.append(v * SI_HF.get(pfx.lower().replace("μ", "µ"), 1.0))
    return out


def walk(node, segs):
    """Yield every value reachable by `segs` ("*" iterates a list)."""
    if not segs:
        yield node
        return
    head, rest = segs[0], segs[1:]
    if head == "*":
        if isinstance(node, list):
            for item in node:
                yield from walk(item, rest)
        return
    if isinstance(node, dict) and head in node:
        yield from walk(node[head], rest)


def first(node, segs):
    for v in walk(node, segs):
        return v
    return None


def record_body(rec):
    """(manufacturer, description, body) for a discriminator-wrapped record."""
    body = rec
    for _ in range(3):
        if not isinstance(body, dict) or "manufacturerInfo" in body:
            break
        keys = [k for k in body if isinstance(body.get(k), dict)]
        if len(keys) != 1:
            break
        body = body[keys[0]]
    mi = body.get("manufacturerInfo", {}) if isinstance(body, dict) else {}
    desc = ""
    for p in DESC_PATHS:
        v = first(mi, p)
        if isinstance(v, str) and v.strip():
            desc += " " + v
    return (mi.get("name") or "?"), desc, mi


def part_id(mi):
    return (first(mi, ["datasheetInfo", "part", "partNumber"])
            or mi.get("reference") or "?")


def specs_for(path: Path, override: str | None):
    """Field specs for a file, by name. Unknown is an ERROR, never a silent pass:
    a guard that reports "0 findings" on a file it never understood is worse than
    no guard at all."""
    name = override or path.name
    if name in FIELDS:
        return FIELDS[name]
    for known in FIELDS:
        if known.split(".")[0] in name:
            return FIELDS[known]
    return None


def scan_file(path: Path, min_cohort: int, specs):
    """[(lineno, manufacturer, part, field, value, verdict, why)] for one file."""
    rows = 0
    # (manufacturer, field) -> value -> [(lineno, part, description)]
    cohorts = defaultdict(lambda: defaultdict(list))
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            rows += 1
            mfr, desc, mi = record_body(rec)
            for field, unit, segs in specs:
                for val in walk(rec, segs):
                    if isinstance(val, (int, float)) and val:
                        cohorts[(mfr, field, unit)][float(val)].append(
                            (lineno, part_id(mi), desc))

    findings = []
    for (mfr, field, unit), by_value in cohorts.items():
        for value, members in by_value.items():
            if len(members) < min_cohort:
                continue
            contradicted, mismatched, agreeing = [], [], 0
            for lineno, part, desc in members:
                stated = parse_quantity(desc, unit)
                if stated:
                    if any(abs(s - value) <= TOLERANCE * max(s, value) for s in stated):
                        agreeing += 1
                        continue
                    contradicted.append((
                        lineno, mfr, part, field, value, "CONTRADICTED",
                        "its own description states %s, cohort of %d parts all "
                        "carry %g" % (
                            "/".join("%g" % s for s in stated), len(members), value)))
                elif unit == "H" and RE_IMPEDANCE_CONTEXT.search(desc or ""):
                    ohms = [z for z in parse_quantity(desc, "OHM")
                            if z >= IMPEDANCE_FLOOR]
                    if ohms:
                        mismatched.append((
                            lineno, mfr, part, field, value, "QUANTITY_MISMATCH",
                            "impedance-defined part (description quotes %s ohm, no "
                            "henries) carrying inductance %g shared by %d parts" % (
                                "/".join("%g" % z for z in ohms), value, len(members))))
            # QUANTITY_MISMATCH stands on its own: an impedance-defined part has
            # no inductance to publish, so a shared one was minted whatever the
            # rest of the cohort looks like. Wuerth genuinely sells 10 uH power
            # inductors, and ABT #1090's 29 fabricated rows sat in the same
            # 1e-05 cohort as those real parts -- a majority test here would
            # have missed the very defect this guard exists for.
            findings.extend(mismatched)
            # CONTRADICTED needs the cohort test. A minted constant contradicts
            # its WHOLE cohort; a corrupt description contradicts one row of an
            # otherwise sound one. YAGEO's 306 genuine 1 Mohm parts include five
            # whose description text is itself broken ("0.001 ohm 1% 0.25W" on a
            # 1 Mohm part): a description defect, not an invented value, and a
            # guard that failed on it would be red forever and get switched off.
            evidenced = len(contradicted) + agreeing
            if len(contradicted) >= min_cohort and len(contradicted) * 2 >= evidenced:
                findings.extend(contradicted)
    findings.sort()
    return findings, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--file", type=Path, action="append", default=[],
                    help="check this NDJSON file instead of the catalogues")
    ap.add_argument("--min-cohort", type=int, default=MIN_COHORT)
    ap.add_argument("--list", action="store_true", help="print every finding")
    ap.add_argument("--as", dest="as_name", default=None,
                    help="treat --file as this catalogue (e.g. magnetics.ndjson)")
    args = ap.parse_args()

    if args.file:
        targets = args.file
    else:
        targets = [args.data / name for name in sorted(FIELDS)]

    total = 0
    for path in targets:
        if not path.exists():
            print("MISSING: %s -- the guard cannot check what is not there" % path,
                  file=sys.stderr)
            return 1
        specs = specs_for(path, args.as_name)
        if specs is None:
            print("UNKNOWN CATALOGUE: %s -- no per-part field spec for this file. "
                  "Add one to FIELDS or pass --as <catalogue>; the guard refuses to "
                  "report a clean result on a file it does not understand." % path.name,
                  file=sys.stderr)
            return 1
        findings, rows = scan_file(path, args.min_cohort, specs)
        print("%s: %d rows, %d finding(s)" % (path.name, rows, len(findings)))
        total += len(findings)
        shown = findings if args.list else findings[:10]
        for lineno, mfr, part, field, value, verdict, why in shown:
            print("  line %d: %s %s -- %s %s: %s" % (
                lineno, mfr, part, verdict, field, why))
        if not args.list and len(findings) > len(shown):
            print("  ... and %d more (--list for all)" % (len(findings) - len(shown)))

    if total:
        print("\nFAIL: %d value(s) shared across a cohort and refuted by the parts' "
              "own descriptions. An importer that has no value for a field must "
              "OMIT it, never mint a constant. Re-source each flagged field from "
              "that part's own datasheet, or drop the field." % total,
              file=sys.stderr)
        return 1
    print("OK: no minted constants found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
