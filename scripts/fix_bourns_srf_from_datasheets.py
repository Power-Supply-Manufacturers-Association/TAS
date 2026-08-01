#!/usr/bin/env python3
"""Put the impedance column back where it belongs on Bourns / J.W. Miller SRF chokes (ABT #431).

    python3 scripts/fix_bourns_srf_from_datasheets.py [--dry-run]

THE DEFECT. 25 Bourns and J.W. Miller SRF common-mode-choke rows hold their datasheet's
IMPEDANCE figure in dcResistances, as ohms. Bourns publishes the two quantities in
different units and in different column orders per series, and the source pass read the
column that happened to be first:

    Bourns SRF7038A datasheet, "Electrical Specifications at 25 C":

      Part Number    Z (ohm) @ 100 MHz   L (uH)     DCR (mohm)  Rated Current  Rated Voltage
                       Min.     Typ.    @ 100 kHz     Max.        (A) Max.      (VDC) Max.
      SRF7038A-102Y     800     1020       6.3          17            3             80

    corpus held   dcResistances [{maximum: 1020.0}]      inductance 1e-5
    Bourns says   DCR max 0.017 ohm                      L 6.3e-6 H

The stored 1020 is the Z Typ. column; the stored 10 uH is neither column - it is a
placeholder (see below). Both are replaced from the table above.

A UNITS-BLIND PARSE REPRODUCES EXACTLY THIS DEFECT, so every number below is written as
the literal cell from that series' own table plus the unit that series' own header
states, and converted here. The units really do differ per series - all four of these
tables are in this fix:

    SRF0502   DCR (mOhm) Max.      impedance in kOhm ("0.25 +- 35 %")
    SRF0504   DCR (mOhm) max.      impedance in Ohm  ("190 +- 35 %")
    SRF0905   DCR (Ohm) max.       no single-frequency impedance at all
    SRF7038A  DCR (mOhm) Max.      impedance in Ohm, Min. AND Typ. columns

Column ORDER differs too: SRF7038A leads with impedance, SRF0504 and SRF3225TP lead with
inductance, SRF4532TA puts inductance between the two impedance columns, and SRF3225TP
merges the DCR cell vertically across its two parts. Each series was read from its own
header, positionally (word x-coordinates), not by column index.

WHICH ROWS, AND HOW THEY WERE FOUND. Every row whose manufacturerInfo.name is "Bourns" or
"J.W. Miller" and whose reference starts with "SRF" - 105 rows - was listed with its DC
resistance read from BOTH the singular `dcResistance` and the plural `dcResistances[]`
shapes (ABT #387 exists because code read only one of them). 31 store >= 1 ohm. 25 of
those are common-mode chokes, and they are this ticket's population. The other 6 are
coupled inductors and are NOT touched here - see the last section.

THREE MORE ROWS ARE CORRECTED THAN THE TICKET COUNTED. Having the series tables in hand,
every OTHER row of the same series was compared against the same document, because
"only the flagged ones" is how a defect stays invisible. The rule for those extra rows is
the strict one: Bourns publishes DCR as a MAXIMUM, so a stored value BELOW it is a tighter
claim, not a contradiction, and is left alone (SRF0905-652Y holds 0.6 against a 1.05 max;
untouched). Only a stored value that EXCEEDS the published maximum is impossible:

    SRF0502-101Y   0.06 ohm stored vs 30.8 mOhm max   (and L 10 uH vs 1.55 uH)
    SRF0905-251Y   0.9 ohm stored vs 0.13 ohm max
    SRF7038A-700Y  0.1 ohm stored vs 5 mOhm max       (and L 1 uH vs 0.83 uH)

These are in a separate audit bucket, `contradictsPublishedMaximum`, so the 25 remain
countable on their own.

THE 10 uH IS A PLACEHOLDER, AND IS REMOVED WHERE NOTHING REPLACES IT. Fourteen of these
rows store inductance exactly 1e-5 H. Seven are contradicted by their datasheet
(SRF0502 x4 at 0.55/1.10/2.50/2.60 uH, SRF3225TP x2 at 2.2/8.2 uH, SRF7038A-102Y at
6.3 uH), which is what identifies 1e-5 as a default rather than data. The remaining six
belong to series whose datasheet publishes NO inductance column at all (SRF2012A,
SRF3225TAP - they are specified by impedance). Inventing one is out of the question and
so is leaving a fabricated 10 uH standing, so `inductance` is REMOVED from those rows and
every removed value is recorded in the audit under `inductanceRemovedNotPublished`. It is
an optional field in MAS's commonModeChoke variant; absent is the truthful state.

THE IMPEDANCE IS KEPT, NOT DISCARDED. It is real datasheet data that landed in the wrong
field, and MAS already has the right one: `impedancePoints[]`, {impedance:{magnitude},
frequency, winding}. One point per part, at the frequency ITS OWN table states - which is
100 MHz for most series, 10 MHz for SRF4532 and SRF3225AB/TABG, and 10 MHz for
SRF0502-101Y ALONE, whose cell carries its own "@ 10 MHz" footnote while the other four
rows of that same table are at 100 MHz. `winding: "common"` is set only where the column
header actually says "Common Mode".

WHAT IS DELIBERATELY NOT DONE.

  * No schema file is touched, and no field is invented. impedancePoint carries a single
    magnitude with no min/typ qualifier, so where a table gives both (SRF7038A 800 min /
    1020 typ, SRF4532 500 min / 1000 typ) only the TYPICAL is recorded and the minimum is
    left out rather than stored as a second, unqualified point at the same frequency.

  * Published L tolerances (SRF0905 +-30 %, SRF4532 +50/-30 %, SRF3225AB +-50 %) are NOT
    expanded into minimum/maximum. The stored bounds that a corrected nominal invalidates
    are removed instead - SRF7038A-700Y held 0.8/1.2 uH around the wrong 1.0 uH nominal.

  * Three rows are NOT corrected, because their part number appears in no document that
    was actually fetched: SRF2012A-201YA, SRF2012A-361YA and SRF2012A-900YA. Bourns'
    SRF2012A datasheet names these parts SRF2012-201YA / -361YA / -900YA (its "How to
    Order" reads model SRF2012 + value code + tolerance Y + model suffix A), while the
    separate SRF2012A-801Y datasheet uses the "SRF2012A-...Y" spelling for the same
    series - so Bourns itself uses both forms and neither matches "SRF2012A-361YA"
    exactly. Citing a document that does not contain the part number would be a false
    provenance entry, so they are reported in the audit under `withheld` instead. The
    J.W. Miller rows SRF2012-301YA / -361YA / -900YA ARE named verbatim in that table and
    are corrected.

  * The 6 Bourns coupled inductors (SRF0703A-151M, SRF1260A-221M/-331M, SRF1280-471M,
    SRF1280A-331M/-681M) are left alone. They also hold >= 1 ohm, but their datasheets
    publish a PARALLEL and a SERIES rating side by side, and these rows hold the parallel
    inductance next to the series DC resistance - SRF0703A-151M is L 150 uH / DCR 0.986
    ohm in parallel and L 608.2 uH / DCR 3.63 ohm in series, and the row holds 150 uH with
    3.63 ohm. That is a real defect but a different one, on a different subtype, and
    picking which of the two ratings a row is supposed to represent is a decision this
    ticket does not cover. Reported, not silently repaired.

  * Rated currents are not touched even where the datasheet disagrees (SRF2012-301YA is
    stored at 0.4 A against a 300 mA IDC max; SRF0502-101Y at 6 A against 2.5 A). Out of
    this ticket's scope; both are listed in the audit under `notedNotCorrected`.

EVERY DOCUMENT CITED HERE WAS FETCHED AND READ. All twelve are
https://www.bourns.com/docs/product-datasheets/<SERIES>.pdf, retrieved 2026-08-01, and
each part corrected below was found by name in the table of the document cited for it.
The SHA-256 of each PDF as read is recorded in the audit.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "magnetics.ndjson"
AUDIT = REPO / "staging" / "bourns_srf_impedance_as_dcr_audit.json"
TODAY = "2026-08-01"

# Relative agreement below which a stored value is considered to already hold the
# datasheet's figure and is left untouched (float noise, not a disagreement).
SAME = 5e-3

UNIT = {"ohm": 1.0, "mohm": 1e-3, "kohm": 1e3, "uH": 1e-6}


def si(value: float, unit: str) -> float:
    """Datasheet cell -> SI, with the trailing float noise trimmed off.

    0.55 * 1e-6 is 5.5000000000000004e-07 in binary floating point; storing that would
    hand the catalogue a 17-significant-figure inductance for a value the datasheet
    prints to two.
    """
    return float(f"{value * UNIT[unit]:.12g}")


# --- the documents ----------------------------------------------------------------
#
# `dcr` / `l` / `z` name the unit that THIS series' own column header states. `zFreq` is
# the frequency that header states. `zWinding` is set only where the header says
# "Common Mode". `columns` is quoted into the provenance entry so a later reader can see
# which columns were read without re-deriving them.
BASE_URL = "https://www.bourns.com/docs/product-datasheets/{}.pdf"

DOCS = {
    "SRF0502": {
        "title": "Bourns SRF0502 Series - Line Filter datasheet",
        "sha256": "c4191c7841176782db96a12830ac9064bb5cecb79a2700e5cbb86b23850d2cfb",
        "columns": ("'Electrical Specifications @ 25 C' table, columns 'Common Mode Impedance "
                    "@ 100 MHz (kOhm)', 'Inductance @ 100 kHz / 0.1 V (uH)' and 'DCR Max. (mOhm)'"),
        "dcr": "mohm", "l": "uH", "z": "kohm", "zFreq": 100e6, "zWinding": "common",
    },
    "SRF0504": {
        "title": "Bourns SRF0504 Series - Line Filter datasheet",
        "sha256": "d9c5cf5099ae63ba2c216ea9347a9d260cdc703035ee201e88408b305f3ca3af",
        "columns": ("'SRF0504 Series - Line Filter' table, columns 'L (uH) (Ref.)', "
                    "'Impedance Z (Ohm) @ 100 MHz' and 'DCR (mOhm) max. (each winding)'"),
        "dcr": "mohm", "l": "uH", "z": "ohm", "zFreq": 100e6, "zWinding": None,
    },
    "SRF0905": {
        "title": "Bourns SRF0905 Series - Line Filter datasheet",
        "sha256": "4a9d164841d5aaeca300cad53d747ca71571f71098f575213cde430c2d520daa",
        "columns": ("'Electrical Specifications @ 25 C' table, columns 'L (uH)' and "
                    "'DCR (Ohm) max. (each winding)'. This series states no single-frequency "
                    "impedance - only a minimum over a frequency RANGE - so no impedance point "
                    "is recorded from it"),
        "dcr": "ohm", "l": "uH", "z": None, "zFreq": None, "zWinding": None,
    },
    "SRF2012A": {
        "title": "Bourns SRF2012A Series - Common Mode Chip Inductors datasheet",
        "sha256": "dbc0532d00bbbe31affbad11814315a9642a1a4b804eac60990ca6c7a9d9c1b2",
        "columns": ("'Electrical Specifications' table, columns 'Impedance @ 100 MHz (Ohm)' and "
                    "'DCR Max. (Ohm)'. This series publishes NO inductance column"),
        "dcr": "ohm", "l": "uH", "z": "ohm", "zFreq": 100e6, "zWinding": None,
    },
    "SRF2012A-801Y": {
        "title": "Bourns SRF2012A-801Y datasheet (SRF2012A Series - Common Mode Chip Inductors)",
        "sha256": "ce1bd2fc78cb70ebea6f716b1ddb096446ac40c8f892be2ce380ac2bf93cf44d",
        "columns": ("'Electrical Specifications at 25 C' table, columns 'Impedance @ 100 MHz / "
                    "1 V (Ohm)' and 'DCR Max. (Ohm)'. This document publishes NO inductance"),
        "dcr": "ohm", "l": "uH", "z": "ohm", "zFreq": 100e6, "zWinding": None,
    },
    "SRF3225AB": {
        "title": "Bourns SRF3225AB - Common Mode Chip Inductor datasheet",
        "sha256": "c3416b0a29fef6a789dc9591b445995b3ad677a0e63f1103e42494115a8dcf30",
        "columns": ("'Electrical Specifications @ 25 C' table, columns 'Inductance @ 100 kHz / "
                    "0.1 V L (uH)', 'DCR (Ohm) Max.' and 'Common Mode Impedance (Ohm) @ 10 MHz "
                    "Typ.'"),
        "dcr": "ohm", "l": "uH", "z": "ohm", "zFreq": 10e6, "zWinding": "common",
    },
    "SRF3225TABG": {
        "title": "Bourns Model SRF3225TABG - Common Mode Chip Inductor datasheet",
        "sha256": "7234768c11c5ce278c129a089be2270a46438ef0a52f76f5d211f50148df5405",
        "columns": ("'Electrical Specifications @ 25 C' table, columns 'Inductance @ 100 kHz / "
                    "0.1 V L (uH)', 'DCR (Ohm) Max.' and 'Common Mode Impedance (Ohm) @ 10 MHz "
                    "Typ.'"),
        "dcr": "ohm", "l": "uH", "z": "ohm", "zFreq": 10e6, "zWinding": "common",
    },
    "SRF3225TAP": {
        "title": "Bourns SRF3225TAP Series - Common Mode Chip Inductors datasheet",
        "sha256": "39da09cf29a4c44952f1465f6ae5d9034cf1c7aa2c48cf44f7ab60fbfee78ed8",
        "columns": ("'Electrical Specifications @ 25 C' table, columns 'Common Mode Impedance "
                    "(Ohm) @ 100 MHz' and 'DCR (Ohm) Max.'. This series publishes NO inductance "
                    "column"),
        "dcr": "ohm", "l": "uH", "z": "ohm", "zFreq": 100e6, "zWinding": "common",
    },
    "SRF3225TP": {
        "title": "Bourns SRF3225TP Series - Common Mode Chokes datasheet",
        "sha256": "eb0fea50228bacd3a5ac1608f4ab62a0732b2363ba13fb0aa752c15330bc1030",
        "columns": ("'Electrical Specifications @ 25 C' table, columns 'Inductance @ 100 KHz / "
                    "0.1 V L (uH)', 'Common Mode Impedance (Ohm) @ 100 MHz' and 'DCR (Ohm) Max.' "
                    "- the DCR cell is merged vertically across both parts of the table"),
        "dcr": "ohm", "l": "uH", "z": "ohm", "zFreq": 100e6, "zWinding": "common",
    },
    "SRF4532": {
        "title": "Bourns SRF4532 Series - Common Mode Chip Inductors datasheet",
        "sha256": "ed6cae1fcf3fd84cede0bf1be3403f92cbc64ef3575b233523854bff23f09de9",
        "columns": ("'Electrical Specifications' table, columns 'Inductance (uH) @ 100 KHz "
                    "Common Mode', 'Impedance (Ohm) @ 10 MHz Common Mode Typ.' and "
                    "'DCR Max. (Ohm)'"),
        "dcr": "ohm", "l": "uH", "z": "ohm", "zFreq": 10e6, "zWinding": "common",
    },
    "SRF4532TA": {
        "title": "Bourns SRF4532TA Series - Common Mode Chip Inductor datasheet",
        "sha256": "244fb5e785448ffba183384e62ed8b3aba4ebffe719f86d9e786f4435ebdf8ac",
        "columns": ("'Electrical Specifications @ 25 C' table, columns 'Impedance (Ohm) "
                    "@ 100 MHz Typ.', 'Inductance @ 100 kHz / 0.1 V L (uH)' and 'DCR (Ohm) Max.'"),
        "dcr": "ohm", "l": "uH", "z": "ohm", "zFreq": 100e6, "zWinding": None,
    },
    "SRF7038A": {
        "title": "Bourns SRF7038A Series - Common Mode Chokes datasheet",
        "sha256": "a9f3bda113947b7d84474f36a7af65af51c1e25744762384c628d83fc7caa240",
        "columns": ("'Electrical Specifications at 25 C' table, columns 'Z (Ohm) @ 100 MHz Typ.', "
                    "'L (uH) @ 100 kHz / 0.1 V' and 'DCR (mOhm) Max.'"),
        "dcr": "mohm", "l": "uH", "z": "ohm", "zFreq": 100e6, "zWinding": None,
    },
}

# --- the table rows, exactly as printed -------------------------------------------
#
#   reference: (document, L cell, DCR cell, impedance cell, kind)
#
# `L` None  = that series publishes no inductance column for this part.
# `Z` None  = that series publishes no single-frequency impedance.
# kind "impedanceAsDcr"        = this ticket's 25: the stored DCR is the impedance column.
# kind "contradictsMaximum"    = same series, stored DCR exceeds the published maximum.
ROWS = {
    # SRF0502: Z (kOhm) | L (uH) | Rated Current (A) | DCR (mOhm) Max.
    "SRF0502-101Y":     ("SRF0502",    1.55, 30.8, 0.10, "contradictsMaximum", 10e6),
    "SRF0502-251Y":     ("SRF0502",    0.55, 20.0, 0.25, "impedanceAsDcr", None),
    "SRF0502-501Y":     ("SRF0502",    1.10, 30.0, 0.50, "impedanceAsDcr", None),
    "SRF0502-102Y":     ("SRF0502",    2.50, 45.0, 1.00, "impedanceAsDcr", None),
    "SRF0502-142Y":     ("SRF0502",    2.60, 55.0, 1.40, "impedanceAsDcr", None),
    # SRF0504: L (uH) | Z (Ohm) | DCR (mOhm) max. | Rated Current (mA)
    "SRF0504-191Y":     ("SRF0504",    0.60, 20.0, 190.0, "impedanceAsDcr", None),
    "SRF0504-152Y":     ("SRF0504",    3.6, 100.0, 1500.0, "impedanceAsDcr", None),
    # SRF0905: L (uH) | DCR (Ohm) max. | Rated Voltage | Rated Current | freq range | min Z
    "SRF0905-250Y":     ("SRF0905",    25.0, 0.16, None, "impedanceAsDcr", None),
    "SRF0905-400Y":     ("SRF0905",    40.0, 0.25, None, "impedanceAsDcr", None),
    "SRF0905-500Y":     ("SRF0905",    50.0, 0.32, None, "impedanceAsDcr", None),
    "SRF0905-501Y":     ("SRF0905",   500.0, 0.15, None, "impedanceAsDcr", None),
    "SRF0905-251Y":     ("SRF0905",   250.0, 0.13, None, "contradictsMaximum", None),
    # SRF2012A: Z (Ohm) | Tol | Insulation Resistance | DCR Max. (Ohm) | IDC Max. (mA)
    "SRF2012-301YA":    ("SRF2012A",   None, 0.50, 300.0, "impedanceAsDcr", None),
    "SRF2012-361YA":    ("SRF2012A",   None, 0.50, 360.0, "impedanceAsDcr", None),
    "SRF2012-900YA":    ("SRF2012A",   None, 0.30, 90.0, "impedanceAsDcr", None),
    "SRF2012A-801Y":    ("SRF2012A-801Y", None, 0.88, 800.0, "impedanceAsDcr", None),
    # SRF3225AB / TABG: L (uH) | Tol | Leakage L | DCR (Ohm) Max. | IDC (A) | Z typ | Z min
    "SRF3225AB-101Y":   ("SRF3225AB",  100.0, 3.5, 5500.0, "impedanceAsDcr", None),
    "SRF3225TABG-101Y": ("SRF3225TABG", 100.0, 3.0, 3000.0, "impedanceAsDcr", None),
    # SRF3225TAP: Z (Ohm) | DCR (Ohm) Max. | Rated Current (mA) | Rated Voltage | IR
    "SRF3225TAP-102Y":  ("SRF3225TAP", None, 0.10, 1000.0, "impedanceAsDcr", None),
    # SRF3225TP: L (uH) | Tol | Z (Ohm) | DCR (Ohm) Max. | Rated Current (mA) | ...
    "SRF3225TP-501Y":   ("SRF3225TP",  2.2, 0.1, 500.0, "impedanceAsDcr", None),
    "SRF3225TP-102Y":   ("SRF3225TP",  8.2, 0.1, 1000.0, "impedanceAsDcr", None),
    # SRF4532: L (uH) | Tol | Z min | Z typ | DCR Max. (Ohm) | IDC Max. (mA)
    "SRF4532-220Y":     ("SRF4532",    22.0, 2.65, 1000.0, "impedanceAsDcr", None),
    "SRF4532-510Y":     ("SRF4532",    51.0, 3.5, 2000.0, "impedanceAsDcr", None),
    "SRF4532-101Y":     ("SRF4532",   100.0, 8.9, 5000.0, "impedanceAsDcr", None),
    # SRF4532TA: Z typ | Z min | L (uH) | DCR (Ohm) Max. | Rated Current (mA)
    "SRF4532TA-282Y":   ("SRF4532TA",  10.00, 0.350, 2800.0, "impedanceAsDcr", None),
    # SRF7038A: Z min | Z typ | L (uH) | DCR (mOhm) Max. | Rated Current (A) | ...
    "SRF7038A-102Y":    ("SRF7038A",   6.3, 17.0, 1020.0, "impedanceAsDcr", None),
    "SRF7038A-700Y":    ("SRF7038A",   0.83, 5.0, 70.0, "contradictsMaximum", None),
}

# Part numbers that appear in NO document that was fetched. Left exactly as they are.
WITHHELD = {
    "SRF2012A-201YA": ("Bourns' SRF2012A datasheet names this part 'SRF2012-201YA' (200 Ohm, "
                       "DCR 0.40 Ohm max, IDC 300 mA); the string 'SRF2012A-201YA' appears in no "
                       "Bourns document fetched. Stored inductance 200 uH equals the impedance "
                       "code and that series publishes no inductance at all, so this row is "
                       "suspect - but correcting it would mean citing a document that does not "
                       "name it"),
    "SRF2012A-361YA": ("Bourns' SRF2012A datasheet names this part 'SRF2012-361YA' (360 Ohm, "
                       "DCR 0.50 Ohm max); the string 'SRF2012A-361YA' appears in no Bourns "
                       "document fetched. The J.W. Miller row SRF2012-361YA, which the document "
                       "does name, IS corrected here and carries the same values"),
    "SRF2012A-900YA": ("Bourns' SRF2012A datasheet names this part 'SRF2012-900YA' (90 Ohm, "
                       "DCR 0.30 Ohm max, IDC 400 mA); the string 'SRF2012A-900YA' appears in no "
                       "Bourns document fetched. Its stored 0.4 Ohm exceeds that maximum and its "
                       "stored 90 uH equals the impedance code, so this row is suspect too"),
}

# Rows found >= 1 ohm by the same scan but deliberately out of this ticket's scope.
COUPLED_INDUCTORS = {
    "SRF0703A-151M": "parallel L 150 uH / DCR 0.986 Ohm, series L 608.2 uH / DCR 3.63 Ohm",
    "SRF1260A-221M": "parallel L 220 uH / DCR 0.354 Ohm, series L 880 uH / DCR 1.416 Ohm",
    "SRF1260A-331M": "parallel L 330 uH / DCR 0.574 Ohm, series L 1320 uH / DCR 2.29 Ohm",
    "SRF1280-471M": "parallel L 470 uH / DCR 0.865 Ohm, series L 1868 uH / DCR 3.3 Ohm",
    "SRF1280A-331M": "parallel L 330 uH / DCR 0.54 Ohm, series L 1294 uH / DCR 2.172 Ohm",
    "SRF1280A-681M": "parallel L 680 uH / DCR 1.296 Ohm, series L 2707 uH / DCR 4.888 Ohm",
}

# Disagreements seen in the same table rows but outside this ticket's fields.
NOTED = {
    "SRF0502-101Y": "stored ratedCurrents [6.0] A; Bourns' table says 2.5 A",
    "SRF2012-301YA": "stored ratedCurrents [0.4] A; Bourns' table says IDC max 300 mA",
    "SRF2012-361YA": "stored ratedCurrents [0.3] A; Bourns' table says IDC max 300 mA - agrees",
    "SRF7038A-700Y": "stored saturationCurrentPeak 15.0 A; Bourns' table says rated current "
                     "15 A max, which is a rated current, not a saturation current",
}

MANUFACTURERS = {"Bourns", "J.W. Miller"}


def variants(datasheet_info):
    el = datasheet_info.get("electrical")
    return el if isinstance(el, list) else ([el] if el else [])


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def stored_dcr(entry):
    """Every DC resistance on this variant, from BOTH shapes, as (container, key, value).

    ABT #387: three validator checks read only `dcResistance` and silently skipped the
    4,450 rows that use `dcResistances`. A repair script that reads only the plural one
    repeats the mistake in the other direction.
    """
    out = []
    single = entry.get("dcResistance")
    if is_number(single):
        out.append((entry, "dcResistance", float(single)))
    elif isinstance(single, dict):
        for k in ("maximum", "nominal", "minimum"):
            if is_number(single.get(k)):
                out.append((single, k, float(single[k])))
    for item in entry.get("dcResistances") or []:
        if isinstance(item, dict):
            for k in ("maximum", "nominal", "minimum"):
                if is_number(item.get(k)):
                    out.append((item, k, float(item[k])))
    return out


def apply_dcr(entry, ds_max):
    """Replace the stored DC resistance with the datasheet MAXIMUM.

    Bourns publishes one figure and calls it a maximum, so that is what is written. A
    stored `nominal`/`minimum` is DROPPED rather than kept or reused: "we do not know the
    typical value" is true, "the typical equals the maximum" is not.
    """
    was = []
    containers = []
    single = entry.get("dcResistance")
    if is_number(single):
        was.append({"shape": "dcResistance", "value": float(single)})
        entry["dcResistance"] = {"maximum": ds_max}
    elif isinstance(single, dict):
        was.append({"shape": "dcResistance", **{k: single[k] for k in
                                                ("nominal", "minimum", "maximum") if k in single}})
        containers.append(single)
    for item in entry.get("dcResistances") or []:
        if isinstance(item, dict):
            was.append({"shape": "dcResistances", **{k: item[k] for k in
                                                     ("nominal", "minimum", "maximum")
                                                     if k in item}})
            containers.append(item)
    for c in containers:
        c.pop("nominal", None)
        c.pop("minimum", None)
        c["maximum"] = ds_max
    return was


def main(argv):
    dry = "--dry-run" in argv
    audit = {
        "ticket": "ABT #431", "date": TODAY,
        "documents": {k: {"url": BASE_URL.format(k), "sha256": v["sha256"],
                          "title": v["title"], "columnsRead": v["columns"]}
                      for k, v in DOCS.items()},
        "impedanceAsDcr": [], "contradictsPublishedMaximum": [],
        "inductanceRemovedNotPublished": [], "impedanceRecorded": [],
        "withheld": [{"reference": r, "reason": why} for r, why in WITHHELD.items()],
        "coupledInductorsNotInScope": [{"reference": r, "datasheet": d}
                                       for r, d in COUPLED_INDUCTORS.items()],
        "notedNotCorrected": [{"reference": r, "note": n} for r, n in NOTED.items()],
        "counts": Counter(),
    }
    seen = Counter()
    tmp = DATA.with_suffix(".ndjson.tmp")

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            line = raw
            if b"SRF" in raw and (b"Bourns" in raw or b"J.W. Miller" in raw):
                try:
                    rec = json.loads(raw)
                    mi = rec["magnetic"]["manufacturerInfo"]
                except Exception:                                     # noqa: BLE001
                    out.write(line)
                    continue
                ref = str(mi.get("reference") or "")
                if mi.get("name") in MANUFACTURERS and ref in ROWS:
                    seen[ref] += 1
                    doc_key, l_cell, dcr_cell, z_cell, kind, z_freq_override = ROWS[ref]
                    doc = DOCS[doc_key]
                    ds_dcr = si(dcr_cell, doc["dcr"])
                    ds_l = si(l_cell, doc["l"]) if l_cell is not None else None
                    di = mi.setdefault("datasheetInfo", {})
                    fields, changed = [], False

                    for e in variants(di):
                        if not isinstance(e, dict) or e.get("subtype") != "commonModeChoke":
                            continue

                        # --- DC resistance ---
                        held = stored_dcr(e)
                        if held:
                            exceeds = any(v > ds_dcr for _, _, v in held)
                            # For the impedance-capture population the stored number is
                            # provably the wrong COLUMN, so it is replaced whatever its
                            # magnitude (SRF3225AB-101Y holds 2.0, the Z-min column
                            # divided by 1000, which happens to sit BELOW the true 3.5
                            # ohm maximum). For the same-series extras the bar is the
                            # strict one: only a value that exceeds the published
                            # maximum is a contradiction.
                            if kind == "impedanceAsDcr" or exceeds:
                                was = apply_dcr(e, ds_dcr)
                                fields.append("electrical.dcResistances")
                                changed = True
                                audit["impedanceAsDcr" if kind == "impedanceAsDcr"
                                      else "contradictsPublishedMaximum"].append(
                                    {"reference": ref, "document": doc_key,
                                     "datasheetCell": f"{dcr_cell} {doc['dcr']}",
                                     "was": was, "nowMaximum": ds_dcr})
                            else:
                                audit["counts"]["dcrBelowPublishedMaximumLeftAlone"] += 1

                        # --- inductance ---
                        ind = e.get("inductance")
                        if ds_l is not None and isinstance(ind, dict):
                            nom = ind.get("nominal")
                            if is_number(nom) and abs(nom - ds_l) > ds_l * SAME:
                                ind["nominal"] = ds_l
                                # Bounds computed from the wrong nominal do not survive it.
                                for b in ("minimum", "maximum"):
                                    ind.pop(b, None)
                                fields.append("electrical.inductance.nominal")
                                changed = True
                                audit["counts"]["inductanceCorrected"] += 1
                                audit["impedanceAsDcr" if kind == "impedanceAsDcr"
                                      else "contradictsPublishedMaximum"].append(
                                    {"reference": ref, "document": doc_key,
                                     "datasheetCell": f"{l_cell} {doc['l']}",
                                     "wasInductance": nom, "nowInductance": ds_l})
                        elif ds_l is None and isinstance(ind, dict):
                            # The series publishes no inductance. The stored value is a
                            # placeholder; absent is the only truthful state.
                            audit["inductanceRemovedNotPublished"].append(
                                {"reference": ref, "document": doc_key, "removed": ind})
                            e.pop("inductance", None)
                            fields.append("electrical.inductance (removed - not published)")
                            changed = True

                        # --- impedance, into the field MAS already has for it ---
                        if z_cell is not None and not e.get("impedancePoints"):
                            point = {"impedance": {"magnitude": si(z_cell, doc["z"])},
                                     "frequency": z_freq_override or doc["zFreq"]}
                            if doc["zWinding"]:
                                point["winding"] = doc["zWinding"]
                            e["impedancePoints"] = [point]
                            fields.append("electrical.impedancePoints")
                            changed = True
                            audit["impedanceRecorded"].append(
                                {"reference": ref, "magnitude": point["impedance"]["magnitude"],
                                 "frequency": point["frequency"],
                                 "datasheetCell": f"{z_cell} {doc['z']}"})

                    if changed:
                        di.setdefault("provenance", []).append({
                            "source": "manufacturerDatasheet",
                            "sourceName": f"{doc['title']}: {doc['columns']}",
                            "sourceUrl": BASE_URL.format(doc_key),
                            "retrievedDate": TODAY,
                            "fields": fields,
                        })
                        audit["counts"]["rows"] += 1
                        audit["counts"][kind] += 1
                        line = json.dumps(rec, separators=(",", ":"),
                                          ensure_ascii=False).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())

    missing = sorted(set(ROWS) - set(seen))
    dupes = sorted(r for r, n in seen.items() if n > 1)
    print(f"rows corrected:                  {audit['counts']['rows']}")
    print(f"  impedance stored as DCR:       {audit['counts']['impedanceAsDcr']}")
    print(f"  exceeds published maximum:     {audit['counts']['contradictsMaximum']}")
    print(f"  inductances corrected:         {audit['counts']['inductanceCorrected']}")
    print(f"  inductances removed (no value):{len(audit['inductanceRemovedNotPublished'])}")
    print(f"  impedance points recorded:     {len(audit['impedanceRecorded'])}")
    print(f"withheld (part not named):       {len(WITHHELD)}")
    if missing:
        print(f"!! expected but NOT FOUND in the corpus: {missing}")
    if dupes:
        print(f"!! matched more than one row: {dupes}")
    for f in audit["impedanceAsDcr"][:6]:
        if "nowMaximum" in f:
            print(f"   R  {f['reference']:20} {f['was']} -> max {f['nowMaximum']}")

    if dry:
        tmp.unlink(missing_ok=True)
        print("\n--dry-run: nothing written")
    else:
        os.replace(tmp, DATA)
        audit["counts"] = dict(audit["counts"])
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\nreplaced {DATA}\naudit -> {AUDIT}")
    return 1 if (missing or dupes) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
