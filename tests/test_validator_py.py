"""Smoke tests for the tas_validator C++/pybind11 module.

Build the module first:
    cd TAS/validator && cmake -B build -G Ninja && cmake --build build

Then run from the TAS repo root:
    PYTHONPATH=validator/build pytest tests/test_validator_py.py
"""
import json
import os
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


def test_known_good_inductor_is_valid():
    """The real WE-MAPI 744383560R33 part must validate."""
    target = None
    for _, rec in iter_records("magnetics", 5000):
        ref = rec.get("magnetic", {}).get("manufacturerInfo", {}).get("reference")
        if ref == "744383560R33":
            target = rec
            break
    if target is None:
        pytest.skip("reference part 744383560R33 not found in sample")
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
