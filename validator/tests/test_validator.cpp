// SPDX-License-Identifier: MIT
// C++ unit tests for the TAS physics validator. One record that passes plus
// records that trip representative IMPOSSIBLE and SUSPICIOUS branches per family.
#include "tas_validator/helpers.hpp"
#include "tas_validator/validator.hpp"

#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <string>

using namespace tas;
using nlohmann::json;

namespace {

PartValidator V;

bool has(const Verdict& v, const std::string& code, Severity sev) {
    return std::any_of(v.findings.begin(), v.findings.end(),
                       [&](const Finding& f) { return f.code == code && f.severity == sev; });
}
bool has_code(const Verdict& v, const std::string& code) {
    return std::any_of(v.findings.begin(), v.findings.end(),
                       [&](const Finding& f) { return f.code == code; });
}

// A real-shaped, physically sane WE-MAPI inductor (744383560R33).
json good_magnetic() {
    return json::parse(R"json({
      "magnetic": {"manufacturerInfo": {"reference": "744383560R33", "datasheetInfo": {
        "part": {"material": "Metal Alloy (Iron)"},
        "electrical": [{"inductance": {"nominal": 3.3e-7, "minimum": 2.64e-7, "maximum": 3.96e-7},
                        "dcResistance": {"maximum": 0.0085},
                        "saturationCurrentPeak": 12.4,
                        "selfResonantFrequency": 1.2e8,
                        "ratedCurrents": [{"rms": 11.0}]}],
        "mechanical": {"length": {"nominal": 0.004}, "width": {"nominal": 0.004},
                       "height": {"nominal": 0.002}},
        "thermal": {"operatingTemperature": {"minimum": -40, "maximum": 125}}
      }}}
    })json");
}

json good_cap() {
    return json::parse(R"json({"capacitor": {"manufacturerInfo": {"reference": "UPW1H102MHD",
      "datasheetInfo": {
        "part": {"technology": "aluminum-electrolytic-wet"},
        "electrical": {"capacitance": {"nominal": 0.001}, "ratedVoltage": 50,
                       "dissipationFactor": 0.1, "esr": 0.034, "leakageCurrent": 0.0015,
                       "insulationResistance": 1e8},
        "mechanical": {"shape": {"volume": {"nominal": 5.0e-6}}}
      }}}})json");
}

}  // namespace

TEST_CASE("Magnetics: GoodPartIsValid", "[magnetics]") {
    Verdict v = V.validate(good_magnetic());
    CHECK(v.valid);
    CHECK(!has_code(v, "MAG_ENERGY_DENSITY"));  // ~0.8 mJ/cm^3, well under ceiling
}

TEST_CASE("Magnetics: EnergyDensityImpossible", "[magnetics]") {
    json p = good_magnetic();
    // Absurd: 1 H at 1000 A in a 4x4x2mm body.
    p["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]["inductance"] = 1.0;
    p["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]["saturationCurrentPeak"] = 1000.0;
    Verdict v = V.validate(p);
    CHECK(has(v, "MAG_ENERGY_DENSITY", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("Magnetics: InductanceToleranceOrdering", "[magnetics]") {
    json p = good_magnetic();
    auto& ind = p["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]["inductance"];
    ind["minimum"] = 5e-7;  // minimum > nominal
    Verdict v = V.validate(p);
    CHECK(has(v, "MAG_L_TOLERANCE", Severity::Impossible));
}

TEST_CASE("Magnetics: MissingInductanceSkips", "[magnetics]") {
    json p = good_magnetic();
    p["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0].erase("inductance");
    Verdict v = V.validate(p);
    CHECK(v.valid);
    CHECK(!v.skipped.empty());
}

TEST_CASE("Capacitors: GoodCapValid", "[capacitors]") {
    Verdict v = V.validate(good_cap());
    CHECK(v.valid);
}

TEST_CASE("Capacitors: ToleranceOrderingImpossible", "[capacitors]") {
    json p = good_cap();
    p["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["capacitance"] =
        json::parse(R"json({"nominal": 0.001, "minimum": 0.002})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "CAP_TOLERANCE", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("Capacitors: EnergyDensityImpossible", "[capacitors]") {
    json p = good_cap();
    // 1 F at 1000 V in 5 mm^3 -> astronomically high density.
    p["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["capacitance"] = 1.0;
    p["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["ratedVoltage"] = 1000.0;
    Verdict v = V.validate(p);
    CHECK(has(v, "CAP_ENERGY_DENSITY", Severity::Impossible));
}

TEST_CASE("Semiconductors: MosfetCapHierarchy", "[semiconductors]") {
    json p = json::parse(R"json({"semiconductor": {"mosfet": {"manufacturerInfo": {
      "reference": "X", "datasheetInfo": {"part": {"technology": "GaN"},
      "electrical": {"inputCapacitance": 1e-12, "outputCapacitance": 2e-12,
                     "reverseTransferCapacitance": 3e-12}}}}}})json");  // inverted order
    Verdict v = V.validate(p);
    CHECK(has(v, "MOS_CAP_HIERARCHY", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("Semiconductors: MosfetChargeHierarchy", "[semiconductors]") {
    json p = json::parse(R"json({"semiconductor": {"mosfet": {"manufacturerInfo": {
      "reference": "X", "datasheetInfo": {"part": {"technology": "Si"},
      "electrical": {"totalGateCharge": 1e-9, "gateSourceCharge": 8e-10,
                     "gateDrainCharge": 8e-10}}}}}})json");  // Qgs+Qgd > Qg
    Verdict v = V.validate(p);
    CHECK(has(v, "MOS_CHARGE_HIERARCHY", Severity::Impossible));
}

TEST_CASE("Semiconductors: DiodeSurgeBelowForward", "[semiconductors]") {
    json p = json::parse(R"json({"semiconductor": {"diode": {"manufacturerInfo": {
      "reference": "X", "datasheetInfo": {"part": {"technology": "Schottky"},
      "electrical": {"reverseVoltage": 60, "forwardCurrent": 30, "surgeCurrent": 10,
                     "forwardVoltage": 0.42}}}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "DIO_SURGE_VS_IF", Severity::Impossible));
}

TEST_CASE("Semiconductors: IgbtVcesatExceedsVces", "[semiconductors]") {
    json p = json::parse(R"json({"semiconductor": {"igbt": {"manufacturerInfo": {
      "reference": "X", "datasheetInfo": {
      "electrical": {"collectorEmitterVoltage": 2.0, "continuousCollectorCurrent": 100,
                     "collectorEmitterSaturation": 3.0}}}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "IGBT_VCESAT_VS_VCES", Severity::Impossible));
}

// Regression tests for the 2026-06-24 recalibration (P0/P1/P2).

// MOS-1: a real SiC MOSFET whose gateThresholdVoltage carries the recommended
// gate-DRIVE window (9/15/19.5 V) must NOT be invalidated.
TEST_CASE("Recalibration: SicDriveWindowVthNotInvalid", "[recalibration]") {
    json p = json::parse(R"json({"semiconductor":{"mosfet":{"manufacturerInfo":{
      "reference":"SCT60","datasheetInfo":{"part":{"technology":"SiC"},
      "electrical":{"gateSourceVoltageMax":18,"onResistanceVgs":15,
        "gateThresholdVoltage":{"minimum":9,"nominal":15,"maximum":19.5}}}}}}})json");
    Verdict v = V.validate(p);
    CHECK(v.valid);
    CHECK(!has(v, "MOS_VGS_VS_VTH", Severity::Impossible));
}

// MOS-2: P-channel Vth labelled by magnitude (min -2, max -4) is valid; a
// nominal outside the [min,max] bracket is impossible (convention-agnostic).
TEST_CASE("Recalibration: PchannelVthBracketOk", "[recalibration]") {
    json p = json::parse(R"json({"semiconductor":{"mosfet":{"manufacturerInfo":{
      "reference":"IRF5305","datasheetInfo":{"part":{"technology":"Si"},
      "electrical":{"gateThresholdVoltage":{"minimum":-2.0,"nominal":-3.0,"maximum":-4.0}}}}}}})json");
    CHECK(!has(V.validate(p), "MOS_VTH_WINDOW", Severity::Impossible));
}

TEST_CASE("Recalibration: VthNominalOutsideBracketImpossible", "[recalibration]") {
    json p = json::parse(R"json({"semiconductor":{"mosfet":{"manufacturerInfo":{
      "reference":"X","datasheetInfo":{"part":{"technology":"Si"},
      "electrical":{"gateThresholdVoltage":{"minimum":2.0,"nominal":6.0,"maximum":4.0}}}}}}})json");
    CHECK(has(V.validate(p), "MOS_VTH_WINDOW", Severity::Impossible));
}

// IGBT-1: PN-digit-leak garbage current is caught.
TEST_CASE("Recalibration: IgbtGarbageCurrentImpossible", "[recalibration]") {
    json p = json::parse(R"json({"semiconductor":{"igbt":{"manufacturerInfo":{
      "reference":"FD16001200","datasheetInfo":{"electrical":{
        "collectorEmitterVoltage":1200,"continuousCollectorCurrent":16001200}}}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "IGBT_IC_RANGE", Severity::Impossible));
    CHECK(!v.valid);
}

// A 0-ohm jumper (resistance == 0) is a real part, not a violation.
TEST_CASE("Recalibration: ZeroOhmJumperValid", "[recalibration]") {
    json p = json::parse(R"json({"resistor":{"manufacturerInfo":{"reference":"YC162-JR-070RL",
      "datasheetInfo":{"electrical":{"resistance":0.0,"powerRating":0.0625}}}}})json");
    Verdict v = V.validate(p);
    CHECK(v.valid);
    CHECK(!has(v, "RES_R_RANGE", Severity::Impossible));
}

TEST_CASE("Recalibration: NegativeResistanceImpossible", "[recalibration]") {
    json p = json::parse(R"json({"resistor":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"electrical":{"resistance":-5.0}}}}})json");
    CHECK(has(V.validate(p), "RES_R_RANGE", Severity::Impossible));
}

// DIO-1: device type from part.subType re-enables the Schottky band.
TEST_CASE("Recalibration: SchottkyDetectedFromSubType", "[recalibration]") {
    json p = json::parse(R"json({"semiconductor":{"diode":{"manufacturerInfo":{
      "reference":"X","datasheetInfo":{"part":{"technology":"Si","subType":"schottky"},
      "electrical":{"reverseVoltage":40,"forwardCurrent":3,"forwardVoltage":0.35}}}}}})json");
    // 0.35 V is fine for a Schottky (band 0.2..1.3) but would trip the Si-PN LO (0.4).
    CHECK(!has(V.validate(p), "DIO_VF_RANGE", Severity::Suspicious));
}

// Provenance warning fires on every part that lacks a provenance trail.
TEST_CASE("AntiSynthesis: ProvenanceMissingWarns", "[antisynthesis]") {
    json p = json::parse(R"json({"resistor":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"electrical":{"resistance":1000}}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "GEN_PROVENANCE_MISSING", Severity::Suspicious));
    CHECK(v.valid);  // a warning must not invalidate
}

TEST_CASE("AntiSynthesis: ProvenancePresentNoWarning", "[antisynthesis]") {
    json p = json::parse(R"json({"resistor":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"provenance":[{"source":"manufacturerDatasheet"}],
      "electrical":{"resistance":1000}}}}})json");
    CHECK(!has_code(V.validate(p), "GEN_PROVENANCE_MISSING"));
}

// Cross-family contamination: an inductor filed as a connector.
TEST_CASE("AntiSynthesis: FamilyMismatchWarns", "[antisynthesis]") {
    json p = json::parse(R"json({"connector":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"part":{"description":"SMD power inductor 10 uH shielded"},
      "electrical":{"ratedVoltage":50}}}}})json");
    CHECK(has(V.validate(p), "GEN_FAMILY_MISMATCH", Severity::Suspicious));
}

TEST_CASE("AntiSynthesis: MultiDiscriminatorImpossible", "[antisynthesis]") {
    json p = json::parse(R"json({"magnetic":{},"capacitor":{}})json");
    CHECK(has(V.validate(p), "GEN_MULTI_DISCRIMINATOR", Severity::Impossible));
}

// P3: IEC 60063 E-series preferred-value membership (resistors / capacitors).
TEST_CASE("AntiSynthesis: EseriesPreferredValueOk", "[antisynthesis]") {
    json p = json::parse(R"json({"resistor":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"electrical":{"resistance":4700.0}}}}})json");
    CHECK(!has_code(V.validate(p), "RES_E_SERIES"));
}

TEST_CASE("AntiSynthesis: EseriesOffGridFlags", "[antisynthesis]") {
    json p = json::parse(R"json({"resistor":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"electrical":{"resistance":9400.0}}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "RES_E_SERIES", Severity::Suspicious));
    CHECK(v.valid);  // anti-synthesis signal must not invalidate
}

TEST_CASE("AntiSynthesis: EseriesShuntAllowlisted", "[antisynthesis]") {
    json p = json::parse(R"json({"resistor":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"electrical":{"resistance":0.008}}}}})json");
    CHECK(!has_code(V.validate(p), "RES_E_SERIES"));  // sub-0.1 ohm shunt skipped
}

TEST_CASE("AntiSynthesis: EseriesCapFloatBoundaryOk", "[antisynthesis]") {
    // 10 uF stored with float error (9.999...e-6) must read as on-grid.
    json p = json::parse(R"json({"capacitor":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"electrical":{"capacitance":9.999999999999999e-06,"ratedVoltage":50}}}}})json");
    CHECK(!has_code(V.validate(p), "CAP_E_SERIES"));
}

TEST_CASE("AntiSynthesis: OverPrecisionFlags", "[antisynthesis]") {
    json p = json::parse(R"json({"resistor":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"electrical":{"resistance":4701.23}}}}})json");
    CHECK(has(V.validate(p), "GEN_OVERPRECISION", Severity::Suspicious));
}

// P4: cross-parameter physics correlations.
TEST_CASE("AntiSynthesis: TvsVoltageOrderingImpossible", "[antisynthesis]") {
    json p = json::parse(R"json({"semiconductor":{"diode":{"manufacturerInfo":{
      "reference":"X","datasheetInfo":{"part":{"subType":"esd"},
      "electrical":{"standoffVoltage":24.0,"clampingVoltage":24.0}}}}}})json");
    CHECK(has(V.validate(p), "DIO_TVS_ORDERING", Severity::Impossible));
}

TEST_CASE("AntiSynthesis: IgbtVcesatRatioIncoherent", "[antisynthesis]") {
    // Vces=100, Vcesat=4: each individually plausible, ratio 0.04 is incoherent.
    json p = json::parse(R"json({"semiconductor":{"igbt":{"manufacturerInfo":{
      "reference":"X","datasheetInfo":{"electrical":{
        "collectorEmitterVoltage":100,"collectorEmitterSaturation":4.0}}}}}})json");
    CHECK(has(V.validate(p), "IGBT_VCESAT_RATIO", Severity::Suspicious));
}

TEST_CASE("AntiSynthesis: SlewGbwIncoherent", "[antisynthesis]") {
    json p = json::parse(R"json({"operationalAmplifier":{"manufacturerInfo":{
      "reference":"X","datasheetInfo":{"electrical":{"slewRate":10,"gainBandwidthProduct":1e6}}}}})json");
    CHECK(has(V.validate(p), "ANA_SLEW_GBW", Severity::Suspicious));
}

// CTAS structural invariants
TEST_CASE("Controllers: UvloOrderImpossible", "[controllers]") {
    json p = json::parse(R"json({"controller":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"function":{"category":"pwmController"},
      "electrical":{"uvlo":[{"startThreshold":8,"stopThreshold":12}]}}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "CTL_UVLO_ORDER", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("Controllers: IsolationOrderImpossible", "[controllers]") {
    json p = json::parse(R"json({"controller":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"function":{"category":"isolatedGateDriver"},
      "electrical":{"isolation":{"workingVoltage":1500,"withstandVoltageRms":5000,"surgeVoltage":3000}}}}}})json");
    CHECK(has(V.validate(p), "CTL_ISO_ORDER", Severity::Impossible));
}

TEST_CASE("Controllers: PhaseCountImpossible", "[controllers]") {
    json p = json::parse(R"json({"controller":{"manufacturerInfo":{"reference":"X",
      "datasheetInfo":{"function":{"category":"multiphaseController","channelCount":4,"maxPhaseCount":2}}}}})json");
    CHECK(has(V.validate(p), "CTL_PHASE_COUNT", Severity::Impossible));
}

TEST_CASE("Controllers: GoodControllerValid", "[controllers]") {
    json p = json::parse(R"json({"controller":{"manufacturerInfo":{"reference":"UCC28730",
      "datasheetInfo":{"function":{"category":"pwmController"},
      "electrical":{"uvlo":[{"startThreshold":21,"stopThreshold":8.5}],
      "referenceVoltage":{"nominal":4.04}}}}}})json");
    Verdict v = V.validate(p);
    CHECK(v.valid);
    CHECK(!has_code(v, "CTL_UVLO_ORDER"));
}

// A wildly-out-of-cohort value (1 GOhm among kOhm parts of the same series) is
// surfaced by the batch screen.
TEST_CASE("Corpus: CohortOutlierDetected", "[corpus]") {
    std::vector<json> recs;
    const double vals[] = {1000, 1100, 1200, 1300, 1500, 1600, 1800, 2000, 2200, 2400};
    for (double R : vals) {
        json r = json::parse(R"json({"resistor":{"manufacturerInfo":{"name":"ACME","reference":"R",
          "datasheetInfo":{"part":{"series":"S"},"electrical":{"powerRating":0.1}}}}})json");
        r["resistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["resistance"] = R;
        recs.push_back(r);
    }
    json bad = json::parse(R"json({"resistor":{"manufacturerInfo":{"name":"ACME","reference":"BAD",
      "datasheetInfo":{"part":{"series":"S"},"electrical":{"resistance":1.0e9,"powerRating":0.1}}}}})json");
    recs.push_back(bad);
    auto f = validate_corpus(recs);
    CHECK(std::any_of(f.begin(), f.end(), [](const CorpusFinding& c) {
        return c.reference == "BAD" && c.code == "GEN_COHORT_OUTLIER";
    }));
}

TEST_CASE("Corpus: SmallCohortNotScreened", "[corpus]") {
    std::vector<json> recs;  // below MIN_COHORT -> no findings
    for (int i = 0; i < 3; ++i)
        recs.push_back(json::parse(R"json({"resistor":{"manufacturerInfo":{"name":"ACME",
          "datasheetInfo":{"electrical":{"resistance":1000.0}}}}})json"));
    CHECK(validate_corpus(recs).empty());
}

TEST_CASE("Framework: UnknownDiscriminatorThrows", "[framework]") {
    CHECK_THROWS_AS(V.validate(json::parse(R"json({"widget": {}})json")), std::invalid_argument);
}

TEST_CASE("Framework: MalformedScalarThrows", "[framework]") {
    json p = json::parse(R"json({"resistor": {"manufacturerInfo": {"reference": "X",
      "datasheetInfo": {"electrical": {"resistance": "not-a-number"}}}}})json");
    CHECK_THROWS_AS(V.validate(p), MalformedField);
}

TEST_CASE("Framework: CheckCodesNonEmpty", "[framework]") {
    CHECK(PartValidator::check_codes().size() > 20);
}
