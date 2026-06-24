// SPDX-License-Identifier: Apache-2.0
// Connector (CONAS) physics checks. `datasheet` is the connector datasheetInfo:
//   electrical.{ratedCurrentPerContact,ratedVoltage,contactResistance,
//     insulationResistance,dielectricWithstandingVoltage,clearance,creepage}.
// Bounds: contact resistance ~0.15 mOhm (power) to ~0.1 Ohm (signal); power
// contacts to ~50 A / 600 V, busbars to a few hundred A, HV connectors to tens
// of kV; air dielectric strength 3 kV/mm sets the minimum clearance for a voltage.
// Sources: Arrow/Harwin connector ratings, IEC 60664-1 clearance/creepage.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <sstream>
#include <string>

namespace tas {
namespace {

std::string fmt(const std::string& s, double a, double b = 0) {
    std::ostringstream os;
    os << s << " (value=" << a;
    if (b != 0) os << ", threshold=" << b;
    os << ")";
    return os.str();
}

}  // namespace

void check_connectors(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                      std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("CONN_*");
        return;
    }
    auto I = scalar_at(*elec, {"ratedCurrentPerContact"});
    auto V = scalar_at(*elec, {"ratedVoltage"});

    // CHECK: positivity.
    if (I && *I <= 0)
        emit(out, ctx, "CONN_POSITIVITY", Severity::Impossible, *I, 0,
             "ratedCurrentPerContact <= 0");
    if (V && *V <= 0)
        emit(out, ctx, "CONN_POSITIVITY", Severity::Impossible, *V, 0, "ratedVoltage <= 0");

    // CHECK: per-contact current range.
    if (I && *I > 0) {
        if (*I > thr::CONN_CURRENT_IMP)
            emit(out, ctx, "CONN_CURRENT_RANGE", Severity::Impossible, *I, thr::CONN_CURRENT_IMP,
                 fmt("ratedCurrentPerContact exceeds any single contact [A]", *I, thr::CONN_CURRENT_IMP));
        else if (*I > thr::CONN_CURRENT_SUS)
            emit(out, ctx, "CONN_CURRENT_RANGE", Severity::Suspicious, *I, thr::CONN_CURRENT_SUS,
                 fmt("ratedCurrentPerContact high for a single contact [A]", *I, thr::CONN_CURRENT_SUS));
    }

    // CHECK: rated voltage range.
    if (V && *V > 0) {
        if (*V > thr::CONN_VOLTAGE_IMP)
            emit(out, ctx, "CONN_VOLTAGE_RANGE", Severity::Impossible, *V, thr::CONN_VOLTAGE_IMP,
                 fmt("ratedVoltage implausibly high [V]", *V, thr::CONN_VOLTAGE_IMP));
        else if (*V > thr::CONN_VOLTAGE_SUS)
            emit(out, ctx, "CONN_VOLTAGE_RANGE", Severity::Suspicious, *V, thr::CONN_VOLTAGE_SUS,
                 fmt("ratedVoltage very high [V]", *V, thr::CONN_VOLTAGE_SUS));
    }

    // CHECK: mated-pair contact resistance.
    if (auto rc = scalar_at(*elec, {"contactResistance"})) {
        if (*rc <= 0)
            emit(out, ctx, "CONN_CONTACT_RESISTANCE", Severity::Impossible, *rc, 0,
                 "contactResistance <= 0");
        else if (*rc > thr::CONN_RCONTACT_IMP_HI)
            emit(out, ctx, "CONN_CONTACT_RESISTANCE", Severity::Impossible, *rc,
                 thr::CONN_RCONTACT_IMP_HI,
                 fmt("contactResistance too high to be a contact [Ohm]", *rc, thr::CONN_RCONTACT_IMP_HI));
        else if (*rc < thr::CONN_RCONTACT_SUS_LO || *rc > thr::CONN_RCONTACT_SUS_HI)
            emit(out, ctx, "CONN_CONTACT_RESISTANCE", Severity::Suspicious, *rc, 0,
                 fmt("contactResistance outside typical 0.01 mOhm..1 Ohm band", *rc));
    }

    // CHECK: insulation resistance.
    if (auto ir = scalar_at(*elec, {"insulationResistance"})) {
        if (*ir <= 0)
            emit(out, ctx, "CONN_INSULATION_R", Severity::Impossible, *ir, 0,
                 "insulationResistance <= 0");
        else if (*ir < thr::CONN_INSULATION_SUS_LO)
            emit(out, ctx, "CONN_INSULATION_R", Severity::Suspicious, *ir, thr::CONN_INSULATION_SUS_LO,
                 fmt("insulationResistance low for an insulator [Ohm]", *ir, thr::CONN_INSULATION_SUS_LO));
    }

    // CHECK: clearance must hold off the rated voltage in air (3 kV/mm).
    auto clearance = scalar_at(*elec, {"clearance"});
    if (clearance && V && *V > 0 && *clearance > 0) {
        double min_clearance = *V / thr::CONN_AIR_DIELECTRIC_VPM;  // metres
        if (*clearance < min_clearance)
            emit(out, ctx, "CONN_CLEARANCE_BREAKDOWN", Severity::Impossible, *clearance, min_clearance,
                 fmt("clearance below air-breakdown minimum for ratedVoltage [m]", *clearance,
                     min_clearance));
    }

    // CHECK: creepage (surface path) must be >= clearance (air path).
    if (auto creepage = scalar_at(*elec, {"creepage"})) {
        if (clearance && *creepage > 0 && *creepage < *clearance)
            emit(out, ctx, "CONN_CREEPAGE_CLEARANCE", Severity::Impossible, *creepage, *clearance,
                 fmt("creepage < clearance (surface path shorter than air path) [m]", *creepage,
                     *clearance));
    }

    // CHECK: dielectric withstanding (proof) voltage must exceed the working voltage.
    if (auto dwv = scalar_at(*elec, {"dielectricWithstandingVoltage"})) {
        if (V && *dwv > 0 && *dwv < *V)
            emit(out, ctx, "CONN_DWV_VS_RATED", Severity::Impossible, *dwv, *V,
                 fmt("dielectricWithstandingVoltage below ratedVoltage [V]", *dwv, *V));
    }
}

}  // namespace tas
