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
