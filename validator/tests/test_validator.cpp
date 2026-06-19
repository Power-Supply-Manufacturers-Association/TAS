// SPDX-License-Identifier: Apache-2.0
// C++ unit tests for the TAS physics validator. One record that passes plus
// records that trip representative IMPOSSIBLE and SUSPICIOUS branches per family.
#include "tas_validator/helpers.hpp"
#include "tas_validator/validator.hpp"

#include <UnitTest++/UnitTest++.h>

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

}  // namespace

SUITE(Magnetics) {
    TEST(GoodPartIsValid) {
        Verdict v = V.validate(good_magnetic());
        CHECK(v.valid);
        CHECK(!has_code(v, "MAG_ENERGY_DENSITY"));  // ~0.8 mJ/cm^3, well under ceiling
    }

    TEST(EnergyDensityImpossible) {
        json p = good_magnetic();
        // Absurd: 1 H at 1000 A in a 4x4x2mm body.
        p["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]["inductance"] = 1.0;
        p["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]["saturationCurrentPeak"] = 1000.0;
        Verdict v = V.validate(p);
        CHECK(has(v, "MAG_ENERGY_DENSITY", Severity::Impossible));
        CHECK(!v.valid);
    }

    TEST(InductanceToleranceOrdering) {
        json p = good_magnetic();
        auto& ind = p["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0]["inductance"];
        ind["minimum"] = 5e-7;  // minimum > nominal
        Verdict v = V.validate(p);
        CHECK(has(v, "MAG_L_TOLERANCE", Severity::Impossible));
    }

    TEST(MissingInductanceSkips) {
        json p = good_magnetic();
        p["magnetic"]["manufacturerInfo"]["datasheetInfo"]["electrical"][0].erase("inductance");
        Verdict v = V.validate(p);
        CHECK(v.valid);
        CHECK(!v.skipped.empty());
    }
}

SUITE(Capacitors) {
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

    TEST(GoodCapValid) {
        Verdict v = V.validate(good_cap());
        CHECK(v.valid);
    }

    TEST(ToleranceOrderingImpossible) {
        json p = good_cap();
        p["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["capacitance"] =
            json::parse(R"json({"nominal": 0.001, "minimum": 0.002})json");
        Verdict v = V.validate(p);
        CHECK(has(v, "CAP_TOLERANCE", Severity::Impossible));
        CHECK(!v.valid);
    }

    TEST(EnergyDensityImpossible) {
        json p = good_cap();
        // 1 F at 1000 V in 5 mm^3 -> astronomically high density.
        p["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["capacitance"] = 1.0;
        p["capacitor"]["manufacturerInfo"]["datasheetInfo"]["electrical"]["ratedVoltage"] = 1000.0;
        Verdict v = V.validate(p);
        CHECK(has(v, "CAP_ENERGY_DENSITY", Severity::Impossible));
    }
}

SUITE(Semiconductors) {
    TEST(MosfetCapHierarchy) {
        json p = json::parse(R"json({"semiconductor": {"mosfet": {"manufacturerInfo": {
          "reference": "X", "datasheetInfo": {"part": {"technology": "GaN"},
          "electrical": {"inputCapacitance": 1e-12, "outputCapacitance": 2e-12,
                         "reverseTransferCapacitance": 3e-12}}}}}})json");  // inverted order
        Verdict v = V.validate(p);
        CHECK(has(v, "MOS_CAP_HIERARCHY", Severity::Impossible));
        CHECK(!v.valid);
    }

    TEST(MosfetChargeHierarchy) {
        json p = json::parse(R"json({"semiconductor": {"mosfet": {"manufacturerInfo": {
          "reference": "X", "datasheetInfo": {"part": {"technology": "Si"},
          "electrical": {"totalGateCharge": 1e-9, "gateSourceCharge": 8e-10,
                         "gateDrainCharge": 8e-10}}}}}})json");  // Qgs+Qgd > Qg
        Verdict v = V.validate(p);
        CHECK(has(v, "MOS_CHARGE_HIERARCHY", Severity::Impossible));
    }

    TEST(DiodeSurgeBelowForward) {
        json p = json::parse(R"json({"semiconductor": {"diode": {"manufacturerInfo": {
          "reference": "X", "datasheetInfo": {"part": {"technology": "Schottky"},
          "electrical": {"reverseVoltage": 60, "forwardCurrent": 30, "surgeCurrent": 10,
                         "forwardVoltage": 0.42}}}}}})json");
        Verdict v = V.validate(p);
        CHECK(has(v, "DIO_SURGE_VS_IF", Severity::Impossible));
    }

    TEST(IgbtVcesatExceedsVces) {
        json p = json::parse(R"json({"semiconductor": {"igbt": {"manufacturerInfo": {
          "reference": "X", "datasheetInfo": {
          "electrical": {"collectorEmitterVoltage": 2.0, "continuousCollectorCurrent": 100,
                         "collectorEmitterSaturation": 3.0}}}}}})json");
        Verdict v = V.validate(p);
        CHECK(has(v, "IGBT_VCESAT_VS_VCES", Severity::Impossible));
    }
}

SUITE(Framework) {
    TEST(UnknownDiscriminatorThrows) {
        CHECK_THROW(V.validate(json::parse(R"json({"widget": {}})json")), std::invalid_argument);
    }

    TEST(MalformedScalarThrows) {
        json p = json::parse(R"json({"resistor": {"manufacturerInfo": {"reference": "X",
          "datasheetInfo": {"electrical": {"resistance": "not-a-number"}}}}})json");
        CHECK_THROW(V.validate(p), MalformedField);
    }

    TEST(CheckCodesNonEmpty) { CHECK(PartValidator::check_codes().size() > 20); }
}

int main() { return UnitTest::RunAllTests(); }
