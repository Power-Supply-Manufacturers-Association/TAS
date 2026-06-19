// SPDX-License-Identifier: Apache-2.0
// Resistor physics checks. `datasheet` is the datasheetInfo object:
//   electrical.{resistance,tolerance,temperatureCoefficient,powerRating,maxVoltage},
//   part.technology, mechanical.{length,width,height}.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cmath>
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

void check_resistors(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                     std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("RES_*");
        return;
    }
    auto R = scalar_at(*elec, {"resistance"});
    auto P = scalar_at(*elec, {"powerRating"});
    auto Vmax = scalar_at(*elec, {"maxVoltage"});

    // Geometry: length/width/height live directly under mechanical.
    const json* mech = at(datasheet, "mechanical");
    auto L = mech ? scalar_at(*mech, {"length"}) : std::nullopt;
    auto W = mech ? scalar_at(*mech, {"width"}) : std::nullopt;

    // CHECK: resistance range.
    if (R) {
        if (*R <= 0)
            emit(out, ctx, "RES_R_RANGE", Severity::Impossible, *R, 0, "resistance <= 0");
        else if (*R < thr::RES_R_MIN_SUS)
            emit(out, ctx, "RES_R_RANGE", Severity::Suspicious, *R, thr::RES_R_MIN_SUS,
                 fmt("resistance below manufacturable floor [ohm]", *R, thr::RES_R_MIN_SUS));
        else if (*R > thr::RES_R_MAX_SUS)
            emit(out, ctx, "RES_R_RANGE", Severity::Suspicious, *R, thr::RES_R_MAX_SUS,
                 fmt("resistance above manufacturable ceiling [ohm]", *R, thr::RES_R_MAX_SUS));
    } else {
        skipped.push_back("RES_R_RANGE");
    }

    // CHECK: power dissipation density over footprint.
    if (P && L && W && *L > 0 && *W > 0) {
        double area_mm2 = (*L * 1000.0) * (*W * 1000.0);
        double density = *P / area_mm2;  // W/mm^2
        if (density > thr::RES_POWER_PER_MM2_IMP)
            emit(out, ctx, "RES_POWER_SIZE", Severity::Impossible, density,
                 thr::RES_POWER_PER_MM2_IMP,
                 fmt("powerRating/footprint impossibly high [W/mm^2]", density,
                     thr::RES_POWER_PER_MM2_IMP));
        else if (density > thr::RES_POWER_PER_MM2_SUS)
            emit(out, ctx, "RES_POWER_SIZE", Severity::Suspicious, density,
                 thr::RES_POWER_PER_MM2_SUS, fmt("powerRating/footprint high [W/mm^2]", density,
                                                 thr::RES_POWER_PER_MM2_SUS));
    }

    // CHECK (NEW): max-voltage field over body length.
    if (Vmax && L && *L > 0) {
        double field = *Vmax / *L;  // V/m
        if (field > thr::RES_FIELD_VPM_IMP)
            emit(out, ctx, "RES_MAXV_SIZE", Severity::Impossible, field, thr::RES_FIELD_VPM_IMP,
                 fmt("maxVoltage/length implies impossible field [V/m]", field,
                     thr::RES_FIELD_VPM_IMP));
        else if (field > thr::RES_FIELD_VPM_SUS)
            emit(out, ctx, "RES_MAXV_SIZE", Severity::Suspicious, field, thr::RES_FIELD_VPM_SUS,
                 fmt("maxVoltage/length implies high field [V/m]", field, thr::RES_FIELD_VPM_SUS));
    }

    // CHECK (NEW): P = V^2/R consistency. Implied power at rated voltage should
    // not greatly exceed the power rating.
    if (Vmax && R && P && *R > 0 && *P > 0) {
        double implied = (*Vmax) * (*Vmax) / *R;
        if (implied > *P * thr::RES_PVR_RATIO_SUS)
            emit(out, ctx, "RES_POWER_V_R", Severity::Suspicious, implied, *P,
                 fmt("maxVoltage^2/R far exceeds powerRating [W]", implied, *P));
    }

    // CHECK (NEW): temperature coefficient magnitude.
    if (auto tc = scalar_at(*elec, {"temperatureCoefficient"})) {
        if (std::fabs(*tc) > thr::RES_TEMPCO_PPM_SUS)
            emit(out, ctx, "RES_TEMPCO", Severity::Suspicious, *tc, thr::RES_TEMPCO_PPM_SUS,
                 fmt("|temperatureCoefficient| very large [ppm/C]", *tc, thr::RES_TEMPCO_PPM_SUS));
    }

    // CHECK (NEW): tolerance band.
    if (auto tol = scalar_at(*elec, {"tolerance"})) {
        if (*tol <= 0)
            emit(out, ctx, "RES_TOLERANCE", Severity::Impossible, *tol, 0, "tolerance <= 0");
        else if (*tol > thr::RES_TOL_MAX_SUS)
            emit(out, ctx, "RES_TOLERANCE", Severity::Suspicious, *tol, thr::RES_TOL_MAX_SUS,
                 fmt("tolerance fraction very large", *tol, thr::RES_TOL_MAX_SUS));
    }
}

}  // namespace tas
