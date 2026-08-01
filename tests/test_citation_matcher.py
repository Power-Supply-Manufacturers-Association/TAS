"""Pin the citation matcher against cases verified by hand (ABT #391).

    pytest tests/test_citation_matcher.py -q

verify_provenance_content.py answers one question: does the document a record cites
actually mention that part? Three separate bugs in its matcher each produced a large,
confident, WRONG answer, and each was found only by opening the cited PDF and looking:

  * a separator-based family rule gave no family form at all to hyphen-less codes,
    so Panasonic returned 89,726 ABSENT against 9,427 FOUND
  * a minimum-length guard applied to the exact part number, so Yageo S1A came back
    ABSENT against the S1 series datasheet that prints it on page one
  * a half-length truncation floor made the true family unreachable for long codes, so
    11,307 rows citing their own RC_L series datasheet came back ABSENT

Every one of those looked like a finding about the DATA. They were findings about the
regex. The verdicts are the input to decisions about deleting or re-sourcing hundreds of
thousands of citations, so the matcher gets a regression suite, built from documents that
were opened and read rather than from what the rules were expected to do.

Each case below records the real part number, a fragment of the REAL document text it was
checked against, and the verdict confirmed by reading that document.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from verify_provenance_content import candidates, check, series_from_url  # noqa: E402


# (name, part number, cited URL, document text, expected verdict)
CASES = [
    # Exact hit. Yageo prints S1A/S1B/S1G on the first page of the S1 series datasheet;
    # a length guard on the exact form had been calling this ABSENT.
    ("short exact code", "S1A",
     "https://yageogroup.com/content/datasheet/asset/file/S1_1",
     "DATA SHEET | GENERAL SERIES RECTIFIER | S1 SERIES | S1A S1B S1D S1G ratings",
     "FOUND"),

    # Series document, family reachable only at a token boundary. Yageo's RC_L series
    # datasheet covers sizes 0075/0100/0402/...; it names RC0100 but never RC0100FR.
    ("long code, family at token boundary", "RC0100FR-073K3L",
     "https://yageogroup.com/content/datasheet/asset/file/PYU-RC_GROUP_51_ROHS_L",
     "GENERAL PURPOSE CHIP RESISTORS RC_L series Sizes 0075/0100/0201/0402/0603 "
     "ordering RC0100 RC0402 RC0603",
     "FAMILY_ONLY"),

    # Hyphen-less Panasonic code: no separator exists, so the family must come from
    # structure. The series document names EEUFC1V but not the full order code.
    ("hyphen-less code", "EEUFC1V102",
     "https://industrial.panasonic.com/cdbs/www-data/pdf/RDF0000/ABA0000C1259.pdf",
     "Aluminium Electrolytic Capacitors FC series  EEUFC1V  35 V  rated ripple",
     "FAMILY_ONLY"),

    # Series in the MIDDLE of the code: 4 V / series MS5 / 22 uF / 4x5 mm. No suffix of
    # the part number is "MS5", so the citation's own filename supplies it.
    ("series named only by the URL", "4MS522MEFC4X5",
     "https://www.rubycon.co.jp/wp-content/uploads/catalog-aluminum/MS5.pdf",
     "RADIAL LEAD ALUMINUM ELECTROLYTIC CAPACITORS  MS5 SERIES  4V 22uF",
     "FAMILY_ONLY"),

    # Genuinely absent, confirmed by hand: Rubycon's KXW.pdf is an "Obsolete Products"
    # dimensions sheet of 5,048 characters in which the string "KXW" does not occur even
    # once, so nothing ties it to the part beyond the filename.
    ("true absence", "16KXW470MEFC5X11",
     "https://www.rubycon.co.jp/wp-content/uploads/catalog-aluminum/KXW.pdf",
     "生産中止品  Obsolete Products  DIMENSIONS (mm)  case sizes and lead pitch",
     "ABSENT"),

    # The ABT #385 defect this pass exists to catch: a SUMIDA part citing a BOURNS
    # datasheet. The document is real and resolves perfectly; it is still the wrong part.
    ("wrong manufacturer's datasheet", "CDRH127-100MC",
     "https://www.bourns.com/docs/product-datasheets/SRP1265A.pdf",
     "SRP1265A Series  Shielded Power Inductors  SRP1265A-100M  Bourns",
     "ABSENT"),
]


@pytest.mark.parametrize("name,ref,url,text,expected",
                         CASES, ids=[c[0] for c in CASES])
def test_verdict(name, ref, url, text, expected):
    got = check(text, [ref], url)[ref]
    assert got["verdict"] == expected, (
        f"{ref} against {url}: expected {expected}, got {got['verdict']} "
        f"(matched as {got['matchedAs']}); forms tried: "
        f"{[f for _, f in candidates(ref)][:8]}")


def test_family_stems_never_cut_mid_token():
    """"CC040" matches nearly every Yageo document; it must never be offered.

    This is the failure the token-boundary rule replaced a length floor to prevent, so
    it is pinned directly rather than trusted to the floor arithmetic.
    """
    forms = [f for _, f in candidates("CC0402JRN1A100")]
    assert "CC040" not in forms
    assert "CC0402" in forms, "the real family boundary must still be offered"


def test_bare_vendor_prefix_is_not_a_family():
    """A stem needs a letter AND a digit, so "RC" or "CL" alone cannot match."""
    for ref in ("RC0100FR-073K3L", "CL10F104ZB8NNNC"):
        for _, form in candidates(ref):
            assert not form.isalpha() or form == ref, f"{form} from {ref} is a bare prefix"


def test_url_series_rule_requires_the_token_to_be_in_the_part_number():
    """The filename may only corroborate a part it is actually a substring of."""
    assert series_from_url(
        "https://www.rubycon.co.jp/wp-content/uploads/catalog-aluminum/MS5.pdf",
        "4MS522MEFC4X5") == ["MS5"]
    # An unrelated vendor's filename must supply nothing, or the ABT #385 Sumida/Bourns
    # defect would be laundered into a match.
    assert series_from_url(
        "https://www.bourns.com/docs/product-datasheets/SRP1265A.pdf",
        "CDRH127-100MC") == []


def test_generic_catalogue_words_cannot_rubber_stamp():
    """"HV" or "IND" in a filename must not count as a series name."""
    assert series_from_url(
        "https://yageogroup.com/content/datasheet/asset/file/KEM_C1106_HV_RAD_IND_HT200C",
        "05HV10B103KN") == []


# ── Known false negatives, recorded rather than asserted as correct ──────────────
#
# These pin CURRENT behaviour so a change is visible, but the verdict they pin is
# WRONG and is tracked. They exist because the alternative — quietly leaving the
# defect undocumented — is how the earlier matcher bugs survived as long as they did.
#
# A note on how one of them got here. An earlier version of this file pinned
# 05HV10B103KN against KEM_C1106 as a "true absence, confirmed by hand: the document
# contains no order code of any kind". That sentence was literally true and the
# conclusion drawn from it was wrong. The document opens with an Ordering Information
# table that decodes the code field by field —
#
#     05 = 500 V | Series HV | Style/Size 10..16 | B = X7R | 103 = cap code
#                | K = +-10 % | N = Nickel
#
# — which is unambiguously this part's own datasheet naming every one of its fields.
# It simply never prints them concatenated. "I could not find the string" is not
# "the part is not in the document", and pinning the first as if it were the second
# put a false claim into the test suite that guards this exact class of error.


def test_known_false_negative_document_decodes_but_never_concatenates():
    """KEMET-style ordering tables name every field of the code but never join them.

    ~100 corpus rows cite a document of this shape. Matching them needs a decoder rule
    (assemble the code from the table's own field columns), not another string form,
    so the gap is recorded here rather than papered over. When such a rule lands, this
    test flips to FOUND or FAMILY_ONLY and should be moved into CASES.
    """
    # Verbatim from the cited document, whitespace collapsed. Note the example code in the
    # header row is a DIFFERENT part (10 HV 12 N 472 K N M) — the table teaches the fields,
    # it does not list this order code, so no string form of the part can ever match. An
    # earlier draft of this fixture invented a header reading "05 HV 10 B 103 K N", which
    # normalises straight into the part number and made the test pass for the wrong reason.
    text = ("Ordering Information 10 HV 12 N 472 K N M Capacitance Capacitance Lead Wire "
            "Voltage Series Style/Size Dielectric Test Level Packaging "
            "05 = 500 V HV 10 B, W = X7R type Two significant J = +-5% N = Nickel")
    got = check(text, ["05HV10B103KN"],
                "https://yageogroup.com/content/datasheet/asset/file/"
                "KEM_C1106_HV_RAD_IND_HT200C")["05HV10B103KN"]
    assert got["verdict"] == "ABSENT", "if this now passes, the decoder gap is closed"


def test_known_false_negative_split_ordering_code():
    """Vishay prints the mask stem in the column header and only the tail in each row.

    013rlc.pdf contains "MAL2013" and "65101E3" as separate tokens and never the joined
    MAL201365101E3, so the part is present in its own datasheet in split form. 6,181
    corpus rows are in this shape — the single largest wrong verdict in the pass.
    """
    text = ("ORDERING CODE  MAL2013.......   "
            "16  100  8.2 x 11  150  3.2  0.13  1.0   55101E3  5.0   65101E3  5.0   35101E3  5.0")
    got = check(text, ["MAL201365101E3"],
                "https://www.vishay.com/docs/28313/013rlc.pdf")["MAL201365101E3"]
    assert got["verdict"] == "ABSENT", "if this now passes, the split-code gap is closed"
