// SPDX-License-Identifier: Apache-2.0
// BJT (SAS) physics checks. `datasheet` is the bjt datasheetInfo:
//   electrical.{collectorEmitterVoltage(VCEO),collectorBaseVoltage(VCBO),
//     collectorCurrent(IC),dcCurrentGain(hFE),saturationVoltage(VCEsat),
//     transitionFrequency(fT)}.
// PNP parts carry negative VCEO/IC/VCEsat -> reason about MAGNITUDES.
// Sources: Toshiba/ROHM BJT app-notes; hFE typ 10-500 (Darlington up to ~30000),
// VCE(sat) ~50 mV-2 V, fT ~1 MHz-hundreds of GHz.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cmath>
#include <sstream>
#include <string>

namespace tas {

void check_bjts(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("BJT_*");
        return;
    }
    auto Vceo = scalar_at(*elec, {"collectorEmitterVoltage"});
    auto Ic = scalar_at(*elec, {"collectorCurrent"});
    auto Vcesat = scalar_at(*elec, {"saturationVoltage"});

    // CHECK: positivity (in magnitude — PNP values are negative).
    if (Vceo && std::fabs(*Vceo) <= 0)
        emit(out, ctx, "BJT_POSITIVITY", Severity::Impossible, *Vceo, 0,
             "collectorEmitterVoltage == 0");
    if (Ic && std::fabs(*Ic) <= 0)
        emit(out, ctx, "BJT_POSITIVITY", Severity::Impossible, *Ic, 0, "collectorCurrent == 0");

    // CHECK: VCE(sat) range. Zero saturation voltage is impossible (was silently
    // accepted by the old `v > 0` guard).
    if (Vcesat) {
        double v = std::fabs(*Vcesat);
        if (v <= 0)
            emit(out, ctx, "BJT_VCESAT_RANGE", Severity::Impossible, v, 0,
                 "saturationVoltage == 0");
        else if (v < thr::BJT_VCESAT_IMP_LO || v > thr::BJT_VCESAT_IMP_HI)
            emit(out, ctx, "BJT_VCESAT_RANGE", Severity::Impossible, v, 0,
                 fmt("|Vce(sat)| outside (0.01,5) V", v));
        else if (v > thr::BJT_VCESAT_SUS_HI)
            emit(out, ctx, "BJT_VCESAT_RANGE", Severity::Suspicious, v, thr::BJT_VCESAT_SUS_HI,
                 fmt("|Vce(sat)| high for a BJT [V]", v, thr::BJT_VCESAT_SUS_HI));
    }

    // CHECK: VCE(sat) must be a small fraction of the rated VCEO.
    if (Vceo && Vcesat && std::fabs(*Vcesat) >= std::fabs(*Vceo))
        emit(out, ctx, "BJT_VCESAT_VS_VCEO", Severity::Impossible, *Vcesat, *Vceo,
             fmt("|Vce(sat)| >= |collectorEmitterVoltage|", std::fabs(*Vcesat), std::fabs(*Vceo)));

    // CHECK: DC current gain hFE band.
    if (auto hfe = scalar_at(*elec, {"dcCurrentGain"})) {
        if (*hfe <= 0)
            emit(out, ctx, "BJT_HFE_RANGE", Severity::Impossible, *hfe, 0, "dcCurrentGain <= 0");
        else if (*hfe < thr::BJT_HFE_SUS_LO || *hfe > thr::BJT_HFE_SUS_HI)
            emit(out, ctx, "BJT_HFE_RANGE", Severity::Suspicious, *hfe, 0,
                 fmt("hFE outside typical 5..50000 band", *hfe));
    }

    // CHECK: VCBO should not be below VCEO (collector-base breakdown >= collector-emitter).
    if (Vceo)
        if (auto vcbo = scalar_at(*elec, {"collectorBaseVoltage"}))
            if (std::fabs(*vcbo) < std::fabs(*Vceo))
                emit(out, ctx, "BJT_VCBO_VS_VCEO", Severity::Suspicious, *vcbo, *Vceo,
                     fmt("|VCBO| < |VCEO| (collector-base usually >= collector-emitter)",
                         std::fabs(*vcbo), std::fabs(*Vceo)));

    // CHECK: transition frequency sanity.
    if (auto ft = scalar_at(*elec, {"transitionFrequency"})) {
        if (*ft > 0 && (*ft < thr::BJT_FT_SUS_LO || *ft > thr::BJT_FT_SUS_HI))
            emit(out, ctx, "BJT_FT_RANGE", Severity::Suspicious, *ft, 0,
                 fmt("transitionFrequency outside 100 kHz..1 THz", *ft));
    }
}

}  // namespace tas
