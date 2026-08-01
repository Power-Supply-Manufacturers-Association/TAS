#!/usr/bin/env python3
"""Re-point citations that resolve to a real datasheet for a DIFFERENT part (ABT #451).

    python3 scripts/fix_wrong_part_citations.py [--dry-run] [--cache DIR]

ABT #391 phase 2 downloaded every cited document and looked for the part in it. Of the
23,038 that came back ABSENT, 3,121 were classified WRONG_PART_CITATION: the URL
resolves, the bytes are a genuine datasheet, and the part is simply not in it. This
script repairs the 3,018 of those that are NOT magnetics and that a document could be
found for, and reports the 20 it deliberately refuses to touch.

Nothing here is repaired from a rule of thumb. Every URL this script writes was fetched
and the part number was found in its text BEFORE the write, and the same check runs
again on every execution — the tables below are candidate documents, not evidence in
themselves. A row whose candidate document does not name it is left alone and reported.


A. VISHAY ALUMINIUM ELECTROLYTICS - 2,969 rows, 38 series.

The defect has a machine signature: the record's OWN `series` field disagrees with the
document it cites, and the document its series names is ALREADY in the corpus, cited
correctly by other rows of the same series.

    MAL214865101E3  series '148 RUS'        cited 28403/146cti.pdf   -> 28315/148rus.pdf
    MAL203023151E3  series '030/031 AS'     cited 28312/036rsp.pdf   -> 28327/030031as.pdf
    MAL205657151E3  series '056/057 PSM-SI' cited 28338/050-052ped-pw.pdf -> 28340/056057psmsi.pdf

Of the 51,805 Vishay capacitor rows, 9,578 are MAL2 ordering codes across 77 named
series (189 carry no series and are out of scope - the selection rule needs one).
For each of the 38 series that has mismatched rows there is exactly ONE candidate
document that contains every one of that series' mismatched part numbers, and it is
always the document whose filename is the series name. No series needed a judgement
call; where two clean candidates existed (042/043 AHH-ELB and AMH-ELB, which some rows
cite to the neighbouring 041/042/043 ASH sheet) the ambiguity was resolved by reading
both documents, not by preferring a filename.

VISHAY'S SPLIT ORDERING CODE is why a literal search fails and why this check is not a
substring test. Vishay prints the ordering-code MASK in the column header and only the
TAIL in each table row, and never the joined code:

    ORDERING CODE
    MAL2148.......                            <- header
       2200  10 x 16  720 ... MF1  53222E3    <- row

so 148rus.pdf contains "MAL2148" and "65101E3" as separate tokens and never
"MAL214865101E3". The mask length is NOT constant and the dot count is NOT the tail
length: alldin.pdf writes "MAL2.......", yet prints a ten-character tail
("13214471E3"); 184cpns.pdf writes "MAL2184..." and prints seven ("97251E3");
140rtm.pdf writes "MAL2140 ....." with a space. So the mask literal is read FROM THE
DOCUMENT (every "MAL2<digits>" that precedes a run of dots), the tail is whatever the
part number has after that literal, and the tail must appear as a standalone token.
Counting dots would have rejected 409 correct rows.

CORROBORATION, because a matching pair of tokens is a weaker claim than it looks: for
each row the line carrying the tail was also checked against the capacitance the record
stores. 2,969 of 2,969 agree - the row that carries "65101E3" carries the record's own
capacitance in the same table line. That is an independent field agreeing with the
document, not another way of reading the part number.


B. VISHAY / ST / ONSEMI / INFINEON SEMICONDUCTORS AND ONE RESISTOR - 49 rows.

The ABT #385 archetype: the URL's own filename names a different part (SQ3427CEV cites
sq3419cev.pdf, SiJ4819DP cites sqj185elp.pdf, SUP40010EL cites sum40010el.pdf).

Vishay's own search index says where this came from, and it is not our error. For
SQ3427CEV it returns two hits: a MATERIAL record, mat_SQ3427CEV-T1_GE3, whose doc_no is
62368 - the SQ3419CEV datasheet - and a PRODUCT record, pcor_62369, whose product name
is SQ3427CEV. The importer took the material row. So the fix is to take the product row:
the hit whose p1001 equals the part number. SiHP050N60E and SiHG050N60E are the same
fault caught in the act, their material rows pointing at each other's documents.

Each resolved doc_no was fetched through vishay.com/doc?<n> and the part number
confirmed in the text - including the four wildcard "Series" part numbers
(VS-301CNQ...PbF Series, RCG0201...C e3), whose full wildcard form Vishay prints as the
document title.


C. WHAT IS DELIBERATELY NOT DONE.

  1. MAGNETICS. 70 different-manufacturer and 8 vendor-serves-wrong-file rows live in
     data/magnetics.ndjson, which another session owns. Not touched, not read.

  2. 20 ROWS ARE REFUSED, with a reason each (WITHHELD below). Eighteen of them are
     parts their own manufacturer's catalogue does not contain, so there is no document
     to cite: Infineon's Coveo index returns zero hits for IPB090N10N3, IPD50R380C6,
     IPP50R99C7, IPI40CN05S4 and IKM120R060M1H while returning the part page for
     IPB090N06N3 in the same run; TI's site search returns "Results 1-0 of 0" for
     CSD86320Q5D, whose cited SLPS223F is the datasheet for CSD86350Q5D; onsemi's
     datasheet endpoint serves a PDF for FPF2G120BF07AS and NVMFD5C466NL but redirects
     NVMFD7N06CL and NTHS4H080N065M2C to a not-found page; Vishay's catalogue has
     SS32-SS36 and SS5P3-SS5P10 but no SS54 rectifier; Nexperia serves GAN041-650WSB
     and GAN063-650WSA but not GAN033-650WSP, and Wolfspeed does not use that naming;
     and Yageo's complete resource library has exactly one anti-sulfurated ARRAY
     datasheet, PYU-AF122_124_162_164, whose size codes are 12 and 16 only - there is
     no AF102/AF104 document and no AF102*/AF104* key in Yageo's own part index.

     Writing a "closest match" datasheet onto any of these would attach a real document
     to a part number that may not be real, which is worse than the citation we have.
     They are reported so the PART NUMBERS can be investigated separately.

  3. THE TWO YAGEO VARISTORS ARE NOT A DEFECT AND ARE LEFT ALONE. 561KN20-P12.5 and
     751KN20-P12.5 cite Yageo's TMOV 20M(E,N) datasheet, and that IS their datasheet:
     its part-number code reads Varistor Voltage / Tolerance K / Type M,E,N / Element
     Diameter 20 / Pitch "-P12.5: 12.5mm (N type)", and its table lists "561KM(E,N)20"
     and "751KM(E,N)20" - one printed row standing for the M, E and N variants. This is
     the decoder-only false negative recorded on ABT #391, not a wrong-part citation,
     and correcting it is a matcher job, not a data job.

  4. NOTHING ELSE IN THE ROW CHANGES. Only manufacturerInfo.datasheetUrl and the
     sourceUrl of the provenance entry that pointed at it. The stored VALUES still come
     from the vendor's parametric table and were NOT re-derived from the datasheet, so
     the provenance keeps saying manufacturerParametric and says so explicitly. A
     corrected citation is not a verified record.

  5. TEN MORE ROWS ARE REPORTED, NOT FIXED. Six rows of series '042 AHH-ELB, 043
     AHH-ELB' and four of '042 AMH-ELB, 043 AMH-ELB' cite 041042043ash.pdf, which does
     not contain them either - they are simply outside the ABSENT population this
     ticket scopes, because the matcher family-matched them. Widening the selection
     rule to catch them would also have swept in rows nobody has verified, so the rule
     stays exactly the population and the ten are named in the audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
AUDIT = REPO / "staging" / "wrong_part_citations_audit.json"
TODAY = "2026-08-01"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blade_gate import BladeGate                                  # noqa: E402

# Where each catalogue's component sits inside its NDJSON envelope.
ENVELOPE = {
    "capacitors": ("capacitor",),
    "mosfets": ("semiconductor", "mosfet"),
    "diodes": ("semiconductor", "diode"),
    "igbts": ("semiconductor", "igbt"),
    "resistors": ("resistor",),
}

# ---------------------------------------------------------------------------
# A. Vishay aluminium electrolytics: series -> the document that series names,
#    plus the documents the mismatched rows currently cite. Selection is
#    "series is here AND the row cites one of `wrong`", which reproduces the
#    ABT #391 population exactly (2,969 rows) and cannot creep.
# ---------------------------------------------------------------------------
VISHAY_SERIES = {
    '030/031 AS': {
        "correct": 'https://www.vishay.com/docs/28327/030031as.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28312/036rsp.pdf',
        ],
        "rows": 11},
    '041 ASH, 042 ASH, 043 ASH': {
        "correct": 'https://www.vishay.com/docs/28329/041042043ash.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28318/048rml.pdf',
        ],
        "rows": 38},
    '042 AHH-ELB, 043 AHH-ELB': {
        "correct": 'https://www.vishay.com/docs/28331/042043ahhelb.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28318/048rml.pdf',
        ],
        "rows": 1},
    '042 AMH-ELB, 043 AMH-ELB': {
        "correct": 'https://www.vishay.com/docs/28330/042043amhelb.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28318/048rml.pdf',
        ],
        "rows": 3},
    '051/053 PEC-PW': {
        "correct": 'https://www.vishay.com/docs/28346/051053pe.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28345/050-052ped-pw.pdf',
        ],
        "rows": 15},
    '056/057 PSM-SI': {
        "correct": 'https://www.vishay.com/docs/28340/056057psmsi.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28345/050-052ped-pw.pdf',
            'https://www.vishay.com/docs/28392/096pll4tsi.pdf',
        ],
        "rows": 139},
    '058/059 PLL-SI': {
        "correct": 'https://www.vishay.com/docs/28342/058059pll-si.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28345/050-052ped-pw.pdf',
        ],
        "rows": 122},
    '090 PUL-SI': {
        "correct": 'https://www.vishay.com/docs/28387/090pulsi.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28382/094pmesi.pdf',
        ],
        "rows": 97},
    '093 PMG-SI': {
        "correct": 'https://www.vishay.com/docs/28383/093pmgsi.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28382/094pmesi.pdf',
        ],
        "rows": 39},
    '095 PLL-4TSI': {
        "correct": 'https://www.vishay.com/docs/28393/095pll4tsi.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28382/094pmesi.pdf',
        ],
        "rows": 75},
    '096 PLL-4TSI': {
        "correct": 'https://www.vishay.com/docs/28392/096pll4tsi.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28382/094pmesi.pdf',
        ],
        "rows": 100},
    '101/102 PHR-ST': {
        "correct": 'https://www.vishay.com/docs/28371/101102phrst.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28341/159pulsi.pdf',
        ],
        "rows": 282},
    '104 PHL-ST': {
        "correct": 'https://www.vishay.com/docs/28389/104phlst.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28341/159pulsi.pdf',
        ],
        "rows": 170},
    '106 PED-ST': {
        "correct": 'https://www.vishay.com/docs/28384/106pedst.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28341/159pulsi.pdf',
        ],
        "rows": 81},
    '110 PHT-ST': {
        "correct": 'https://www.vishay.com/docs/28411/110phtst.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28335/119ahtdin.pdf',
        ],
        "rows": 34},
    '116 RLL': {
        "correct": 'https://www.vishay.com/docs/28316/116rll.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28335/119ahtdin.pdf',
        ],
        "rows": 9},
    '118 AHT': {
        "correct": 'https://www.vishay.com/docs/28334/118aht.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28335/119ahtdin.pdf',
            'https://www.vishay.com/docs/28336/120atc.pdf',
        ],
        "rows": 24},
    '132/133 ALL-DIN': {
        "correct": 'https://www.vishay.com/docs/28366/alldin.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28321/136rvi.pdf',
        ],
        "rows": 58},
    '138 AML': {
        "correct": 'https://www.vishay.com/docs/28332/138aml.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28321/136rvi.pdf',
        ],
        "rows": 32},
    '140 RTM': {
        "correct": 'https://www.vishay.com/docs/28322/140rtm.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28403/146cti.pdf',
        ],
        "rows": 47},
    '148 RUS': {
        "correct": 'https://www.vishay.com/docs/28315/148rus.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28318/048rml.pdf',
            'https://www.vishay.com/docs/28403/146cti.pdf',
        ],
        "rows": 105},
    '150 RMI': {
        "correct": 'https://www.vishay.com/docs/28323/150rmi.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28395/150crz.pdf',
        ],
        "rows": 43},
    '152 RMH': {
        "correct": 'https://www.vishay.com/docs/28320/152rmh.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28395/150crz.pdf',
        ],
        "rows": 13},
    '156 PUM-SI': {
        "correct": 'https://www.vishay.com/docs/28337/156pumsi.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28395/150crz.pdf',
        ],
        "rows": 104},
    '157 PUM-SI': {
        "correct": 'https://www.vishay.com/docs/28338/157pumsi.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28395/150crz.pdf',
        ],
        "rows": 120},
    '158 PUL-SI': {
        "correct": 'https://www.vishay.com/docs/28375/158pulsi.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28395/150crz.pdf',
        ],
        "rows": 153},
    '159 PUL-SI': {
        "correct": 'https://www.vishay.com/docs/28341/159pulsi.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28395/150crz.pdf',
        ],
        "rows": 99},
    '162/163 PLL-PW': {
        "correct": 'https://www.vishay.com/docs/28347/162163pll-pw.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28420/160rla.pdf',
        ],
        "rows": 68},
    '182 CPHZ': {
        "correct": 'https://www.vishay.com/docs/28433/182cphz.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28415/180cps.pdf',
        ],
        "rows": 18},
    '183 CPHT': {
        "correct": 'https://www.vishay.com/docs/28434/183cpht.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28415/180cps.pdf',
        ],
        "rows": 5},
    '184 CPNS': {
        "correct": 'https://www.vishay.com/docs/28437/184cpns.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28415/180cps.pdf',
        ],
        "rows": 106},
    '185 CPNZ': {
        "correct": 'https://www.vishay.com/docs/28436/185cpnz.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28415/180cps.pdf',
        ],
        "rows": 51},
    '186 CPNT': {
        "correct": 'https://www.vishay.com/docs/28435/186cpnt.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28415/180cps.pdf',
        ],
        "rows": 46},
    '198 PHR-SI': {
        "correct": 'https://www.vishay.com/docs/28339/198phr.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28458/193pursi.pdf',
        ],
        "rows": 66},
    '257 PRM-SI': {
        "correct": 'https://www.vishay.com/docs/28460/257prm-si.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28410/256pmg-si.pdf',
        ],
        "rows": 194},
    '259 PHM-SI': {
        "correct": 'https://www.vishay.com/docs/28441/259phmsi.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28410/256pmg-si.pdf',
        ],
        "rows": 186},
    '260 RLA-V': {
        "correct": 'https://www.vishay.com/docs/28448/260rla-v.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28581/269plt-si.pdf',
        ],
        "rows": 28},
    '500 PGP-ST': {
        "correct": 'https://www.vishay.com/docs/28390/500pgpst.pdf',
        "wrong": [
            'https://www.vishay.com/docs/28456/501pgm-st.pdf',
        ],
        "rows": 187},
}

# ---------------------------------------------------------------------------
# B. One entry per semiconductor / resistor row: (catalogue, reference,
#    the URL it cites now, the document that names it).
# ---------------------------------------------------------------------------
SEMICONDUCTORS = [
    ('diodes', 'VS-301CNQ...PbF Series',
     'https://www.vishay.com/docs/93354/vs-30mq040m.pdf',
     'https://www.vishay.com/docs/94176/vs-301cnq045pbfseries.pdf'),
    ('diodes', 'VS-401CNQ...PbF Series',
     'https://www.vishay.com/docs/94997/vs-4csh01hm3.pdf',
     'https://www.vishay.com/docs/94205/vs-401cnq045pbf.pdf'),
    ('diodes', 'VS-409CNQ...PbF Series',
     'https://www.vishay.com/docs/94997/vs-4csh01hm3.pdf',
     'https://www.vishay.com/docs/94207/vs-409cnq150pbf.pdf'),
    ('diodes', 'VS-UFH280FA30',
     'https://www.vishay.com/docs/96937/vs-u5fh30ba60.pdf',
     'https://www.vishay.com/docs/96136/vs-ufh280fa30.pdf'),
    ('diodes', 'VS-VSKD56.., VS-VSKE56.., VS-VSKJ56.., VS-VSKC56.. Series',
     'https://www.vishay.com/docs/94417/vs-vsk170pb.pdf',
     'https://www.vishay.com/docs/94625/vs-vsk56.pdf'),
    ('diodes', 'VS-VSKD71.., VS-VSKE71.., VS-VSKJ71.., VS-VSKC71.. Series',
     'https://www.vishay.com/docs/94417/vs-vsk170pb.pdf',
     'https://www.vishay.com/docs/94626/vs-vsk71.pdf'),
    ('diodes', 'VS-VSKD91.., VS-VSKC91.., VS-VSKJ91.., VS-VSKE91.. Series',
     'https://www.vishay.com/docs/94417/vs-vsk170pb.pdf',
     'https://www.vishay.com/docs/94627/vs-vsk91.pdf'),
    ('diodes', 'VS-VSKJS403/100',
     'https://www.vishay.com/docs/94636/vs-vskcs403.pdf',
     'https://www.vishay.com/docs/96083/vs-vskjs403_100.pdf'),
    ('igbts', 'FPF2G120BF07AS',
     'https://www.onsemi.com/pub/Collateral/TND6237-D.PDF',
     'https://www.onsemi.com/download/data-sheet/pdf/fpf2g120bf07as-d.pdf'),
    ('igbts', 'VS-40MT120PHAPbF',
     'https://www.vishay.com/docs/96734/vs-50mt060phtapbf.pdf',
     'https://www.vishay.com/docs/96762/vs-40mt120phapbf.pdf'),
    ('mosfets', 'IPB090N06N3',
     'https://www.infineon.com/dgdl/irf1010ezpbf.pdf?fileId=5546d462533600a4015355da68861885',
     'https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipb090n06n3-g-datasheet-en.pdf'),
    ('mosfets', 'Si4062DY',
     'https://www.vishay.com/docs/78131/si4056ady.pdf',
     'https://www.vishay.com/docs/62857/si4062dy.pdf'),
    ('mosfets', 'Si4670DY',
     'https://www.vishay.com/docs/74030/si4634dy.pdf',
     'https://www.vishay.com/docs/69595/si4670dy.pdf'),
    ('mosfets', 'Si5429DU',
     'https://www.vishay.com/docs/73102/si5404bdc.pdf',
     'https://www.vishay.com/docs/63933/si5429du.pdf'),
    ('mosfets', 'Si5446DU',
     'https://www.vishay.com/docs/73102/si5404bdc.pdf',
     'https://www.vishay.com/docs/62931/si5446du.pdf'),
    ('mosfets', 'Si8851EDB',
     'https://www.vishay.com/docs/62829/si8810edb.pdf',
     'https://www.vishay.com/docs/64197/si8851edb.pdf'),
    ('mosfets', 'SiA413ADJ',
     'https://www.vishay.com/docs/63163/sia4263dj.pdf',
     'https://www.vishay.com/docs/63650/sia413adj.pdf'),
    ('mosfets', 'SiA923AEDJ',
     'https://www.vishay.com/docs/77643/sia938djt.pdf',
     'https://www.vishay.com/docs/62936/sia923aedj.pdf'),
    ('mosfets', 'SiDR4612LEP',
     'https://www.vishay.com/docs/77513/sidr170dp.pdf',
     'https://www.vishay.com/docs/61786/sidr4612lep.pdf'),
    ('mosfets', 'SiHG050N60E',
     'https://www.vishay.com/docs/92091/sihp050n60e.pdf',
     'https://www.vishay.com/docs/92090/sihg050n60e.pdf'),
    ('mosfets', 'SiHG15N80AE',
     'https://www.vishay.com/docs/93298/vs-30wq06fn.pdf',
     'https://www.vishay.com/docs/92352/sihg15n80ae.pdf'),
    ('mosfets', 'SiHG15N80AEF',
     'https://www.vishay.com/docs/93298/vs-30wq06fn.pdf',
     'https://www.vishay.com/docs/92398/sihg15n80aef.pdf'),
    ('mosfets', 'SiHG64N65E',
     'https://www.vishay.com/docs/92333/sihg186n60ef.pdf',
     'https://www.vishay.com/docs/91566/sihg64n65e.pdf'),
    ('mosfets', 'SiHH27N60EF',
     'https://www.vishay.com/docs/91744/sihh21n60ef.pdf',
     'https://www.vishay.com/docs/91985/sihh27n60ef.pdf'),
    ('mosfets', 'SiHK075N65E',
     'https://www.vishay.com/docs/92563/sihk065n65e.pdf',
     'https://www.vishay.com/docs/92579/sihk075n65e.pdf'),
    ('mosfets', 'SiHL040N65E',
     'https://www.vishay.com/docs/92582/sihl039n60e.pdf',
     'https://www.vishay.com/docs/92601/sihl040n65e.pdf'),
    ('mosfets', 'SiHP050N60E',
     'https://www.vishay.com/docs/92090/sihg050n60e.pdf',
     'https://www.vishay.com/docs/92091/sihp050n60e.pdf'),
    ('mosfets', 'SiHP186N60EF',
     'https://www.vishay.com/docs/91498/sihp6n40d.pdf',
     'https://www.vishay.com/docs/92277/sihp186n60ef.pdf'),
    ('mosfets', 'SiHP21N65EF',
     'https://www.vishay.com/docs/91498/sihp6n40d.pdf',
     'https://www.vishay.com/docs/91550/sihp21n65ef.pdf'),
    ('mosfets', 'SiHP24N65EF',
     'https://www.vishay.com/docs/91498/sihp6n40d.pdf',
     'https://www.vishay.com/docs/91549/sihp24n65ef.pdf'),
    ('mosfets', 'SiHW21N80AE',
     'https://www.vishay.com/docs/96629/vs-eth0806m3.pdf',
     'https://www.vishay.com/docs/92269/sihw21n80ae.pdf'),
    ('mosfets', 'SiJ150DP',
     'https://www.vishay.com/docs/76825/sij186dp.pdf',
     'https://www.vishay.com/docs/77134/sij150dp.pdf'),
    ('mosfets', 'SiJ4819DP',
     'https://www.vishay.com/docs/62115/sqj185elp.pdf',
     'https://www.vishay.com/docs/62215/sij4819dp.pdf'),
    ('mosfets', 'SiR4606DP',
     'https://www.vishay.com/docs/63029/sir450dp.pdf',
     'https://www.vishay.com/docs/63129/sir4606dp.pdf'),
    ('mosfets', 'SiR4608DP',
     'https://www.vishay.com/docs/62029/sis4604ldn.pdf',
     'https://www.vishay.com/docs/62009/sir4608dp.pdf'),
    ('mosfets', 'SiRC16DP',
     'https://www.vishay.com/docs/76402/sirc18dp.pdf',
     'https://www.vishay.com/docs/77722/sirc16dp.pdf'),
    ('mosfets', 'SiS4608DN',
     'https://www.vishay.com/docs/62024/sidr5102ep.pdf',
     'https://www.vishay.com/docs/62014/sis4608dn.pdf'),
    ('mosfets', 'SiSA12BDN',
     'https://www.vishay.com/docs/63176/sisa10bdn.pdf',
     'https://www.vishay.com/docs/63179/sisa12bdn.pdf'),
    ('mosfets', 'SiSA16DN',
     'https://www.vishay.com/docs/76198/sisa01dn.pdf',
     'https://www.vishay.com/docs/62900/sisa16dn.pdf'),
    ('mosfets', 'SiZF918DT',
     'https://www.vishay.com/docs/62055/sizf5302dt.pdf',
     'https://www.vishay.com/docs/75963/sizf918dt.pdf'),
    ('mosfets', 'SQ3427CEV',
     'https://www.vishay.com/docs/62368/sq3419cev.pdf',
     'https://www.vishay.com/docs/62369/sq3427cev.pdf'),
    ('mosfets', 'SQA468CEJW',
     'https://www.vishay.com/docs/76236/sqa470eej.pdf',
     'https://www.vishay.com/docs/61633/sqa468cejw.pdf'),
    ('mosfets', 'SQJ968EP',
     'https://www.vishay.com/docs/61817/sidr178dp.pdf',
     'https://www.vishay.com/docs/62817/sqj968ep.pdf'),
    ('mosfets', 'SQSA82CENW',
     'https://www.vishay.com/docs/62076/sqsa84cenw.pdf',
     'https://www.vishay.com/docs/63157/sqsa82cenw.pdf'),
    ('mosfets', 'STP12N60M2',
     'https://aosmd.com/res/data_sheets/AOB7S60.pdf',
     'https://www.st.com/resource/en/datasheet/stp12n60m2.pdf'),
    ('mosfets', 'STP6N60M2',
     'https://www.aosmd.com/res/packaging_information/TO220.pdf',
     'https://www.st.com/resource/en/datasheet/stp6n60m2.pdf'),
    ('mosfets', 'SUM70090E',
     'https://www.vishay.com/docs/65436/sup70090e.pdf',
     'https://www.vishay.com/docs/64432/sum70090e.pdf'),
    ('mosfets', 'SUP40010EL',
     'https://www.vishay.com/docs/66984/sum40010el.pdf',
     'https://www.vishay.com/docs/66964/sup40010el.pdf'),
    ('resistors', 'RCG0201...C e3',
     'https://www.vishay.com/docs/20047/rcge3.pdf',
     'https://www.vishay.com/docs/20068/rcg0201e3.pdf'),
]

# ---------------------------------------------------------------------------
# C. Refused, with the evidence for refusing. Reported, never written.
# ---------------------------------------------------------------------------
WITHHELD = [
    ("mosfets", "IPB090N10N3", "Infineon",
     "Infineon's own site index (Coveo, IFXGlobalSearchHub) returns 0 results for "
     "IPB090N10N3 and for the stem IPB090N10, while returning the part page for "
     "IPB090N06N3 in the same run; www.infineon.com/part/IPB090N10N3 (and -G, G, -7) "
     "all 404. No Infineon document to cite"),
    ("mosfets", "IPI40CN05S4", "Infineon",
     "Infineon Coveo returns 0 results for IPI40CN05S4 and for IPI40CN05; /part/ 404s"),
    ("mosfets", "IPD50R380C6", "Infineon",
     "Infineon Coveo returns 0 results for IPD50R380C6; the stem IPD50R380 returns only "
     "IPD50R380CE, a different series. Cited document is a PANJIT part (PJMD360N60EC)"),
    ("mosfets", "IPP50R99C7", "Infineon",
     "Infineon Coveo returns 0 results for IPP50R99C7 and for IPP50R99. Cited document "
     "is an onsemi part (FCP099N65S3)"),
    ("mosfets", "IKM120R060M1H", "Infineon",
     "Infineon Coveo returns 0 results for IKM120R060M1H; IMZ120R060M1H (the part the "
     "cited document is about) exists. Re-citing IMZ would assert the record is that "
     "part, which is a re-identification, not a citation fix"),
    ("mosfets", "CSD86320Q5D", "Texas Instruments",
     "TI's own site search returns 'Results 1-0 of 0' for CSD86320Q5D and ti.com/lit/"
     "gpn/CSD86320Q5D 404s. The cited SLPS223F is the datasheet for CSD86350Q5D"),
    ("mosfets", "NVMFD7N06CL", "ON Semiconductor",
     "onsemi's datasheet endpoint discriminates - it serves a PDF for FPF2G120BF07AS "
     "and NVMFD5C466NL - and redirects nvmfd7n06cl-d.pdf (both cases, both path forms) "
     "to design/technical-documentation?notFound="),
    ("mosfets", "NTHS4H080N065M2C", "onsemi",
     "same control: onsemi redirects nths4h080n065m2c-d.pdf to notFound=. The record "
     "also carries series 'NexFET', which is a TI trademark, not an onsemi one"),
    ("mosfets", "GAN033-650WSP", "Wolfspeed",
     "the GANxxx-650WSy naming is Nexperia's, not Wolfspeed's; Nexperia serves "
     "GAN041-650WSB and GAN063-650WSA from assets.nexperia.com but not GAN033-650WSP "
     "or GAN033-650WSA. Neither vendor has a document for this code"),
    ("diodes", "SS54", "Vishay",
     "Vishay's part search has no SS54 rectifier: SS5* returns SS5N42, SS5NH102S, "
     "SS5P3/4/5/6/9/10 and MOSFETs, and the cited 88751 covers SS32-SS36 only. SS54 is "
     "a generic 5 A / 40 V SMC Schottky sold by other makers"),
    ("resistors", "AF102MJR-0715RL", "YAGEO", "no AF102/AF104 document exists at Yageo"),
    ("resistors", "AF102MJR-0722RL", "YAGEO", "no AF102/AF104 document exists at Yageo"),
    ("resistors", "AF102MJR-0730RL", "YAGEO", "no AF102/AF104 document exists at Yageo"),
    ("resistors", "AF102MJR-0733RL", "YAGEO", "no AF102/AF104 document exists at Yageo"),
    ("resistors", "AF102MJR-0743RL", "YAGEO", "no AF102/AF104 document exists at Yageo"),
    ("resistors", "AF102MJR-0747RL", "YAGEO", "no AF102/AF104 document exists at Yageo"),
    ("resistors", "AF104MJR-0736RL", "YAGEO", "no AF102/AF104 document exists at Yageo"),
    ("resistors", "AF104MJR-0739RL", "YAGEO", "no AF102/AF104 document exists at Yageo"),
    ("varistors", "561KN20-P12.5", "YAGEO",
     "NOT A DEFECT. The cited TMOV 20M(E,N) datasheet IS this part's datasheet: its "
     "code table reads Tolerance K / Type M,E,N / Element Diameter 20 / '-P12.5: "
     "12.5mm (N type)' and its table row '561KM(E,N)20' stands for the M, E and N "
     "variants. Matcher false negative, not a wrong-part citation"),
    ("varistors", "751KN20-P12.5", "YAGEO",
     "NOT A DEFECT - same TMOV 20M(E,N) collapsed row, '751KM(E,N)20'"),
]

# Reported for the record: rows outside the ABT #391 ABSENT population whose citation
# is nonetheless wrong. Named, deliberately not swept in - see docstring note 5.
ALSO_WRONG_NOT_IN_SCOPE = (
    "6 rows of series '042 AHH-ELB, 043 AHH-ELB' and 4 of '042 AMH-ELB, 043 AMH-ELB' "
    "cite 28329/041042043ash.pdf, which contains neither: their ordering codes appear "
    "only in 042043ahhelb.pdf / 042043amhelb.pdf. They are FAMILY_ONLY in phase 2, so "
    "they are not in this ticket's population and are left for the matcher work")

# Vishay writes the ordering-code mask as MAL2<digits> followed by a run of dots (and
# sometimes a space before them). The dot count is decorative - see docstring.
MASK = re.compile(r"MAL2(\d*)\s*\.{2,}")


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def cache_path(cache: Path, url: str) -> Path:
    """Same key scheme as verify_provenance_content.py, so its cache is reused."""
    return cache / hashlib.sha256(url.encode()).hexdigest()[:24]


def fetch(url: str, cache: Path) -> Path | None:
    p = cache_path(cache, url)
    if p.exists():
        return p
    cache.mkdir(parents=True, exist_ok=True)
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=90)
    except Exception as e:                                        # noqa: BLE001
        print(f"  fetch failed {url}: {type(e).__name__}")
        return None
    if r.status_code >= 400 or not r.content[:5].startswith(b"%PDF"):
        print(f"  fetch failed {url}: HTTP {r.status_code}, pdf="
              f"{r.content[:5].startswith(b'%PDF')}")
        return None
    p.write_bytes(r.content)
    return p


def text_of(path: Path) -> str:
    if not path.read_bytes()[:5].startswith(b"%PDF"):
        return ""
    r = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                       capture_output=True, text=True, errors="replace")
    return r.stdout


class Doc:
    """A fetched document plus the ordering-code masks it prints."""

    def __init__(self, url, cache):
        p = fetch(url, cache)
        self.url = url
        self.text = text_of(p) if p else ""
        self.flat = self.text.replace(" ", "").replace("\n", "")
        self.masks = sorted({"MAL2" + m.group(1) for m in MASK.finditer(self.text)},
                            key=len, reverse=True)

    def names_vishay_cap(self, ref):
        """How this document names `ref`, or None. Never a partial match."""
        if not self.text:
            return None, None
        if ref in self.flat:
            return "joined", None
        for lit in self.masks:
            if not ref.startswith(lit):
                continue
            tail = ref[len(lit):]
            m = re.search(r"(?<![A-Za-z0-9])" + re.escape(tail) + r"(?![A-Za-z0-9])",
                          self.text)
            if m:
                start = self.text.rfind("\n", 0, m.start()) + 1
                end = self.text.find("\n", m.end())
                return f"split-mask:{lit}+{tail}", self.text[start:end].strip()
        return None, None

    def names(self, ref):
        return bool(self.text) and norm(ref) in norm(self.text)


def unwrap(rec, path):
    o = rec
    for k in path:
        o = o[k]
    return o


CORRECTION = ("[sourceUrl corrected {date} under ABT #451: the previous citation, {old}, "
              "is a genuine {vendor} datasheet for a different part; {ref} was confirmed "
              "present in this document ({how}). The stored VALUES still come from the "
              "parametric source above and were NOT re-derived from this datasheet.]")
STALE_NOTE = ("[inferred from the record's own URL — this record was not verified "
              "against that source]")


def repoint(component, new_url, old_url, ref, how, vendor):
    """Move the citation. Nothing else in the record is touched."""
    mi = component["manufacturerInfo"]
    di = mi["datasheetInfo"]
    mi["datasheetUrl"] = new_url
    touched = 0
    for prov in di.get("provenance") or []:
        if prov.get("sourceUrl") != old_url:
            continue
        prov["sourceUrl"] = new_url
        prov["retrievedDate"] = TODAY
        name = prov.get("sourceName") or ""
        note = CORRECTION.format(date=TODAY, old=old_url.rsplit("/", 1)[-1].split("?")[0],
                                 vendor=vendor, ref=ref, how=how)
        prov["sourceName"] = (name.replace(STALE_NOTE, "").strip() + " " + note).strip()
        touched += 1
    return touched


def process(catalogue, plan_vishay, plan_semi, cache, dry, audit):
    """plan_vishay: series->entry (capacitors only). plan_semi: ref->(old,new)."""
    path = ENVELOPE[catalogue]
    src = DATA / f"{catalogue}.ndjson"
    tmp = src.with_suffix(".ndjson.tmp")
    gate = BladeGate(path)
    docs = {}
    fixed = unverified = blocked = 0
    # Cheap byte pre-filter: a row can only be in scope if it carries one of the
    # wrong URLs verbatim. Parsing every line of a 600 MB catalogue to discover
    # that 99 % of them are irrelevant costs minutes for nothing.
    needles = [u.encode() for e in (plan_vishay or {}).values() for u in e["wrong"]]
    needles += [old.encode() for old, _ in plan_semi.values()]
    with open(src, "rb") as fh, open(tmp, "wb") as out:
        for raw in fh:
            line = raw
            if any(n in raw for n in needles):
                try:
                    rec = json.loads(raw)
                    comp = unwrap(rec, path)
                    mi = comp["manufacturerInfo"]
                except Exception:                                 # noqa: BLE001
                    out.write(line)
                    continue
                ref = mi.get("reference")
                url = mi.get("datasheetUrl")
                di = mi.get("datasheetInfo") or {}
                series = (di.get("part") or {}).get("series")
                new_url = how = None
                if plan_vishay and series in plan_vishay and url in plan_vishay[series]["wrong"] \
                        and str(ref).startswith("MAL2") and mi.get("name") == "Vishay":
                    cand = plan_vishay[series]["correct"]
                    doc = docs.get(cand) or docs.setdefault(cand, Doc(cand, cache))
                    how, evidence_line = doc.names_vishay_cap(ref)
                    if how:
                        new_url = cand
                    else:
                        unverified += 1
                        audit["unverified"].append(
                            {"catalogue": catalogue, "reference": ref, "series": series,
                             "candidate": cand,
                             "why": "candidate document does not name this part"})
                elif ref in plan_semi and url == plan_semi[ref][0]:
                    cand = plan_semi[ref][1]
                    doc = docs.get(cand) or docs.setdefault(cand, Doc(cand, cache))
                    if doc.names(ref):
                        new_url, how = cand, "exact part number present"
                    else:
                        unverified += 1
                        audit["unverified"].append(
                            {"catalogue": catalogue, "reference": ref,
                             "candidate": cand,
                             "why": "candidate document does not name this part"})
                if new_url:
                    n = repoint(comp, new_url, url, ref, how, mi.get("name") or "vendor")
                    ok, why = gate.check(comp)
                    if not ok:
                        blocked += 1
                        audit["blocked"].append({"catalogue": catalogue,
                                                 "reference": ref, "why": why})
                        out.write(line)
                        continue
                    fixed += 1
                    audit["fixed"].append({"catalogue": catalogue, "reference": ref,
                                           "series": series, "from": url, "to": new_url,
                                           "matchedAs": how, "provenanceEntries": n})
                    line = json.dumps(rec, separators=(",", ":")).encode() + b"\n"
            out.write(line)
        out.flush()
        os.fsync(out.fileno())
    print(f"{catalogue:11} re-cited {fixed:5}   unverified {unverified:3}   "
          f"blocked {blocked:3}")
    print("   " + gate.summary().replace("\n", "\n   "))
    if dry or fixed == 0:
        tmp.unlink(missing_ok=True)
    else:
        os.replace(tmp, src)
    return fixed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cache", default="/tmp/tas_docs",
                    help="document cache shared with verify_provenance_content.py")
    args = ap.parse_args(argv)
    cache = Path(args.cache)

    semi_by_cat = defaultdict(dict)
    for cat, ref, old, new in SEMICONDUCTORS:
        semi_by_cat[cat][ref] = (old, new)

    audit = {"ticket": "ABT #451", "date": TODAY, "fixed": [], "unverified": [],
             "blocked": [],
             "withheld": [{"catalogue": c, "reference": r, "manufacturer": m,
                           "reason": why} for c, r, m, why in WITHHELD],
             "alsoWrongNotInScope": ALSO_WRONG_NOT_IN_SCOPE}

    total = 0
    for catalogue in ("capacitors", "mosfets", "diodes", "igbts", "resistors"):
        total += process(catalogue,
                         VISHAY_SERIES if catalogue == "capacitors" else None,
                         semi_by_cat.get(catalogue, {}), cache, args.dry_run, audit)

    by_cat = Counter(f["catalogue"] for f in audit["fixed"])
    print(f"\nre-cited {total} rows: " + ", ".join(f"{k} {v}" for k, v in by_cat.most_common()))
    print(f"could not verify a candidate: {len(audit['unverified'])}")
    print(f"withheld with a reason:       {len(audit['withheld'])}")
    if args.dry_run:
        print("\n--dry-run: nothing written")
    else:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        AUDIT.write_text(json.dumps(audit, indent=1))
        print(f"\naudit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
