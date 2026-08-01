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

// A real-shaped, physically sane MEMS oscillator (SiTime SiT8008, 25 MHz —
// TDAS/examples/mems-oscillator-25mhz.json values).
json good_oscillator() {
    return json::parse(R"json({"timeBase": {"oscillator": {"manufacturerInfo": {
      "name": "SiTime", "reference": "SiT8008BI-73-33E-25.000000", "datasheetInfo": {
        "part": {"partNumber": "SiT8008BI-73-33E-25.000000", "package": "2016 4-pad"},
        "electrical": {"technology": "mems", "frequency": 25000000.0, "outputType": "cmos",
                       "frequencyStability": 2.5e-05, "agingPerYear": 1e-06,
                       "rmsPhaseJitter": 1.1e-12, "jitterBandLow": 12000.0,
                       "jitterBandHigh": 20000000.0, "startupTime": 0.005,
                       "dutyCycle": {"minimum": 0.45, "maximum": 0.55},
                       "enableFunction": "outputEnable",
                       "supply": {"minimumSupplyVoltage": 2.25, "maximumSupplyVoltage": 3.63,
                                  "currentConsumption": 0.0038}},
        "thermal": {"operatingTemperature": {"minimum": -40, "maximum": 85}},
        "provenance": [{"source": "manufacturerDatasheet"}]
      }}}}})json");
}

// A real-shaped bare watch crystal (Würth 830502587, 32.768 kHz —
// TDAS/examples/watch-crystal-32768.json values).
json good_crystal() {
    return json::parse(R"json({"timeBase": {"oscillator": {"manufacturerInfo": {
      "name": "Würth Elektronik", "reference": "830502587", "datasheetInfo": {
        "part": {"partNumber": "830502587", "package": "3215"},
        "electrical": {"technology": "quartzCrystal", "frequency": 32768.0,
                       "mode": "fundamental", "outputType": "none",
                       "frequencyTolerance": 2e-05, "loadCapacitance": 1.25e-11,
                       "equivalentSeriesResistance": 70000.0},
        "thermal": {"operatingTemperature": {"minimum": -40, "maximum": 85}},
        "provenance": [{"source": "manufacturerDatasheet"}]
      }}}}})json");
}

bool has_tb_finding(const Verdict& v) {
    return std::any_of(v.findings.begin(), v.findings.end(),
                       [](const Finding& f) { return f.code.rfind("TB_", 0) == 0; });
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

TEST_CASE("Magnetics: SubtypeMismatchCmcAsInductor", "[magnetics]") {
    // The ABT #279 shape: description says common-mode choke, subtype says inductor.
    json p = good_magnetic();
    auto& ds = p["magnetic"]["manufacturerInfo"]["datasheetInfo"];
    ds["part"]["description"] = "WE-CMBH Horizontal Common Mode Power Line Choke, 1mH, 10A";
    ds["electrical"][0]["subtype"] = "inductor";
    Verdict v = V.validate(p);
    CHECK(has(v, "MAG_SUBTYPE_MISMATCH", Severity::Suspicious));

    // Correctly tagged: any electrical entry declaring the subtype clears it.
    ds["electrical"][0]["subtype"] = "commonModeChoke";
    CHECK(!has_code(V.validate(p), "MAG_SUBTYPE_MISMATCH"));
}

TEST_CASE("Magnetics: SubtypeMismatchTransformerAndBead", "[magnetics]") {
    json p = good_magnetic();
    auto& ds = p["magnetic"]["manufacturerInfo"]["datasheetInfo"];
    ds["part"]["description"] = "Gate drive transformer 1:1:1";
    ds["electrical"][0]["subtype"] = "inductor";
    CHECK(has(V.validate(p), "MAG_SUBTYPE_MISMATCH", Severity::Suspicious));
    ds["electrical"][0]["subtype"] = "transformer";
    CHECK(!has_code(V.validate(p), "MAG_SUBTYPE_MISMATCH"));

    ds["part"]["description"] = "Ferrite bead 600 ohm @ 100 MHz";
    ds["electrical"][0]["subtype"] = "inductor";
    CHECK(has(V.validate(p), "MAG_SUBTYPE_MISMATCH", Severity::Suspicious));
    ds["electrical"][0]["subtype"] = "chipBead";
    CHECK(!has_code(V.validate(p), "MAG_SUBTYPE_MISMATCH"));
}

TEST_CASE("Magnetics: SubtypeCoherencePlainInductorUnaffected", "[magnetics]") {
    // A DM "power line choke" or a plain inductor must NOT be flagged — the
    // check requires the common-mode noun, not just "choke"/"filter".
    json p = good_magnetic();
    auto& ds = p["magnetic"]["manufacturerInfo"]["datasheetInfo"];
    ds["part"]["description"] = "Power line choke for EMI filter applications";
    ds["electrical"][0]["subtype"] = "inductor";
    CHECK(!has_code(V.validate(p), "MAG_SUBTYPE_MISMATCH"));
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

// Time bases (TDAS): oscillators / crystals / timers / latches + behavioral atoms.
TEST_CASE("TimeBases: GoodMemsOscillatorValid", "[timebases]") {
    Verdict v = V.validate(good_oscillator());
    CHECK(v.valid);
    CHECK(!has_tb_finding(v));  // SiT8008 datasheet values fire nothing
}

TEST_CASE("TimeBases: GoodBareCrystalValid", "[timebases]") {
    Verdict v = V.validate(good_crystal());
    CHECK(v.valid);
    CHECK(!has_tb_finding(v));  // outputType none + no supply is coherent for a bare crystal
}

TEST_CASE("TimeBases: QuartzStabilityTooGoodImpossible", "[timebases]") {
    // A plain (uncompensated) quartz crystal claiming 0.1 ppm over temperature:
    // that is TCXO/OCXO physics, below the 0.5 ppm IMPOSSIBLE floor.
    json p = good_crystal();
    p["timeBase"]["oscillator"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
     ["frequencyStability"] = 0.1e-6;
    Verdict v = V.validate(p);
    CHECK(has(v, "TB_OSC_STABILITY", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("TimeBases: OcxoUndercurrentImpossible", "[timebases]") {
    // 5 mA cannot keep an oven at temperature (real OCXOs draw 0.3-1.5 W steady).
    json p = json::parse(R"json({"timeBase": {"oscillator": {"manufacturerInfo": {
      "name": "X", "reference": "OCX-10M", "datasheetInfo": {"part": {},
      "electrical": {"technology": "ocxo", "frequency": 10000000.0, "outputType": "sine",
                     "supply": {"minimumSupplyVoltage": 4.75, "maximumSupplyVoltage": 5.25,
                                "currentConsumption": 0.005}}}}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "TB_OSC_SUPPLY", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("TimeBases: CmosOutputTooFastImpossible", "[timebases]") {
    // Single-ended CMOS at 600 MHz: above the 500 MHz format ceiling.
    json p = good_oscillator();
    auto& elec = p["timeBase"]["oscillator"]["manufacturerInfo"]["datasheetInfo"]["electrical"];
    elec["frequency"] = 600.0e6;
    Verdict v = V.validate(p);
    CHECK(has(v, "TB_OSC_OUTPUT_TYPE", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("TimeBases: JitterBelowThermalFloorImpossible", "[timebases]") {
    json p = good_oscillator();
    p["timeBase"]["oscillator"]["manufacturerInfo"]["datasheetInfo"]["electrical"]
     ["rmsPhaseJitter"] = 1.0e-15;  // below the 5 fs thermal floor
    Verdict v = V.validate(p);
    CHECK(has(v, "TB_OSC_JITTER", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("TimeBases: ZeroJitterSkipsNotFires", "[timebases]") {
    // Exactly 0 for aging/jitter/startup is vendor-CSV missing data, not a value.
    json p = good_oscillator();
    auto& elec = p["timeBase"]["oscillator"]["manufacturerInfo"]["datasheetInfo"]["electrical"];
    elec["rmsPhaseJitter"] = 0.0;
    elec["agingPerYear"] = 0.0;
    elec["startupTime"] = 0.0;
    Verdict v = V.validate(p);
    CHECK(v.valid);
    CHECK(!has_code(v, "TB_OSC_JITTER"));
    CHECK(!has_code(v, "TB_OSC_AGING"));
    CHECK(!has_code(v, "TB_OSC_STARTUP"));
}

TEST_CASE("TimeBases: Bipolar555TooFastImpossible", "[timebases]") {
    json p = json::parse(R"json({"timeBase": {"timer": {"manufacturerInfo": {
      "name": "X", "reference": "NE555X", "datasheetInfo": {"part": {},
      "electrical": {"technology": "bipolar555", "maximumFrequency": 6000000.0,
                     "supply": {"minimumSupplyVoltage": 4.5, "maximumSupplyVoltage": 16.0}}}}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "TB_TMR_FREQ", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("TimeBases: BareResonatorWithSupplyContradiction", "[timebases]") {
    json p = good_crystal();
    p["timeBase"]["oscillator"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["supply"] =
        json::parse(R"json({"minimumSupplyVoltage": 1.8, "maximumSupplyVoltage": 3.3})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "TB_OSC_RESONATOR_SUPPLY", Severity::Suspicious));
    CHECK(v.valid);  // contradiction is a warning, not a physics violation
}

TEST_CASE("TimeBases: LatchSubHundredPsImpossible", "[timebases]") {
    json p = json::parse(R"json({"timeBase": {"latch": {"manufacturerInfo": {
      "name": "X", "reference": "74HC279X", "datasheetInfo": {"part": {},
      "electrical": {"technology": "CMOS", "propagationDelay": 5e-11,
                     "numberOfChannels": 4}}}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "TB_LATCH_TPD", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("TimeBases: BehavioralOscillatorUnitSlipSuspicious", "[timebases]") {
    // Part-less behavioral atom (design intent): light screening only.
    json p = json::parse(R"json({"timeBase": {"oscillator": {"behavioral": {
      "shape": "sawtooth", "frequency": 5.0e10, "amplitude": 1.0, "offset": 0.0}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "TB_BEHAVIORAL", Severity::Suspicious));
    CHECK(v.valid);  // behavioral atoms are never invalidated
}

TEST_CASE("TimeBases: BehavioralMonostableHourPlusSuspicious", "[timebases]") {
    json p = json::parse(R"json({"timeBase": {"timer": {"behavioral": {
      "mode": "monostable", "outputHigh": 5.0, "outputLow": 0.0, "threshold": 2.5,
      "polarity": "risingEdge", "onTime": 7200.0, "retriggerable": false}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "TB_BEHAVIORAL", Severity::Suspicious));
    CHECK(v.valid);
}

TEST_CASE("TimeBases: BehavioralOnlyRecordSkipsDatasheetChecks", "[timebases]") {
    json p = json::parse(R"json({"timeBase": {"latch": {"behavioral": {
      "setThreshold": 2.5, "resetThreshold": 1.0, "outputHigh": 5.0, "outputLow": 0.0,
      "dominance": "reset"}}}})json");
    Verdict v = V.validate(p);
    CHECK(v.valid);
    CHECK(v.findings.empty());  // no datasheet, no physics claims, nothing to flag
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

// ---- Thermistors (THERM_*) -------------------------------------------------
namespace {
// A real-shaped NTC (Vishay NTCLE100E3, 10 kOhm, B25/85 = 3977 K).
json good_thermistor() {
    return json::parse(R"json({
      "thermistor": {"manufacturerInfo": {"name": "Vishay", "reference": "NTCLE100E3103",
        "datasheetInfo": {
          "part": {"technology": "ntc"},
          "electrical": {"resistanceAt25C": {"nominal": 10000.0}, "resistanceTolerance": 0.01,
                         "bConstant": 3977.0, "bConstantTemperatures": [25, 85],
                         "dissipationConstant": 0.007, "thermalTimeConstant": 15.0},
          "thermal": {"operatingTemperature": {"minimum": -55, "maximum": 125}},
          "provenance": [{"source": "manufacturerDatasheet"}]}}}})json");
}
}  // namespace

TEST_CASE("Thermistor: good part passes", "[thermistor]") {
    Verdict v = V.validate(good_thermistor());
    CHECK(v.valid);
    CHECK(!has_code(v, "THERM_R25_RANGE"));
    CHECK(!has_code(v, "THERM_BETA_RANGE"));
    CHECK(!has_code(v, "THERM_HEAT_CAPACITY"));
}

TEST_CASE("Thermistor: R25 out of range impossible", "[thermistor]") {
    json p = good_thermistor();
    p["thermistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["resistanceAt25C"]["nominal"] = 1e9;
    Verdict v = V.validate(p);
    CHECK(has(v, "THERM_R25_RANGE", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("Thermistor: negative R25 impossible", "[thermistor]") {
    json p = good_thermistor();
    p["thermistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["resistanceAt25C"]["nominal"] = -5.0;
    CHECK(has(V.validate(p), "THERM_POSITIVITY", Severity::Impossible));
}

TEST_CASE("Thermistor: beta out of range impossible", "[thermistor]") {
    json p = good_thermistor();
    p["thermistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["bConstant"] = 12000.0;
    CHECK(has(V.validate(p), "THERM_BETA_RANGE", Severity::Impossible));
}

TEST_CASE("Thermistor: percent-as-fraction tolerance impossible", "[thermistor]") {
    json p = good_thermistor();
    p["thermistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["resistanceTolerance"] = 200.0;
    CHECK(has(V.validate(p), "THERM_TOLERANCE", Severity::Impossible));
}

TEST_CASE("Thermistor: PTC carrying a beta is suspicious", "[thermistor]") {
    json p = good_thermistor();
    p["thermistor"]["manufacturerInfo"]["datasheetInfo"]["part"]["technology"] = "ptc";
    CHECK(has(V.validate(p), "THERM_PTC_HAS_BETA", Severity::Suspicious));
}

TEST_CASE("Thermistor: excessive time constant impossible", "[thermistor]") {
    json p = good_thermistor();
    p["thermistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["thermalTimeConstant"] = 7200.0;
    CHECK(has(V.validate(p), "THERM_TIME_CONSTANT", Severity::Impossible));
}

TEST_CASE("Thermistor: implausible implied heat capacity suspicious", "[thermistor]") {
    json p = good_thermistor();
    auto& el = p["thermistor"]["manufacturerInfo"]["datasheetInfo"]["electrical"];
    el["thermalTimeConstant"] = 100.0;
    el["dissipationConstant"] = 5.0;  // C_th = 500 J/K -> absurd for a bead
    CHECK(has(V.validate(p), "THERM_HEAT_CAPACITY", Severity::Suspicious));
}

TEST_CASE("Thermistor: operating temperature below absolute zero impossible", "[thermistor]") {
    json p = good_thermistor();
    p["thermistor"]["manufacturerInfo"]["datasheetInfo"]["thermal"]["operatingTemperature"]["minimum"] = -300.0;
    CHECK(has(V.validate(p), "THERM_ABS_ZERO", Severity::Impossible));
}

// ---- AAS analog ICs: wrapped-format dispatch + switch leakage --------------
TEST_CASE("AAS: PEAS-wrapped {analog:{...}} record dispatches to check_analog", "[analog]") {
    // TAS/data/analog_ics.ndjson stores the AAS subtype nested under `analog`.
    json p = json::parse(R"json({"analog": {"operationalAmplifier": {"manufacturerInfo": {
      "name": "TI", "reference": "OPAX", "datasheetInfo": {
        "part": {"partNumber": "OPAX"},
        "electrical": {"numberOfChannels": 1, "maximumSupplyVoltage": 36.0},
        "provenance": [{"source": "manufacturerDatasheet"}]}}}}})json");
    CHECK_NOTHROW(V.validate(p));           // must NOT throw "no known discriminator"
    Verdict v = V.validate(p);
    CHECK(v.valid);
}

TEST_CASE("AAS: analog switch with mA off-leakage is impossible", "[analog]") {
    json p = json::parse(R"json({"analog": {"analogSwitch": {"manufacturerInfo": {
      "name": "X", "reference": "SW", "datasheetInfo": {
        "part": {"partNumber": "SW"},
        "electrical": {"switchConfiguration": "SPDT", "onResistance": 50.0,
                       "offLeakageCurrent": 0.05},
        "provenance": [{"source": "manufacturerDatasheet"}]}}}}})json");
    Verdict v = V.validate(p);
    CHECK(has(v, "SW_LEAK", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("AAS: multiplexer with absurd on-resistance is impossible", "[analog]") {
    json p = json::parse(R"json({"analog": {"multiplexer": {"manufacturerInfo": {
      "name": "X", "reference": "MUX", "datasheetInfo": {
        "part": {"partNumber": "MUX"},
        "electrical": {"multiplexerConfiguration": "8:1", "onResistance": 5.0e6},
        "provenance": [{"source": "manufacturerDatasheet"}]}}}}})json");
    CHECK(has(V.validate(p), "SW_RON", Severity::Impossible));
}

// --- Connectors (CONAS) -------------------------------------------------------
// Every threshold exercised below was calibrated against all 392,346 records of
// TAS/data/connectors.ndjson; the fire counts quoted in connectors.cpp are the
// measured ones. See that file's header for the three checks that were designed,
// measured and REJECTED for firing on 45-85% of the catalog.

namespace {
// A real, physically sane 2.54 mm gold-plated pin header.
json good_connector() {
    return json::parse(R"json({"connector": {"manufacturerInfo": {
      "name": "Harwin", "reference": "M20-9760246", "datasheetInfo": {
        "part": {"partNumber": "M20-9760246", "matingPolarity": "male"},
        "electrical": {"ratedCurrentPerContact": 3.0, "ratedVoltage": 250.0,
                       "contactResistance": {"maximum": 0.02},
                       "insulationResistance": 1.0e10},
        "mechanical": {"pitch": 0.00254, "positions": 4, "rows": 2, "matingCycles": 500},
        "material": {"contactPlating": {"matingAreaMaterialRef": "au-gold",
                                        "matingAreaThickness": 7.62e-7}},
        "environmental": {"operatingTemperature": {"minimum": -55.0, "maximum": 105.0}},
        "familyDetails": {"family": "pinHeaderSocket", "tailLength": 0.003},
        "provenance": [{"source": "manufacturerDatasheet"}]}}}})json");
}
}  // namespace

TEST_CASE("CONN: a real pin header fires no connector finding", "[connector]") {
    Verdict v = V.validate(good_connector());
    CHECK(v.valid);
    for (const auto& f : v.findings) CHECK(f.code.rfind("CONN_", 0) != 0);
}

// Holm voltage-temperature relation. 20 A through a stated 20 mOhm is 0.400 V,
// 3.1x the 0.13 V melting voltage of tin — the two specs cannot describe the
// same measurement. SUSPICIOUS, not IMPOSSIBLE: the largest ratio in the whole
// catalog is 3.08x, which a bulk-LLCR-vs-mated-pair convention mismatch explains.
TEST_CASE("CONN: rated current through stated resistance melts its own tin plating", "[connector]") {
    json p = good_connector();
    auto& d = p["connector"]["manufacturerInfo"]["datasheetInfo"];
    d["electrical"]["ratedCurrentPerContact"] = 20.0;
    d["electrical"]["contactResistance"] = 0.02;
    d["material"]["contactPlating"]["matingAreaMaterialRef"] = "sn-tin";
    Verdict v = V.validate(p);
    CHECK(has(v, "CONN_CONTACT_VOLTAGE", Severity::Suspicious));
    CHECK(v.valid);  // a provenance defect, not an impossible part
}

TEST_CASE("CONN: contact voltage past every metal's melting voltage is impossible", "[connector]") {
    json p = good_connector();
    auto& d = p["connector"]["manufacturerInfo"]["datasheetInfo"];
    d["electrical"]["ratedCurrentPerContact"] = 100.0;
    d["electrical"]["contactResistance"] = 0.1;  // 10 V across a "contact"
    d["material"]["contactPlating"].erase("matingAreaMaterialRef");
    Verdict v = V.validate(p);
    CHECK(has(v, "CONN_CONTACT_VOLTAGE", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("CONN: gold plating tolerates a voltage that would melt tin", "[connector]") {
    json p = good_connector();
    auto& d = p["connector"]["manufacturerInfo"]["datasheetInfo"];
    d["electrical"]["ratedCurrentPerContact"] = 3.0;
    d["electrical"]["contactResistance"] = 0.02;  // 0.06 V: under gold's 0.08 V softening
    CHECK(!has_code(V.validate(p), "CONN_CONTACT_VOLTAGE"));
}

// Paschen, not a linear kV/mm rule. A 1 mm gap breaks down at 5.03 kV ideal, so
// 4 kV across 1 mm must NOT fire (the old 3 kV/mm rule would have called it
// impossible) while 8 kV must.
TEST_CASE("CONN: clearance uses the Paschen curve, not a linear kV/mm rule", "[connector]") {
    json p = good_connector();
    auto& e = p["connector"]["manufacturerInfo"]["datasheetInfo"]["electrical"];
    e["clearance"] = 0.001;
    e["ratedVoltage"] = 4000.0;
    e["dielectricWithstandingVoltage"] = 4000.0;
    CHECK(!has_code(V.validate(p), "CONN_CLEARANCE_BREAKDOWN"));
    e["ratedVoltage"] = 8000.0;
    e["dielectricWithstandingVoltage"] = 8000.0;
    Verdict v = V.validate(p);
    CHECK(has(v, "CONN_CLEARANCE_BREAKDOWN", Severity::Impossible));
    CHECK(has(v, "CONN_DWV_VS_CLEARANCE", Severity::Impossible));
}

TEST_CASE("CONN: sub-Paschen-minimum gaps decline to fire rather than guess", "[connector]") {
    json p = good_connector();
    auto& e = p["connector"]["manufacturerInfo"]["datasheetInfo"]["electrical"];
    e["clearance"] = 1.0e-6;  // left of the Paschen minimum: undefined, must not fire
    e["ratedVoltage"] = 250.0;
    CHECK(!has_code(V.validate(p), "CONN_CLEARANCE_BREAKDOWN"));
}

// Unit slip: a pitch of 2.54 is 2.54 METRES.
TEST_CASE("CONN: a pitch stored in millimetres is impossible", "[connector]") {
    json p = good_connector();
    p["connector"]["manufacturerInfo"]["datasheetInfo"]["mechanical"]["pitch"] = 2.54;
    Verdict v = V.validate(p);
    CHECK(has(v, "CONN_UNIT_SCALE", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("CONN: a plating thickness stored in micrometres is impossible", "[connector]") {
    json p = good_connector();
    p["connector"]["manufacturerInfo"]["datasheetInfo"]["material"]["contactPlating"]
     ["matingAreaThickness"] = 0.762;
    CHECK(has(V.validate(p), "CONN_UNIT_SCALE", Severity::Impossible));
}

// The contact cross-section a 0.5 mm pitch allows cannot carry 20 A.
TEST_CASE("CONN: current density beyond any contact cross-section is suspicious", "[connector]") {
    json p = good_connector();
    auto& d = p["connector"]["manufacturerInfo"]["datasheetInfo"];
    d["mechanical"]["pitch"] = 0.0005;
    d["electrical"]["ratedCurrentPerContact"] = 20.0;
    d["electrical"]["contactResistance"] = 0.001;
    Verdict v = V.validate(p);
    CHECK(has(v, "CONN_CURRENT_DENSITY", Severity::Suspicious));
    CHECK(v.valid);  // hybrid power+signal connectors legitimately reach this
}

// 392 degC is 200 degC read off a Fahrenheit datasheet. 260 degC is a real PTFE part.
TEST_CASE("CONN: an unconverted Fahrenheit maximum is caught", "[connector]") {
    json p = good_connector();
    p["connector"]["manufacturerInfo"]["datasheetInfo"]["environmental"]["operatingTemperature"]
     ["maximum"] = 392.0;
    CHECK(has(V.validate(p), "CONN_TEMPERATURE_UNIT", Severity::Suspicious));
}

TEST_CASE("CONN: a genuine 260 degC PTFE part is not called Fahrenheit", "[connector]") {
    json p = good_connector();
    p["connector"]["manufacturerInfo"]["datasheetInfo"]["environmental"]["operatingTemperature"]
     ["maximum"] = 260.0;
    CHECK(!has_code(V.validate(p), "CONN_TEMPERATURE_UNIT"));
}

TEST_CASE("CONN: inverted operating temperature range is impossible", "[connector]") {
    json p = good_connector();
    auto& t = p["connector"]["manufacturerInfo"]["datasheetInfo"]["environmental"]
               ["operatingTemperature"];
    t["minimum"] = 125.0;
    t["maximum"] = -40.0;
    Verdict v = V.validate(p);
    CHECK(has(v, "CONN_TEMPERATURE_RANGE", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("CONN: more rows than positions is suspicious", "[connector]") {
    json p = good_connector();
    auto& m = p["connector"]["manufacturerInfo"]["datasheetInfo"]["mechanical"];
    m["positions"] = 2;
    m["rows"] = 3;
    CHECK(has(V.validate(p), "CONN_ROWS_POSITIONS", Severity::Suspicious));
}

TEST_CASE("CONN: durability beyond a tin mating surface is suspicious", "[connector]") {
    json p = good_connector();
    auto& d = p["connector"]["manufacturerInfo"]["datasheetInfo"];
    d["material"]["contactPlating"]["matingAreaMaterialRef"] = "sn-tin";
    d["mechanical"]["matingCycles"] = 5000;
    CHECK(has(V.validate(p), "CONN_DURABILITY", Severity::Suspicious));
}

TEST_CASE("CONN: durability beyond a gold flash is suspicious", "[connector]") {
    json p = good_connector();
    auto& d = p["connector"]["manufacturerInfo"]["datasheetInfo"];
    d["material"]["contactPlating"]["matingAreaThickness"] = 5.0e-8;  // 0.05 um flash
    d["mechanical"]["matingCycles"] = 5000;
    CHECK(has(V.validate(p), "CONN_DURABILITY", Severity::Suspicious));
}

TEST_CASE("CONN: 30 uin gold at 500 cycles is fine", "[connector]") {
    CHECK(!has_code(V.validate(good_connector()), "CONN_DURABILITY"));
}

TEST_CASE("CONN: a VSWR below unity is impossible", "[connector]") {
    json p = good_connector();
    auto& f = p["connector"]["manufacturerInfo"]["datasheetInfo"]["familyDetails"];
    f["family"] = "rf";
    f["characteristicImpedance"] = 50.0;
    f["maxVswr"] = 0.8;
    Verdict v = V.validate(p);
    CHECK(has(v, "CONN_RF_BAND", Severity::Impossible));
    CHECK(!v.valid);
}

TEST_CASE("CONN: an inverted RF frequency range is impossible", "[connector]") {
    json p = good_connector();
    auto& f = p["connector"]["manufacturerInfo"]["datasheetInfo"]["familyDetails"];
    f["family"] = "rf";
    f["characteristicImpedance"] = 50.0;
    f["frequencyRange"] = json::parse(R"json({"minimum": 1.8e10, "maximum": 6.0e9})json");
    CHECK(has(V.validate(p), "CONN_RF_BAND", Severity::Impossible));
}

TEST_CASE("CONN: a 50 Ohm SMA fires no RF finding", "[connector]") {
    json p = good_connector();
    auto& f = p["connector"]["manufacturerInfo"]["datasheetInfo"]["familyDetails"];
    f["family"] = "rf";
    f["interface"] = "SMA";
    f["characteristicImpedance"] = 50.0;
    f["frequencyRange"] = json::parse(R"json({"maximum": 1.8e10})json");
    f["maxVswr"] = 1.3;
    CHECK(!has_code(V.validate(p), "CONN_RF_BAND"));
}

TEST_CASE("CONN: mated heights spanning an order of magnitude are suspicious", "[connector]") {
    json p = good_connector();
    p["connector"]["manufacturerInfo"]["datasheetInfo"]["mating"] = json::parse(R"json(
      {"matesWith": [{"series": "A", "relation": "mates", "matedHeight": 0.005},
                     {"series": "B", "relation": "mates", "matedHeight": 0.09}]})json");
    CHECK(has(V.validate(p), "CONN_MATED_HEIGHT", Severity::Suspicious));
}

TEST_CASE("CONN: a connector with no electrical block skips rather than passes", "[connector]") {
    json p = json::parse(R"json({"connector": {"manufacturerInfo": {
      "name": "X", "reference": "Y", "datasheetInfo": {"part": {"partNumber": "Y"}}}}})json");
    Verdict v = V.validate(p);
    CHECK(std::find(v.skipped.begin(), v.skipped.end(), "CONN_ELECTRICAL_*") != v.skipped.end());
}
