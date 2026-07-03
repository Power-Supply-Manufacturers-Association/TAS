// SPDX-License-Identifier: MIT
// Thermistor (RAS) physics checks. `datasheet` is the thermistor datasheetInfo:
//   part.technology in {ntc, ptc};
//   electrical.{resistanceAt25C, resistanceTolerance, bConstant,
//     bConstantTemperatures[], dissipationConstant, thermalTimeConstant,
//     maximumSteadyStateCurrent, switchTemperature};
//   thermal.operatingTemperature{minimum,maximum}.
// Bounds from NTC/PTC datasheets + app notes (Vishay NTCLE/NTCLG, TDK/EPCOS B57,
// Murata NCP/NCU, Amphenol/Thermometrics, Littelfuse, Ametherm inrush limiters):
// R25 ~0.05 Ohm (inrush) to a few MOhm (sensing); NTC beta ~2000..5500 K;
// dissipation constant ~0.5 mW/K (0402) to ~0.1 W/K (probe); time constant
// ~0.5 s (bead) to ~150 s (potted probe). A PTC has a switch temperature and no
// beta; an NTC has beta and no switch temperature.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <string>

namespace tas {

void check_thermistors(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                       std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("THERM_*");
        return;
    }

    // technology discriminates NTC vs PTC (ntc has beta; ptc has switch temperature).
    std::string tech;
    if (const json* part = at(datasheet, "part"))
        if (part->contains("technology") && (*part)["technology"].is_string())
            tech = (*part)["technology"].get<std::string>();

    // CHECK: R25 positivity + physical range.
    auto r25 = scalar_at(*elec, {"resistanceAt25C"});
    if (r25) {
        if (*r25 <= 0)
            emit(out, ctx, "THERM_POSITIVITY", Severity::Impossible, *r25, 0,
                 "resistanceAt25C <= 0");
        else if (*r25 < thr::THERM_R25_IMP_LO || *r25 > thr::THERM_R25_IMP_HI)
            emit(out, ctx, "THERM_R25_RANGE", Severity::Impossible, *r25,
                 *r25 < thr::THERM_R25_IMP_LO ? thr::THERM_R25_IMP_LO : thr::THERM_R25_IMP_HI,
                 fmt("resistanceAt25C outside any real thermistor [Ohm]", *r25));
        else if (*r25 < thr::THERM_R25_SUS_LO || *r25 > thr::THERM_R25_SUS_HI)
            emit(out, ctx, "THERM_R25_RANGE", Severity::Suspicious, *r25, 0,
                 fmt("resistanceAt25C outside typical 0.05 Ohm..10 MOhm band", *r25));
    }

    // CHECK: NTC beta (B constant). PTC parts legitimately have none.
    auto beta = scalar_at(*elec, {"bConstant"});
    if (beta) {
        if (*beta <= 0)
            emit(out, ctx, "THERM_POSITIVITY", Severity::Impossible, *beta, 0, "bConstant <= 0");
        else if (*beta < thr::THERM_B_IMP_LO || *beta > thr::THERM_B_IMP_HI)
            emit(out, ctx, "THERM_BETA_RANGE", Severity::Impossible, *beta,
                 *beta < thr::THERM_B_IMP_LO ? thr::THERM_B_IMP_LO : thr::THERM_B_IMP_HI,
                 fmt("bConstant outside any real NTC [K]", *beta));
        else if (*beta < thr::THERM_B_SUS_LO || *beta > thr::THERM_B_SUS_HI)
            emit(out, ctx, "THERM_BETA_RANGE", Severity::Suspicious, *beta, 0,
                 fmt("bConstant outside typical 2000..5500 K", *beta));
        // A beta on a part declared PTC is a technology/data mismatch (PTC has no beta).
        if (tech == "ptc")
            emit(out, ctx, "THERM_PTC_HAS_BETA", Severity::Suspicious, *beta, 0,
                 "bConstant present on a part declared technology=ptc (beta is NTC-only)");
    }

    // CHECK: resistance tolerance (fraction, 0.01 = 1%).
    if (auto tol = scalar_at(*elec, {"resistanceTolerance"})) {
        if (*tol < 0)
            emit(out, ctx, "THERM_POSITIVITY", Severity::Impossible, *tol, 0,
                 "resistanceTolerance < 0");
        else if (*tol >= thr::THERM_TOL_IMP_HI)
            emit(out, ctx, "THERM_TOLERANCE", Severity::Impossible, *tol, thr::THERM_TOL_IMP_HI,
                 fmt("resistanceTolerance >= 100% (is it a percent stored as a fraction?)", *tol));
        else if (*tol > thr::THERM_TOL_SUS_HI)
            emit(out, ctx, "THERM_TOLERANCE", Severity::Suspicious, *tol, thr::THERM_TOL_SUS_HI,
                 fmt("resistanceTolerance looser than any real grade [fraction]", *tol,
                     thr::THERM_TOL_SUS_HI));
    }

    // CHECK: dissipation constant (W/K).
    auto diss = scalar_at(*elec, {"dissipationConstant"});
    if (diss) {
        if (*diss <= 0)
            emit(out, ctx, "THERM_POSITIVITY", Severity::Impossible, *diss, 0,
                 "dissipationConstant <= 0");
        else if (*diss < thr::THERM_DISS_IMP_LO || *diss > thr::THERM_DISS_IMP_HI)
            emit(out, ctx, "THERM_DISSIPATION", Severity::Impossible, *diss,
                 *diss < thr::THERM_DISS_IMP_LO ? thr::THERM_DISS_IMP_LO : thr::THERM_DISS_IMP_HI,
                 fmt("dissipationConstant outside any real thermistor [W/K]", *diss));
        else if (*diss < thr::THERM_DISS_SUS_LO || *diss > thr::THERM_DISS_SUS_HI)
            emit(out, ctx, "THERM_DISSIPATION", Severity::Suspicious, *diss, 0,
                 fmt("dissipationConstant outside typical 0.2 mW/K..0.25 W/K", *diss));
    }

    // CHECK: thermal time constant (s).
    auto tau = scalar_at(*elec, {"thermalTimeConstant"});
    if (tau) {
        if (*tau <= 0)
            emit(out, ctx, "THERM_POSITIVITY", Severity::Impossible, *tau, 0,
                 "thermalTimeConstant <= 0");
        else if (*tau > thr::THERM_TAU_IMP_HI)
            emit(out, ctx, "THERM_TIME_CONSTANT", Severity::Impossible, *tau, thr::THERM_TAU_IMP_HI,
                 fmt("thermalTimeConstant > 1 h, not a thermistor [s]", *tau, thr::THERM_TAU_IMP_HI));
        else if (*tau > thr::THERM_TAU_SUS_HI)
            emit(out, ctx, "THERM_TIME_CONSTANT", Severity::Suspicious, *tau, thr::THERM_TAU_SUS_HI,
                 fmt("thermalTimeConstant high for a thermistor [s]", *tau, thr::THERM_TAU_SUS_HI));
    }

    // CHECK: implied heat capacity C_th = tau * dissipationConstant [J/K] must be a
    // plausible small ceramic body (both fields present and positive).
    if (tau && diss && *tau > 0 && *diss > 0) {
        double cth = *tau * *diss;
        if (cth < thr::THERM_CTH_SUS_LO || cth > thr::THERM_CTH_SUS_HI)
            emit(out, ctx, "THERM_HEAT_CAPACITY", Severity::Suspicious, cth, 0,
                 fmt("implied heat capacity tau*dissipationConstant outside 1e-5..100 J/K", cth));
    }

    // CHECK: max steady-state current (inrush-limiter NTC) [A].
    if (auto iss = scalar_at(*elec, {"maximumSteadyStateCurrent"})) {
        if (*iss <= 0)
            emit(out, ctx, "THERM_POSITIVITY", Severity::Impossible, *iss, 0,
                 "maximumSteadyStateCurrent <= 0");
        else if (*iss > thr::THERM_ISS_IMP_HI)
            emit(out, ctx, "THERM_MAX_CURRENT", Severity::Impossible, *iss, thr::THERM_ISS_IMP_HI,
                 fmt("maximumSteadyStateCurrent implausibly high [A]", *iss, thr::THERM_ISS_IMP_HI));
        else if (*iss > thr::THERM_ISS_SUS_HI)
            emit(out, ctx, "THERM_MAX_CURRENT", Severity::Suspicious, *iss, thr::THERM_ISS_SUS_HI,
                 fmt("maximumSteadyStateCurrent high for an NTC limiter [A]", *iss,
                     thr::THERM_ISS_SUS_HI));
    }

    // CHECK: PTC switch (reference) temperature [deg C].
    auto tsw = scalar_at(*elec, {"switchTemperature"});
    if (tsw) {
        if (*tsw < thr::THERM_ABS_ZERO_C)
            emit(out, ctx, "THERM_ABS_ZERO", Severity::Impossible, *tsw, thr::THERM_ABS_ZERO_C,
                 fmt("switchTemperature below absolute zero [deg C]", *tsw, thr::THERM_ABS_ZERO_C));
        else if (*tsw > thr::THERM_PTC_TSW_IMP_HI)
            emit(out, ctx, "THERM_PTC_TSWITCH", Severity::Impossible, *tsw, thr::THERM_PTC_TSW_IMP_HI,
                 fmt("switchTemperature beyond any real PTC [deg C]", *tsw, thr::THERM_PTC_TSW_IMP_HI));
        else if (*tsw > thr::THERM_PTC_TSW_SUS_HI)
            emit(out, ctx, "THERM_PTC_TSWITCH", Severity::Suspicious, *tsw, thr::THERM_PTC_TSW_SUS_HI,
                 fmt("switchTemperature high for a PTC [deg C]", *tsw, thr::THERM_PTC_TSW_SUS_HI));
        // A switch temperature on a part declared NTC is a mismatch (switch temp is PTC-only).
        if (tech == "ntc")
            emit(out, ctx, "THERM_NTC_HAS_TSWITCH", Severity::Suspicious, *tsw, 0,
                 "switchTemperature present on a part declared technology=ntc (PTC-only field)");
    }

    // CHECK: operating temperature sanity (absolute zero + ordering + max ceiling).
    if (const json* thermal = at(datasheet, "thermal")) {
        if (const json* ot = at(*thermal, "operatingTemperature")) {
            auto tmin = scalar_at(*ot, {"minimum"});
            auto tmax = scalar_at(*ot, {"maximum"});
            if (tmin && *tmin < thr::THERM_ABS_ZERO_C)
                emit(out, ctx, "THERM_ABS_ZERO", Severity::Impossible, *tmin, thr::THERM_ABS_ZERO_C,
                     fmt("operatingTemperature.minimum below absolute zero [deg C]", *tmin,
                         thr::THERM_ABS_ZERO_C));
            if (tmax && *tmax > thr::THERM_TEMP_MAX_IMP)
                emit(out, ctx, "THERM_TEMP_RANGE", Severity::Impossible, *tmax, thr::THERM_TEMP_MAX_IMP,
                     fmt("operatingTemperature.maximum beyond any real thermistor [deg C]", *tmax,
                         thr::THERM_TEMP_MAX_IMP));
            else if (tmax && *tmax > thr::THERM_TEMP_MAX_SUS)
                emit(out, ctx, "THERM_TEMP_RANGE", Severity::Suspicious, *tmax, thr::THERM_TEMP_MAX_SUS,
                     fmt("operatingTemperature.maximum high for a thermistor [deg C]", *tmax,
                         thr::THERM_TEMP_MAX_SUS));
            if (tmin && tmax && *tmin >= *tmax)
                emit(out, ctx, "THERM_TEMP_ORDER", Severity::Impossible, *tmin, *tmax,
                     fmt("operatingTemperature.minimum >= maximum [deg C]", *tmin, *tmax));
        }
    }
}

}  // namespace tas
