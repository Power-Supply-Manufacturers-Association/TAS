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
namespace {

std::string fmt(const std::string& s, double a, double b = 0) {
    std::ostringstream os;
    os << s << " (value=" << a;
    if (b != 0) os << ", threshold=" << b;
    os << ")";
    return os.str();
}

}  // namespace

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

    // CHECK (NEW): Vce(sat) range.
    if (Vcesat) {
        if (*Vcesat < thr::IGBT_VCESAT_HARD_LO || *Vcesat > thr::IGBT_VCESAT_HARD_HI)
            emit(out, ctx, "IGBT_VCESAT_RANGE", Severity::Impossible, *Vcesat, 0,
                 fmt("Vce(sat) outside (0.3,8) V", *Vcesat));
        else if (*Vcesat < thr::IGBT_VCESAT_SUS_LO || *Vcesat > thr::IGBT_VCESAT_SUS_HI)
            emit(out, ctx, "IGBT_VCESAT_RANGE", Severity::Suspicious, *Vcesat, 0,
                 fmt("Vce(sat) outside typical (0.8,4.5) V", *Vcesat));
    } else {
        skipped.push_back("IGBT_VCESAT_RANGE");
    }

    // CHECK (NEW): saturation voltage must be a small fraction of the rated CE voltage.
    if (Vces && Vcesat && *Vcesat >= *Vces)
        emit(out, ctx, "IGBT_VCESAT_VS_VCES", Severity::Impossible, *Vcesat, *Vces,
             fmt("Vce(sat) >= rated collectorEmitterVoltage", *Vcesat, *Vces));
}

}  // namespace tas
