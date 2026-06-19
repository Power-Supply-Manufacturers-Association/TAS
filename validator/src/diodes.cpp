// SPDX-License-Identifier: Apache-2.0
// Diode physics checks. `datasheet` is the diode datasheetInfo object:
//   electrical.{reverseVoltage,forwardCurrent,surgeCurrent,forwardVoltage,
//     reverseLeakageCurrent,junctionCapacitance,junctionCapacitanceVr,
//     powerDissipation,reverseRecoveryCharge}, part.technology.
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

void check_diodes(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                  std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("DIO_*");
        return;
    }
    std::string tech = norm_tech(at(datasheet, "part", "technology"));
    bool majority = tech_has(tech, "schottky") || tech_has(tech, "gan");
    // TVS / Zener parts: the "forwardVoltage" field stores the clamp/breakdown
    // voltage (tens of volts), not a PN forward drop. Detect by the TVS-only
    // fields or technology so the high value is not treated as an impossible Vf.
    bool tvs = elec->contains("standoffVoltage") || elec->contains("clampingVoltage") ||
               elec->contains("breakdownVoltage") || tech_has(tech, "tvs") ||
               tech_has(tech, "transient") || tech_has(tech, "zener") || tech_has(tech, "esd");

    auto Vr = scalar_at(*elec, {"reverseVoltage"});
    auto If = scalar_at(*elec, {"forwardCurrent"});
    auto Vf = scalar_at(*elec, {"forwardVoltage"});

    // CHECK (NEW): positivity.
    if (Vr && *Vr <= 0)
        emit(out, ctx, "DIO_POSITIVITY", Severity::Impossible, *Vr, 0, "reverseVoltage <= 0");
    if (If && *If <= 0)
        emit(out, ctx, "DIO_POSITIVITY", Severity::Impossible, *If, 0, "forwardCurrent <= 0");

    // CHECK (NEW): forward-voltage range by technology (skipped for TVS/Zener,
    // whose forwardVoltage field carries clamp/breakdown voltage).
    if (Vf && tvs) {
        skipped.push_back("DIO_VF_RANGE");
    } else if (Vf) {
        if (*Vf < thr::DIO_VF_HARD_LO || *Vf > thr::DIO_VF_HARD_HI) {
            emit(out, ctx, "DIO_VF_RANGE", Severity::Impossible, *Vf, 0,
                 fmt("forwardVoltage outside (0.05,5) V", *Vf));
        } else if (tech_has(tech, "schottky")) {
            if (*Vf > thr::DIO_VF_SCHOTTKY_HI)
                emit(out, ctx, "DIO_VF_RANGE", Severity::Suspicious, *Vf, thr::DIO_VF_SCHOTTKY_HI,
                     fmt("forwardVoltage high for Schottky", *Vf, thr::DIO_VF_SCHOTTKY_HI));
        } else if (tech_has(tech, "sic")) {
            if (*Vf < thr::DIO_VF_SIC_LO || *Vf > thr::DIO_VF_SIC_HI)
                emit(out, ctx, "DIO_VF_RANGE", Severity::Suspicious, *Vf, 0,
                     fmt("forwardVoltage outside SiC band", *Vf));
        } else {  // assume Si PN
            if (*Vf < thr::DIO_VF_SI_LO || *Vf > thr::DIO_VF_SI_HI)
                emit(out, ctx, "DIO_VF_RANGE", Severity::Suspicious, *Vf, 0,
                     fmt("forwardVoltage outside Si PN band", *Vf));
        }
    } else {
        skipped.push_back("DIO_VF_RANGE");
    }

    // CHECK (NEW): surge current must exceed continuous forward current.
    if (auto surge = scalar_at(*elec, {"surgeCurrent"})) {
        if (If && *surge < *If)
            emit(out, ctx, "DIO_SURGE_VS_IF", Severity::Impossible, *surge, *If,
                 fmt("surgeCurrent < forwardCurrent", *surge, *If));
    }

    // CHECK (NEW): Vf*If conduction loss vs power-dissipation rating (not
    // meaningful for TVS, whose Vf field is a clamp voltage).
    if (Vf && If && !tvs) {
        if (auto pd = scalar_at(*elec, {"powerDissipation"})) {
            double cond = (*Vf) * (*If);
            if (*pd > 0 && cond > *pd * thr::DIO_VFIF_RATIO_SUS)
                emit(out, ctx, "DIO_VF_POWER", Severity::Suspicious, cond, *pd,
                     fmt("Vf*If far exceeds powerDissipation rating [W]", cond, *pd));
        }
    }

    // CHECK (NEW): majority-carrier devices should have ~0 reverse-recovery charge.
    if (majority) {
        if (auto qrr = scalar_at(*elec, {"reverseRecoveryCharge"})) {
            if (*qrr > thr::DIO_QRR_MAJORITY_SUS)
                emit(out, ctx, "DIO_QRR_SCHOTTKY", Severity::Suspicious, *qrr,
                     thr::DIO_QRR_MAJORITY_SUS,
                     fmt("non-zero Qrr for majority-carrier (Schottky/GaN) device [C]", *qrr,
                         thr::DIO_QRR_MAJORITY_SUS));
        }
    }

    // CHECK (NEW): junction capacitance positivity, and its test Vr <= rated Vr.
    if (auto cj = scalar_at(*elec, {"junctionCapacitance"})) {
        if (*cj <= 0)
            emit(out, ctx, "DIO_CJ_VR", Severity::Impossible, *cj, 0, "junctionCapacitance <= 0");
    }
    if (Vr) {
        if (auto cjvr = scalar_at(*elec, {"junctionCapacitanceVr"})) {
            if (*cjvr > *Vr)
                emit(out, ctx, "DIO_CJ_VR", Severity::Suspicious, *cjvr, *Vr,
                     fmt("junctionCapacitanceVr exceeds rated reverseVoltage", *cjvr, *Vr));
        }
    }
}

}  // namespace tas
