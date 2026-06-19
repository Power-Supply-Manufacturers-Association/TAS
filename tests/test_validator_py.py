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
