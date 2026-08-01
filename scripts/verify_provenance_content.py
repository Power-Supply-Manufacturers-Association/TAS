#!/usr/bin/env python3
"""Phase 2: confirm the cited document actually mentions the part (ABT #391).

    python3 scripts/verify_provenance_content.py QUEUE.json PHASE1.jsonl OUT.jsonl
                                                 [--limit N] [--cache DIR]

Phase 1 (verify_provenance_urls.py) asks "is this URL real?". That is necessary and
not sufficient. A LIVE URL proves a document exists; it says nothing about whether
the part is in it. The Sumida row on ABT #385 is the case in point: its citation
resolved perfectly — to a BOURNS datasheet, for a SUMIDA part. Phase 1 would have
called that LIVE and been right, and the citation was still worthless.

So this pass downloads each LIVE document and looks for the part number in it. Only
a record whose part is FOUND has provenance that can honestly carry a retrievedDate.

MATCHING, and why it is deliberately generous. Vendors write part numbers in ways a
literal comparison misses:

  * packaging and tolerance suffixes the catalogue keeps and the table drops
    (SRP1265A-R56M vs the datasheet's row "SRP1265A-R56M" but AIAP-01-100K-T vs a
    table listing "AIAP-01-100K")
  * separators that differ (SER2918H-332KL / SER2918H 332KL)
  * PDFs whose text layer breaks a number across spaces

Each candidate form is tried in turn and the LOOSEST form that matched is recorded,
so a later reader can see HOW confident the match is rather than just that one
happened. A family-level match is reported as FAMILY_ONLY, never as FOUND: it means
the document covers the series but does not list this exact part, which is precisely
the AIAP-01 situation on ABT #386 where 51 corpus part numbers appear nowhere in the
datasheet they cite.

VERDICTS ARE NOT INTERCHANGEABLE, and the order the rules run in is what keeps them
apart. FOUND means the document prints this exact order code, in some arrangement:
the exact string, the string minus a packaging suffix, the code split across a mask
header and a table row (split_code_match), or the code with footnote markers standing
in for its variable letters (footnote_wildcard_match). Everything else is FAMILY_ONLY
— the document is about this part's series and does not print the code. Promoting a
family rule to FOUND would silently overstate how well sourced the corpus is, so the
family rules run only after every FOUND rule has failed, as pure fallbacks that can
turn an ABSENT into a FAMILY_ONLY and can never weaken a tighter verdict.

AND ONE VERDICT IS AN ADMISSION RATHER THAN A FINDING. DOC_UNREADABLE says the pass
never actually read the citation — an HTML shell where a PDF was expected, a text
layer with no ToUnicode CMap, an empty scan. Those must not be counted as ABSENT
(they say nothing about the part) and must not be counted as FOUND either, which is
what was happening to 383 rows matched against vendor SEARCH pages that contain the
part number because it is in the query string. See unreadable_reason().

WHAT THIS COSTS. Documents are cached by URL, not per record — 86,000 URLs back
318,391 records, so most downloads serve many parts. Size is capped: a datasheet
beyond MAX_BYTES is fetched only as far as the cap, since a part number in a 40 MB
catalogue is not the kind of citation we want anyway. Nothing is re-downloaded
between runs, and results append as they are produced, so this is interruptible.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path
from urllib.parse import urlparse

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
MAX_PER_HOST = 2
HOST_DELAY = 0.4
TIMEOUT = 60
MAX_BYTES = 25 * 1024 * 1024

_host_lock = defaultdict(threading.Lock)
_host_last = defaultdict(float)
_write_lock = threading.Lock()


def host_of(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def download(url, cache: Path):
    cache.mkdir(parents=True, exist_ok=True)
    import hashlib
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    p = cache / key
    if p.exists():
        return p, "cached"
    h = host_of(url)
    with _host_lock[h]:
        wait = HOST_DELAY - (time.monotonic() - _host_last[h])
        if wait > 0:
            time.sleep(wait)
        _host_last[h] = time.monotonic()
    try:
        with requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                          stream=True) as r:
            if r.status_code >= 400:
                return None, f"HTTP {r.status_code}"
            buf = bytearray()
            for chunk in r.iter_content(65536):
                buf += chunk
                if len(buf) >= MAX_BYTES:
                    break
            p.write_bytes(bytes(buf))
            return p, "fetched"
    except Exception as e:                                        # noqa: BLE001
        return None, type(e).__name__


def text_of(path: Path):
    head = path.read_bytes()[:5]
    if head.startswith(b"%PDF"):
        r = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                           capture_output=True, text=True, errors="replace")
        return r.stdout
    try:
        return path.read_text(errors="replace")
    except Exception:                                             # noqa: BLE001
        return ""


# ── Is this even a datasheet? (ABT #452) ────────────────────────────────────────
#
# 150 of the ABSENT verdicts were passed on documents nobody could have read: HTML
# shells where a PDF was expected (every na.industrial.panasonic.com/file-download/*,
# onsemi's "Technical Documentation | onsemi" page, murata's pdfdownloadapi React app),
# and PDFs whose text layer has no ToUnicode CMap so pdftotext emits '! "#$ % " &' ' ('
# — essentially every Infineon document in the pass. An ABSENT on those says nothing
# about the part; it says we never read the citation.
#
# It cuts the other way too, and that half was invisible until this detector was
# written: 383 rows were scoring FOUND against vishay.com/search?searchText=<part> and
# ti.com/product/<part>, pages that contain the part number because it is in the URL.
HTML_MARKER = re.compile(r"<html|<!doctype", re.I)
MIN_READABLE_CHARS = 300
MIN_WORDS_PER_1000 = 5
MIN_ALNUM_FRACTION = 0.55
MAX_CJK_FRACTION = 0.05

# Ordinary English that any real datasheet uses, in enough quantity to be a signal.
# The list only has to separate prose from mojibake, so it is deliberately dull.
COMMON_WORDS = frozenset("""
the and for with from that this these those are was were will shall may can must not
all any each other than when where which while into over under between maximum minimum
typical rated voltage current temperature power resistance capacitance inductance
frequency series type size code note notes page product products data sheet datasheet
part number package dimensions specifications features applications ordering
information general characteristics conditions tolerance operating storage test testing
value values unit units please see reference rohs compliant lead free typ min max
description available design designed device devices used using use time life
""".split())
ALPHA_WORD = re.compile(r"[A-Za-z]{2,}")
ASCII_ALNUM = re.compile(r"[A-Za-z0-9]")
# Kana, CJK punctuation, and the ideographic planes. Full-width Latin (U+FF01-FF5E) is
# deliberately NOT here: it is Latin text and NFKC folds it, so counting it as CJK would
# let a Rubycon cover page masquerade as Japanese.
CJK_CHAR = re.compile(r"[　-ヿ㐀-䶿一-鿿豈-﫿]")


def unreadable_reason(text):
    """Why this document cannot be used as evidence about any part — or None.

    NOT ONE OF THE THREE TESTS IS SAFE ALONE, which is why they are ANDed. Rubycon
    publishes bilingual Japanese catalogues, and scored as English prose they look like
    noise: the weakest, ZT.pdf, has 2.1 common words per 1000 characters against a
    threshold of 5. The word test alone would throw away 1,210 good citations.

    WHICH TEST ACTUALLY SAVES THEM IS WORTH STATING, because ABT #452 names the wrong
    one. It is the ALNUM fraction, not the CJK clause: Rubycon's characters are real, so
    their ASCII-alnum share of non-whitespace runs 0.68-0.82 across all 143 of their
    documents, against 0.33-0.45 for the Infineon files with no ToUnicode CMap, and no
    window of any Rubycon document falls below 0.55. The CJK clause never gets to
    decide. It is kept because it costs nothing and is the right insurance if the alnum
    measure is ever redefined — and the definition matters enormously: counting
    WHITESPACE in the denominator instead of excluding it condemns 14 % of ordinary
    datasheets, pdftotext -layout output being mostly spaces. That margin is asserted in
    tests/test_citation_matcher.py so the redefinition fails a test rather than a vendor.

    The measured gap the 0.55 alnum threshold sits in is worth recording, because a
    partially garbled document is still usable: Infineon-IPP075N15N3-DS-v02_06-en.pdf
    scores 0.61 and its body is mojibake, but its header still prints "IPP075N15N3 G",
    so it can and does answer for its part. Fully garbled scores 0.33-0.45; partially
    garbled but usable, 0.59-0.62; everything else, 0.65 and up.

    The MIN_READABLE_CHARS floor is an addition beyond ABT #452's stated rule, which its
    "2 near-empty" line asks for but its thresholds do not reach. Across all 14,107 live
    documents exactly two have between 1 and 300 characters of text, and both are the
    same Molex sales drawing whose only text layer is a rotated "HISTORY" watermark.
    """
    if not text.strip():
        return "no extractable text (scanned image?)"
    if HTML_MARKER.search(text[:600]):
        return "not a datasheet: an HTML page was served where a document was expected"
    if len(text.strip()) < MIN_READABLE_CHARS:
        return f"near-empty text layer ({len(text.strip())} characters)"
    nonspace = sum(1 for c in text if not c.isspace()) or 1
    words = sum(1 for w in ALPHA_WORD.findall(text) if w.lower() in COMMON_WORDS)
    if (1000.0 * words / len(text) < MIN_WORDS_PER_1000
            and len(ASCII_ALNUM.findall(text)) / nonspace < MIN_ALNUM_FRACTION
            and len(CJK_CHAR.findall(text)) / nonspace < MAX_CJK_FRACTION):
        return "unusable text layer: no ToUnicode CMap, extracted as mojibake"
    return None


def normalise(s):
    """Upper-case, alphanumerics only — after folding compatibility characters.

    The NFKC step is not cosmetic. Rubycon's ZLJ.pdf titles itself "ＺＬＪ" in U+FF2A
    &c., and Panasonic's AOA0000C304.pdf spells its part-number decoder out one
    character per column as "E R J 3 R B Ｄ 1 0 0 2 V" — with a FULL-WIDTH Ｄ. Without
    folding, [^A-Z0-9] deletes those characters instead of matching them, so ZLJ.pdf
    never names the ZLJ series and ERJ3RBD1002V is reported absent from the document
    that prints it, over one character. 278 rows plus that exact miss.
    """
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKC", s).upper())


TOKEN = re.compile(r"[A-Za-z0-9]+")
HAS_ALPHA = re.compile(r"[A-Z]")
MIN_SPLIT_PART = 4
MIN_FAMILY_TOKEN = 4

# An ordering-code MASK: the invariant stem of a table's codes, printed with the
# varying tail replaced by a dot run — "ORDERING CODE MAL2013.......".
MASK_RUN = r"[ ]{0,2}[.·…_]{2,}"
MASK_STEM = re.compile(r"([A-Za-z0-9]+)" + MASK_RUN)
_MASK_AFTER = re.compile(r"^" + MASK_RUN)


# A FOOTNOTE MARKER inside an ordering code: KEMET prints "T35(1)A475(3)003A(4)" and
# "L1X(3)505105(2)(4)12", where each (n) points at a table of the letters that may go
# there. Each marker therefore stands for exactly ONE character of the real code.
FOOTNOTE_WORD = re.compile(r"[A-Za-z0-9()]+")
FOOTNOTE_MARK = re.compile(r"\((\d)\)")
MIN_WILDCARD_LITERALS = 6


def wildcard_patterns(text):
    """Ordering codes the document prints with footnote markers standing in for letters.

    APPLIED PER DELIMITED WORD, NEVER TO THE SQUEEZED DOCUMENT, and that is not a
    detail: a first attempt normalised the whole text first and matched GAN033-650WSP
    against twelve consecutive dots. A word is a run of alphanumerics and parentheses,
    so a dot, a hyphen or a space ends it — "0.120 (3.05)" can never become a template.

    Guards: at least MIN_WILDCARD_LITERALS literal characters, and wildcards may be no
    more than a third of the pattern, so "(1)(2)(3)" or "5(1)" cannot match anything.
    Patterns are indexed by total length because the match is anchored at both ends —
    the template describes the WHOLE code, not a prefix of it.
    """
    by_len = defaultdict(list)
    for word in FOOTNOTE_WORD.findall(text):
        if not FOOTNOTE_MARK.search(word):
            continue
        chunks = [normalise(c) for c in FOOTNOTE_MARK.split(word)[0::2]]
        wilds = len(chunks) - 1
        literals = sum(len(c) for c in chunks)
        if wilds < 1 or literals < MIN_WILDCARD_LITERALS:
            continue
        total = literals + wilds
        if wilds * 3 > total:
            continue
        by_len[total].append((re.compile("[A-Z0-9]".join(chunks) + "$"), word))
    return by_len


def footnote_wildcard_match(flat_ref, idx):
    """The exact code, printed with its variable letters replaced by footnote markers.

    KEMET's T35X datasheet lists "T35(1)F396(3)006A(4)" and never T356F396M006AS; the
    markers resolve to 6, M and S from the three tables beside it. All 873 rows of this
    shape match, and 65/65 of the C1031 references. FOUND, not FAMILY_ONLY: every
    character of the order code is accounted for by the document.
    """
    for rx, word in idx.wildcards.get(len(flat_ref), ()):
        if rx.match(flat_ref):
            return word
    return None


class DocIndex:
    """What the document PRINTS, in the four forms the rules below need.

    Built once per document because check() runs it against every part that cites it —
    industrial.panasonic.com/.../AOA0000C304.pdf alone answers for 6,774 of them.

    tokens      every alphanumeric run, upper-cased
    free        tokens seen at least once NOT immediately followed by a mask dot run
    mask_stems  tokens that introduce an ordering-code mask ("MAL2013.......")
    wildcards   footnote-marker templates, indexed by the code length they describe
    """

    __slots__ = ("flat", "tokens", "free", "mask_stems", "wildcards")

    def __init__(self, text):
        # Fold compatibility characters ONCE, here, so the tokeniser, the mask scanner
        # and the wildcard scanner all see the same ASCII the reference is reduced to.
        text = unicodedata.normalize("NFKC", text)
        self.flat = normalise(text)
        self.tokens = set()
        self.free = set()
        for m in TOKEN.finditer(text):
            w = m.group(0).upper()
            self.tokens.add(w)
            if not _MASK_AFTER.match(text[m.end():m.end() + 4]):
                self.free.add(w)
        self.mask_stems = {m.upper() for m in MASK_STEM.findall(text)}
        self.wildcards = wildcard_patterns(text)


def family_token_match(flat_ref, idx):
    """A family name the DOCUMENT ITSELF prints, of which this code is an extension.

    Panasonic's ERJ datasheet prints "ERJ1GJ" free-standing — in the taping table, in
    the note "for the automotive application, please use ERJ1GJ as 0201 inch size", and
    on its own line above the ratings row — but never ERJ1GJF1000C. The structural stem
    rule in candidates() cannot reach it: ERJ1GJF1000C tokenises as ERJ|1|GJF|1000|C, so
    ERJ1G and ERJ1GJ are never offered and ERJ1 is below the floor. 11,133 rows are that
    shape, Panasonic 10,672 of them.

    The token must be the DOCUMENT'S OWN, never a prefix computed from the part number.
    That is the whole difference between this rule and the one recorded as rejected
    below: a computed prefix asserts a family the document never claimed.

    TWO VETOES, both load-bearing, both aimed at "MAL2":

      * a token that is only ever a MASK STEM is not a family name. Vishay's 030031as.pdf
        prints "MAL2.........." and nothing else — MAL2 occurs there exclusively as the
        stem of a fill-in-the-blanks pattern, and it is the prefix of every Vishay
        aluminium electrolytic ever made.
      * a token that a LONGER mask stem extends is a truncation of the document's own
        stem. 094pmesi.pdf writes "Ordering code: MAL2 094 46331 E3" — MAL2 free-standing,
        because pdftotext kept the spaces — but its tables are headed "MAL2094.......",
        and that mask is the document saying the fixed part of its codes is MAL2094.

    Without them MAL2 alone hands every Vishay electrolytic every Vishay datasheet:
    444 rescues across the ABSENT set of which 401 are wrong-part citations that the
    classification had already identified as citing a SIBLING SERIES. With them, this
    rule rescues 11,132 rows and not one row of any WRONG_PART_CITATION subclass.

    A pure-digit token is rejected for the reason given in split_code_match: four digits
    are not a distinctive token in a document full of dimensions, dates and part counts.
    """
    best = None
    for tok in idx.tokens:
        if not (MIN_FAMILY_TOKEN <= len(tok) < len(flat_ref)):
            continue
        if not flat_ref.startswith(tok):
            continue
        if not HAS_ALPHA.search(tok):
            continue
        if tok not in idx.free:
            continue
        if any(m != tok and m.startswith(tok) for m in idx.mask_stems):
            continue
        if best is None or len(tok) > len(best):
            best = tok
    return best


def infix_variant_match(ref, flat):
    """The code with ONE short infix letter group dropped, listed verbatim.

    Bourns' 2100 Series datasheet tabulates 2101-RC, 2102-RC, 2103-RC and notes
    "Horizontal or vertical mount" beside a drawing labelled Vertical / Horizontal; the
    catalogue carries the orientation in the order code as 2101-H-RC and 2101-V-RC. The
    document lists the part in every respect except the letter that says which way up it
    is. 121 rows, all Bourns.

    FAMILY_ONLY, NOT FOUND. The orderable code as written is not in the document — a
    reader looking for "2101-H-RC" will not find that string — so this is evidence that
    the document covers the part, not that it prints it.

    The group must be INFIX: separators on both sides, so neither the leading group
    (which carries the series) nor the trailing one (already handled by the packaging
    suffix rule) can be dropped, and only 1-2 letters, so no digit or meaningful field
    can vanish.
    """
    groups = re.split(r"[-_ ]+", ref)
    if len(groups) < 3:
        return None
    for i in range(1, len(groups) - 1):
        g = groups[i]
        if not (1 <= len(g) <= 2 and g.isalpha()):
            continue
        cand = "-".join(groups[:i] + groups[i + 1:])
        if len(normalise(cand)) >= 5 and normalise(cand) in flat:
            return g, cand
    return None


PRODUCT_LINE = re.compile(r"^([A-Za-z]{2,3})[-_ ](.+)$")


def product_line_prefix_match(ref, doc_tokens):
    """The code without its leading product-line prefix, printed as a whole token.

    Bourns' cd-df4xxsl.pdf is titled "CD-DF4xxS(L) Series Surface Mount Bridge Rectifier
    Diode" and then heads its parameter columns DF406S / DF408S / DF410S / DF406SL /
    DF408SL / DF410SL — the "CD-" line prefix appears in the title and the filename but
    not in the table. 14 rows, all Bourns.

    The remainder must be a WHOLE TOKEN of the document and carry both a letter and a
    digit, which is what stops this from becoming the rejected longest-prefix rule with
    the ends swapped.
    """
    m = PRODUCT_LINE.match(ref)
    if not m:
        return None
    rest = normalise(m.group(2))
    if len(rest) < 5 or not (HAS_ALPHA.search(rest) and re.search(r"\d", rest)):
        return None
    return (m.group(1), rest) if rest in doc_tokens else None


# A RULE TRIED AND REJECTED, recorded so it is not proposed again: "the longest PREFIX
# of the code that occurs anywhere in the document". It rescues 62 rows, and it also
# rescues SRP2512A-1R0M against bourns.com/.../srp2512a.pdf on the strength of the five
# characters "SRP25" — a document whose every part is an SRP2510A, served by the vendor
# under the wrong filename (ABT #451). A substring is not a claim; only a whole token
# the document actually prints is, which is why family_token_match() requires one.


def split_code_match(flat_ref, doc_tokens):
    """The exact code, printed as two separate tokens of the document.

    Vishay prints the ordering-code MASK in the column header and only the tail in
    each row: docs/28313/013rlc.pdf carries "ORDERING CODE MAL2013......." above a
    table whose rows read "55101E3  5.0  65101E3  5.0  35101E3  5.0", and the joined
    MAL201365101E3 never occurs anywhere in the file. 6,181 corpus rows are that
    shape and every one of them was reported ABSENT against its own datasheet.

    Both halves must be WHOLE tokens of the document, so the join is evidence rather
    than coincidence: "MAL2013" comes from the mask (the dots are delimiters, so the
    stem tokenises out on its own) and "65101E3" from the row.

    BOTH HALVES MUST CONTAIN A LETTER, and that guard is load-bearing. The one row
    this rejects is Molex 734040230 against 734040230_sd.pdf, which would otherwise
    be rescued by "73404" (the drawing's SERIES field) plus "0230" — and the "0230"
    in that drawing belongs to 73403-0230, a DIFFERENT part. A four-digit run is not
    a distinctive token in a document full of dimensions and dates; a token with a
    letter in it is. Same failure mode as the rejected longest-prefix rule below.
    """
    for i in range(MIN_SPLIT_PART, len(flat_ref) - MIN_SPLIT_PART + 1):
        head, tail = flat_ref[:i], flat_ref[i:]
        if not (HAS_ALPHA.search(head) and HAS_ALPHA.search(tail)):
            continue
        if head in doc_tokens and tail in doc_tokens:
            return head, tail
    return None


# Suffixes vendors routinely omit from the part table: packaging, tape-and-reel,
# RoHS and tolerance letters. Stripped only when looking for a LOOSER match, and
# the level that matched is always reported.
SUFFIX = re.compile(r"[-_ ]?(T|TR|RC|LF|L|E|B|CT|ND|G|GT|P|PB|Y)$", re.I)


GENERIC = {"KEM", "PDF", "DATA", "DATASHEET", "CAT", "CATALOG", "CATALOGUE", "SERIES",
           "PROD", "PRODUCT", "SPEC", "TYPE", "IND", "CAP", "RES", "HV", "LV", "SMD",
           "RAD", "AXIAL", "LEAD", "ALUMINUM", "ALUMINIUM", "FILE", "ASSET", "DOC",
           "EN", "JP", "US", "REV", "NEW", "ALL", "GEN"}
SERIES_TOKEN = re.compile(r"[A-Za-z0-9]{2,}")


def series_from_url(url, ref):
    """Series names the citation's own filename claims, that occur in this part number.

    Rubycon's 4MS522MEFC4X5 is 4 V / series MS5 / 22 uF / M / 4x5 mm, and it cites
    catalog-aluminum/MS5.pdf. The catalogue is the right one and prints "MS5" all over
    itself, but never the concatenated order code — and no amount of trimming from the
    RIGHT reaches MS5, because the series sits in the middle behind a voltage prefix.
    Same for KEMET T493X106K050BH6110 -> KEM_T2007_T493.

    The filename is evidence, so use it: a token of the URL that is also a substring of
    the part number ties the document to the part in a way an unrelated document does
    not survive. This is exactly the discrimination the pass exists for — the ABT #385
    Sumida row cites a BOURNS datasheet, and no Bourns filename token is a substring of
    a Sumida order code. Generic catalogue words are excluded so "IND" or "HV" in a
    filename cannot rubber-stamp anything.
    """
    tail = urlparse(url).path.rsplit("/", 1)[-1]
    tail = re.sub(r"\.(pdf|html?|ashx|aspx)$", "", tail, flags=re.I)
    flat_ref = normalise(ref)
    whole = normalise(tail)
    out = []
    for tok in SERIES_TOKEN.findall(tail):
        up = tok.upper()
        if up in GENERIC:
            continue
        if len(up) < 3 and not (up.isalpha() and up == whole):
            # Two characters are allowed ONLY when the entire basename is that token.
            # Rubycon publishes its catalogues as PK.pdf, PX.pdf, ZL.pdf, WA.pdf,
            # ML.pdf, AX.pdf, NA.pdf, ZT.pdf, MS.pdf, NS.pdf — the filename IS the
            # series, and 850 rows died on the three-character floor. A two-character
            # token appearing among others in a longer filename is not that evidence,
            # it is a fragment, so the test is on the WHOLE basename; and it must be
            # alphabetic, so a "10.pdf" cannot match every code containing a 10.
            continue
        if normalise(up) and normalise(up) in flat_ref:
            out.append(tok)
    return out


def candidates(ref):
    """Progressively looser forms of a part number, most exact first.

    The family form MUST NOT depend on a separator. A first version derived it as
    re.split(r"[-_]", ref)[0], which silently produced NO family form at all for any
    vendor whose codes have no hyphen — Panasonic EEUFC1V102, Yageo CC0402JRN1A100,
    Rubycon 35ZLH1000MEFC12.5X20. Their series datasheets legitimately list the
    family rather than every order code, so those rows could only ever come back
    ABSENT: 89,726 of them for Panasonic alone, against 9,427 FOUND, while
    separator-bearing vendors behaved (Würth 4,618 FOUND / 27 ABSENT). The verdict
    was measuring my regex, not the citations.

    So the family is derived at TOKEN BOUNDARIES. A part number is an interleaving of
    letter runs and digit runs, and a series name is a whole number of those runs — never
    half of one. RC0100FR-073K3L splits as RC | 0100 | FR | - | 073 | K | 3 | L, so the
    candidate families are RC0100, RC0100FR, RC0100FR-073 ... and Yageo's "RC_L series,
    sizes 0075/0100/0402/..." datasheet does contain RC0100.

    An earlier version cut at arbitrary character positions instead, keeping at least half
    the code and 6 characters. That floor was there to stop "CC0402JRN1A100" degrading to
    "CC040" — a fragment that matches essentially every Yageo document — but for a long
    code it also made the true family unreachable: RC0100FR-073K3L could get no shorter
    than RC0100FR, which appears nowhere, so 11,307 rows citing their own series datasheet
    came back ABSENT. Token boundaries solve both: "CC040" is not a boundary and is never
    offered, while "RC0100" and "CC0402" are.

    A stem must also be at least 5 characters and contain both a letter and a digit, so a
    bare vendor prefix ("RC", "CL") cannot match on its own.
    """
    forms = [("exact", ref)]
    stripped = SUFFIX.sub("", ref)
    if stripped != ref:
        forms.append(("suffix-stripped", stripped))
    seen = {normalise(ref), normalise(stripped)}
    stems = []
    acc = ""
    for tok in re.findall(r"[A-Za-z]+|\d+|[^A-Za-z0-9]+", ref):
        acc += tok
        cand = acc.strip()
        if len(cand) < 5 or cand == ref:
            continue
        if not (re.search(r"[A-Za-z]", cand) and re.search(r"\d", cand)):
            continue
        stems.append(cand)
    # longest first, so the tightest family that matches is the one reported
    for cand in reversed(stems):
        if normalise(cand) in seen:
            continue
        seen.add(normalise(cand))
        forms.append(("family", cand))
    return forms


def check(text, refs, url=""):
    """For each ref, the tightest form found in this document.

    The minimum-length guard applies ONLY to derived family stems. A first version
    applied it to every form including the exact part number, which auto-ABSENTed every
    genuinely short code: Yageo S1A, S1B and S1G came back ABSENT against the S1 series
    datasheet that prints all three on page one. A vendor's real part number is never
    "too short to count" — only a stem I invented by truncation can be.
    """
    idx = DocIndex(text)
    flat = idx.flat
    out = {}
    for ref in refs:
        verdict, how = "ABSENT", None
        for level, form in candidates(ref):
            if level == "family" and len(normalise(form)) < 4:
                continue
            if not normalise(form):
                continue
            if normalise(form) in flat:
                verdict = "FAMILY_ONLY" if level == "family" else "FOUND"
                how = level
                break
            if level in ("exact", "suffix-stripped"):
                # The exact code, printed split across a mask header and a table row.
                # Tried before any family stem, because it establishes the stronger
                # claim: the part itself is in the document, not merely its series.
                split = split_code_match(normalise(form), idx.tokens)
                if split:
                    verdict, how = "FOUND", "split-code:%s+%s" % split
                    break
                marker = footnote_wildcard_match(normalise(form), idx)
                if marker:
                    verdict, how = "FOUND", f"footnote-wildcard:{marker}"
                    break
        if verdict == "ABSENT":
            # Family rules last, and only as a fallback, so they can only ever turn an
            # ABSENT into a FAMILY_ONLY — never weaken a verdict something tighter won.
            variant = infix_variant_match(ref, flat)
            if variant:
                verdict, how = "FAMILY_ONLY", "infix-variant-dropped:%s->%s" % variant
        if verdict == "ABSENT":
            line = product_line_prefix_match(ref, idx.tokens)
            if line:
                verdict, how = "FAMILY_ONLY", "product-line-prefix-dropped:%s->%s" % line
        if verdict == "ABSENT":
            fam = family_token_match(normalise(ref), idx)
            if fam:
                verdict, how = "FAMILY_ONLY", f"family-token-in-document:{fam}"
        if verdict == "ABSENT":
            for tok in series_from_url(url, ref):
                if normalise(tok) in flat:
                    verdict, how = "FAMILY_ONLY", f"series-named-by-url:{tok}"
                    break
        out[ref] = {"verdict": verdict, "matchedAs": how}
    return out


def main(argv):
    queue = json.loads(Path(argv[0]).read_text())
    phase1 = [json.loads(l) for l in Path(argv[1]).open(encoding="utf-8")]
    out_path = Path(argv[2])
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    cache = Path(argv[argv.index("--cache") + 1]) if "--cache" in argv \
        else Path("/tmp/tas_provenance_docs")

    live = [r["url"] for r in phase1 if r.get("verdict") == "LIVE"]
    done = set()
    if out_path.exists():
        for line in out_path.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["url"])
            except Exception:
                pass
    todo = [u for u in live if u not in done]
    if limit:
        todo = todo[:limit]
    buckets = defaultdict(list)
    for u in todo:
        buckets[host_of(u)].append(u)
    todo = [u for g in zip_longest(*buckets.values()) for u in g if u]
    print(f"{len(live)} LIVE URLs, {len(done)} already checked, {len(todo)} to fetch")

    counts = Counter()
    unreadable_docs = Counter()
    fh = out_path.open("a", encoding="utf-8")

    def run(url):
        path, how = download(url, cache)
        # queue entries are [catalogue_file, part_number] — the PART NUMBER is what
        # has to appear in the document; the catalogue name obviously never will.
        refs = [pair[1] for pair in queue.get(url, []) if len(pair) > 1]
        rec = {"url": url, "host": host_of(url), "refs": len(refs)}
        if not path:
            rec["error"] = how
            rec["results"] = {}
        else:
            txt = text_of(path)
            rec["textChars"] = len(txt)
            reason = unreadable_reason(txt)
            rec["results"] = {} if reason else check(txt, refs, url)
            if reason:
                rec["error"] = reason
        with _write_lock:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            for v in rec["results"].values():
                counts[v["verdict"]] += 1
            if rec.get("error"):
                # counted PER CITATION, like every other verdict: what the run is
                # reporting is how many CITATIONS it could and could not decide, and a
                # single unreadable catalogue can stand behind several hundred of them.
                counts["DOC_UNREADABLE"] += max(1, len(refs))
                unreadable_docs[rec["error"]] += 1
            n = sum(counts.values())
            if n % 100 == 0:
                print(f"  {n}  " + "  ".join(f"{k}:{v}" for k, v in counts.most_common()))

    with ThreadPoolExecutor(max_workers=min(24, MAX_PER_HOST * max(1, len(buckets)))) as ex:
        list(ex.map(run, todo))
    fh.close()
    print("\n" + "  ".join(f"{k}:{v}" for k, v in counts.most_common()))
    print(f"{sum(unreadable_docs.values())} documents could not be read:")
    for reason, n in unreadable_docs.most_common():
        print(f"  {n:6d}  {reason}")
    print(f"-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
