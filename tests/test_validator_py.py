"""Smoke tests for the tas_validator C++/pybind11 module.

Build the module first:
    cd TAS/validator && cmake -B build -G Ninja && cmake --build build

Then run from the TAS repo root:
    PYTHONPATH=validator/build pytest tests/test_validator_py.py
"""
import json
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "validator" / "build"

# Make the freshly built module importable without installing it.
if BUILD.exists():
    sys.path.insert(0, str(BUILD))

tas_validator = pytest.importorskip(
    "tas_validator",
    reason=f"build the module first (see TAS/validator/BUILD.md); looked in {BUILD}",
)

DATA = REPO / "data"
FILES = ["magnetics", "capacitors", "resistors", "diodes", "mosfets", "igbts"]
SAMPLE = int(os.environ.get("TAS_VALIDATOR_SAMPLE", "500"))


def iter_records(name, limit):
    path = DATA / f"{name}.ndjson"
    if not path.exists():
        pytest.skip(f"{path} not present")
    with open(path) as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            line = line.strip()
            if line:
                yield i, json.loads(line)


def test_module_surface():
    codes = tas_validator.check_codes()
    assert isinstance(codes, list) and len(codes) > 20
    assert "MAG_ENERGY_DENSITY" in codes


# check_codes() is a hand-maintained list, so a new rule can be emitted by the
# validator while nothing declares it — it then reads as "not a code this
# validator has" to anything that enumerates the registry. Tie the list back to
# the emit sites so the drift cannot recur silently.
EMIT_SITES = [
    # emit(out, ctx, "CODE", …) — the per-part rules
    re.compile(r'\bemit\s*\([^,]+,\s*[^,]+,\s*"([A-Z][A-Z0-9_]+)"'),
    # out.push_back({ref, "CODE", …}) — the corpus rules in corpus.cpp
    re.compile(r'\bout\.push_back\s*\(\s*\{[^;]*?"([A-Z][A-Z0-9_]+)"'),
]


def test_every_emitted_code_is_declared():
    """Every code the C++ can emit must appear in check_codes()."""
    src_dir = REPO / "validator" / "src"
    if not src_dir.is_dir():
        pytest.skip(f"validator sources not present at {src_dir}")

    emitted = set()
    for path in sorted(src_dir.glob("*.cpp")):
        text = path.read_text()
        for pattern in EMIT_SITES:
            emitted.update(pattern.findall(text))
    assert emitted, f"no emit sites found under {src_dir} — the scan is broken"

    declared = set(tas_validator.check_codes()) | set(tas_validator.circuit_check_codes())
    undeclared = sorted(emitted - declared)
    assert not undeclared, (
        f"{len(undeclared)} check code(s) are emitted but missing from "
        f"check_codes(): {undeclared}"
    )


def test_known_good_inductor_is_valid():
    """The real WE-MAPI 74438356010 part must validate.

    NOTE: this test previously referenced 744383560R33, which was found to be
    fabricated (absent from Würth's own REDEXPERT catalogue and its datasheet
    endpoint 404s) and quarantined 2026-07-31 (see data/quarantine.ndjson,
    _validatorQuarantine.reason). A "known good" reference whose part turned
    out not to exist must not silently `pytest.skip` when it can no longer be
    found — that hides the exact regression it exists to catch. So this now
    hard-fails (not skips) if the reference part disappears from the catalog
    again, and points at a part independently citation-verified 2026-08-01.
    """
    target = None
    for _, rec in iter_records("magnetics", 5000):
        ref = rec.get("magnetic", {}).get("manufacturerInfo", {}).get("reference")
        if ref == "74438356010":
            target = rec
            break
    if target is None:
        pytest.fail(
            "reference part 74438356010 not found in the first 5000 magnetics "
            "records — the known-good fixture has moved or been removed; this "
            "must be investigated, not silently skipped"
        )
    v = tas_validator.validate(target)
    assert v.valid, [(f.code, f.message) for f in v.findings]


@pytest.mark.parametrize("name", FILES)
def test_sample_validates_without_crashing(name):
    """Every sampled record returns a Verdict (or raises only on malformed data)."""
    seen = 0
    malformed = 0
    for _, rec in iter_records(name, SAMPLE):
        seen += 1
        try:
            v = tas_validator.validate(rec)
        except RuntimeError:
            # MalformedField surfaces as RuntimeError in Python — acceptable, the
            # record has a bad field shape and the validator refused to guess.
            malformed += 1
            continue
        assert isinstance(v.valid, bool)
        for f in v.findings:
            assert f.severity in ("SUSPICIOUS", "IMPOSSIBLE")
    if seen:
        # Sanity: a healthy catalog should not be (almost) entirely malformed.
        assert malformed < seen, f"{name}: {malformed}/{seen} records malformed"


def test_dict_and_json_string_agree():
    rec = next(iter_records("magnetics", 1))[1]
    v1 = tas_validator.validate(rec)
    v2 = tas_validator.validate_json(json.dumps(rec))
    assert v1.valid == v2.valid
    assert len(v1.findings) == len(v2.findings)


def _magnetic(electrical, mech=None, description=""):
    ds = {"electrical": [electrical]}
    if mech:
        ds["mechanical"] = mech
    if description:
        ds["part"] = {"description": description}
    return {"magnetic": {"manufacturerInfo": {
        "name": "Fixture", "reference": "FIX-1", "status": "production",
        "datasheetInfo": ds}}}


def _codes(rec):
    return {(f.code, f.severity) for f in tas_validator.validate(rec).findings}


def test_diss_density_flags_the_abt351_defect_class():
    """The real pre-repair VLBUC12060110R20LF4 numbers: 59 ohm stored as the DCR
    of a 70 A busbar choke in a 12x6x6 mm package — every older window passed it;
    this is the row class MAG_DISS_DENSITY exists for."""
    rec = _magnetic({"subtype": "inductor", "inductance": {"nominal": 1e-07},
                     "dcResistances": [{"maximum": 59.0}], "ratedCurrents": [70.0]},
                    mech={"length": {"nominal": 0.012}, "width": {"nominal": 0.006},
                          "height": {"nominal": 0.006}})
    assert ("MAG_DISS_DENSITY", "IMPOSSIBLE") in _codes(rec)


def test_diss_density_reads_the_plural_dcr_shape():
    """dcResistances[0] (the common-mode-choke form) must be read — the worst
    offenders of ABT #351 stored their corruption in the plural field."""
    rec = _magnetic({"subtype": "commonModeChoke", "inductance": {"nominal": 1e-05},
                     "dcResistances": [{"maximum": 160.0}], "ratedCurrents": [75.0]},
                    mech={"length": {"nominal": 0.014}, "width": {"nominal": 0.014},
                          "height": {"nominal": 0.010}})
    assert ("MAG_DISS_DENSITY", "IMPOSSIBLE") in _codes(rec)


def test_diss_density_passes_a_large_legitimate_part():
    """A 41 mm three-phase CMC at its 40 C-rise rating (the WE 744837006400
    numbers, REDEXPERT-exact): 5.1 W over a big package is normal physics."""
    rec = _magnetic({"subtype": "commonModeChoke", "inductance": {"nominal": 6e-04},
                     "dcResistances": [{"maximum": 0.0032}], "ratedCurrents": [40.0]},
                    mech={"length": {"nominal": 0.041}, "width": {"nominal": 0.041},
                          "height": {"nominal": 0.030}})
    assert not any(c == "MAG_DISS_DENSITY" for c, _ in _codes(rec))


def test_diss_density_floor_spares_small_parts_at_vendor_ratings():
    """An 0402 RF inductor at its vendor-rated 1770 mA dissipates ~0.13 W — under
    the absolute floor, where pad conduction dominates and the areal model is
    invalid (the Murata pass-1 gate mistake, encoded so it stays fixed)."""
    rec = _magnetic({"subtype": "inductor", "inductance": {"nominal": 5.6e-09},
                     "dcResistance": {"maximum": 0.04}, "ratedCurrents": [1.77]},
                    mech={"length": {"nominal": 0.001}, "width": {"nominal": 0.0006},
                          "height": {"nominal": 0.0005}})
    assert not any(c == "MAG_DISS_DENSITY" for c, _ in _codes(rec))


def test_diss_density_excludes_the_false_positive_classes():
    """F1 current-sense (primary current x winding R is not a dissipation),
    F2 Isat-quoted molded parts, F3 chip beads (WE 7427920's own datasheet pairs
    9600 mA with 0.15 ohm — non-simultaneous ratings)."""
    ct = _magnetic({"subtype": "inductor", "dcResistance": {"maximum": 1.7},
                    "ratedCurrents": [170.0]},
                   mech={"length": {"nominal": 0.010}, "width": {"nominal": 0.010}},
                   description="Current Sense Transformer 1:100")
    isat = _magnetic({"subtype": "inductor", "dcResistance": {"maximum": 0.004},
                      "ratedCurrents": [128.0]},
                     mech={"length": {"nominal": 0.011}, "width": {"nominal": 0.010}})
    bead = _magnetic({"subtype": "chipBead", "dcResistance": {"maximum": 0.15},
                      "ratedCurrents": [9.6]},
                     mech={"length": {"nominal": 0.002}, "width": {"nominal": 0.00125},
                           "height": {"nominal": 0.0009}})
    for rec in (ct, isat, bead):
        assert not any(c == "MAG_DISS_DENSITY" for c, _ in _codes(rec))


def test_severity_is_a_screaming_case_string_not_an_enum():
    """Pin the shape of Finding.severity — five repair scripts got this wrong.

    The binding exposes a `Severity` enum whose members are `Impossible` and
    `Suspicious`, which makes `str(f.severity).endswith("Impossible")` look right.
    It is not: `severity` on a Finding is a plain str in SCREAMING case, so that
    test is ALWAYS False. Five ABT #351 repair scripts used it as their
    post-repair "reject anything Blade Runner calls impossible" gate, and every
    one of those gates was a silent no-op — the worst kind, because it reported
    success. Found 2026-07-30 when a corpus sweep written the same way returned
    0 findings against a row that visibly fires MAG_DISS_DENSITY.

    If this test fails, the comparison idiom in scripts/*.py must change with it.
    """
    rec = _magnetic({"subtype": "inductor", "inductance": {"nominal": 1e-07},
                     "dcResistances": [{"maximum": 59.0}], "ratedCurrents": [70.0]},
                    mech={"length": {"nominal": 0.012}, "width": {"nominal": 0.006},
                          "height": {"nominal": 0.006}})
    findings = tas_validator.validate(rec).findings
    assert findings, "expected this row to fire MAG_DISS_DENSITY"
    for f in findings:
        assert isinstance(f.severity, str), f"severity is {type(f.severity)}, not str"
        assert f.severity in ("OK", "SUSPICIOUS", "IMPOSSIBLE"), f.severity
        assert not str(f.severity).endswith("Impossible")   # the trap, kept explicit
        assert not str(f.severity).endswith("Suspicious")


# ── ABT #387: the three older MAG_* checks must see common-mode chokes ───────────
#
# An inductor stores its winding resistance as a singular `dcResistance`; a
# common-mode choke stores `dcResistances[]`, one entry per winding. MAG_DCR_GEOM,
# MAG_DCR_PER_H and MAG_ISAT_POWER read only the singular field, so on every choke
# in the catalogue they found NOTHING — which is indistinguishable from a clean
# part. They reported success on a population they had never looked at.
#
# These tests pin the fix by asserting the SAME record fires the SAME code in both
# shapes. If a future reader regresses to the singular field, the plural case goes
# quiet and these fail — which is the only way this class of bug is visible.

def _both_shapes(dcr, **electrical):
    """The same operating point written the inductor way and the choke way."""
    singular = dict(electrical, dcResistance={"maximum": dcr})
    plural = dict(electrical, dcResistances=[{"maximum": dcr}])
    return singular, plural


def test_dcr_geom_sees_the_plural_choke_shape():
    """MAG_DCR_GEOM: DCR*size^2/L, on a 41x41x30 mm choke with an absurd 3.2 kohm."""
    mech = {"length": {"nominal": 0.041}, "width": {"nominal": 0.041},
            "height": {"nominal": 0.030}}
    sing, plur = _both_shapes(3200.0, subtype="commonModeChoke",
                              inductance={"nominal": 6e-04})
    for rec in (_magnetic(sing, mech=mech), _magnetic(plur, mech=mech)):
        assert any(c == "MAG_DCR_GEOM" for c, _ in _codes(rec)), _codes(rec)


def test_dcr_per_h_sees_the_plural_choke_shape():
    """MAG_DCR_PER_H: 5 kohm across a 1 mH choke is 5e6 ohm/H, either way round.

    Deliberately NOT written at L = 1 uH: the suspicious tier requires L strictly
    greater than 1 uH, so a fixture sitting exactly on the boundary reports nothing
    and would look like the plural read had failed.
    """
    sing, plur = _both_shapes(5000.0, subtype="commonModeChoke",
                              inductance={"nominal": 1e-03})
    for rec in (_magnetic(sing), _magnetic(plur)):
        assert any(c == "MAG_DCR_PER_H" for c, _ in _codes(rec)), _codes(rec)


def test_isat_power_sees_the_plural_choke_shape():
    """MAG_ISAT_POWER: Isat^2*DCR = 40^2 * 5 = 8 kW, however the DCR is stored."""
    sing, plur = _both_shapes(5.0, subtype="commonModeChoke",
                              inductance={"nominal": 1e-03},
                              saturationCurrentPeak=40.0)
    for rec in (_magnetic(sing), _magnetic(plur)):
        assert any(c == "MAG_ISAT_POWER" for c, _ in _codes(rec)), _codes(rec)


def test_a_sound_choke_stays_clean_in_the_plural_shape():
    """The fix must not turn 'never examined' into 'always flagged'.

    A real Wurth-class 600 uH CMC — 3.2 mohm across a 41x41x30 mm core — has to come
    back clean, or the un-hidden population would drown in false positives.
    """
    rec = _magnetic({"subtype": "commonModeChoke", "inductance": {"nominal": 6e-04},
                     "dcResistances": [{"maximum": 0.0032}], "ratedCurrents": [40.0]},
                    mech={"length": {"nominal": 0.041}, "width": {"nominal": 0.041},
                          "height": {"nominal": 0.030}})
    for code in ("MAG_DCR_GEOM", "MAG_DCR_PER_H", "MAG_ISAT_POWER"):
        assert not any(c == code for c, _ in _codes(rec)), _codes(rec)


# ── ABT #432: a DCR/L ratio needs both to describe the SAME winding ──────────────
#
# The three ratio checks (MAG_DCR_GEOM, MAG_DCR_PER_H, MAG_ISAT_POWER) divide a
# winding's resistance by an inductance. That is a physical quantity only when both
# belong to the same winding. On a transformer they need not: Wuerth's WE-CST
# 7492540500 is a 1:500 current sense transformer whose 0.135 H is the 500-turn
# SECONDARY and whose 0.28 mohm is the single-turn PRIMARY through the core, so the
# ratio came out a quarter of a million times off with the data entirely correct.
# Nine parts were flagged that way.
#
# The pair of tests below guards both directions at once, which is the only useful
# shape: it must stop firing on the transformer AND keep firing on the common-mode
# choke. Exempting all multi-winding parts would have been the easy fix and would
# have re-hidden the 2,895 chokes that ABT #387 spent its existence un-hiding.


def test_ratio_checks_skip_a_transformer_and_say_so():
    """The real WE-CST 7492540500 numbers: 1:500, 135 mH secondary, 0.28 mohm primary.

    Two assertions, and the second matters as much as the first. The ratio checks
    must not fire — and the skip must be RECORDED, because a check that quietly
    examines nothing is indistinguishable from one that found nothing wrong. That
    confusion is exactly what ABT #387 was raised for.
    """
    rec = _magnetic({"subtype": "transformer", "inductance": {"nominal": 0.135},
                     "dcResistances": [{"nominal": 0.00028, "maximum": 0.00028}],
                     "ratedCurrents": [40.0]},
                    mech={"length": {"nominal": 0.0202}, "width": {"nominal": 0.01448},
                          "height": {"nominal": 0.0105}})
    v = tas_validator.validate(rec)
    fired = {f.code for f in v.findings}
    assert not (fired & {"MAG_DCR_GEOM", "MAG_DCR_PER_H", "MAG_ISAT_POWER"}), \
        f"ratio check fired across windings: {[(f.code, f.message) for f in v.findings]}"
    assert any("not associated with the inductance" in s for s in v.skipped), \
        f"the skip must be recorded, not silent; skipped={list(v.skipped)}"


def test_ratio_checks_still_examine_common_mode_chokes():
    """A choke's windings are identical, so its DCR and inductance DO pair.

    The real Bourns SRF7038A-102Y corruption from ABT #431: the datasheet's 1020 ohm
    IMPEDANCE stored as a DC resistance against a 10 uH inductance. If narrowing the
    transformer case ever swallows this, the 2,895 chokes go dark again.
    """
    rec = _magnetic({"subtype": "commonModeChoke", "inductance": {"nominal": 1e-05},
                     "dcResistances": [{"maximum": 1020.0}]},
                    mech={"length": {"nominal": 0.0070}, "width": {"nominal": 0.0038},
                          "height": {"nominal": 0.0030}})
    fired = {c for c, _ in _codes(rec)}
    assert "MAG_DCR_PER_H" in fired, f"choke must still be examined; fired={fired}"


def test_a_plain_inductor_is_unaffected_by_the_pairing_rule():
    """One winding, so the pairing is trivially satisfied and nothing changes."""
    rec = _magnetic({"subtype": "inductor", "inductance": {"nominal": 1e-07},
                     "dcResistance": {"maximum": 59.0}},
                    mech={"length": {"nominal": 0.012}, "width": {"nominal": 0.006},
                          "height": {"nominal": 0.006}})
    fired = {c for c, _ in _codes(rec)}
    assert fired & {"MAG_DCR_GEOM", "MAG_DCR_PER_H"}, \
        f"a single-winding part must still be checked; fired={fired}"


# ── ABT #458: a transformer's short winding-resistance list ──────────────────────
#
# MAS documents dcResistances as "DC resistance per winding" (positional) and
# turnsRatios as "one entry per secondary", so a record that declares secondaries and
# records fewer resistances than windings is missing its secondaries' copper. An
# ABSENT list says "no data" plainly; a SHORT one looks populated and is not, so a
# loss calculation sees a fraction of the copper with no warning.


def test_short_winding_resistance_list_is_flagged():
    """A 1:1-declared transformer with two windings and one resistance is incomplete."""
    rec = _magnetic({"subtype": "transformer", "inductance": {"nominal": 1e-03},
                     "turnsRatios": [1.0], "dcResistances": [{"maximum": 0.5}]})
    assert ("MAG_WINDING_DATA_INCOMPLETE", "SUSPICIOUS") in _codes(rec)


def test_complete_winding_resistance_list_is_not_flagged():
    """Two windings, two resistances — nothing missing."""
    rec = _magnetic({"subtype": "transformer", "inductance": {"nominal": 1e-03},
                     "turnsRatios": [1.0],
                     "dcResistances": [{"maximum": 0.5}, {"maximum": 0.9}]})
    assert not any(c == "MAG_WINDING_DATA_INCOMPLETE" for c, _ in _codes(rec))


def test_short_list_is_flagged_even_with_no_inductance():
    """The check must run ABOVE check_point's inductance early-return.

    This is the regression that matters. The check compares two counts and needs no
    inductance, but it was first written below the `if (!L) return;` guard — where it
    silently skipped 287 of the 485 affected entries, 59 %, because a transformer that
    records no magnetizing inductance never reached it. That is the ABT #387 failure
    reproduced inside the fix for its own follow-up: a check that examines nothing and
    reports success. Caught by measuring the corpus and finding 198 where 485 were
    expected, not by reading the code.
    """
    rec = _magnetic({"subtype": "transformer", "turnsRatios": [1.0, 2.0],
                     "dcResistances": [{"maximum": 0.5}]})
    assert ("MAG_WINDING_DATA_INCOMPLETE", "SUSPICIOUS") in _codes(rec), \
        "a transformer with no inductance must still have its winding count checked"


def test_absent_resistance_list_is_not_flagged_as_incomplete():
    """No resistances at all is missing data, not a misleadingly short list."""
    rec = _magnetic({"subtype": "transformer", "inductance": {"nominal": 1e-03},
                     "turnsRatios": [1.0]})
    assert not any(c == "MAG_WINDING_DATA_INCOMPLETE" for c, _ in _codes(rec))


# ---------------------------------------------------------------------------
# Finding.severity: the enum/string comparison trap
#
# `Finding.severity` used to be a plain uppercase str ('IMPOSSIBLE') while the
# module ALSO exported a `Severity` enum whose members stringify as
# 'Severity.Impossible'. So the obvious way to write the gate --
#     any(f.severity == tas_validator.Severity.Impossible for f in v.findings)
# -- was SILENTLY False for every record ever validated: "do we have any
# impossible parts?" answered "no" vacuously, with nothing raised anywhere.
#
# The fix returns the enum, and keeps every older spelling working (str(), the
# f-string form, the bare 'IMPOSSIBLE' comparison, and use as a dict/set key)
# rather than breaking it. These tests fail on the pre-fix binding: the first
# two by asserting the enum comparison is True where it used to be False.
# ---------------------------------------------------------------------------


def _impossible_verdict():
    """A record with a guaranteed IMPOSSIBLE finding (a fabrication-template MPN)."""
    rec = {"capacitor": {"manufacturerInfo": {
        "reference": "MLCC123456",
        "datasheetInfo": {"part": {"technology": "ceramic-class-2"},
                          "electrical": {"capacitance": {"nominal": 1e-7},
                                         "ratedVoltage": 50.0},
                          "provenance": [{"source": "manufacturerDatasheet"}]}}}}
    v = tas_validator.validate(rec)
    assert v.findings, "fixture no longer produces any finding"
    return v


def test_severity_compares_equal_to_the_exported_enum():
    v = _impossible_verdict()
    assert any(f.severity == tas_validator.Severity.Impossible for f in v.findings)
    assert not v.valid


def test_severity_enum_membership_test_is_not_vacuous():
    """The shape a caller actually writes: a set of enum members."""
    v = _impossible_verdict()
    wanted = {tas_validator.Severity.Impossible}
    assert any(f.severity in wanted for f in v.findings)


def test_severity_string_spelling_keeps_working():
    v = _impossible_verdict()
    sev = [f.severity for f in v.findings if f.code == "GEN_FABRICATED_MPN"][0]
    assert sev == "IMPOSSIBLE"
    assert "IMPOSSIBLE" == sev              # reflected comparison
    assert str(sev) == "IMPOSSIBLE"
    assert f"{sev}" == "IMPOSSIBLE"
    assert sev != "SUSPICIOUS"
    assert sev in ("SUSPICIOUS", "IMPOSSIBLE")


def test_severity_hashes_as_its_own_name():
    """An enum member and its uppercase name must be interchangeable as keys —
    otherwise a Counter built one way cannot be read the other."""
    sev = tas_validator.Severity.Impossible
    assert hash(sev) == hash("IMPOSSIBLE")
    assert {"IMPOSSIBLE": 1}[sev] == 1
    assert {sev: 1}["IMPOSSIBLE"] == 1


def test_severity_does_not_claim_equality_with_unrelated_types():
    sev = tas_validator.Severity.Impossible
    assert sev != 3.14
    assert sev != None  # noqa: E711
    assert sev != "SUSPICIOUS"


def test_severity_is_a_str_subclass_so_old_consumers_are_unaffected():
    """The transition itself must not produce the mirror-image false pass.

    A consumer written against the old plain-string attribute (`f.severity ==
    'IMPOSSIBLE'`, `f.severity in ('SUSPICIOUS','IMPOSSIBLE')`, `"%s" %
    f.severity`, a Counter keyed on it) keeps working unchanged, because the
    enum members ARE the uppercase strings they print. A pure (non-str) enum
    would silently answer False to all of those — which is exactly the failure
    being removed, pointed the other way.
    """
    sev = tas_validator.Severity.Impossible
    assert isinstance(sev, str)
    assert sev == "IMPOSSIBLE"
    assert "%s" % sev == "IMPOSSIBLE"
    assert "IMPOSSIBLE".__eq__(sev) is True
    assert sorted({tas_validator.Severity.Ok, "OK"}) == ["OK"]


def test_corpus_finding_severity_is_the_same_type():
    """CorpusFinding.severity goes through the same binding as Finding.severity."""
    sev = tas_validator.Severity.Suspicious
    assert str(sev) == "SUSPICIOUS"
    rec = {"capacitor": {"manufacturerInfo": {
        "name": "Fixture", "reference": "FIX-1",
        "datasheetInfo": {"electrical": {"capacitance": {"nominal": 1e-7},
                                         "ratedVoltage": 50.0}}}}}
    findings = tas_validator.validate_corpus([rec] * 3)
    for f in findings:
        assert isinstance(f.severity, tas_validator.Severity)
        assert f.severity in (tas_validator.Severity.Suspicious,
                              tas_validator.Severity.Impossible)
        assert str(f.severity) in ("SUSPICIOUS", "IMPOSSIBLE")


# ---------------------------------------------------------------------------
# The four families that used to throw "no known component discriminator"
# ---------------------------------------------------------------------------

NEW_FILES = ["relays", "switches", "potentiometers", "connector_accessories"]


@pytest.mark.parametrize("name", NEW_FILES)
def test_formerly_unjudgeable_families_are_judged(name):
    seen = 0
    for _, rec in iter_records(name, SAMPLE):
        seen += 1
        v = tas_validator.validate(rec)     # must NOT raise ValueError any more
        assert isinstance(v.valid, bool)
    assert seen, f"{name}.ndjson produced no records"


def test_circuit_bricks_are_named_as_such_instead_of_unknown():
    brick = {"name": "b", "ports": [{"name": "a"}], "components": [], "connections": []}
    with pytest.raises(ValueError, match="validate_circuit"):
        tas_validator.validate(brick)


def test_converter_documents_are_named_as_such():
    with pytest.raises(ValueError, match="converter document"):
        tas_validator.validate({"inputs": {}, "topology": {"stages": []}})


# ---------------------------------------------------------------------------
# Completeness for the families that used to return the -1.0 "not scored"
# sentinel: diodes, time bases and connector accessories.
# ---------------------------------------------------------------------------

def _diode(sub_type, electrical):
    return {"semiconductor": {"diode": {"manufacturerInfo": {
        "name": "Fixture", "reference": "FIX-D",
        "datasheetInfo": {"part": {"partNumber": "FIX-D", "subType": sub_type},
                          "electrical": electrical}}}}}


def test_diode_completeness_is_scored_per_subtype():
    """A zener's manifest is the zener one, not the rectifying one."""
    zener_block = {"breakdownVoltage": 12.0, "powerDissipation": 0.5,
                   "zenerTestCurrent": 0.005}
    assert tas_validator.validate(_diode("zener", zener_block)).completeness == 1.0
    # The SAME electrical block on a rectifier carries none of {reverseVoltage,
    # forwardVoltage, forwardCurrent, surgeCurrent|reverseLeakageCurrent}. If the
    # family shared one manifest this could not differ from the line above.
    assert tas_validator.validate(_diode("rectifier", zener_block)).completeness == 0.0

    rect_block = {"reverseVoltage": 100.0, "forwardVoltage": 0.7,
                  "forwardCurrent": 1.0, "surgeCurrent": 30.0}
    assert tas_validator.validate(_diode("rectifier", rect_block)).completeness == 1.0
    assert tas_validator.validate(_diode("zener", rect_block)).completeness == 0.0
    # No subType at all falls to the rectifying manifest (the SAS schema's own
    # else-branch), not to "unscored".
    assert tas_validator.validate(_diode(None, rect_block)).completeness == 1.0


def test_diode_partial_manifest_gives_a_fraction():
    v = tas_validator.validate(_diode("tvs", {"standoffVoltage": 5.0,
                                              "clampingVoltage": 9.2}))
    assert v.completeness == pytest.approx(0.5)


def test_time_base_completeness_is_scored():
    osc = {"timeBase": {"oscillator": {"manufacturerInfo": {
        "name": "Fixture", "reference": "FIX-X",
        "datasheetInfo": {"part": {"partNumber": "FIX-X"},
                          "electrical": {"frequency": 25e6,
                                         "frequencyStability": 50e-6,
                                         "outputType": "lvcmos"}}}}}}
    assert tas_validator.validate(osc).completeness == 1.0
    bare = json.loads(json.dumps(osc))
    bare["timeBase"]["oscillator"]["manufacturerInfo"]["datasheetInfo"]["electrical"] = {
        "technology": "quartzCrystal"}
    # `technology` is parametric tagging, not a datasheet spec — it must not score.
    assert tas_validator.validate(bare).completeness == 0.0


def test_connector_accessory_is_scored_outside_the_electrical_object():
    """Four fifths of accessories have no `electrical`; their content is elsewhere."""
    def acc(datasheet_info):
        return {"connectorAccessory": {"manufacturerInfo": {
            "name": "Fixture", "reference": "FIX-A",
            "datasheetInfo": datasheet_info}}}

    rich = acc({"part": {"partNumber": "FIX-A"},
                "accessoryDetails": {"kind": "backshell", "cableExit": "straight"},
                "hostSystem": {"series": "Mini-Fit Jr."}})
    assert tas_validator.validate(rich).completeness == 1.0

    half = acc({"part": {"partNumber": "FIX-A"},
                "accessoryDetails": {"kind": "backshell", "cableExit": "straight"}})
    assert tas_validator.validate(half).completeness == pytest.approx(0.5)

    # Identity only: the kind alone is not a datasheet field, it is schema-required.
    stub = acc({"part": {"partNumber": "FIX-A"},
                "accessoryDetails": {"kind": "tooling"}})
    assert tas_validator.validate(stub).completeness == 0.0

    # An accessory whose only content is a contact rating scores off `electrical`
    # through the same path mechanism.
    contact = acc({"part": {"partNumber": "FIX-A"},
                   "accessoryDetails": {"kind": "contact"},
                   "electrical": {"ratedCurrentPerContact": 13.0}})
    assert tas_validator.validate(contact).completeness == pytest.approx(0.5)


NEWLY_SCORED = ["diodes", "timing_devices", "connector_accessories"]


@pytest.mark.parametrize("name", NEWLY_SCORED)
def test_live_rows_of_newly_scored_families_are_not_sentinel(name):
    seen = 0
    for _, rec in iter_records(name, SAMPLE):
        seen += 1
        v = tas_validator.validate(rec)
        assert v.completeness >= 0.0, f"{name} row {seen} still returns the -1 sentinel"
        assert v.completeness <= 1.0
    assert seen, f"{name}.ndjson produced no records"


def test_newly_scored_families_have_real_spread():
    """A rich family must not sit at 0 and a stub family must not sit at 1."""
    osc = [tas_validator.validate(r).completeness
           for _, r in iter_records("timing_devices", SAMPLE)]
    assert min(osc) > 0.0 and sum(osc) / len(osc) > 0.9

    # A wider window than SAMPLE: the first identity-only accessory in the live
    # file is row 1,993, so a 500-row head would never see the 0.0 end.
    acc = [tas_validator.validate(r).completeness
           for _, r in iter_records("connector_accessories", max(SAMPLE, 2500))]
    assert min(acc) == 0.0, "identity-only accessories must score 0"
    assert max(acc) == 1.0, "fully described accessories must score 1"


def test_not_scored_sentinel_is_still_reachable():
    """-1.0 must stay a live value for a family with no manifest."""
    integrator = {"analog": {"integrator": {"manufacturerInfo": {
        "name": "Fixture", "reference": "FIX-I",
        "datasheetInfo": {"part": {"partNumber": "FIX-I"},
                          "behavioral": {"integratorType": "inverting"}}}}}}
    assert tas_validator.validate(integrator).completeness == -1.0


def test_electrical_only_families_keep_their_scores():
    """The path-aware lookup must not perturb a family scored off `electrical`."""
    mosfet = {"semiconductor": {"mosfet": {"manufacturerInfo": {
        "name": "Fixture", "reference": "FIX-M",
        "datasheetInfo": {"part": {"partNumber": "FIX-M"},
                          "electrical": {"onResistance": 0.05,
                                         "drainSourceVoltage": 600.0,
                                         "continuousDrainCurrent": 10.0,
                                         "gateThresholdVoltage": 3.0}}}}}}
    assert tas_validator.validate(mosfet).completeness == 1.0
    cap = {"capacitor": {"manufacturerInfo": {
        "name": "Fixture", "reference": "FIX-C",
        "datasheetInfo": {"electrical": {"capacitance": {"nominal": 1e-7}}}}}}
    assert tas_validator.validate(cap).completeness == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# The connector manifest and the rf family's own vocabulary
# ---------------------------------------------------------------------------

def _connector(family, electrical, impedance=None):
    fd = {"family": family}
    if impedance is not None:
        fd["characteristicImpedance"] = impedance
    return {"connector": {"manufacturerInfo": {
        "name": "Fixture", "reference": "FIX-K",
        "datasheetInfo": {"part": {"partNumber": "FIX-K"},
                          "familyDetails": fd,
                          "electrical": electrical}}}}


def test_rf_connectors_score_on_characteristic_impedance():
    """CONAS exempts rf from ratedCurrentPerContact; the manifest must agree."""
    rf = _connector("rf", {"ratedVoltage": 500.0}, impedance=50.0)
    v = tas_validator.validate(rf)
    assert v.completeness == 1.0
    assert not [f for f in v.findings if f.code == "GEN_SPARSE"]

    # Without the impedance there is nothing in the current-rating slot at all.
    no_z = _connector("rf", {"ratedVoltage": 500.0})
    assert tas_validator.validate(no_z).completeness == pytest.approx(0.5)


def test_rf_connectors_still_need_a_rated_voltage():
    """The alternation must not silence the working-voltage gap it does not cover."""
    v = tas_validator.validate(_connector("rf", {}, impedance=50.0))
    assert v.completeness == pytest.approx(0.5)
    assert [f for f in v.findings if f.code == "GEN_SPARSE"]


def test_non_rf_connector_sparseness_is_untouched():
    """The eleven other families are scored exactly as before."""
    thin = _connector("pinHeaderSocket", {"ratedCurrentPerContact": 3.0})
    v = tas_validator.validate(thin)
    assert v.completeness == pytest.approx(0.5)
    assert [f for f in v.findings if f.code == "GEN_SPARSE"], (
        "a non-rf connector missing ratedVoltage is a real sourcing gap and must "
        "stay reported"
    )
    full = _connector("pinHeaderSocket",
                      {"ratedCurrentPerContact": 3.0, "ratedVoltage": 250.0})
    assert tas_validator.validate(full).completeness == 1.0
    # A non-rf family cannot buy its way out with an impedance: the manifest is
    # chosen by the declared family, not by whatever the record happens to carry.
    cheat = _connector("pinHeaderSocket", {}, impedance=50.0)
    assert tas_validator.validate(cheat).completeness == 0.0


# 2026-09-23: the three checks added for blind spots that let bad data sit with
# zero findings. Exercised through the binding so a stale .so cannot pass them.
def test_blind_spot_codes_are_emittable():
    codes = set(tas_validator.check_codes())
    assert {"CONN_DWV_FLOOR", "DIO_VF_PROTECTION", "GEN_FOREIGN_ORDER_SUFFIX"} <= codes


def _codes(rec):
    return {(f.code, f.severity) for f in tas_validator.validate(rec).findings}


def test_connector_dwv_floor_needs_no_rated_voltage():
    rec = {"connector": {"manufacturerInfo": {"name": "TE Connectivity", "reference": "X",
           "datasheetInfo": {"provenance": [{"source": "manufacturerDatasheet"}],
                             "electrical": {"dielectricWithstandingVoltage": 37.0}}}}}
    assert ("CONN_DWV_FLOOR", "SUSPICIOUS") in _codes(rec)
    rec["connector"]["manufacturerInfo"]["datasheetInfo"]["electrical"][
        "dielectricWithstandingVoltage"] = 100.0
    assert not any(c == "CONN_DWV_FLOOR" for c, _ in _codes(rec))


def test_protection_diode_forward_voltage():
    def rec(vf):
        return {"semiconductor": {"diode": {"manufacturerInfo": {"reference": "ESD411",
                "datasheetInfo": {"provenance": [{"source": "manufacturerDatasheet"}],
                                  "part": {"subType": "esd", "technology": "Si"},
                                  "electrical": {"standoffVoltage": 5.5,
                                                 "forwardVoltage": vf}}}}}}
    assert ("DIO_VF_PROTECTION", "IMPOSSIBLE") in _codes(rec(7.4))
    assert not any(c == "DIO_VF_PROTECTION" for c, _ in _codes(rec(3.5)))


def test_foreign_order_suffix_is_suspicious_and_scoped():
    def rec(mfr, ref):
        return {"controller": {"manufacturerInfo": {"name": mfr, "reference": ref,
                "datasheetInfo": {"provenance": [{"source": "manufacturerDatasheet"}],
                                  "function": {"category": "pwmController"},
                                  "part": {"deviceType": "controller", "partNumber": ref}}}}}
    assert ("GEN_FOREIGN_ORDER_SUFFIX", "SUSPICIOUS") in _codes(rec("Maxim Integrated",
                                                                    "MAX17501ASLE"))
    assert not any(c == "GEN_FOREIGN_ORDER_SUFFIX" for c, _ in _codes(rec("Infineon",
                                                                          "BSC010N04LS")))
