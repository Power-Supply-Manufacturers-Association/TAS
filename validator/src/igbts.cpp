// SPDX-License-Identifier: Apache-2.0
// IGBT physics checks. `datasheet` is the igbt datasheetInfo object:
//   electrical.{collectorEmitterVoltage,continuousCollectorCurrent,
//     collectorEmitterSaturation}.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <sstream>
#include <string>

namespace tas {

void check_igbts(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                 std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("IGBT_*");
        return;
    }
    auto Vces = scalar_at(*elec, {"collectorEmitterVoltage"});
    auto Ic = scalar_at(*elec, {"continuousCollectorCurrent"});
    auto Vcesat = scalar_at(*elec, {"collectorEmitterSaturation"});

    // CHECK (NEW): positivity.
    if (Vces && *Vces <= 0)
        emit(out, ctx, "IGBT_POSITIVITY", Severity::Impossible, *Vces, 0,
             "collectorEmitterVoltage <= 0");
    if (Ic && *Ic <= 0)
        emit(out, ctx, "IGBT_POSITIVITY", Severity::Impossible, *Ic, 0,
             "continuousCollectorCurrent <= 0");

    // CHECK (NEW): magnitude sanity. Catches PN-digit-leak / unit-slip garbage
    // (a 16,000,000 A part passed before). Largest real module Ic ~3.6 kA, Vces ~4.5 kV.
    if (Ic && *Ic > 0) {
        if (*Ic > thr::IGBT_IC_IMP)
            emit(out, ctx, "IGBT_IC_RANGE", Severity::Impossible, *Ic, thr::IGBT_IC_IMP,
                 fmt("continuousCollectorCurrent implausibly high [A]", *Ic, thr::IGBT_IC_IMP));
        else if (*Ic > thr::IGBT_IC_SUS)
            emit(out, ctx, "IGBT_IC_RANGE", Severity::Suspicious, *Ic, thr::IGBT_IC_SUS,
                 fmt("continuousCollectorCurrent very high [A]", *Ic, thr::IGBT_IC_SUS));
    }
    if (Vces && *Vces > 0) {
        if (*Vces > thr::IGBT_VCES_IMP)
            emit(out, ctx, "IGBT_VCES_RANGE", Severity::Impossible, *Vces, thr::IGBT_VCES_IMP,
                 fmt("collectorEmitterVoltage implausibly high [V]", *Vces, thr::IGBT_VCES_IMP));
        else if (*Vces > thr::IGBT_VCES_SUS)
            emit(out, ctx, "IGBT_VCES_RANGE", Severity::Suspicious, *Vces, thr::IGBT_VCES_SUS,
                 fmt("collectorEmitterVoltage very high [V]", *Vces, thr::IGBT_VCES_SUS));
    }

    // CHECK (NEW): Vce(sat) range. The SUS upper ceiling is Vces-dependent —
    // 1600-1700 V parts legitimately reach ~7 V Vce(sat).
    if (Vcesat) {
        if (*Vcesat < thr::IGBT_VCESAT_HARD_LO || *Vcesat > thr::IGBT_VCESAT_HARD_HI) {
            emit(out, ctx, "IGBT_VCESAT_RANGE", Severity::Impossible, *Vcesat, 0,
                 fmt("Vce(sat) outside (0.3,8) V", *Vcesat));
        } else {
            double sus_hi = (Vces && *Vces > thr::IGBT_VCESAT_HV_VCES)
                                ? thr::IGBT_VCESAT_SUS_HI_HV
                                : thr::IGBT_VCESAT_SUS_HI;
            if (*Vcesat < thr::IGBT_VCESAT_SUS_LO || *Vcesat > sus_hi)
                emit(out, ctx, "IGBT_VCESAT_RANGE", Severity::Suspicious, *Vcesat, sus_hi,
                     fmt("Vce(sat) outside typical range for rated Vces", *Vcesat, sus_hi));
        }
    } else {
        skipped.push_back("IGBT_VCESAT_RANGE");
    }

    // CHECK (NEW): saturation voltage must be a small fraction of the rated CE voltage.
    if (Vces && Vcesat && *Vcesat >= *Vces)
        emit(out, ctx, "IGBT_VCESAT_VS_VCES", Severity::Impossible, *Vcesat, *Vces,
             fmt("Vce(sat) >= rated collectorEmitterVoltage", *Vcesat, *Vces));

    // CHECK (NEW, cross-parameter): Vce(sat)/Vces ratio band. Catches a part whose
    // Vce(sat) and Vces are each individually plausible but jointly incoherent.
    if (Vces && Vcesat && *Vces > 0 && *Vcesat > 0) {
        double ratio = *Vcesat / *Vces;
        if (ratio < thr::IGBT_VCESAT_RATIO_LO || ratio > thr::IGBT_VCESAT_RATIO_HI)
            emit(out, ctx, "IGBT_VCESAT_RATIO", Severity::Suspicious, ratio, 0,
                 fmt("Vce(sat)/Vces ratio outside typical 0.0003..0.02", ratio));
    }
}

}  // namespace tas
