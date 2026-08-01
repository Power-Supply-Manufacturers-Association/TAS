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

A FOURTH round (ABT #450, seven rules; ABT #452, the unreadable-document detector) took
ABSENT from 23,038 to 3,439 — again without a single change to the data. Its cases are
below too, each with the document it was verified against named in the comment.

EVERY FIXTURE IS QUOTED FROM BYTES THAT EXIST. That rule is here because breaking it
already produced a green test that proved nothing: an invented header reading
"05 HV 10 B 103 K N" normalises straight into the part number it was supposed to be
absent from, so the test passed for exactly the wrong reason. Whitespace may be
collapsed and nothing else. If a fixture cannot be quoted, the case is not understood
well enough to pin.

Each case below records the real part number, a fragment of the REAL document text it was
checked against, and the verdict confirmed by reading that document.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from verify_provenance_content import (  # noqa: E402
    ALPHA_WORD, ASCII_ALNUM, CJK_CHAR, COMMON_WORDS, MIN_ALNUM_FRACTION,
    MIN_WORDS_PER_1000, DocIndex, candidates, check, series_from_url,
    split_code_match, unreadable_reason, wildcard_patterns,
)


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

    # ── ABT #450 rule 1: SPLIT ORDERING CODE ────────────────────────────────────
    # Vishay prints the mask in the column header and only the tail in each row, so the
    # joined code occurs nowhere. 6,181 corpus rows are this shape — the single largest
    # wrong verdict in the pass, and FOUND rather than FAMILY_ONLY: the exact part is
    # printed, just not contiguously.
    #
    # Verbatim from docs/28313/013rlc.pdf (whitespace collapsed): the header line
    # "ORDERING CODE MAL2013......." and the 16 V / 100 uF row of Table 1.
    ("split ordering code, mask header + row tail", "MAL201365101E3",
     "https://www.vishay.com/docs/28313/013rlc.pdf",
     "ORDERING CODE  MAL2013.......   "
     "16  100  8.2 x 11  150  3.2  0.13  1.0   55101E3  5.0   65101E3  5.0   35101E3  5.0",
     "FOUND"),

    # ── ABT #450 rule 2: FAMILY ATTESTED BY THE DOCUMENT ────────────────────────
    # ERJ1GJF1000C tokenises as ERJ|1|GJF|1000|C, so the structural stem rule can offer
    # ERJ1GJF but never ERJ1GJ — which is the name Panasonic's own datasheet prints.
    # 11,133 rows are this shape. FAMILY_ONLY: the series is named, the order code is not.
    #
    # Verbatim from industrial.panasonic.com/.../RDA0000/AOA0000C304.pdf (whitespace
    # collapsed): the taping table row, the automotive note, and the ratings-table label.
    ("family named by the document but unreachable by truncation", "ERJ1GJF1000C",
     "https://industrial.panasonic.com/cdbs/www-data/pdf/RDA0000/AOA0000C304.pdf",
     "2RK  0402  0.1 W  2 mm pitch, 15,000 pcs  ERJ1GJ    "
     "* For the automotive application, please use ERJ1GJ as 0201 inch size from the "
     "new design.  Embossed carrier taping    ERJ1GJ  0.05  25  50  +-1  10 to 1 M  *4",
     "FAMILY_ONLY"),

    # The veto that makes rule 2 safe. MAL209022152E3 is a Vishay 090 PUL-SI part cited
    # to the 094 PM-ESI datasheet — a sibling series, one of the 3,038 wrong-part
    # citations. pdftotext keeps the spaces in "Ordering code: MAL2 094 46331 E3", so
    # "MAL2" IS a free-standing token of this document and is a prefix of every Vishay
    # aluminium electrolytic; on its own it would rescue 401 wrong-part citations. The
    # document's own mask "MAL2094......." says the fixed stem is MAL2094, not MAL2.
    #
    # Verbatim from vishay.com/docs/28382/094pmesi.pdf.
    ("mask stem vetoes a truncated family token", "MAL209022152E3",
     "https://www.vishay.com/docs/28382/094pmesi.pdf",
     "ESR  Max. equivalent series resistance at 120 Hz (1)  "
     "Ordering code: MAL2 094 46331 E3   Former 12NC: 2222 094 46331   "
     "(uF)  (A)  ()  ()  MAL2094.......",
     "ABSENT"),

    # ── ABT #450 rule 3: FOOTNOTE-WILDCARD ORDERING CODES ───────────────────────
    # KEMET prints the code with footnote markers where the variable letters go, each
    # (n) pointing at a table of the letters allowed there. FOUND, not FAMILY_ONLY:
    # every character of the order code is accounted for.
    #
    # Verbatim from KEM_T2043_T35X, the 39 uF / 6 V row. (1)->6, (3)->M, (4)->S give
    # T356F396M006AS exactly.
    ("footnote markers stand in for the variable letters", "T356F396M006AS",
     "https://yageogroup.com/content/datasheet/asset/file/KEM_T2043_T35X",
     "6  39.0  F  T35(1)F396(3)006A(4)  1.9  6",
     "FOUND"),

    # Verbatim from KEM_C1031_KPS_SMPS_49470_STACKS, the 2.7 uF row — three markers,
    # two of them adjacent, and a "(3.05)"-style dimension on the same line that must
    # NOT be read as a marker. (3)->N, (2)->K, (4)->B give L1XN501275KB48.
    ("adjacent footnote markers", "L1XN501275KB48",
     "https://yageogroup.com/content/datasheet/asset/file/KEM_C1031_KPS_SMPS_49470_STACKS",
     "(1)49470X01275(2)B(3)  2.7  5  0.480 (12.19)  K, M  N, L, J  L1X(3)501275(2)(4)48",
     "FOUND"),

    # ── ABT #450 rule 4: FULL-WIDTH TEXT ────────────────────────────────────────
    # The sharpest miss in the whole pass: one character. AOA0000C304.pdf lays its
    # part-number decoder out a character per column, and the seventh column carries a
    # FULL-WIDTH D (U+FF24). [^A-Z0-9] deleted it, so the document that prints this
    # part was reporting it absent. Verbatim from the document, including the U+FF24.
    ("full-width character inside the code", "ERJ3RBD1002V",
     "https://industrial.panasonic.com/cdbs/www-data/pdf/RDA0000/AOA0000C304.pdf",
     "1  2  3  4  5  6  7  8  9  10  11  12    "
     "E  R  J  3  R  B  Ｄ  1  0  0  2  V    "
     "Product code    Size, Power rating    T.C.R.，Marking    Resistance tolerance",
     "FOUND"),

    # 278 rows: Rubycon's ZLJ.pdf writes its own series name in full-width letters, so
    # the citation's filename token "ZLJ" found nothing to corroborate. Verbatim from
    # the cover of catalog-aluminum/ZLJ.pdf.
    ("full-width series name in the title", "6.3ZLJ220M5X11",
     "https://www.rubycon.co.jp/wp-content/uploads/catalog-aluminum/ZLJ.pdf",
     "リード線形アルミニウム電解コンデンサ ＺＬＪ "
     "ＲＡＤＩＡＬ ＬＥＡＤ "
     "ＡＬＵＭＩＮＵＭ "
     "ＥＬＥＣＴＲＯＬＹＴＩＣ "
     "ＣＡＰＡＣＩＴＯＲＳ "
     "ＺＬＪ series 105℃ 6000～10000時間品 高リプル 長寿命 低インピーダンス品",
     "FAMILY_ONLY"),

    # ── ABT #450 rule 5: TWO-CHARACTER SERIES NAMES ─────────────────────────────
    # Rubycon publishes catalog-aluminum/AX.pdf, PK.pdf, PX.pdf, ZL.pdf ... where the
    # whole filename IS the series name. The URL-series rule required three characters,
    # so 850 rows citing their own catalogue came back ABSENT. Verbatim from AX.pdf.
    ("two-character series, whole basename", "6.3AX82MEFC5X7",
     "https://www.rubycon.co.jp/wp-content/uploads/catalog-aluminum/AX.pdf",
     "AX  RADIAL LEAD ALUMINUM ELECTROLYTIC CAPACITORS   AX series   "
     "105°C 2000時間品 超小形化品  Load Life: 105°C 2000 hours, Ultra Miniaturized",
     "FAMILY_ONLY"),

    # ── ABT #450 rule 6: INFIX VARIANT LETTER ───────────────────────────────────
    # The catalogue carries the mount orientation in the order code (2101-H-RC,
    # 2101-V-RC); Bourns' own table lists 2101-RC and says "Horizontal or vertical
    # mount" in the feature list. FAMILY_ONLY, because the string 2101-H-RC is not in
    # the document. Verbatim from bourns.com/.../2100_series.pdf.
    ("infix orientation letter dropped by the datasheet", "2101-H-RC",
     "https://bourns.com/docs/product-datasheets/2100_series.pdf?sfvrsn=7e01698c_7",
     "2100 Series  Special Features  L (uH)  L (uH)  DCR  Dim.  Dim.  Dim.   "
     "• Low core loss   2101-RC  10  10.8  6.3  0.006  0.34  0.77  0.053   "
     "• High current capacity   2102-RC  12  10.3  7.4  0.007  0.34  0.77  0.053   "
     "• Horizontal or vertical mount   2103-RC  15  7.7  10.2  0.012  0.33  0.76  0.042",
     "FAMILY_ONLY"),

    # ── ABT #450 rule 7: PRODUCT-LINE PREFIX ────────────────────────────────────
    # The "CD-" line prefix is in the title and the filename, never in the table.
    # Verbatim from bourns.com/.../cd-df4xxsl.pdf.
    ("product-line prefix absent from the table", "CD-DF408S",
     "https://www.bourns.com/docs/product-datasheets/cd-df4xxsl.pdf",
     "CD-DF4xxS(L) Series Surface Mount Bridge Rectifier Diode   "
     "Parameter  Symbol  Unit   "
     "DF406S  DF408S  DF410S  DF406SL  DF408SL  DF410SL   Maximum Repetitive",
     "FAMILY_ONLY"),
]


@pytest.mark.parametrize("name,ref,url,text,expected",
                         CASES, ids=[c[0] for c in CASES])
def test_verdict(name, ref, url, text, expected):
    got = check(text, [ref], url)[ref]
    assert got["verdict"] == expected, (
        f"{ref} against {url}: expected {expected}, got {got['verdict']} "
        f"(matched as {got['matchedAs']}); forms tried: "
        f"{[f for _, f in candidates(ref)][:8]}")


# ── ABT #452: a document nobody could read must never produce a verdict ──────────
#
# All four fixtures below are verbatim from the cached bytes, whitespace collapsed.

def test_html_shell_is_not_a_datasheet():
    """The fetch returned a web page. Verbatim from onsemi.com/pdf/datasheet/fca190n60f-d.pdf,
    whose <title> is "Technical Documentation | onsemi"."""
    text = ('<!doctype html> <!--suppress HtmlRequiredTitleElement --> <html lang="en"> <head> '
            '<meta charset="UTF-8">     <meta name="viewport" content="width=device-width, '
            'initial-scale=1">               <title>Technical Documentation | onsemi</title> '
            '<meta name="KEYWORDS"/>     <met')
    assert unreadable_reason(text) == (
        "not a datasheet: an HTML page was served where a document was expected")


def test_html_search_page_containing_the_part_number_is_still_not_a_datasheet():
    """This half of #452 was invisible until the detector existed.

    383 rows were scoring FOUND against vishay.com/search?searchText=<part> and
    ti.com/product/<part> — pages that contain the part number precisely because it is
    in the URL. Verbatim head of the Vishay search page for IRF530NPBF, which does
    contain the string "IRF530NPBF" further down.
    """
    text = ('<!DOCTYPE html><html><head><meta http-equiv="X-UA-Compatible" content="IE=Edge; '
            'IE=10; IE=9"/><meta charSet="utf-8"/><meta name="viewport" '
            'content="width=device-width, initial-scale=1"/>  IRF530NPBF')
    assert unreadable_reason(text) is not None
    # and it WOULD have matched, which is the point
    assert check(text, ["IRF530NPBF"], "")["IRF530NPBF"]["verdict"] == "FOUND"


def test_garbled_text_layer_is_not_evidence():
    """No ToUnicode CMap, so pdftotext emits mojibake. Verbatim from
    infineon.com/assets/row/public/documents/24/49/infineon-bsz105n04nsg-ds-en.pdf,
    characters 20,000-24,000 with whitespace collapsed. Over the whole 32,711-character
    document the alnum fraction is 0.364 against a threshold of 0.55."""
    text = (
        '$ 94R") =H\x07\x17 J) 9Hf5*f$ 9f\' 9H"[Z#YMc , R^4R"$ 9\x07\x17 ( V\x18 \x0e \x11 S @1A1=5C5A\x16 ( V (\'\' (\'\' '
        '/\' /\' -\' -\' ) MY *$ + " 4 *\x15 + +\' +\' \x11 S )\' )\' \x0e \x11 S \' \' ) * + , - . / \' +\' /\' ()\' '
        '(-\' \' 7@ *\' + " 4 *\x15 + + 5E \x0e @175 \x11 \x0e \x0f \x15 \x16 $) \x10 \x0f $ \x1b \x14 \x18 <,49\x08=: ?<.0 : 9\x08=>,>0 '
        '<0=4=>,9.0 % C; 2,>0 >3<0=3: 7/ @: 7>,20 \' 9H"[Z#4R"( V\x07\x17 $ 9\x18 \x0e \x1a \x17 ) =H\x18 . ) '
        '=H"_T#4R"( V\x07\x17 ) =H4) 9H\x17 $ 9\x18 \x10 U \x1a )\' + (, * % 4@!VU" FT G \x15\x14 \x05 \' 7@!ZO" *\' + (\' ) '
        "_d\\ , ( ' ' $-' $)' )' -' ('' (+' (/' $-' $)' )' -' ('' (+' (/' & Q *F\x17 + & Q *F\x17 + "
        '% C; .,;,.4>,9.0= \x1a : <A ,</ .3,<,.>0<4=>4.= : 1<0@0<=0 /4: /0 ! 4R") 9H\x07\x17 ) =H\x18 .\x17 '
        '+ \x18 & " I $ <4R") H9# @1A1=5C5A\x16 ( V (\'+ (\'\'\' * (\' 8U^^ (\'\' ')
    assert unreadable_reason(text) == (
        "unusable text layer: no ToUnicode CMap, extracted as mojibake")


def test_a_japanese_catalogue_is_not_garbage():
    """The guard the whole detector exists to not break. 1,210 citations ride on it.

    Verbatim from rubycon.co.jp/.../catalog-aluminum/USH.pdf, characters 5,100-6,300.
    Scored as English prose this page is BELOW the word threshold — 4.6 common words per
    1000 characters against a floor of 5 — so the word test alone would condemn it.

    A CORRECTION TO ABT #452, measured rather than assumed: it is the ALNUM fraction that
    saves the Rubycon catalogues, not the CJK clause. Their characters are real, so the
    ASCII-alnum share of non-whitespace runs 0.68-0.82 across all 143 Rubycon documents
    against a 0.55 threshold, and no window of any of them falls below it. The CJK clause
    never gets to decide. It is kept because it costs nothing and is the correct
    insurance, and the margins are asserted here so that redefining the alnum measure —
    counting whitespace in the denominator, say, which would condemn 14 % of ORDINARY
    datasheets — fails this test instead of silently discarding a vendor.
    """
    text = ('D±1 4±0.5 基板穴寸法図 MOUNTING HOLES ※但しφ35は3.5±0.5 For φ35, 3.5±0.5 '
            '基板自立形アルミニウム電解コンデンサ USH SNAP-IN ALUMINUM ELECTROLYTIC CAPACITORS ◆標準品一覧表 '
            'STANDARD SIZE Vdc 400 Cap φD (μF) φ22 φ25 φ30 φ35 150 22×25 1.37 180 22×30 '
            '1.58 220 22×35 1.81 25×25 1.66 270 22×40 2.06 25×30 1.92 30×25 1.88 330 '
            '22×45 2.32 25×35 2.20 30×30 2.18 390 22×50 2.54 25×40 2.46 30×30 2.28 35×25 '
            '2.13 470 22×60 2.93 25×45 2.73 30×35 2.59 35×30 2.41 560 25×55 3.19 30×40 '
            '2.90 3 ')
    assert unreadable_reason(text) is None
    nonspace = sum(1 for c in text if not c.isspace())
    words = sum(1 for w in ALPHA_WORD.findall(text) if w.lower() in COMMON_WORDS)
    assert 1000.0 * words / len(text) < MIN_WORDS_PER_1000, "the word test does condemn it"
    assert len(ASCII_ALNUM.findall(text)) / nonspace > MIN_ALNUM_FRACTION, "and this saves it"
    assert len(CJK_CHAR.findall(text)) / nonspace > 0, "with real Japanese present"


def test_near_empty_drawing_is_not_evidence():
    """126 characters, all of them a rotated "HISTORY" watermark. Verbatim from
    molex.com/.../734/73414/734140080_sd.pdf. Exactly two documents of the 14,107 have
    between 1 and 300 characters of text and both are this drawing."""
    text = ("       R Y\n    T O\n I S\nH\n\x0c" * 5)
    assert unreadable_reason(text) == "near-empty text layer (126 characters)"


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


def test_two_character_series_needs_the_whole_basename():
    """Two characters are evidence only when the filename is nothing else.

    AX.pdf says "this is the AX catalogue". A stray "AX" inside a longer filename says
    nothing, and a two-character token is short enough to occur inside a great many
    order codes, so the rule is keyed on the WHOLE basename or it is not applied.
    """
    ref = "6.3AX82MEFC5X7"
    assert series_from_url(
        "https://www.rubycon.co.jp/wp-content/uploads/catalog-aluminum/AX.pdf", ref) == ["AX"]
    for other in ("catalog-aluminum/AX_2021_rev3.pdf", "catalog-aluminum/rubycon-ax.pdf",
                  "catalog-aluminum/82.pdf"):
        assert series_from_url("https://www.rubycon.co.jp/wp-content/uploads/" + other,
                               ref) == [], other


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
#
# The OTHER known false negative that used to live here — the split ordering code — is
# now fixed and has moved into CASES as "split ordering code, mask header + row tail".
#
# THREE ROWS THE FOURTH ROUND DELIBERATELY LEAVES ABSENT, each with its own test above
# rather than a rule, because in every case the rule that would rescue it also rescues
# something it must not:
#   * Molex 734040230 — see test_split_code_halves_must_each_contain_a_letter
#   * Bourns SRP2512A-* — see test_the_rejected_longest_prefix_rule_stays_rejected
#   * KEMET R75MF215050H0J against KEM_F3121_R75H, whose family token "R75" is three
#     characters. Dropping the family-token floor to three would let short vendor stems
#     back in, for one row.


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


def test_footnote_wildcards_never_apply_to_the_squeezed_document():
    """The markers are a per-WORD template; run over squeezed text they match anything.

    The first draft normalised the whole document before matching and paired
    GAN033-650WSP with twelve consecutive dots. A word ends at a dot, a hyphen or a
    space, so a dot run can never become a template — and a short marked fragment like
    "5(1)" is below the literal-character floor.
    """
    assert wildcard_patterns("Note (1) ............ (2) see page 4 5(1) 2(3)") == {}
    # a real template is still recognised in the middle of ordinary table text
    pats = wildcard_patterns("6  39.0  F  T35(1)F396(3)006A(4)  1.9  6")
    assert [w for _, w in pats[14]] == ["T35(1)F396(3)006A(4)"]


def test_the_rejected_longest_prefix_rule_stays_rejected():
    """"The longest prefix of the code occurring anywhere in the document" must not exist.

    It rescues 62 rows and launders this one: bourns.com/.../srp2512a.pdf is served by
    the vendor under the wrong filename and contains ONLY SRP2510A parts, but the five
    characters "SRP25" occur in every row of it. Rules 1 and 2 both require a WHOLE
    TOKEN the document prints, which is what keeps this ABSENT.

    Verbatim from srp2512a.pdf.
    """
    text = ("SRP2510A Series - Shielded Power Inductors   "
            "SRP2510A-R22M  0.22  +-20  35  170  9  12.5  5.9  7.9  SELECTOR LIBRARY   "
            "SRP2510A-1R0M  1.0  +-20  35  94  45  54  3.0  3.5  (Temperature rise included)")
    url = "https://www.bourns.com/docs/Product-Datasheets/srp2512a.pdf"
    for ref in ("SRP2512A-1R0M", "SRP2512A-R47M"):
        assert check(text, [ref], url)[ref]["verdict"] == "ABSENT", ref


def test_split_code_halves_must_each_contain_a_letter():
    """A four-digit run is not a distinctive token, and one row proves it.

    Molex 734040230 cites 734040230_sd.pdf, whose text carries "73404" (the drawing's
    SERIES field) and "0230" — but that "0230" is part of "73403-0230", a DIFFERENT
    part number. Allowing digit-only halves rescues one row and launders that one.
    """
    text = ("73403-0230 NICKEL   PART NO. PLATING (BODY, OUTER CONTACT, REAR BODY, CRIMP TUBE)   "
            "SD-73404-023  PSD 001  C-SIZE  73404  SEE TABLE  GENERAL MARKET")
    url = ("https://www.molex.com/content/dam/molex/molex-dot-com/products/automated/"
           "en-us/salesdrawingpdf/734/73404/734040230_sd.pdf")
    assert check(text, ["734040230"], url)["734040230"]["verdict"] == "ABSENT"
    # and the halves are genuinely both there, so it is the letter guard doing the work
    tokens = DocIndex(text).tokens
    assert split_code_match("734040230", tokens) is None
    assert "73404" in tokens and "0230" in tokens
