// SPDX-License-Identifier: MIT
// Catch2 tests for tas_metrics. Run the binary directly: ./build/tas_metrics_tests
#include "tas_metrics/metrics.hpp"

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cstdio>
#include <fstream>

using namespace tas::metrics;
using Catch::Approx;

namespace {

const json kSpec = json::parse(R"({
  "weights": {"existence": 15, "traced": 25, "fields": 25, "curves": 15, "url": 10, "valid": 10},
  "catalogues": {
    "diodes": {"file": "diodes.ndjson", "wrap": ["semiconductor", "diode"],
      "fields": ["electrical.forwardCurrent", "electrical.surgeCurrent",
                 {"path": "electrical.reverseVoltage", "when": {"part.subType": ["schottky"]}},
                 "electrical.junctionCapacitance"],
      "curves": ["electrical.forwardVoltagePoints"]},
    "magnetics": {"file": "magnetics.ndjson", "wrap": ["magnetic"],
      "fields": ["electrical[].inductance"], "curves": []},
    "analog_ics": {"file": "analog_ics.ndjson", "wrap": ["analog", "*"],
      "fields": [{"path": "electrical.gbw", "when": {"$wrap": ["operationalAmplifier"]}}, "part.partNumber"]}
  },
  "placeholders": {"magnetics": [{"path": "electrical[].inductance", "value": 1e-9,
                                  "when": {"electrical[].subtype": ["chipBead"]}}]}
})");

json provenance(const char* verification, const char* source, json fields) {
    json e = {{"source", source}, {"verification", verification}, {"date", "2026-10-17"}};
    if (!fields.is_null()) e["fields"] = std::move(fields);
    return e;
}

json diode(json prov, double surge = 30.0, const char* subtype = "schottky") {
    json di;
    di["part"] = {{"partNumber", "D-1 A"}, {"subType", subtype}};
    di["electrical"] = {{"forwardCurrent", 1.0}, {"surgeCurrent", surge}, {"reverseVoltage", 40}};
    di["provenance"] = std::move(prov);
    json mi = {{"name", "Example Semi"}, {"reference", "D-1 A"}};
    mi["datasheetInfo"] = std::move(di);
    json rec;
    rec["semiconductor"]["diode"]["manufacturerInfo"] = std::move(mi);
    return rec;
}

}  // namespace

TEST_CASE("weighted checklist of a datasheet-traced part", "[metrics]") {
    const Scorer s(kSpec);
    json e = provenance("valuesReadFromSource", "manufacturer",
                        {"electrical.forwardCurrent", "manufacturerInfo.datasheetInfo.electrical.surgeCurrent"});
    e["sourceUrl"] = "https://example.com/d1.pdf";
    const auto p = s.score(diode(json::array({e})), "diodes", true, 0.8);
    REQUIRE(p);
    CHECK(p->blade_codes.empty());
    CHECK(p->checks.existence == Approx(1.0));
    CHECK(p->checks.traced == Approx(2.0 / 3.0));  // If and surge covered, Vr not
    CHECK(p->checks.fields == Approx(0.75));       // Cj missing
    REQUIRE(p->checks.curves);
    CHECK(*p->checks.curves == Approx(0.0));
    CHECK(p->checks.url == Approx(0.8));
    CHECK(p->checks.valid == Approx(1.0));
    CHECK(p->score == Approx((15 * 1.0 + 25 * (2.0 / 3.0) + 25 * 0.75 + 15 * 0.0 + 10 * 0.8 + 10 * 1.0) / 100.0));
    CHECK(p->identity_pn == "D1A");
    CHECK(p->identity_mfr == "examplesemi");
}

TEST_CASE("series datasheet 0.6, distributor 0.8x, existence-only counts for existence", "[metrics]") {
    const Scorer s(kSpec);
    const auto series = s.score(diode(json::array({provenance("seriesConfirmed", "distributor", nullptr)})), "diodes",
                                true, 0.0);
    REQUIRE(series);
    CHECK(series->checks.existence == Approx(0.48));
    // fields omitted: a whole-record claim counts only for valuesReadFromSource
    CHECK(series->checks.traced == Approx(0.0));

    const auto whole = s.score(diode(json::array({provenance("valuesReadFromSource", "distributor", nullptr)})),
                               "diodes", true, 0.0);
    CHECK(whole->checks.traced == Approx(0.8));

    const auto exist = s.score(
        diode(json::array({provenance("existenceConfirmed", "manufacturer", {"electrical.forwardCurrent"})})),
        "diodes", true, 0.0);
    CHECK(exist->checks.existence == Approx(1.0));
    CHECK(exist->checks.traced == Approx(0.0));

    const auto unverified =
        s.score(diode(json::array({provenance("inferred", "manufacturer", {"electrical.forwardCurrent"})})), "diodes",
                true, 0.0);
    CHECK(unverified->checks.existence == Approx(0.0));
}

TEST_CASE("datasheetUrl on manufacturerInfo earns the url check", "[metrics]") {
    const Scorer s(kSpec);
    json rec = diode(json::array({provenance("valuesReadFromSource", "manufacturer", nullptr)}));
    CHECK(s.score(rec, "diodes", true, 0.9)->checks.url == Approx(0.0));
    rec["semiconductor"]["diode"]["manufacturerInfo"]["datasheetUrl"] = "https://example.com/d1.pdf";
    CHECK(s.score(rec, "diodes", true, 0.9)->checks.url == Approx(0.9));
}

TEST_CASE("valid: schema failure and Blade Runner IMPOSSIBLE both score 0", "[metrics]") {
    const Scorer s(kSpec);
    const json prov = json::array({provenance("valuesReadFromSource", "manufacturer", nullptr)});
    CHECK(s.score(diode(prov), "diodes", false, 0.0)->checks.valid == Approx(0.0));
    const auto bad = s.score(diode(prov, 0.5), "diodes", true, 0.0);  // surge below forward current
    CHECK(bad->checks.valid == Approx(0.0));
    CHECK(std::find(bad->blade_codes.begin(), bad->blade_codes.end(), "DIO_SURGE_VS_IF") != bad->blade_codes.end());
}

TEST_CASE("a false 'when' drops the field from the denominator", "[metrics]") {
    const Scorer s(kSpec);
    const json prov = json::array({provenance("valuesReadFromSource", "manufacturer", nullptr)});
    CHECK(s.score(diode(prov, 30.0, "rectifier"), "diodes", true, 0.0)->checks.fields == Approx(2.0 / 3.0));
}

TEST_CASE("placeholder value counts -1; bracketed spec path matches plain provenance field", "[metrics]") {
    const Scorer s(kSpec);
    json bead = {{"magnetic",
                  {{"manufacturerInfo",
                    {{"name", "X"},
                     {"reference", "B1"},
                     {"datasheetInfo",
                      {{"electrical", json::array({{{"subtype", "chipBead"}, {"inductance", 1e-9}}})},
                       {"provenance", json::array({provenance("valuesReadFromSource", "manufacturer",
                                                              {"electrical.inductance"})})}}}}}}}};
    const auto p = s.score(bead, "magnetics", true, 0.0);
    REQUIRE(p);
    CHECK(p->checks.fields == Approx(0.0));
    CHECK(p->checks.traced == Approx(1.0));
    CHECK_FALSE(p->checks.curves);  // no curves expected: their weight moves to the others
    const double w = 15 + 25 + 25 + 10 + 10;
    CHECK(p->score == Approx((15 * 1.0 + 25 * 1.0 + 25 * 0.0 + 10 * 0.0 + 10 * p->checks.valid) / w));

    bead["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]["inductance"] = 2.2e-6;
    CHECK(s.score(bead, "magnetics", true, 0.0)->checks.fields == Approx(1.0));
}

TEST_CASE("'*' wrap exposes the inner kind to $wrap conditions", "[metrics]") {
    const Scorer s(kSpec);
    json op = {{"analog",
                {{"operationalAmplifier",
                  {{"manufacturerInfo",
                    {{"name", "X"}, {"reference", "OP1"}, {"datasheetInfo", {{"part", {{"partNumber", "OP1"}}}}}}}}}}}};
    const auto p = s.score(op, "analog_ics", true, 0.0);
    REQUIRE(p);
    CHECK(p->kind == "operationalAmplifier");
    CHECK(p->checks.fields == Approx(0.5));  // gbw applies and is missing
    json cmp = {{"analog", {{"comparator", op["analog"]["operationalAmplifier"]}}}};
    CHECK(s.score(cmp, "analog_ics", true, 0.0)->checks.fields == Approx(1.0));
}

TEST_CASE("unwrap failure and bad spec", "[metrics]") {
    const Scorer s(kSpec);
    CHECK_FALSE(s.score(json{{"capacitor", json::object()}}, "diodes", true, 0.0));
    CHECK_THROWS(s.score(json::object(), "nope", true, 0.0));
    CHECK_THROWS(Scorer(json{{"catalogues", json::object()}}));
}

TEST_CASE("score_file honours the schema mask and stops at its length", "[metrics]") {
    const Scorer s(kSpec);
    const std::string path = "tas_metrics_test.ndjson";
    {
        std::ofstream o(path);
        const json prov = json::array({provenance("valuesReadFromSource", "manufacturer", nullptr)});
        o << diode(prov).dump() << "\n\n" << diode(prov).dump() << "\n" << json{{"capacitor", 1}}.dump() << "\n"
          << diode(prov).dump() << "\n";
    }
    const auto t = s.score_file(path, "diodes", 0.0, "1101");  // 4 lines read: the last diode is cut off
    std::remove(path.c_str());
    CHECK(t.live == 2);
    CHECK(t.malformed == 1);
    CHECK(t.schema_failed == 1);
    CHECK(t.distinct == 1);
    CHECK(t.check_n.at("valid") == 2);
    CHECK(t.check_sum.at("valid") == Approx(1.0));
}

TEST_CASE("relative_field and normalise_pn", "[metrics]") {
    CHECK(relative_field("manufacturerInfo.datasheetInfo.electrical[0].inductance") == "electrical.inductance");
    CHECK(relative_field("datasheetInfo.electrical[].x") == "electrical.x");
    CHECK(relative_field("electrical.x") == "electrical.x");
    CHECK(normalise_pn(" ab-c_d/e.1 ") == "ABCDE.1");
}

TEST_CASE("manufacturer aliases collapse identities", "[metrics]") {
    const json aliases = json::parse(R"({"manufacturers": [
        {"name": "Example Semiconductor", "aliases": ["Example Semi", "EXSEMI"]}]})");
    const Scorer plain(kSpec), aliased(kSpec, aliases);
    const json rec = diode(json::array({provenance("valuesReadFromSource", "manufacturer", nullptr)}));
    CHECK(plain.score(rec, "diodes", true, 0.0)->identity_mfr == "examplesemi");
    CHECK(aliased.score(rec, "diodes", true, 0.0)->identity_mfr == "examplesemiconductor");
    CHECK(manufacturer_key("Texas-Instruments, Inc.") == "texasinstrumentsinc");
    CHECK_THROWS(Scorer(kSpec, json::parse(R"({"manufacturers": [{"name": "A", "aliases": ["X"]},
                                                                {"name": "B", "aliases": ["x"]}]})")));
}
