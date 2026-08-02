// SPDX-License-Identifier: MIT
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

void check_diodes(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                  std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("DIO_*");
        return;
    }
    // Device type lives in part.subType (schottky/sicSchottky/tvs/zener/ultrafast/
    // esd/rectifier...); part.technology only ever carries Si/SiC in the catalog.
    // Combine both so the type-specific branches (Schottky Vf band, Qrr-majority)
    // actually fire instead of being dead code.
    std::string tech = norm_tech(at(datasheet, "part", "technology")) +
                       norm_tech(at(datasheet, "part", "subType"));
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
    auto Ilk = scalar_at(*elec, {"reverseLeakageCurrent"});
    if (Ilk && *Ilk < 0)
        emit(out, ctx, "DIO_POSITIVITY", Severity::Impossible, *Ilk, 0,
             "reverseLeakageCurrent < 0");

    // CHECK (NEW): reverse leakage against the forward-current rating. Both are
    // amps, so the ratio is unit-free and a uA/mA figure left in the amps field
    // shows up as a leakage comparable to the rating — Infineon's IDV08E65D2
    // carried its datasheet's "40 uA" as 40, i.e. 5x the part's own 8 A forward
    // rating and 26 kW dissipated while blocking (ABT #524). Suspicious only: the
    // ratio says the two numbers disagree, not which of them is the wrong one —
    // every live record it fires on today is a bad forwardCurrent, not a bad
    // leakage. See the calibration note on DIO_LEAK_IF_SUS.
    if (Ilk && If && *Ilk > 0 && *If > 0 && *Ilk / *If > thr::DIO_LEAK_IF_SUS)
        emit(out, ctx, "DIO_LEAKAGE_VS_IF", Severity::Suspicious, *Ilk, *If,
             fmt("reverseLeakageCurrent above any datasheet fraction of "
                 "forwardCurrent [A]", *Ilk, *If));

    // CHECK (NEW): forward-voltage range by technology (skipped for TVS/Zener,
    // whose forwardVoltage field carries clamp/breakdown voltage).
    if (Vf && tvs) {
        skipped.push_back("DIO_VF_RANGE");
    } else if (Vf) {
        if (*Vf < thr::DIO_VF_HARD_LO || *Vf > thr::DIO_VF_HARD_HI) {
            emit(out, ctx, "DIO_VF_RANGE", Severity::Impossible, *Vf, 0,
                 fmt("forwardVoltage outside (0.05,5) V", *Vf));
        } else if (tech_has(tech, "sic")) {  // SiC (incl. sicSchottky) — wider Vf band
            if (*Vf < thr::DIO_VF_SIC_LO || *Vf > thr::DIO_VF_SIC_HI)
                emit(out, ctx, "DIO_VF_RANGE", Severity::Suspicious, *Vf, 0,
                     fmt("forwardVoltage outside SiC band", *Vf));
        } else if (tech_has(tech, "schottky")) {  // silicon Schottky — low Vf
            if (*Vf < thr::DIO_VF_SCHOTTKY_LO || *Vf > thr::DIO_VF_SCHOTTKY_HI)
                emit(out, ctx, "DIO_VF_RANGE", Severity::Suspicious, *Vf, 0,
                     fmt("forwardVoltage outside Schottky band", *Vf));
        } else {  // assume Si PN
            if (*Vf < thr::DIO_VF_SI_LO || *Vf > thr::DIO_VF_SI_HI)
                emit(out, ctx, "DIO_VF_RANGE", Severity::Suspicious, *Vf, 0,
                     fmt("forwardVoltage outside Si PN band", *Vf));
        }
    } else {
        skipped.push_back("DIO_VF_RANGE");
    }

    // CHECK (NEW, cross-parameter): TVS voltage ordering. The working (standoff)
    // voltage sits below the 1 mA breakdown, which sits below the surge clamp.
    if (tvs) {
        auto vso = scalar_at(*elec, {"standoffVoltage"});
        auto vbr = scalar_at(*elec, {"breakdownVoltage"});
        auto vcl = scalar_at(*elec, {"clampingVoltage"});
        // ESD steering-diode arrays spec the FORWARD clamping voltage (the
        // steering path's VF at IPP), which legitimately sits far below the
        // working voltage: Eaton STS142700UL55's own datasheet is VRWM 70 V
        // with "** Forward Clamping voltage" 7 V typ / 9 V max at 24 A, and
        // reverse breakdown 85 V. On an esd subType the inversion is therefore
        // Suspicious, not impossible; avalanche TVS keep the hard check.
        bool esd_array = tech_has(tech, "esd");
        if (vso && vcl && *vso > 0 && *vcl > 0 && *vso >= *vcl)
            emit(out, ctx, "DIO_TVS_ORDERING",
                 esd_array ? Severity::Suspicious : Severity::Impossible, *vso, *vcl,
                 fmt(esd_array ? "standoffVoltage >= clampingVoltage (esd array: "
                                 "clamp may be the steering diode's forward VF)"
                               : "standoffVoltage >= clampingVoltage", *vso, *vcl));
        // Snapback ESD parts guard-band the 1 mA breakdown LIMIT below the
        // working voltage: Toshiba DF2B26M4SL's own table is VRWM 24 V with
        // VBR min 21.0 V (leakage separately guaranteed 0.1 uA at 24 V), a
        // 0.875x ratio that is a spec convention, not a broken record. Only a
        // breakdown MEANINGFULLY below standoff (a wrong-column value) is
        // impossible; the guard band stays visible as Suspicious.
        if (vso && vbr && *vso > 0 && *vbr > 0 && *vbr < *vso * thr::DIO_TVS_VBR_VSO_GUARD)
            emit(out, ctx, "DIO_TVS_ORDERING", Severity::Impossible, *vbr, *vso,
                 fmt("breakdownVoltage < standoffVoltage", *vbr, *vso));
        else if (vso && vbr && *vso > 0 && *vbr > 0 && *vbr < *vso)
            emit(out, ctx, "DIO_TVS_ORDERING", Severity::Suspicious, *vbr, *vso,
                 fmt("breakdownVoltage inside the snapback guard band below "
                     "standoffVoltage", *vbr, *vso));
        if (vbr && vcl && *vbr > 0 && *vcl > 0 && *vcl < *vbr)
            emit(out, ctx, "DIO_TVS_ORDERING",
                 esd_array ? Severity::Suspicious : Severity::Impossible, *vcl, *vbr,
                 fmt(esd_array ? "clampingVoltage < breakdownVoltage (esd array: "
                                 "a forward-VF clamp sits below reverse breakdown)"
                               : "clampingVoltage < breakdownVoltage", *vcl, *vbr));
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
