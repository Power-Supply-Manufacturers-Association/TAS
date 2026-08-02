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

Two signatures, both evidence-backed:

 1. KNOWN GENERATOR TEMPLATE -- the exact MPN shapes the fabrication scripts
    emitted, with case-sensitive units (nH/uH/mH, not the NF/MF/PF suffix codes
    real vendors use). These are provably synthetic.

 2. FORMULA-DERIVED DCR ON A BARE STUB -- dcResistance reproduces a known
    generator formula AND the record carries none of the corroboration a real
    datasheet entry has (no datasheet URL, no description, no saturation current,
    no SRF, no mechanical dimensions). The formula alone is not enough; the
    emptiness around it is what proves nothing was ever read from a datasheet.
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
    (re.compile(r"^(Coi|Bou|TDK|Wur|Vis|Mur|Pul|Sum)\d{3}u[A-Za-z0-9]+_\d+$"), "phase5 magnetics generator"),
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


def iter_parts(record):
    """Yield every (manufacturerInfo, electrical-dict) in a catalogue record.

    A record is identified by manufacturerInfo.reference OR
    datasheetInfo.part.partNumber — the phase2-5 generators wrote partNumber
    ONLY, which is precisely how 17,183 fabricated records slipped past the
    reference-keyed version of this guard (found in the ABT #256 audit).
    """
    if not isinstance(record, dict):
        return
    info = record.get("manufacturerInfo")
    if isinstance(info, dict):
        datasheet = info.get("datasheetInfo") or {}
        part_number = (datasheet.get("part") or {}).get("partNumber")
        if info.get("reference") or part_number:
            electrical = datasheet.get("electrical")
            if isinstance(electrical, list):
                electrical = electrical[0] if electrical else {}
            yield info, (electrical if isinstance(electrical, dict) else {})
    for value in record.values():
        if isinstance(value, dict):
            yield from iter_parts(value)


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
    """References already condemned as fabricated must never reappear live.

    Self-maintaining denylist: every *.quarantine_fabricated.ndjson record's
    reference. Zero false-positive risk (each entry was individually
    evidence-checked when it was quarantined) and exact recall against
    re-imports of the same invented parts.
    """
    refs = set()
    for qpath in data_dir.glob("*.quarantine_fabricated.ndjson"):
        with qpath.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for info, _ in iter_parts(record):
                    refs.add(str(info.get("reference", "")))
                    refs.add(str((((info.get("datasheetInfo") or {}).get("part")) or {})
                                 .get("partNumber", "")))
    refs.discard("")
    return refs


def check_file(path, quarantined_refs=frozenset()):
    findings = []
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
            for info, electrical in iter_parts(record):
                reference = str(info.get("reference", ""))
                part_number = str((((info.get("datasheetInfo") or {}).get("part")) or {})
                                  .get("partNumber", ""))
                ids = [i for i in (reference, part_number) if i]
                hit = next((why for pattern, why in KNOWN_TEMPLATES
                            for i in ids if pattern.match(i)), None)
                if hit:
                    findings.append((lineno, ids[0], f"MPN matches the {hit} generator template"))
                    continue
                if any(i in quarantined_refs for i in ids):
                    findings.append((lineno, ids[0],
                                     "reference was previously quarantined as fabricated "
                                     "(*.quarantine_fabricated.ndjson) and must not reappear live"))
                    continue
                bad_url = fake_provenance(info)
                if bad_url:
                    findings.append((lineno, ids[0] if ids else reference,
                                     f"sole provenance URL is not a product page: {bad_url}"))
                    continue
                inductance = (electrical.get("inductance") or {}).get("nominal")
                dcr = (electrical.get("dcResistance") or {}).get("maximum")
                if formula_dcr(inductance, dcr) and is_bare_stub(info, electrical):
                    findings.append((lineno, reference,
                                     "DCR reproduces a generator formula on a record with no "
                                     "datasheet, description, Isat, SRF or dimensions"))
                    continue
                impossible = impossible_ratings(info, electrical)
                if impossible:
                    findings.append((lineno, reference, impossible))
    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    args = parser.parse_args()

    total = 0
    quarantined_refs = load_quarantined_fabricated(args.data)
    for path in sorted(args.data.glob("*.ndjson")):
        name = path.name
        # quarantine files are where fabricated parts are SUPPOSED to live
        if "quarantine" in name or "pending" in name or name.endswith(".bak"):
            continue
        findings = check_file(path, quarantined_refs)
        if findings:
            total += len(findings)
            print(f"\n{name}: {len(findings)} fabricated part(s)")
            for lineno, reference, why in findings[:10]:
                print(f"  line {lineno}: {reference} -- {why}")
            if len(findings) > 10:
                print(f"  ... and {len(findings) - 10} more")

    if total:
        print(
            f"\nFAIL: {total} fabricated or physically impossible part(s) in live catalogues.\n"
            "Neither must ship. Quarantine them (quarantine_fabricated_magnetics.py and "
            "quarantine_impossible_diodes.py are the templates), or if a flagged part is "
            "genuinely real, correct it from its datasheet with real values and provenance.",
            file=sys.stderr,
        )
        return 1
    print("OK: no fabricated or physically impossible parts in live catalogues")
    return 0


if __name__ == "__main__":
    sys.exit(main())
