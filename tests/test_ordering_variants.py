"""The ingest gate's ordering-code variant check (scripts/ingest_gate.py).

A vendor prints a device once (Si7942DP) and orders it under packaging/finish codes
(Si7942DP-T1-E3, -T1-GE3). On 2026-10-18 a vendor-pull agent stored the ordering code for 18
Vishay MOSFETs already live under the bare name, and the exact-spelling duplicate check passed
all 18. These pin the grammar and the lookup, including what must NOT be treated as a suffix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ingest_gate import KnownSpellings, ordering_base  # noqa: E402


def test_documented_vishay_suffixes_strip():
    assert ordering_base("Si7942DP-T1-E3", "Vishay") == "SI7942DP"
    assert ordering_base("Si7415DN-T1-GE3", "Vishay Siliconix") == "SI7415DN"
    assert ordering_base("SiSS52DN-T1-BE3", "Vishay") == "SISS52DN"
    assert ordering_base("GPP10J-E3", "Vishay General Semiconductor - Diodes Division") == "GPP10J"
    assert ordering_base("Si2302ADS-T1", "Vishay") == "SI2302ADS"


def test_identity_hyphens_and_other_vendors_are_untouched():
    assert ordering_base("SUP57N20-33", "Vishay") is None      # "-33" is part of the device name
    assert ordering_base("Si7942DP", "Vishay") is None
    assert ordering_base("10BJ6-0A", "Eaton") is None           # Eaton: hyphen is a decimal point
    assert ordering_base("Si7942DP-T1-E3", "onsemi") is None    # grammar is per manufacturer


def test_variants_both_directions_same_manufacturer_only():
    ks = KnownSpellings()
    ks.add("Si7942DP", "Vishay", "live")
    ks.add("Si7415DN-T1-GE3", "Vishay Siliconix", "live")
    assert [e[1] for e in ks.ordering_variants("Si7942DP-T1-E3", "Vishay")] == ["Si7942DP"]
    assert [e[1] for e in ks.ordering_variants("Si7415DN", "Vishay")] == ["Si7415DN-T1-GE3"]
    assert ks.ordering_variants("Si7943DP-T1-E3", "Vishay") == []
    assert ks.ordering_variants("Si7942DP-T1-E3", "SomeOtherCo") == []
    assert ks.ordering_variants("Si7942DP", "Vishay") == []      # its own exact spelling is not a variant


def test_index_rebuilds_after_add():
    ks = KnownSpellings()
    ks.add("Si7942DP", "Vishay", "live")
    assert ks.ordering_variants("Si7942DP-T1-E3", "Vishay")
    ks.add("Si9999DY-T1-E3", "Vishay", "live")
    assert ks.ordering_variants("Si9999DY", "Vishay")
