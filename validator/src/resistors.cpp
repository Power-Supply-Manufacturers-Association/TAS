// SPDX-License-Identifier: MIT
// Resistor physics checks. `datasheet` is the datasheetInfo object:
//   electrical.{resistance,tolerance,temperatureCoefficient,powerRating,maxVoltage},
//   part.technology, mechanical.{length,width,height}.
#include "tas_validator/eseries.hpp"
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cmath>
#include <sstream>
#include <string>

namespace tas {

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

    // CHECK: resistance range. resistance == 0 is a real 0-ohm jumper/link (Yageo
    // YC-series arrays, many vendors), so only a NEGATIVE resistance is impossible.
    if (R) {
        if (*R < 0)
            emit(out, ctx, "RES_R_RANGE", Severity::Impossible, *R, 0, "resistance < 0");
        else if (*R > 0 && *R < thr::RES_R_MIN_SUS)
            emit(out, ctx, "RES_R_RANGE", Severity::Suspicious, *R, thr::RES_R_MIN_SUS,
                 fmt("resistance below manufacturable floor [ohm]", *R, thr::RES_R_MIN_SUS));
        else if (*R > thr::RES_R_MAX_SUS)
            emit(out, ctx, "RES_R_RANGE", Severity::Suspicious, *R, thr::RES_R_MAX_SUS,
                 fmt("resistance above manufacturable ceiling [ohm]", *R, thr::RES_R_MAX_SUS));
    } else {
        skipped.push_back("RES_R_RANGE");
    }

    // CHECK: positivity of the power and voltage ratings (were silently accepted).
    if (P && *P <= 0)
        emit(out, ctx, "RES_POWER_SIZE", Severity::Impossible, *P, 0, "powerRating <= 0");
    if (Vmax && *Vmax <= 0)
        emit(out, ctx, "RES_MAXV_SIZE", Severity::Impossible, *Vmax, 0, "maxVoltage <= 0");

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

    // (Removed RES_POWER_V_R: maxVoltage^2/R >> powerRating is normal for high-ohm
    // voltage-limited resistors, and ~76% of catalog parts back-compute maxVoltage as
    // sqrt(P*R) so the field carries no independent information. Real-vs-synthetic
    // discrimination for resistors belongs to the E-series preferred-value check.)

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

    // CHECK (NEW, anti-synthesis): the nominal resistance should land on an IEC
    // 60063 E-series preferred value, and not carry more significant figures than a
    // real preferred value. Skip sub-0.1 ohm sense/shunt parts (non-E-series by
    // design). SUSPICIOUS only — a real-vs-fabricated signal, not a physics bound.
    if (const json* rf = at(*elec, "resistance")) {
        std::optional<double> rnom;
        if (rf->is_number())
            rnom = rf->get<double>();
        else if (rf->is_object() && rf->contains("nominal") && (*rf)["nominal"].is_number())
            rnom = (*rf)["nominal"].get<double>();
        std::string tech = norm_tech(at(datasheet, "part", "technology"));
        bool sense = tech_has(tech, "shunt") || tech_has(tech, "current") ||
                     tech_has(tech, "sense");
        if (rnom && *rnom >= 0.1 && !sense) {
            if (!eseries::on_grid(*rnom))
                emit(out, ctx, "RES_E_SERIES", Severity::Suspicious, *rnom, 0,
                     fmt("resistance is not an IEC 60063 E-series preferred value [ohm]", *rnom));
            if (eseries::sig_figs(*rnom) > 4)
                emit(out, ctx, "GEN_OVERPRECISION", Severity::Suspicious, *rnom, 0,
                     fmt("resistance nominal carries more significant figures than a preferred "
                         "value [ohm]",
                         *rnom));
        }
    }
}

}  // namespace tas
