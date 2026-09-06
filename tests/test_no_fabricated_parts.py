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


# ── 2026-09-06: mid-string ladders ───────────────────────────────────────────
# The cohort key was ``^(stem)(index)$`` -- a TRAILING numeric run only. Real
# vendor numbering puts the varying digits in the MIDDLE (IPW60R080P7,
# C3M0075120K, FDMU81000), so a generator imitating a real family produced a
# different stem per member and the rule was inert against exactly the shape it
# was written to catch. These tests pin the widened key: revert ladder_keys() to
# a trailing-run-only key and the first one fails with an empty finding list.

def test_mid_string_arithmetic_ladder_is_caught(tmp_path):
    """ACME100N65..ACME124N65: the index is mid-string, the package token trails."""
    rows = [ladder_record(
        f"ACME{100 + i}N65",
        forwardVoltage=0.20 + 0.01 * i,
        powerDissipation=10 * (0.20 + 0.01 * i),
        reverseVoltage=650.0, forwardCurrent=1.0, surgeCurrent=8.0,
    ) for i in range(25)]
    found = ladder_findings(tmp_path, rows)
    assert len(found) == 25, found
    assert "exact linear function of the part index" in found[0][2]


def test_mid_string_cohort_is_reported_once_per_row(tmp_path):
    """A label has several numeric runs, so it joins several candidate cohorts.

    Two of them (the mid-string index and the trailing '65') can both look like a
    ladder; the row must still be reported exactly once, or a human reading the
    guard's output cannot count the damage.
    """
    rows = [ladder_record(
        f"ACME{100 + i}N65",
        forwardVoltage=0.20 + 0.01 * i,
        powerDissipation=10 * (0.20 + 0.01 * i),
        reverseVoltage=650.0,
    ) for i in range(12)]
    found = ladder_findings(tmp_path, rows)
    assert len(found) == 12, found
    assert len({(lineno, label) for lineno, label, _ in found}) == 12


def test_value_coded_mid_string_family_is_not_flagged(tmp_path):
    """THE calibration case: Bourns CE0603G-2N0C..2N9C and Murata GCQ..6R0..6R9.

    A value-coded MPN spells its own quantity, so that ONE quantity is affine in
    the index by construction and the rest of the row is legitimately identical.
    Without the >= LADDER_MIN_MIDSTRING_FIELDS condition this shape condemns
    13,142 real live records (11,473 capacitors, 1,669 magnetics). Its three
    tolerance bounds are ONE measurement, not three independent ladders.
    """
    rows = [ladder_record(
        f"CE0603G-2N{i}C", manufacturer="Bourns",
        inductance={"nominal": 2.0e-9 + 1.0e-10 * i,
                    "minimum": 1.8e-9 + 1.0e-10 * i,
                    "maximum": 2.2e-9 + 1.0e-10 * i},
        selfResonantFrequency=6.0e9,
    ) for i in range(10)]
    assert ladder_findings(tmp_path, rows, name="magnetics.ndjson") == []


def test_mid_string_single_quantity_ladder_is_the_stated_gap(tmp_path):
    """Pins the COST the rule accepts, so a future relaxation is a deliberate act.

    One affine quantity mid-string is not condemned -- it is the Murata/Bourns
    shape above. If this ever starts failing, the value-coded families are being
    condemned again; check the live counts before 'fixing' it.
    """
    rows = [ladder_record(
        f"ACME{100 + i}N65",
        forwardVoltage=0.20 + 0.01 * i,
        reverseVoltage=650.0,
    ) for i in range(12)]
    assert ladder_findings(tmp_path, rows) == []


def test_mid_string_real_family_is_not_flagged(tmp_path):
    """The widened key must not start condemning families the old key never saw.

    Same mid-string shape as above, but the other parameters move too -- which is
    what a real family looks like and what the corroboration exists to require.
    """
    rows = [ladder_record(
        f"ACME{100 + i}N65",
        reverseVoltage=650.0 + 10 * i,
        forwardVoltage=0.35 + 0.004 * i * i,
        forwardCurrent=1.0 + (i % 3),
        surgeCurrent=25.0 + (i % 5) * 3,
    ) for i in range(20)]
    assert ladder_findings(tmp_path, rows) == []


def test_ladder_keys_enumerates_every_numeric_run():
    """The trailing run is still produced (suffix ''), plus the mid-string ones."""
    assert set(guard.ladder_keys("ACME100N65")) == {
        ("ACME", "N65", 100), ("ACME100N", "", 65)}
    # A part number that STARTS with its digits has no stem for that run.
    assert set(guard.ladder_keys("1N4001")) == {("1N", "", 4001)}


def test_identical_cohort_is_not_a_ladder(tmp_path):
    """Every field constant is a duplicate problem, not this rule's business.

    Reporting it here would put the wrong reason on the row, and 'fabricated' is
    not a label to apply loosely.
    """
    rows = [ladder_record(f"ABC{i}", forwardVoltage=0.4, reverseVoltage=200.0)
            for i in range(20)]
    assert ladder_findings(tmp_path, rows) == []


# ── identity: partNumber first, reference as fallback, NEITHER is a failure ──
# Two fabricated batches reached production because every screen in the corpus
# keyed on manufacturerInfo.reference, which is optional: 35,966 live capacitors
# and (until 2026-09-04) the 448 fabricated TDK magnetics had no such field, and
# a screen keyed on it reported them clean by never looking. The other identity
# is not universal either -- 51,741 live magnetics carry reference and no
# partNumber -- so the guard keys on both, partNumber first, and a row carrying
# neither is a loud failure rather than a silent skip.

TEMPLATE_MPN = "7443HCF-1000-0100"           # a KNOWN_TEMPLATES shape, always flagged


def identified(manufacturer_info, family="magnetic"):
    return {family: {"manufacturerInfo": manufacturer_info}}


def stats_and_findings(tmp_path, *records, name="magnetics.ndjson"):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    stats = guard.new_stats()
    return stats, guard.check_file(path, stats=stats)


def test_partnumber_only_row_is_screened(tmp_path):
    """The TDK shape: partNumber, no reference. Every rule must still see it."""
    stats, found = stats_and_findings(tmp_path, identified({
        "name": "Wuerth", "datasheetInfo": {"part": {"partNumber": TEMPLATE_MPN}}}))
    assert stats["screened"] == 1 and stats["unidentifiable"] == 0
    assert [f[1] for f in found] == [TEMPLATE_MPN], found
    assert "generator template" in found[0][2]


def test_reference_only_row_is_screened(tmp_path):
    """The other population: 51,741 live magnetics have reference and no partNumber."""
    stats, found = stats_and_findings(tmp_path, identified({
        "name": "Wuerth", "reference": TEMPLATE_MPN, "datasheetInfo": {}}))
    assert stats["screened"] == 1
    assert [f[1] for f in found] == [TEMPLATE_MPN], found


def test_partnumber_is_the_label_and_reference_the_fallback():
    assert guard.part_ids({"reference": "REF-1",
                           "datasheetInfo": {"part": {"partNumber": "PN-1"}}}) == ["PN-1", "REF-1"]
    assert guard.part_ids({"reference": "REF-1"}) == ["REF-1"]
    assert guard.part_ids({"datasheetInfo": {"part": {"partNumber": "PN-1"}}}) == ["PN-1"]
    assert guard.part_ids({"name": "TDK", "reference": None,
                           "datasheetInfo": {"part": {"partNumber": ""}}}) == []


def test_template_on_the_reference_is_caught_when_partnumber_is_clean(tmp_path):
    """Both identities are screened, not just the first one found."""
    stats, found = stats_and_findings(tmp_path, identified({
        "name": "Wuerth", "reference": TEMPLATE_MPN,
        "datasheetInfo": {"part": {"partNumber": "744314100"}}}))
    assert len(found) == 1 and found[0][1] == "744314100", found


def test_row_with_neither_identity_fails_loudly(tmp_path):
    """A record nothing can key on is a FAILURE. It used to be skipped in silence."""
    stats, found = stats_and_findings(tmp_path, identified({
        "name": "TDK", "datasheetInfo": {"part": {"description": "1 mH power inductor"},
                                          "electrical": [{"inductance": {"nominal": 1e-3}}]}}))
    assert stats["unidentifiable"] == 1 and stats["screened"] == 0
    assert len(found) == 1, found
    assert found[0][1] == "<no identity>"
    assert found[0][2].startswith("UNIDENTIFIABLE")


def test_component_row_without_manufacturer_info_fails_loudly(tmp_path):
    """An empty seed in a LIVE catalogue has nothing to screen -- so it fails."""
    stats, found = stats_and_findings(tmp_path, {"capacitor": {}}, name="capacitors.ndjson")
    assert stats["unidentifiable"] == 1
    assert len(found) == 1 and found[0][2].startswith("UNIDENTIFIABLE"), found


def test_bricks_and_converters_are_not_unidentified_parts(tmp_path):
    """circuits.ndjson and converters.ndjson carry no manufacturerInfo by design."""
    brick = {"name": "half-bridge", "ports": [{"name": "sw"}],
             "components": [{"name": "Qh", "data": "TAS/data/mosfets.ndjson?partNumber=X"}],
             "connections": []}
    converter = {"inputs": {"designRequirements": {}}, "topology": {"stages": []}}
    stats, found = stats_and_findings(tmp_path, brick, converter, name="circuits.ndjson")
    assert found == []
    assert stats["nonComponentRows"] == 2 and stats["unidentifiable"] == 0


def test_nested_building_block_without_identity_is_not_the_part(tmp_path):
    """A core's or wire's manufacturerInfo naming only the vendor is not a missing MPN.

    The part's OWN manufacturerInfo is what identifies the record; a building
    block inside it is counted, not condemned -- otherwise the first MAS magnetic
    with a bare core manufacturer would block every shard build.
    """
    stats, found = stats_and_findings(tmp_path, {"magnetic": {
        "manufacturerInfo": {"name": "Wuerth", "datasheetInfo": {"part": {"partNumber": "744314100"}}},
        "core": {"manufacturerInfo": {"name": "TDK"}}}})
    assert found == []
    assert stats["screened"] == 1 and stats["nestedUnidentified"] == 1


def test_nested_building_block_with_a_template_mpn_is_screened(tmp_path):
    stats, found = stats_and_findings(tmp_path, {"magnetic": {
        "manufacturerInfo": {"name": "Wuerth", "datasheetInfo": {"part": {"partNumber": "744314100"}}},
        "core": {"manufacturerInfo": {"name": "Wuerth", "reference": TEMPLATE_MPN}}}})
    assert stats["nestedScreened"] == 1
    assert [f[1] for f in found] == [TEMPLATE_MPN], found


def test_inline_part_inside_a_brick_component_list_is_screened(tmp_path):
    """A CIAS brick may inline a PEAS document under components[].data -- a list."""
    brick = {"name": "b", "ports": [], "connections": [], "components": [
        {"name": "L1", "data": identified({"name": "Wuerth", "reference": TEMPLATE_MPN})}]}
    stats, found = stats_and_findings(tmp_path, brick, name="circuits.ndjson")
    assert [f[1] for f in found] == [TEMPLATE_MPN], found


def test_provenance_rule_screens_partnumber_only_rows(tmp_path):
    """The ABT #351 signature on a row shaped like the TDK batch (no reference)."""
    r = record("EPL2010-100ML", [{"source": "scrape", "sourceUrl": FAKE_URL}])
    del r["magnetic"]["manufacturerInfo"]["reference"]
    found = findings_for(tmp_path, r)
    assert len(found) == 1 and found[0][1] == "EPL2010-100ML", found


def test_formula_dcr_rule_labels_a_partnumber_only_row(tmp_path):
    """The bare-stub DCR rule used to report reference, i.e. '' for this shape."""
    stats, found = stats_and_findings(tmp_path, identified({
        "name": "Wuerth", "datasheetInfo": {
            "part": {"partNumber": "WE-FAKE-1"},
            "electrical": [{"inductance": {"nominal": 1e-6}, "dcResistance": {"maximum": 0.0062}}]}}))
    assert len(found) == 1 and found[0][1] == "WE-FAKE-1", found
    assert "generator formula" in found[0][2]


def test_arithmetic_ladder_screens_partnumber_only_rows(tmp_path):
    """The rule added the same day builds cohorts on the reported label -- so a
    generator that wrote partNumber only lands in the same cohort."""
    rows = []
    for i in range(25):
        r = ladder_record(f"RSR012E{i}", forwardVoltage=0.20 + 0.01 * i,
                          powerDissipation=10 * (0.20 + 0.01 * i), reverseVoltage=200.0)
        del r["semiconductor"]["diode"]["manufacturerInfo"]["reference"]
        rows.append(r)
    found = ladder_findings(tmp_path, rows)
    assert len(found) == 25, found
    assert {f[1] for f in found} == {f"RSR012E{i}" for i in range(25)}


def test_stats_report_what_the_guard_saw(tmp_path):
    stats, _ = stats_and_findings(
        tmp_path,
        identified({"name": "A", "reference": "R1"}),
        identified({"name": "A", "datasheetInfo": {"part": {"partNumber": "P1"}}}),
        identified({"name": "A"}))
    assert stats["rows"] == 3 and stats["screened"] == 2 and stats["unidentifiable"] == 1


def test_denylist_includes_batches_condemned_inline(tmp_path):
    """The 448 TDK rows were tagged 'magnetics.ndjson (fabricated cohort 13, ...)'.

    The denylist used to select on the literal 'quarantine_fabricated' and so did
    not contain them -- the old guard passed all 448 when replayed as live rows.
    Duplicates and incomplete rows are still NOT fabricated and stay out.
    """
    (tmp_path / "quarantine.ndjson").write_text("".join(json.dumps(r) + "\n" for r in [
        dict(identified({"name": "TDK", "datasheetInfo": {"part": {"partNumber": "TDK001m08051065_50"}}}),
             _quarantineSource=["magnetics.ndjson (fabricated cohort 13, TDK Meister provenance refuted, 2026-09-04)"]),
        dict(identified({"name": "X", "reference": "OLDFAB"}),
             _quarantineSource=["magnetics.quarantine_fabricated.ndjson"]),
        dict(identified({"name": "X", "reference": "REALDUP"}),
             _quarantineSource=["magnetics.quarantine_duplicates.ndjson"]),
        dict(identified({"name": "X", "reference": "REALINC"}),
             _quarantineSource="connectors.quarantine_incomplete.ndjson"),
    ]))
    assert guard.load_quarantined_fabricated(tmp_path) == {"TDK001m08051065_50", "OLDFAB"}


def test_cli_fails_on_an_unidentifiable_row(tmp_path):
    """End to end: the shard build calls the CLI and trusts its exit code."""
    import subprocess, sys
    (tmp_path / "magnetics.ndjson").write_text(json.dumps(identified({"name": "TDK"})) + "\n")
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "check_no_fabricated_parts.py"),
                           "--data", str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "CANNOT IDENTIFY" in proc.stderr
    assert "1 UNIDENTIFIABLE" in proc.stdout


def test_cli_passes_and_reports_what_it_screened(tmp_path):
    import subprocess, sys
    (tmp_path / "magnetics.ndjson").write_text(
        json.dumps(identified({"name": "Wuerth", "reference": "744314100"})) + "\n")
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "check_no_fabricated_parts.py"),
                           "--data", str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "magnetics.ndjson: 1 rows, 1 part(s) screened" in proc.stdout


def test_phase5_template_knows_every_unit_letter():
    """Cohort 13 was the phase5 generator writing 'm' where the template knew 'u'."""
    for mpn in ("TDK001m08051065_50", "Coi010u0603_1", "Bou100n1210_3"):
        assert any(p.match(mpn) for p, _ in guard.KNOWN_TEMPLATES), mpn
    for mpn in ("TDK001k08051065_50", "SLF7032T-331MR22-2PF", "B82559A0472A033"):
        assert not any(p.match(mpn) for p, _ in guard.KNOWN_TEMPLATES), mpn
