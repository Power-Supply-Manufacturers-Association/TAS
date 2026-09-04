"""The fabricated-parts guard must keep catching what it was taught.

scripts/check_no_fabricated_parts.py is the last gate before a catalogue ships, and
its rules are deliberately narrow — each one demands corroborating evidence rather
than a lone resemblance, because a guard that cries wolf gets switched off. That
narrowness is exactly what makes silent regression easy: widen a regex by one
character and a whole fabrication batch walks through.

These tests pin the ABT #351 signature (a sole provenance URL that is not a product
page) together with the negative cases that keep it honest.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_no_fabricated_parts", REPO / "scripts" / "check_no_fabricated_parts.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

FAKE_URL = "https://www.coilcraft.com/en-us/products/power-inductors/epl2010/"
REAL_URL = ("https://www.coilcraft.com/en-us/products/power/shielded-inductors/"
            "ferrite-drum/lps/lps4018/")


def record(reference, provenance):
    """A magnetic shaped the way the corpus stores one."""
    return {"magnetic": {"manufacturerInfo": {
        "name": "Coilcraft", "reference": reference,
        "datasheetInfo": {
            "part": {"partNumber": reference},
            "electrical": [{"inductance": {"nominal": 1e-6},
                            "dcResistances": [{"maximum": 0.03}],
                            "ratedCurrents": [8.0]}],
            "mechanical": {"length": {"nominal": 0.005}},
            "provenance": provenance}}}}


def findings_for(tmp_path, *records):
    path = tmp_path / "magnetics.ndjson"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return guard.check_file(path)


def test_fabricated_provenance_url_is_caught(tmp_path):
    """The ABT #351 batch: a real-looking MPN whose only source is a category page."""
    found = findings_for(tmp_path, record("EPL2010-100ML", [
        {"source": "scrape", "sourceName": "Coilcraft parametric API (scraped JSON)",
         "sourceUrl": FAKE_URL}]))
    assert len(found) == 1, found
    assert "not a product page" in found[0][2]


def test_rule_generalises_beyond_the_quarantined_references(tmp_path):
    """It must catch a family nobody has quarantined yet, not just the known 195.

    check_file() is called WITHOUT the denylist here on purpose — a fabricator who
    mints a new family name would otherwise sail through.
    """
    found = findings_for(tmp_path, record("XYZ9999-100ML", [
        {"source": "scrape",
         "sourceUrl": "https://www.coilcraft.com/en-us/products/power-inductors/xyz9999/"}]))
    assert len(found) == 1, found


def test_real_product_url_is_not_flagged(tmp_path):
    """Identical record, genuine deep product URL — the guard must stay quiet."""
    assert findings_for(tmp_path, record("LPS4018-102MRB", [
        {"source": "scrape", "sourceUrl": REAL_URL}])) == []


def test_corroborated_record_is_not_flagged(tmp_path):
    """A second, independent source means someone did read a real page.

    Genuine Coilcraft rows in this catalogue routinely carry both a scrape and a
    manufacturerParametric entry; only the fabricated batch cites the bad URL alone.
    """
    assert findings_for(tmp_path, record("EPL2010-102ML", [
        {"source": "scrape", "sourceUrl": FAKE_URL},
        {"source": "manufacturerParametric",
         "sourceName": "Coilcraft parametric search API"}])) == []


def test_unfamiliar_url_alone_is_not_evidence(tmp_path):
    """Only URL shapes VERIFIED to resolve to a non-product page belong in the list.

    Guards the design rule in the script's header: a merely unrecognised source is
    not proof of fabrication, and treating it as such is how a gate becomes noise.
    """
    assert findings_for(tmp_path, record("ABC1234-100ML", [
        {"source": "scrape", "sourceUrl": "https://example.com/parts/abc1234/"}])) == []


@pytest.mark.parametrize("url", [
    "https://www.coilcraft.com/en-us/products/power-inductors/ser2918/",
    "https://coilcraft.com/en-us/products/power-inductors/pa4310",
    "https://www.coilcraft.com/en-us/products/power-inductors/SLC0402T/",
])
def test_fake_url_variants(url):
    """www-less, trailing-slash-less and mixed-case forms are the same fabrication."""
    assert guard.fake_provenance(
        {"datasheetInfo": {"provenance": [{"source": "scrape", "sourceUrl": url}]}})


def test_live_magnetics_catalogue_is_clean():
    """The 195 rows are quarantined; the live file must not regrow them."""
    path = REPO / "data" / "magnetics.ndjson"
    if not path.exists() or path.stat().st_size < 10_000:
        pytest.skip("magnetics.ndjson not materialised (git-lfs pointer)")
    assert guard.check_file(path, guard.load_quarantined_fabricated(REPO / "data")) == []


# ── backfill_provenance.py must stay retired (ABT #391) ─────────────────────────

def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_provenance_refuses_to_run():
    """It invented provenance for 318,391 records; it must never run again.

    That script inferred a source from a record's own datasheetUrl host and stamped a
    concrete retrievedDate, without ever fetching anything. It is the step that made
    five separate batches of fabricated parts indistinguishable from sourced ones
    (ABT #247, #256, #351, #391). There is no correct version of it: writing
    provenance for records whose origin nobody recorded is inventing evidence.

    If this test fails, someone has re-enabled it. Do not "fix" the test.
    """
    mod = _load("backfill_provenance")
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code != 0


def test_the_retired_maps_survive_for_fingerprinting():
    """Retired, but not deleted — its tables identify its own output.

    relabel_url_inferred_provenance.py matches the exact (sourceName, retrievedDate)
    pairs this script invented, to find records still carrying its stamps. Deleting
    the maps would blind that check, so the module must still import cleanly even
    though it will not execute.
    """
    mod = _load("backfill_provenance")
    assert len(mod.DOMAIN_MAP) > 20
    assert len(mod.MANUF_MAP) > 20
    relabel = _load("relabel_url_inferred_provenance")
    assert len(relabel.BACKFILL_STAMPS) > 10


# ── ABT #1014: arithmetic ladders ────────────────────────────────────────────
# Two fabricated batches were found on 2026-09-04 that every other rule in the
# guard passed, because their fields VARY — by formula. The ROHM batch of ABT
# #1011 is the specimen these tests are built from: forwardVoltage = 0.20 +
# 0.01*i and powerDissipation = 10*forwardVoltage held exactly across 25 parts
# while every other field held one identical value.
#
# The negatives matter as much as the positive. This rule looks at a whole
# cohort rather than a row, so a careless version condemns real product families
# that legitimately step one parameter — and quarantining a real part is the
# more expensive mistake.

def ladder_record(part_number, manufacturer="ROHM", **electrical):
    """A part shaped the way the corpus stores one, with given scalar electricals."""
    return {"semiconductor": {"diode": {"manufacturerInfo": {
        "name": manufacturer, "reference": part_number,
        "datasheetInfo": {
            "part": {"partNumber": part_number},
            "electrical": dict(electrical),
            "provenance": [{"source": "scrape", "sourceUrl": REAL_URL}]}}}}}


def ladder_findings(tmp_path, records, name="diodes.ndjson"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return guard.check_file(path)


def test_arithmetic_ladder_is_caught(tmp_path):
    """The ABT #1011 shape: one field walks in exact steps, the rest are identical."""
    rows = [ladder_record(
        f"RSR012E{i}",
        forwardVoltage=0.20 + 0.01 * i,
        powerDissipation=10 * (0.20 + 0.01 * i),
        reverseVoltage=200.0, forwardCurrent=1.0, surgeCurrent=8.0,
    ) for i in range(25)]
    found = ladder_findings(tmp_path, rows)
    assert len(found) == 25, found
    assert "exact linear function of the part index" in found[0][2]


def test_real_family_that_steps_one_parameter_is_not_flagged(tmp_path):
    """A genuine voltage ladder: the OTHER parameters move too, so it is a family.

    This is the false positive that would make the rule unusable — real
    catalogues are full of families whose part numbers step a rating.
    """
    rows = [ladder_record(
        f"MBR{i}",
        reverseVoltage=20.0 + 10 * i,          # steps exactly, like a real ladder
        forwardVoltage=0.35 + 0.004 * i * i,   # but the rest are not affine in i
        forwardCurrent=1.0 + (i % 3),
        surgeCurrent=25.0 + (i % 5) * 3,
    ) for i in range(20)]
    assert ladder_findings(tmp_path, rows) == []


def test_short_run_is_not_flagged(tmp_path):
    """Below the minimum length a coincidence is likely; the rule must not fire."""
    rows = [ladder_record(f"ABC{i}", forwardVoltage=0.2 + 0.01 * i,
                          reverseVoltage=200.0) for i in range(guard.LADDER_MIN - 1)]
    assert ladder_findings(tmp_path, rows) == []


def test_non_contiguous_indices_are_not_flagged(tmp_path):
    """Real families skip numbers; a generator's for-loop does not."""
    rows = [ladder_record(f"ABC{i}", forwardVoltage=0.2 + 0.01 * i,
                          reverseVoltage=200.0) for i in (0, 1, 2, 5, 8, 13, 21, 34, 55, 89)]
    assert ladder_findings(tmp_path, rows) == []


def test_identical_cohort_is_not_a_ladder(tmp_path):
    """Every field constant is a duplicate problem, not this rule's business.

    Reporting it here would put the wrong reason on the row, and 'fabricated' is
    not a label to apply loosely.
    """
    rows = [ladder_record(f"ABC{i}", forwardVoltage=0.4, reverseVoltage=200.0)
            for i in range(20)]
    assert ladder_findings(tmp_path, rows) == []
