// SPDX-License-Identifier: Apache-2.0
// Varistor (MOV, RAS) physics checks. `datasheet` is the varistor datasheetInfo:
//   electrical.{varistorVoltage(V_1mA),maxContinuousAcVoltage,maxContinuousDcVoltage,
//     clampingVoltage,peakSurgeCurrent,capacitance,nonlinearityCoefficient}.
// Ordering law: MCOV < V_1mA < clampingVoltage. Sources: Littelfuse/EPCOS MOV
// app-notes; clamping ratio V_C/V_1mA typ 1.5-4; alpha typ 15-50.
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

void check_varistors(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                     std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("VAR_*");
        return;
    }
    auto Vnom = scalar_at(*elec, {"varistorVoltage"});
    auto Vc = scalar_at(*elec, {"clampingVoltage"});
    auto Ipp = scalar_at(*elec, {"peakSurgeCurrent"});

    // CHECK: positivity.
    if (Vnom && *Vnom <= 0)
        emit(out, ctx, "VAR_POSITIVITY", Severity::Impossible, *Vnom, 0, "varistorVoltage <= 0");
    if (Vc && *Vc <= 0)
        emit(out, ctx, "VAR_POSITIVITY", Severity::Impossible, *Vc, 0, "clampingVoltage <= 0");
    if (Ipp && *Ipp <= 0)
        emit(out, ctx, "VAR_POSITIVITY", Severity::Impossible, *Ipp, 0, "peakSurgeCurrent <= 0");

    // CHECK: continuous operating voltage must stay below the conduction knee V_1mA.
    if (Vnom && *Vnom > 0) {
        if (auto mcovDc = scalar_at(*elec, {"maxContinuousDcVoltage"}))
            if (*mcovDc >= *Vnom)
                emit(out, ctx, "VAR_MCOV_VS_VNOM", Severity::Impossible, *mcovDc, *Vnom,
                     fmt("maxContinuousDcVoltage >= varistorVoltage (V_1mA)", *mcovDc, *Vnom));
        if (auto mcovAc = scalar_at(*elec, {"maxContinuousAcVoltage"}))
            if (*mcovAc >= *Vnom)
                emit(out, ctx, "VAR_MCOV_VS_VNOM", Severity::Impossible, *mcovAc, *Vnom,
                     fmt("maxContinuousAcVoltage(RMS) >= varistorVoltage (V_1mA)", *mcovAc, *Vnom));
    }

    // CHECK: clamping voltage ordering + ratio.
    if (Vnom && Vc && *Vnom > 0 && *Vc > 0) {
        if (*Vc <= *Vnom)
            emit(out, ctx, "VAR_CLAMP_VS_VNOM", Severity::Impossible, *Vc, *Vnom,
                 fmt("clampingVoltage <= varistorVoltage (clamp must exceed the 1 mA knee)", *Vc,
                     *Vnom));
        else {
            double ratio = *Vc / *Vnom;
            if (ratio < thr::VAR_CLAMP_RATIO_SUS_LO || ratio > thr::VAR_CLAMP_RATIO_SUS_HI)
                emit(out, ctx, "VAR_CLAMP_RATIO", Severity::Suspicious, ratio, 0,
                     fmt("clamping ratio Vc/V_1mA outside typical 1.2..5", ratio));
        }
    }

    // CHECK: non-linearity exponent alpha.
    if (auto alpha = scalar_at(*elec, {"nonlinearityCoefficient"})) {
        if (*alpha <= thr::VAR_ALPHA_IMP_LO)
            emit(out, ctx, "VAR_NONLINEARITY", Severity::Impossible, *alpha, thr::VAR_ALPHA_IMP_LO,
                 fmt("nonlinearityCoefficient <= 1 (not a varistor)", *alpha));
        else if (*alpha < thr::VAR_ALPHA_SUS_LO || *alpha > thr::VAR_ALPHA_SUS_HI)
            emit(out, ctx, "VAR_NONLINEARITY", Severity::Suspicious, *alpha, 0,
                 fmt("nonlinearityCoefficient outside typical MOV band 10..100", *alpha));
    }

    // CHECK: peak surge current sanity.
    if (Ipp && *Ipp > 0) {
        if (*Ipp > thr::VAR_SURGE_IMP)
            emit(out, ctx, "VAR_SURGE_RANGE", Severity::Impossible, *Ipp, thr::VAR_SURGE_IMP,
                 fmt("peakSurgeCurrent exceeds any MOV [A]", *Ipp, thr::VAR_SURGE_IMP));
        else if (*Ipp > thr::VAR_SURGE_SUS)
            emit(out, ctx, "VAR_SURGE_RANGE", Severity::Suspicious, *Ipp, thr::VAR_SURGE_SUS,
                 fmt("peakSurgeCurrent very high [A]", *Ipp, thr::VAR_SURGE_SUS));
    }

    // CHECK: capacitance positivity.
    if (auto cap = scalar_at(*elec, {"capacitance"}))
        if (*cap <= 0)
            emit(out, ctx, "VAR_CAPACITANCE", Severity::Impossible, *cap, 0, "capacitance <= 0");
}

}  // namespace tas
