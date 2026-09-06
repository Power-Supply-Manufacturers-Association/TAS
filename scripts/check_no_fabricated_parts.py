#!/usr/bin/env python3
"""GUARD: fail if any LIVE catalogue contains a fabricated part or an impossible one.

Runs in the shard-build path, so a fabricated record physically cannot reach a
deployed catalogue. Exit 0 = clean, exit 1 = fabricated parts found (build stops).

    python3 scripts/check_no_fabricated_parts.py [--data DIR]

Background (2026-07-20, user-reported from kelvin.openconverters.com):
The April-2026 bulk sourcing campaign shipped 177 invented magnetics -- scripts
that looped over a hardcoded value list, minted a value-encoded MPN, and computed
DCR/Isat from a made-up scaling formula. They looked legitimate in the UI because
a later provenance back-fill stamped them with a plausible manufacturer-database
source they never earned. A user found them in the cross-reference tool.

DESIGN NOTE -- why this guard is deliberately narrow. Real catalogues are full of
MPNs that superficially look synthesized: CM150DY-12NF (Mitsubishi IGBT),
SLF7032T-331MR22-2PF (TDK), MM-212-021-161-00MF (connector), SRU3028-330MH
(Bourns). A broad "looks like a value" heuristic flags all of them. Likewise a
real part can land on a generator formula by coincidence -- Wuerth 784771010 is a
genuine 1.0uH part whose 8.5 mOhm DCR happens to equal 0.01 * 0.85. So each rule
below demands CORROBORATING evidence of fabrication, never a lone resemblance.
A guard that cries wolf gets switched off, and then it protects nothing.

IDENTITY. Every rule keys on part_ids(): datasheetInfo.part.partNumber first,
manufacturerInfo.reference as the fallback. Both are optional and neither is
universal (35,966 capacitors have only partNumber, 51,741 magnetics only
reference), so a screen keyed on one field silently skips the other population --
ABT #256 and the 448 TDK magnetics of 2026-09-04 both got through that way. A
record with NEITHER identity is a loud failure, never a skip.

Two signatures, both evidence-backed:

 1. KNOWN GENERATOR TEMPLATE -- the exact MPN shapes the fabrication scripts
    emitted, with case-sensitive units (nH/uH/mH, not the NF/MF/PF suffix codes
    real vendors use). These are provably synthetic.

 2. FORMULA-DERIVED DCR ON A BARE STUB -- dcResistance reproduces a known
    generator formula AND the record carries none of the corroboration a real
    datasheet entry has (no datasheet URL, no description, no saturation current,
    no SRF, no mechanical dimensions). The formula alone is not enough; the
    emptiness around it is what proves nothing was ever read from a datasheet.

 3. TWO-SEED EXPANSION IN ONE ROW -- a MOSFET whose powerDissipation is exactly
    0.3 * Vds * Id, corroborated by the derived constants written beside it
    (Id@100C = 0.65*Id, onResistanceId = Id/2, capacitanceMeasurementVds =
    Vds/2, Coss = Ciss/5, Crss = Ciss/50). Unlike (4) this needs no cohort: it
    is an arithmetic identity WITHIN a single row, which is how it reaches the
    generated families whose part numbers ladder mid-string and therefore look
    exactly like a real vendor's value-coded numbering.
"""
import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_DATA = Path("/home/alf/PSMA/TAS/data")

# (1) exact templates emitted by the known fabrication scripts. Case-sensitive:
# real MPNs use uppercase suffix codes (12NF, 2PF, 330MH) that must NOT match.
KNOWN_TEMPLATES = [
    (re.compile(r"^7443HCF-\d{4}-\d{4}$"), "aggressive_mag_sourcing (WE-HCF)"),
    (re.compile(r"^7443MAPI-\d{4}-\d{4}$"), "aggressive_mag_sourcing (WE-MAPI)"),
    (re.compile(r"^WE-HCF-\d+(nH|uH|mH)-(STD|HC|XC)$"), "parametric_sourcing (WE-HCF)"),
    (re.compile(r"^WE-HCI-\d{4}-\d+$"), "bulk sourcing (WE-HCI)"),
    (re.compile(r"^CC-[A-Z0-9]+-\d+(nH|uH|mH)$"), "aggressive_mag_sourcing (Coilcraft)"),
    (re.compile(r"^TDK-SPM-\d+(nH|uH|mH)$"), "aggressive_mag_sourcing (TDK)"),
    (re.compile(r"^SRR-\d+(nH|uH|mH)$"), "aggressive_mag_sourcing (Bourns)"),
    (re.compile(r"^IHLP-\d+(nH|uH|mH)$"), "aggressive_mag_sourcing (Vishay)"),
    (re.compile(r"^WCAP-(ATH|MLCC)-[\d.]+(uF|nF)-[\d.]+V$"), "bulk sourcing (WCAP)"),
    # ABT #247 follow-up audit (2026-07-21): output of the OTHER disabled scripts.
    # Verified against the WE released Access DB: zero real Wuerth MPNs have the
    # short 7443-value shape (real catalogue numbers are 6 or 9+ digits there).
    (re.compile(r"^7443\d{3,4}$"), "bulk sourcing (WE-HCF short value-code)"),
    # Real ST duals end in ...CT (STPS30H60CT); the generator minted ...C and
    # attributed them to Texas Instruments, which never made STPS parts.
    (re.compile(r"^STPS\d{2}H\d{3}C$"), "parametric sourcing (Schottky, fake TI attribution)"),
    (re.compile(r"^SiC\d{2}H\d{4}$"), "parametric sourcing (SiC Schottky value-code)"),
    # ABT #256 audit (2026-07-22): the phase2-5 'reach 100K entries' generators.
    # Vendor-abbreviated internal codes no vendor sells; verified against live
    # catalogues (17,183 quarantined, zero real MPNs match these shapes).
    # 2026-09-04: the thirteenth batch (448 TDK, quarantine tag "fabricated cohort
    # 13") was this generator's output with the unit letter 'm' where the template
    # only knew 'u' (TDK001m08051065_50) -- so it slipped a template written for it.
    # Widened to n/u/m; zero live MPNs match the widened shape (scan of every live
    # catalogue that day), while 3,696 quarantined 'm' rows do.
    (re.compile(r"^(Coi|Bou|TDK|Wur|Vis|Mur|Pul|Sum)\d{3}[num][A-Za-z0-9]+_\d+$"), "phase5 magnetics generator"),
    (re.compile(r"^(Vis|Yag|Bou|Pan|KOA)(wir|car|mel|met|thi|MCS|PTF)\d+R\d{4}\d{4}$"), "phase3/5 resistor generator"),
    (re.compile(r"^(GRM|CL|FK)\d{4}\d{4}\d{3}V$"), "phase2 MLCC generator (fake GRM/CL/FK numbering)"),
    (re.compile(r"^MLCC\d{6}$"), "phase2 MLCC generator (generic fallback)"),
    # ABT #507 audit (2026-08-02): the "wave 2" SiC-diode ladder generator. Real
    # Infineon SiC Schottky numbering carries the voltage class in the token
    # (IDH06S60C, IDH03G65C6, IDH05G120C5 -> 60/65/120); this one minted
    # 10C/11C/12C/20C/21C/22C and swept every integer amp 2..30 A across three
    # package variants (DPAK/TO-252/SO-8) of a single die.
    (re.compile(r"^IDH\d{2}S[GO]?(?:1[0-2]|2[0-2])C$"), "wave2 SiC diode ladder generator"),
]

# (1b) PROVENANCE CITING A URL THE VENDOR DOES NOT USE. A fabricator can mint a
# convincing MPN, but it still has to name a source, and an invented source has to
# be a URL nobody fetched. These templates were each checked against the live site:
# every one returns the vendor's generic CATEGORY page, never a product page, so a
# record citing one was provably never read from it.
#
# ABT #351 (2026-07-30), fourth fabrication batch — 195 Coilcraft magnetics across
# 17 real family names (EPL2010, SER2918, MSD1514, SLC0402T, PA4310 ...). Coilcraft
# product URLs are deep and category-bearing:
#     /en-us/products/power/shielded-inductors/ferrite-drum/lps/lps4018/
#         -> 235 KB, "LPS4018 Series Low Profile Shielded Power Inductors"
# The flat /products/power-inductors/<family>/ shape below is not a Coilcraft route;
# all 17 fabricated variants return one byte-identical 176,381-byte page titled
# "Power Inductors". Corroborated by physics: those rows carry a single DC-resistance
# constant per henry (median 30.0 mOhm/uH) across five DIFFERENT package sizes, where
# the 4,134 genuine Coilcraft rows median 5.7 spread over 1.0-62 — bigger cores need
# fewer turns of thicker wire, so a size-independent DCR/L cannot be measured data.
# Checked against real EPL2010: corpus "EPL2010-100ML" claims 1.0uH/30mOhm/8.0A in
# 5.0x2.5x2.0mm; the real EPL2010-102ML is 1.0uH/119mOhm/1.36A in 2.0x2.0x1.0mm.
#
# Narrow on purpose, like the MPN templates: only URL shapes VERIFIED to resolve to
# a non-product page belong here. A merely unfamiliar URL is not evidence.
FAKE_PROVENANCE_URLS = [
    (re.compile(r"^https?://(?:www\.)?coilcraft\.com/en-us/products/power-inductors/[a-z0-9]+/?$", re.I),
     "Coilcraft flat /products/power-inductors/<family>/ (ABT #351 batch); the real "
     "route is /products/power/<category>/<subcategory>/<family>/ and this shape "
     "serves the generic category page"),
]


def fake_provenance(info):
    """The record's only cited source is a URL that is not a product page."""
    provenance = (info.get("datasheetInfo") or {}).get("provenance") or []
    if len(provenance) != 1 or not isinstance(provenance[0], dict):
        return None  # a corroborated record cites more than the one bad URL
    url = str(provenance[0].get("sourceUrl", ""))
    return next((why for pattern, why in FAKE_PROVENANCE_URLS if pattern.match(url)), None)


# (2) generator DCR formulas: dcr = base / (L/1uH) * package_scale
FORMULA_BASES = (0.008, 0.01)
# ABT #247 follow-up: manufacturer_sourcing's WE-LQM generator used the
# MULTIPLICATIVE form dcr = base * (L/1uH), base 0.08 (74437401 = 1uH/0.08R,
# 7443740401 = 401nH/0.03208R -- float noise and all).
FORMULA_MUL_BASES = (0.08,)
FORMULA_SCALES = (1.0, 1.2, 0.85, 0.70, 1.3)
REL_TOL = 1e-9

# ABT #247 follow-up: manufacturer/bulk sourcing hardcoded this exact
# (L, DCRmax) ladder for invented Wuerth "WE-HCF" rows. Exact float pairs,
# matched only on bare stubs -- a real part with these values would carry a
# datasheet URL / description / Isat / dimensions and never reach this test.
GENERATOR_LADDER = {
    (220e-9, 0.0018), (330e-9, 0.0024), (470e-9, 0.0032), (680e-9, 0.0045),
    (1e-6, 0.0062), (1.5e-6, 0.0088), (2.2e-6, 0.012), (3.3e-6, 0.017),
    (4.7e-6, 0.023), (6.8e-6, 0.032), (10e-6, 0.044), (15e-6, 0.062),
    (22e-6, 0.085), (33e-6, 0.118), (47e-6, 0.162), (68e-6, 0.224),
    (100e-6, 0.31),
}


def formula_dcr(inductance, dcr):
    """True when dcr lands exactly on a known generator formula output."""
    if not inductance or not dcr or inductance <= 0 or dcr <= 0:
        return False
    if (inductance, dcr) in GENERATOR_LADDER:
        return True
    lu = inductance / 1e-6
    scale_factor = 1.0 / max(lu, 0.1)
    for base in FORMULA_BASES:
        for scale in FORMULA_SCALES:
            expected = base / scale_factor * scale
            if abs(dcr - expected) <= REL_TOL * max(abs(dcr), abs(expected)):
                return True
    for base in FORMULA_MUL_BASES:
        expected = base * lu
        if abs(dcr - expected) <= REL_TOL * max(abs(dcr), abs(expected)):
            return True
    return False


def is_bare_stub(info, electrical):
    """True when the record has none of the corroboration a real datasheet entry has."""
    if info.get("datasheetUrl"):
        return False
    datasheet = info.get("datasheetInfo") or {}
    if (datasheet.get("part") or {}).get("description"):
        return False
    if datasheet.get("mechanical") or datasheet.get("thermal"):
        return False
    for key in ("saturationCurrentPeak", "saturationCurrents", "selfResonantFrequency",
                "ratedCurrents", "temperatureRise"):
        if electrical.get(key):
            return False
    return True


# ── (3) the two-seed MOSFET generator ────────────────────────────────────────
# ABT: found 2026-09-06, after the two MOSFET batches of 2026-09-04/06 had
# already been condemned by hand. Both batches were found by cohort reasoning --
# a numeric ladder in the part number -- and both were nearly missed, because
# the cohort rules cannot see them:
#
#   * the trailing-run ladder key never grouped NTH2312P6..NTH2452P12 (the run
#     is mid-string, with a package token after it), and
#   * the mid-string key deliberately demands TWO quantities laddering, because
#     a single-quantity mid-string ladder is indistinguishable from a Murata
#     value code -- 13,142 real rows have that shape.
#
# But those rows do not need a cohort at all. Every single one of them stores,
# EXACTLY in double precision, a relation between three fields of the SAME ROW:
#
#     powerDissipation = 0.3 * drainSourceVoltage * |continuousDrainCurrent|
#
# 0.3x100x80 = 2400, 0.3x650x23 = 4485, 0.3x1200x40 = 14400. There is no such
# quantity in semiconductor physics: dissipation is set by the package and the
# heatsink (Tj-Tc)/RthJC, and has nothing to do with the product of the two
# absolute maximum ratings, which is the DC power the part is guaranteed never
# to see. A row that states it was not read from a datasheet; it was expanded
# from two seeds, Vds and Id, by arithmetic. That is a statement about ONE ROW,
# so it needs no cohort, no contiguous run and no shared stem -- which is
# exactly why it reaches the rows the ladder rules cannot.
#
# The same expansion writes a family of derived constants beside it, and those
# are what CORROBORATE, in the spirit of every other rule here. A lone
# arithmetic coincidence is not evidence: measured over the live catalogue,
# 0.3*Vds*Id lands exactly on the stored powerDissipation of NDT3055 -- a REAL
# onsemi SOT-223 part (0.3 x 60 V x 4 A = 72.0 W, stored 72.0), whose row came
# from the onsemi parametric CSV with a mis-mapped dissipation column and shares
# that defect with its neighbours NDT014 (43 W) and NDT014L (115 W). It is a
# broken real part, not an invented one, and condemning it as fabricated would
# delete a part onsemi ships. So the rule requires the identity AND at least
# SEED_MIN_RATIOS of the derived constants below.
#
# CALIBRATION, measured on data/mosfets.ndjson (9,283 live rows, 2026-09-06):
#   * 283 rows satisfy the Pd identity at float noise;
#   * 282 of them also carry ALL FIVE derived ratios exactly -- and they are the
#     suspect block (EPC 89, ST 63, onsemi 56, Power Integrations 47, Infineon
#     27, Microsemi 1), 281 of which cite nothing but a datasheetpdf.com search
#     query built from their own part number;
#   * exactly ONE row (NDT3055) has the identity and NONE of the ratios;
#   * no row anywhere in the file has the identity plus one ratio, so the
#     >= 2 bar and a >= 1 bar select the same 282 rows today. The bar is 2
#     because two independent corroborations is what the other rules here
#     demand, not because one row forced it.
#   * the nearest MISS is NDT014 at 11.5% (43.0 W stored against 48.6), so
#     there is no cluster of real parts hovering near the identity: it is
#     satisfied exactly or not at all.
#   * igbts.ndjson (2,251 rows) and bjts.ndjson (3,666) were checked for the
#     analogous Vce x Ic identity: 0 hits. The generator was MOSFET-only.
#
# COST, stated plainly: a generator that expands Pd from the same two seeds but
# writes real capacitances and a real Id@100C is not caught here. That is the
# price of refusing to delete NDT3055 on one arithmetic coincidence.
SEED_PD_FACTOR = 0.3
SEED_TOL = 1e-9
SEED_MIN_RATIOS = 2
# (derived field, seed field, factor) -- the constants the same expansion writes.
SEED_RATIOS = (
    ("continuousDrainCurrentAt100C", "continuousDrainCurrent", 0.65),
    ("onResistanceId", "continuousDrainCurrent", 0.5),
    ("capacitanceMeasurementVds", "drainSourceVoltage", 0.5),
    ("outputCapacitance", "inputCapacitance", 0.2),
    ("reverseTransferCapacitance", "inputCapacitance", 0.02),
)


def _seed_scalar(value):
    """A field's scalar, whether it is stored bare or as a dimensionWithTolerance."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for bound in ("nominal", "maximum", "minimum"):
            v = value.get(bound)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
    return None


def _seed_equal(a, b):
    """Equal at double-precision noise -- the generator's arithmetic is exact,
    but the decimal that was written back is not always the same double."""
    return abs(a - b) <= SEED_TOL * max(abs(a), abs(b), 1e-30)


def seed_expanded_mosfet(electrical):
    """Why-string when a MOSFET row's electricals were expanded from two seeds.

    None when the row is silent on any of the three identity fields, when the
    identity does not hold exactly, or when fewer than SEED_MIN_RATIOS of the
    derived constants corroborate it.
    """
    vds = _seed_scalar((electrical or {}).get("drainSourceVoltage"))
    idc = _seed_scalar((electrical or {}).get("continuousDrainCurrent"))
    pdis = _seed_scalar((electrical or {}).get("powerDissipation"))
    if vds is None or idc is None or pdis is None:
        return None
    # P-channel parts store both ratings negative; the generator multiplied
    # magnitudes. Zero or negative dissipation is a different defect entirely.
    if vds == 0 or idc == 0 or pdis <= 0:
        return None
    if not _seed_equal(pdis, SEED_PD_FACTOR * abs(vds) * abs(idc)):
        return None
    corroboration = []
    for derived, seed, factor in SEED_RATIOS:
        a = _seed_scalar((electrical or {}).get(derived))
        b = _seed_scalar((electrical or {}).get(seed))
        if a is None or b is None or b == 0:
            continue
        if _seed_equal(a, factor * b):
            corroboration.append(f"{derived} = {factor:g} * {seed}")
    if len(corroboration) < SEED_MIN_RATIOS:
        return None
    return ("powerDissipation = %g * drainSourceVoltage * continuousDrainCurrent "
            "holds exactly (%g W = %g * %g V * %g A), which is not a physical "
            "relation -- dissipation follows from the package, not from the "
            "product of two absolute maximum ratings; corroborated by %d derived "
            "constant(s) from the same two seeds: %s"
            % (SEED_PD_FACTOR, pdis, SEED_PD_FACTOR, vds, idc,
               len(corroboration), ", ".join(corroboration)))


# ── (4) arithmetic ladders ───────────────────────────────────────────────────
# ABT #1014. The two batches found on 2026-09-04 both sailed past every rule
# above, and past screen_fabrication_signatures.py, for the same structural
# reason: their fields VARY. A degenerate-field screen looks for one value
# repeated across a cohort; these vary by FORMULA instead.
#
# The ROHM batch (ABT #1011, 25 rows) is the clean specimen. RSR012E00..E024 is
# one contiguous ladder in which
#
#     forwardVoltage   = 0.20 + 0.01 * i     exact, 25/25
#     powerDissipation = 10 * forwardVoltage exact, 25/25
#
# while every other electrical field holds ONE identical value across all 25
# parts. 2.0-4.4 W in a TO-220 is entirely plausible, so no impossible-value
# rule fires either. It was found by a regex coincidence, which is not a
# detection strategy.
#
# THE CORROBORATION, in the spirit of the rules above -- an exact linear fit
# alone is NOT enough to condemn a cohort. Real product families walk in regular
# steps all the time (voltage ladders, E-series values, pin counts), so this rule
# demands all four of:
#   * a contiguous numeric-suffix ladder of at least LADDER_MIN parts sharing a
#     stem and a manufacturer -- a real family is rarely a perfect run,
#   * at least one field that is an EXACT affine function of the ladder index
#     (relative residual at float noise, not merely a good fit),
#   * the surrounding cohort being degenerate: most other numeric fields carry a
#     single identical value across every member, which is what says nothing was
#     read per part,
#   * and more than one member, obviously.
# A real family that steps one parameter also varies the others -- package,
# current, dissipation, capacitance move together. A generator moves one.
#
# MID-STRING LADDERS (2026-09-06). The cohort key above used to be
# ``^(stem)(index)$`` -- a TRAILING numeric run and nothing else. That is not
# where real vendors put the varying digits: IPW60R080P7, C3M0075120K,
# FDMU81000, IRFB4110PbF all carry the family's varying number in the MIDDLE of
# the part number, with a package/grade token after it. A cohort indexed
# mid-string therefore produced a DIFFERENT stem for every member
# (ACME100N65 -> "ACME100N", ACME101N65 -> "ACME101N"), so every cohort had
# exactly one member and the whole rule was inert against it. That is precisely
# the shape a generator imitating a real vendor family emits, and it is how the
# 14 fabricated FDMU81000 MOSFETs escaped this guard.
#
# So the key is now derived from EVERY numeric run in the label, not just a
# trailing one: a label yields one candidate (prefix, suffix, index) per run, and
# ACME100N65/ACME101N65 group on prefix "ACME", suffix "N65". The trailing-run
# behaviour is preserved exactly -- it is simply the run whose suffix is empty.
#
# Nothing else about the rule changes. All four corroboration conditions
# (contiguous run of >= LADDER_MIN, an EXACT affine field, a degenerate
# surrounding cohort, > 1 member) still have to hold, which is what keeps the
# widened key from condemning real families: a member now appears in several
# candidate cohorts instead of one, and a real family fails the corroboration in
# every one of them.
#
# ONE EXTRA CONDITION IS REQUIRED, AND IT WAS MEASURED, NOT ASSUMED. Run with the
# widened key alone, the rule fires on 13,142 LIVE records -- 11,473 capacitors
# and 1,669 magnetics -- and every one of them is a real part:
#     Murata  GCQ0335C1H6R0DB01..6R9DB01   capacitance.nominal = 6.0..6.9 pF
#     Bourns  CE0603G-2N0C..2N9C           inductance          = 2.0..2.9 nH
#     Murata  LQP02TQ10NH02..22NH02        inductance          = 1.0..2.2 nH
# These are not ladders that slipped through; they are what a VALUE-CODED part
# number IS. When the vendor spells the value in the MPN, the encoded quantity is
# affine in the index BY CONSTRUCTION, and the rest of the row (one tolerance, one
# rated voltage) is legitimately identical across the decade. The trailing-run key
# never met this because a value code is almost always followed by a tolerance or
# packaging token -- which is exactly why widening the key walks straight into it.
#
# The discriminator, taken from what separates those 13,142 from the ABT #1011
# specimen: count DISTINCT BASE quantities that are affine in the index, treating
# .nominal/.minimum/.maximum of one field as ONE quantity. Every single false
# positive above has exactly ONE (the quantity the part number spells; its three
# tolerance bounds are not three independent measurements). The ROHM specimen has
# TWO independent ones -- forwardVoltage AND powerDissipation. A vendor's part
# number encodes one quantity; a generator's loop index drives several. So a
# MID-STRING cohort must show >= LADDER_MIN_MIDSTRING_FIELDS independent affine
# quantities. Measured with that condition in place: 0 findings across all 13
# live component catalogues (1.06M records), while a planted mid-string ladder is
# caught.
#
# The condition is applied ONLY to mid-string cohorts. The trailing-run key is
# already calibrated and already clean, and narrowing it here would silently
# weaken a rule that is not the one being changed.
#
# COST, stated plainly: a fabricated mid-string cohort that ladders exactly ONE
# quantity is not caught by this rule. From the fields alone it is
# indistinguishable from a Murata value code -- and condemning 13,142 real parts
# to reach it is not a trade this guard makes. That gap needs a different signal
# (provenance, the cohort's own citations), not a looser bar here.
LADDER_MIN = 8
LADDER_MIN_MIDSTRING_FIELDS = 2
# Every maximal digit run, with what surrounds it.
LADDER_RUN = re.compile(r"\d+")
LADDER_DEGENERATE_FRACTION = 0.7
LADDER_TOL = 1e-9


def ladder_keys(label):
    """Yield (prefix, suffix, index) for every maximal numeric run in `label`.

    The trailing run yields suffix == "", which reproduces the old
    ``^(stem)(index)$`` key exactly; the other runs are what that key could not
    see. A prefix is required (an MPN that STARTS with its varying digits has no
    stem to group on, same as before).
    """
    for m in LADDER_RUN.finditer(label):
        prefix, digits, suffix = label[:m.start()], m.group(0), label[m.end():]
        if not prefix:
            continue
        yield prefix, suffix, int(digits)


def _affine_exact(indices, values):
    """True when values[i] == a * indices[i] + b holds at float noise for all i.

    Fitted from the two extreme points rather than by least squares: an exact
    ladder passes through them, and a cohort that is NOT one fails immediately
    instead of being smoothed into a plausible-looking fit.
    """
    if len(set(values)) < 2:
        return False                      # constant is degeneracy, not a ladder
    i0, i1 = indices[0], indices[-1]
    if i1 == i0:
        return False
    slope = (values[-1] - values[0]) / (i1 - i0)
    if slope == 0:
        return False
    intercept = values[0] - slope * i0
    for i, v in zip(indices, values):
        expected = slope * i + intercept
        if abs(v - expected) > LADDER_TOL * max(abs(v), abs(expected), 1e-30):
            return False
    return True


def find_arithmetic_ladders(cohorts):
    """Yield (members, why) for each cohort that is a generator's output.

    `cohorts` maps (manufacturer, prefix, suffix) -> list of (index, lineno,
    part_number, {field: value}); `suffix` is "" for a trailing-index cohort and
    the tail token (e.g. "N65") for a mid-string one. Only numeric fields present
    on EVERY member are considered: a field missing from some rows says the rows
    were populated separately.
    """
    for (manufacturer, stem, suffix), members in sorted(cohorts.items()):
        if len(members) < LADDER_MIN:
            continue
        members = sorted(members)
        indices = [m[0] for m in members]
        if indices != list(range(indices[0], indices[0] + len(indices))):
            continue                      # not a contiguous run
        shared = set(members[0][3])
        for m in members[1:]:
            shared &= set(m[3])
        if not shared:
            continue
        ladder_fields, degenerate = [], 0
        for field in sorted(shared):
            values = [m[3][field] for m in members]
            if len(set(values)) == 1:
                degenerate += 1
            elif _affine_exact(indices, values):
                ladder_fields.append(field)
        if not ladder_fields:
            continue
        # Distinct BASE quantities: inductance.minimum/.nominal/.maximum are three
        # bounds of ONE measurement, not three independent ladders.
        base_quantities = {f.split(".", 1)[0] for f in ladder_fields}
        if suffix and len(base_quantities) < LADDER_MIN_MIDSTRING_FIELDS:
            continue                      # a value-coded MPN, not a generated cohort
        others = len(shared) - len(ladder_fields)
        if others and degenerate / others < LADDER_DEGENERATE_FRACTION:
            continue                      # the rest of the cohort varies: a real family
        why = (f"cohort of {len(members)} parts {stem}{indices[0]}{suffix}.."
               f"{stem}{indices[-1]}{suffix} "
               f"({manufacturer or 'unknown manufacturer'}): "
               + ", ".join(f"{f} is an exact linear function of the part index"
                           for f in ladder_fields)
               + f", while {degenerate} of the other {others} shared numeric field(s) "
               "hold one identical value across every member")
        yield members, why


def _ladder_numeric_fields(electrical):
    """Flat {field: float} of a record's own scalar electricals, for cohort tests."""
    out = {}
    for key, value in (electrical or {}).items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out[key] = float(value)
        elif isinstance(value, dict):
            for bound in ("nominal", "maximum", "minimum"):
                if isinstance(value.get(bound), (int, float)) and not isinstance(value.get(bound), bool):
                    out[f"{key}.{bound}"] = float(value[bound])
    return out


# ── record identity ──────────────────────────────────────────────────────────
# Every rule keys on part_ids(), never on one field. Both identity fields are
# OPTIONAL in PEAS and NEITHER is universal in this corpus (census of the live
# tree, 2026-09-04): 35,966 capacitors carry datasheetInfo.part.partNumber and
# no manufacturerInfo.reference, while 51,741 magnetics carry reference and no
# partNumber. A screen keyed on either one alone silently skips the other
# population and reports clean -- that is how 17,183 partNumber-only rows walked
# past the reference-keyed version of this guard (ABT #256) and how the 448
# reference-less TDK magnetics of 2026-09-04 walked past every reference-keyed
# screen in the corpus. partNumber comes first: it is the identity a vendor
# datasheet actually carries; reference is the fallback, not the other way round.
#
# A record that has NEITHER is not skipped -- it is reported as UNIDENTIFIABLE
# and fails the build (see check_file). A gate that cannot run must fail, never
# pass.
UNIDENTIFIABLE = ("UNIDENTIFIABLE: record carries neither datasheetInfo.part.partNumber "
                  "nor manufacturerInfo.reference, so no rule can key on it; the guard "
                  "will not pass a part it cannot name")
NO_MANUFACTURER_INFO = ("UNIDENTIFIABLE: component record has no manufacturerInfo at all, "
                        "so there is nothing for the guard to key on")
# Top-level keys that mark a record as a CIAS brick / TAS converter document
# rather than a component -- those carry no manufacturerInfo by design.
NON_COMPONENT_KEYS = frozenset({"components", "connections", "topology", "stages"})


def part_ids(info):
    """The identities a manufacturerInfo can be keyed on, most reliable first.

    [partNumber, reference] with empties dropped -- so ids[0] is the label to
    report and ``any(rule(i) for i in ids)`` is how a rule screens a row. An empty
    list means the record cannot be identified.
    """
    datasheet = info.get("datasheetInfo") if isinstance(info.get("datasheetInfo"), dict) else {}
    part = datasheet.get("part") if isinstance(datasheet.get("part"), dict) else {}
    return [str(i) for i in (part.get("partNumber"), info.get("reference")) if i]


def iter_parts(record, _nested=False):
    """Yield every (manufacturerInfo, electrical-dict, nested) in a record.

    Yields EVERY manufacturerInfo dict, identified or not -- deciding what to do
    about a missing identity is check_file's job, and doing it here is exactly
    the silent skip this guard exists to refuse. The outermost manufacturerInfo
    (``nested`` False) is the part's own and MUST be identifiable; deeper ones
    (a core's or wire's manufacturer inside a MAS magnetic, an inline PEAS
    document inside a CIAS brick's component) are building-block references
    and are screened when they carry an identity.
    """
    if isinstance(record, list):
        for value in record:
            yield from iter_parts(value, _nested)
        return
    if not isinstance(record, dict):
        return
    info = record.get("manufacturerInfo")
    if isinstance(info, dict):
        datasheet = info.get("datasheetInfo") if isinstance(info.get("datasheetInfo"), dict) else {}
        electrical = datasheet.get("electrical")
        if isinstance(electrical, list):
            electrical = electrical[0] if electrical else {}
        yield info, (electrical if isinstance(electrical, dict) else {}), _nested
        _nested = True
    for key, value in record.items():
        if key != "manufacturerInfo" and isinstance(value, (dict, list)):
            yield from iter_parts(value, _nested)


# ── physically impossible ratings ────────────────────────────────────────────
# Separate failure mode from fabrication: these rows describe REAL part numbers,
# but a source column mis-mapping gave them ratings that cannot exist. Found on
# the live site — a 40 V / 200 mA BAS40 stored as 1000 V / 120 A was offered as a
# recommended drop-in upgrade. Each rule rejects a combination that is impossible
# in silicon, not one that is merely unusual.
SCHOTTKY_MAX_VRRM = 300.0
SCHOTTKY_MAX_VF = 1.2


def impossible_ratings(info, electrical):
    part = (info.get("datasheetInfo") or {}).get("part") or {}
    subtype = str(part.get("subType") or "").lower()
    technology = str(part.get("technology") or "").lower()
    if subtype != "schottky" or "sic" in technology:
        return None
    def num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    vrrm, vf = num(electrical.get("reverseVoltage")), num(electrical.get("forwardVoltage"))
    if vrrm is not None and vrrm > SCHOTTKY_MAX_VRRM:
        return f"silicon Schottky rated {vrrm:g} V reverse (barrier height caps this ~{SCHOTTKY_MAX_VRRM:g} V)"
    if vf is not None and vf > SCHOTTKY_MAX_VF:
        return f"Schottky with a {vf:g} V forward drop — mis-typed PN/ultrafast rectifier"
    return None


def load_quarantined_fabricated(data_dir):
    """Identities already condemned as fabricated must never reappear live.

    Reads ``data/fabricated_denylist.ndjson`` -- one line per condemned identity,
    ``{"id": ..., "source": ..., "date": ...}``.

    The fabricated RECORDS themselves are gone. An invented part is not a record with
    a problem, it is not a record, so it is deleted outright rather than kept in the
    quarantine beside genuinely broken real parts (2026-09-04: 37,102 obliterated,
    207,653 real ones kept). What survives is the blocklist, because the guard has to
    recognise a re-import: identity, where it was condemned, and when. 36,852 entries,
    3.7 MB, against the 88 MB of complete invented objects it replaces. The full rows
    remain in git-LFS history if anyone ever has to prove what was there.

    The older quarantine harvest is kept as a fallback so an old checkout still works.
    """
    refs = set()

    deny = data_dir / "fabricated_denylist.ndjson"
    if deny.is_file():
        with deny.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    refs.add(str(json.loads(line)["id"]))
                except Exception:
                    continue
        refs.discard("")
        return refs

    def harvest(record):
        for info, _elec, _nested in iter_parts(record):
            for i in part_ids(info):
                refs.add(i)

    consolidated = data_dir / "quarantine.ndjson"
    if consolidated.is_file():
        with consolidated.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sources = record.get("_quarantineSource") or []
                if isinstance(sources, str):
                    sources = [sources]
                if any("fabricated" in s for s in sources):
                    harvest(record)

    refs.discard("")
    return refs

def new_stats():
    return {"rows": 0, "screened": 0, "nestedScreened": 0, "unidentifiable": 0,
            "nonComponentRows": 0, "nestedUnidentified": 0}


def check_file(path, quarantined_refs=frozenset(), stats=None):
    """Findings [(lineno, label, why)] for one catalogue.

    ``stats`` (optional dict, see new_stats) is filled with what the guard SAW:
    rows read, parts screened, and -- the number this guard used to hide --
    rows it could not identify. Those rows are also findings: a record with no
    identity is a failure, not a skip.
    """
    findings = []
    stats = stats if stats is not None else new_stats()
    cohorts = {}          # (manufacturer, prefix, suffix) -> [(index, lineno, pn, fields)]
    with path.open(encoding="utf-8", errors="replace") as fh:
        first = fh.readline()
        if first.startswith("version https://git-lfs"):
            return findings  # not checked out locally
        fh.seek(0)
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            stats["rows"] += 1
            parts = list(iter_parts(record))
            if not parts:
                if isinstance(record, dict) and NON_COMPONENT_KEYS.isdisjoint(record):
                    stats["unidentifiable"] += 1
                    findings.append((lineno, "<no identity>", NO_MANUFACTURER_INFO))
                else:
                    stats["nonComponentRows"] += 1   # a CIAS brick or a TAS document
                continue
            for info, electrical, nested in parts:
                ids = part_ids(info)
                if not ids:
                    if nested:
                        stats["nestedUnidentified"] += 1   # a building block, not the part
                    else:
                        stats["unidentifiable"] += 1
                        findings.append((lineno, "<no identity>", UNIDENTIFIABLE))
                    continue
                stats["nestedScreened" if nested else "screened"] += 1
                label = ids[0]
                hit = next((why for pattern, why in KNOWN_TEMPLATES
                            for i in ids if pattern.match(i)), None)
                if hit:
                    findings.append((lineno, label, f"MPN matches the {hit} generator template"))
                    continue
                if any(i in quarantined_refs for i in ids):
                    findings.append((lineno, label,
                                     "part was previously quarantined as fabricated "
                                     "and must not reappear live"))
                    continue
                bad_url = fake_provenance(info)
                if bad_url:
                    findings.append((lineno, label,
                                     f"sole provenance URL is not a product page: {bad_url}"))
                    continue
                inductance = (electrical.get("inductance") or {}).get("nominal")
                dcr = (electrical.get("dcResistance") or {}).get("maximum")
                if formula_dcr(inductance, dcr) and is_bare_stub(info, electrical):
                    findings.append((lineno, label,
                                     "DCR reproduces a generator formula on a record with no "
                                     "datasheet, description, Isat, SRF or dimensions"))
                    continue
                seeded = seed_expanded_mosfet(electrical)
                if seeded:
                    findings.append((lineno, label, seeded))
                    continue
                impossible = impossible_ratings(info, electrical)
                if impossible:
                    findings.append((lineno, label, impossible))
                    continue
                # cohort accumulation for rule (3); per-record rules above have
                # already had their say, so only survivors are grouped. Keyed on
                # the same label every other rule reports: partNumber, else
                # reference -- a generator that writes one field only must land
                # in the same cohort as one that writes both.
                fields = _ladder_numeric_fields(electrical)
                if fields:
                    for prefix, suffix, index in ladder_keys(label):
                        key = (str(info.get("name") or ""), prefix, suffix)
                        cohorts.setdefault(key, []).append((index, lineno, label, fields))
    # A label now belongs to one candidate cohort per numeric run, so the same
    # record can be condemned by more than one of them. Report it once.
    seen = set()
    for members, why in find_arithmetic_ladders(cohorts):
        for _index, lineno, label, _fields in members:
            if (lineno, label) in seen:
                continue
            seen.add((lineno, label))
            findings.append((lineno, label, why))
    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    args = parser.parse_args()

    total = unidentifiable = 0
    quarantined_refs = load_quarantined_fabricated(args.data)
    for path in sorted(args.data.glob("*.ndjson")):
        name = path.name
        # quarantine files are where fabricated parts are SUPPOSED to live
        if "quarantine" in name or "pending" in name or name.endswith(".bak"):
            continue
        # the denylist is the guard's own memory -- a list of condemned IDENTITIES,
        # not component records. Screening it reports every one of its lines as
        # UNIDENTIFIABLE, which buries the real findings under its own bookkeeping.
        if name == "fabricated_denylist.ndjson":
            continue
        stats = new_stats()
        findings = check_file(path, quarantined_refs, stats)
        seen = (f"{name}: {stats['rows']} rows, {stats['screened']} part(s) screened"
                + (f", {stats['nestedScreened']} nested" if stats["nestedScreened"] else "")
                + (f", {stats['nonComponentRows']} non-component row(s)" if stats["nonComponentRows"] else "")
                + (f", {stats['unidentifiable']} UNIDENTIFIABLE" if stats["unidentifiable"] else ""))
        print(seen)
        unidentifiable += stats["unidentifiable"]
        if findings:
            total += len(findings)
            print(f"  {len(findings)} finding(s)")
            for lineno, label, why in findings[:10]:
                print(f"  line {lineno}: {label} -- {why}")
            if len(findings) > 10:
                print(f"  ... and {len(findings) - 10} more")

    if total:
        print(
            f"\nFAIL: {total} finding(s) in live catalogues"
            + (f", of which {unidentifiable} row(s) the guard CANNOT IDENTIFY (neither "
               "partNumber nor reference) -- give them an identity or quarantine them; "
               "a row the guard cannot name is not passed" if unidentifiable else "")
            + ".\nFabricated and physically impossible parts must not ship. Quarantine them "
            "(quarantine_fabricated_magnetics.py and quarantine_impossible_diodes.py are "
            "the templates), or if a flagged part is genuinely real, correct it from its "
            "datasheet with real values and provenance.",
            file=sys.stderr,
        )
        return 1
    print("OK: no fabricated, physically impossible or unidentifiable parts in live catalogues")
    return 0


if __name__ == "__main__":
    sys.exit(main())
