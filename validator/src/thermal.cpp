// SPDX-License-Identifier: MIT
// Thermal-management physics checks (TMAS thermalManagement.json). `datasheet` is
// the datasheetInfo object of one TMAS family; the family itself is NOT inside
// it: it is the pinned `family` const on the thermalManagement object, so it is
// read from ctx.component_obj (the {family, manufacturerInfo} object run() was
// handed).
//
// Families: heatsink, thermalInterfaceMaterial, fan, heatsinkAccessory (mounting
// hardware, no physics to check).
//
// Only IMPOSSIBLE checks are implemented, and only hard bounds: a thermal
// resistance, a conductivity, a thickness, an airflow, a pressure or a speed
// cannot be zero or negative. The schema already rejects a non-positive SCALAR
// for most of these, but not a point of a curve or an end of a
// {nominal, minimum, maximum} band, and Blade Runner also judges records that
// never went through the schema. The catalogue had no thermal_management.ndjson
// when this file was written (2026-10-18); the SUSPICIOUS screens of the TMAS
// proposal (Rth non-increasing in air velocity, forced below natural, TIM
// impedance non-increasing in pressure and not below its bulk term, fan P-Q
// endpoints, P = V*I, noise and speed bands) wait for live rows to set their
// tolerances.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <optional>
#include <string>
#include <vector>

namespace tas {

namespace {

// A string field: nullopt when absent; MalformedField when present but not a string.
std::optional<std::string> thm_string_at(const json& obj, const char* key,
                                         const std::string& label) {
    const json* s = at(obj, key);
    if (s == nullptr || s->is_null()) return std::nullopt;
    if (!s->is_string()) throw MalformedField(label + " is not a string");
    return s->get<std::string>();
}

// The smallest value a field PRINTS: a bare number, or the smallest of the
// nominal / minimum / maximum ends a band object carries. nullopt when the field
// is absent or carries no number. Deliberately not the dimension resolver: a
// positivity bound must see every printed end, not one chosen scalar.
std::optional<double> smallest_printed(const json* field, const std::string& label) {
    if (field == nullptr || field->is_null()) return std::nullopt;
    if (field->is_number()) return field->get<double>();
    if (!field->is_object()) throw MalformedField(label + " is neither a number nor an object");
    std::optional<double> lo;
    for (const char* end : {"nominal", "minimum", "maximum"}) {
        const auto v = scalar(at(*field, end), label + "." + end);
        if (v && (!lo || *v < *lo)) lo = v;
    }
    return lo;
}

// The smallest y value of a PEAS curve; nullopt when the curve or its yData is absent.
std::optional<double> smallest_y(const json* curve, const std::string& label) {
    if (curve == nullptr || curve->is_null()) return std::nullopt;
    if (!curve->is_object()) throw MalformedField(label + " is not an object");
    const json* y = at(*curve, "yData");
    if (y == nullptr || y->is_null()) return std::nullopt;
    if (!y->is_array()) throw MalformedField(label + ".yData is not an array");
    std::optional<double> lo;
    for (std::size_t i = 0; i < y->size(); ++i) {
        const auto v = scalar(&(*y)[i], label + ".yData[" + std::to_string(i) + "]");
        if (v && (!lo || *v < *lo)) lo = v;
    }
    return lo;
}

// Emit `code` when the smallest of the given values is <= 0; record a skip when
// none of them is present.
void positive_or_fire(const std::vector<std::pair<std::optional<double>, std::string>>& values,
                      const char* code, const std::string& what, const Ctx& ctx,
                      std::vector<Finding>& out, std::vector<std::string>& skipped) {
    bool any = false;
    for (const auto& [v, where] : values) {
        if (!v) continue;
        any = true;
        if (*v <= 0.0) {
            emit(out, ctx, code, Severity::Impossible, *v, 0.0,
                 fmt(where + ": " + what + " is not positive", *v));
            return;  // one finding per code per record
        }
    }
    if (!any) skipped.push_back(code);
}

// ---------------------------------------------------------------- heatsink
void check_heatsink(const json& ds, const Ctx& ctx, std::vector<Finding>& out,
                    std::vector<std::string>& skipped) {
    const json* th = at(ds, "thermal");
    const json* nat = th != nullptr ? at(*th, "naturalConvection") : nullptr;
    // CHECK THM_HS_RTH_NONPOSITIVE: a heatsink conducts heat from a hotter
    // surface to cooler air through a finite resistance; zero would be a perfect
    // conductor to ambient and a negative one would pump heat uphill.
    positive_or_fire(
        {{nat != nullptr ? scalar(at(*nat, "thermalResistance"),
                                  "thermal.naturalConvection.thermalResistance")
                         : std::nullopt,
          "thermal.naturalConvection.thermalResistance"},
         {th != nullptr ? smallest_y(at(*th, "thermalResistanceVsAirVelocity"),
                                     "thermal.thermalResistanceVsAirVelocity")
                        : std::nullopt,
          "thermal.thermalResistanceVsAirVelocity"}},
        "THM_HS_RTH_NONPOSITIVE", "thermal resistance [K/W]", ctx, out, skipped);
}

// ------------------------------------------------- thermal interface material
void check_tim(const json& ds, const Ctx& ctx, std::vector<Finding>& out,
               std::vector<std::string>& skipped) {
    const json* th = at(ds, "thermal");
    const json* mech = at(ds, "mechanical");
    // CHECK THM_TIM_K_NONPOSITIVE: every material conducts heat; a conductivity
    // of zero or below is not a material.
    positive_or_fire({{th != nullptr ? scalar(at(*th, "thermalConductivity"),
                                              "thermal.thermalConductivity")
                                     : std::nullopt,
                       "thermal.thermalConductivity"}},
                     "THM_TIM_K_NONPOSITIVE", "thermal conductivity [W/(m K)]", ctx, out,
                     skipped);
    // CHECK THM_TIM_IMPEDANCE_NONPOSITIVE: a layer of material between two
    // surfaces always adds a positive thermal impedance.
    positive_or_fire({{th != nullptr ? smallest_y(at(*th, "thermalImpedanceVsPressure"),
                                                  "thermal.thermalImpedanceVsPressure")
                                     : std::nullopt,
                       "thermal.thermalImpedanceVsPressure"}},
                     "THM_TIM_IMPEDANCE_NONPOSITIVE", "thermal impedance [K m^2/W]", ctx, out,
                     skipped);
    // CHECK THM_TIM_THICKNESS_NONPOSITIVE: a pad, sheet or bond line has a
    // thickness; every printed end of it is above zero.
    positive_or_fire({{mech != nullptr ? smallest_printed(at(*mech, "thickness"),
                                                          "mechanical.thickness")
                                       : std::nullopt,
                       "mechanical.thickness"}},
                     "THM_TIM_THICKNESS_NONPOSITIVE", "thickness [m]", ctx, out, skipped);
}

// --------------------------------------------------------------------- fan
void check_fan(const json& ds, const Ctx& ctx, std::vector<Finding>& out,
               std::vector<std::string>& skipped) {
    const json* perf = at(ds, "performance");
    // CHECK THM_FAN_AIRFLOW_NONPOSITIVE: free-delivery airflow is the flow at
    // zero back-pressure; a fan that moves no air (or moves it backwards at
    // free delivery) is not a fan.
    positive_or_fire({{perf != nullptr ? scalar(at(*perf, "maximumAirflow"),
                                                "performance.maximumAirflow")
                                       : std::nullopt,
                       "performance.maximumAirflow"}},
                     "THM_FAN_AIRFLOW_NONPOSITIVE", "maximum airflow [m^3/s]", ctx, out,
                     skipped);
    // CHECK THM_FAN_PRESSURE_NONPOSITIVE: shut-off static pressure is what the
    // running rotor holds at zero flow; it is above zero for any running fan.
    positive_or_fire({{perf != nullptr ? scalar(at(*perf, "maximumStaticPressure"),
                                                "performance.maximumStaticPressure")
                                       : std::nullopt,
                       "performance.maximumStaticPressure"}},
                     "THM_FAN_PRESSURE_NONPOSITIVE", "maximum static pressure [Pa]", ctx, out,
                     skipped);
    // CHECK THM_FAN_SPEED_NONPOSITIVE: a rated rotor speed (every printed end
    // of the band) is above zero rpm.
    positive_or_fire({{perf != nullptr ? smallest_printed(at(*perf, "speed"),
                                                          "performance.speed")
                                       : std::nullopt,
                       "performance.speed"}},
                     "THM_FAN_SPEED_NONPOSITIVE", "speed [rpm]", ctx, out, skipped);
}

}  // namespace

void check_thermal_management(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                              std::vector<std::string>& skipped) {
    // The family is the pinned const on the thermalManagement object itself.
    const json* obj = ctx.component_obj;
    const auto family = (obj != nullptr && obj->is_object())
                            ? thm_string_at(*obj, "family", "thermalManagement.family")
                            : std::nullopt;
    if (!family) {
        // The schema requires `family` on every non-empty thermalManagement
        // object; without it the record cannot say which block to read. Missing
        // data skips, it never reads as valid.
        skipped.push_back("THM_*");
        return;
    }
    if (*family == "heatsink")
        check_heatsink(datasheet, ctx, out, skipped);
    else if (*family == "thermalInterfaceMaterial")
        check_tim(datasheet, ctx, out, skipped);
    else if (*family == "fan")
        check_fan(datasheet, ctx, out, skipped);
    else if (*family == "heatsinkAccessory")
        skipped.push_back("THM_ACC_*");  // clips, pins, kits, tools: no physics bound
    else
        throw MalformedField("family '" + *family + "' is not a TMAS thermalManagement family");
}

}  // namespace tas
