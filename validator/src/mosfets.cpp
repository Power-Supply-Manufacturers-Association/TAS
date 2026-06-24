// SPDX-License-Identifier: Apache-2.0
// MOSFET physics checks. `datasheet` is the mosfet datasheetInfo object:
//   electrical.{drainSourceVoltage,gateSourceVoltageMax,onResistance,
//     gateThresholdVoltage,inputCapacitance(Ciss),outputCapacitance(Coss),
//     reverseTransferCapacitance(Crss),totalGateCharge,gateSourceCharge,
//     gateDrainCharge,bodyDiodeForwardVoltage,powerDissipation},
//   thermal.{thermalResistanceJunctionCase,junctionTemperatureMax},
//   part.technology.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cmath>
#include <sstream>
#include <string>

namespace tas {

void check_mosfets(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                   std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("MOS_*");
        return;
    }
    std::string tech = norm_tech(at(datasheet, "part", "technology"));

    // CHECK (NEW): capacitance hierarchy Ciss > Coss > Crss > 0.
    auto ciss = scalar_at(*elec, {"inputCapacitance"});
    auto coss = scalar_at(*elec, {"outputCapacitance"});
    auto crss = scalar_at(*elec, {"reverseTransferCapacitance"});
    if (ciss && coss && crss) {
        if (!(*ciss > *coss && *coss > *crss && *crss > 0)) {
            std::ostringstream m;
            m << "capacitance order violated: require Ciss>Coss>Crss>0; Ciss=" << *ciss
              << " Coss=" << *coss << " Crss=" << *crss;
            emit(out, ctx, "MOS_CAP_HIERARCHY", Severity::Impossible, *crss, 0, m.str());
        }
    } else {
        skipped.push_back("MOS_CAP_HIERARCHY");
    }

    // CHECK (NEW): gate-charge hierarchy.
    auto qg = scalar_at(*elec, {"totalGateCharge"});
    auto qgs = scalar_at(*elec, {"gateSourceCharge"});
    auto qgd = scalar_at(*elec, {"gateDrainCharge"});
    if (qg && qgs && qgd && *qg > 0) {
        double sum = *qgs + *qgd;
        if (*qgs > *qg || *qgd > *qg || sum > *qg * thr::MOS_QG_SUM_SLACK)
            emit(out, ctx, "MOS_CHARGE_HIERARCHY", Severity::Impossible, sum, *qg,
                 fmt("Qgs+Qgd exceeds total Qg", sum, *qg));
    } else {
        skipped.push_back("MOS_CHARGE_HIERARCHY");
    }

    // CHECK (NEW): gate-threshold window + ordering + technology band.
    // P-channel parts carry negative Vth / Vgs; reason about MAGNITUDES so the
    // sign convention does not masquerade as a violation.
    const json* vthf = at(*elec, "gateThresholdVoltage");
    double vth_mag = 0;  // representative |Vth|, reused by the Vgs headroom check
    if (vthf) {
        auto nom = scalar_at(*vthf, {"nominal"});
        auto mn = scalar_at(*vthf, {"minimum"});
        auto mx = scalar_at(*vthf, {"maximum"});
        // Ordering: nominal must lie within the [min,max] bracket. Convention-
        // agnostic — P-channel datasheets label Vth min/max by magnitude in some
        // catalog records and by signed value in others, so neither pure signed nor
        // pure magnitude ordering is correct. We flag only a nominal outside the
        // bracket, which is a true error under either convention.
        if (nom && mn && mx) {
            double lo = std::min(*mn, *mx), hi = std::max(*mn, *mx);
            if (*nom < lo - 1e-9 || *nom > hi + 1e-9)
                emit(out, ctx, "MOS_VTH_WINDOW", Severity::Impossible, *nom, 0,
                     "Vth nominal outside [minimum, maximum] bracket");
        }
        double lo = thr::MOS_VTH_SI_LO, hi = thr::MOS_VTH_SI_HI;
        if (tech_has(tech, "sic")) { lo = thr::MOS_VTH_SIC_LO; hi = thr::MOS_VTH_SIC_HI; }
        else if (tech_has(tech, "gan")) { lo = thr::MOS_VTH_GAN_LO; hi = thr::MOS_VTH_GAN_HI; }
        if (nom) vth_mag = std::fabs(*nom);
        else if (mn && mx) vth_mag = 0.5 * (std::fabs(*mn) + std::fabs(*mx));
        else if (mn) vth_mag = std::fabs(*mn);
        else if (mx) vth_mag = std::fabs(*mx);
        if (vth_mag > 0 && (vth_mag < lo || vth_mag > hi))
            emit(out, ctx, "MOS_VTH_WINDOW", Severity::Suspicious, vth_mag, 0,
                 fmt("|Vth| outside expected band for technology", vth_mag));
    } else {
        skipped.push_back("MOS_VTH_WINDOW");
    }

    // CHECK: gate-drive coherence (SUSPICIOUS, never IMPOSSIBLE). The old check
    // compared abs-max Vgs to Vth(max) and mass-invalidated real ROHM/Infineon SiC
    // parts whose gateThresholdVoltage field is polluted with the recommended gate-
    // DRIVE window (~15 V), not the true ~3 V threshold. The correct invariant uses
    // the Rds(on) test drive (onResistanceVgs): it must exceed |Vth(max)| and stay
    // within |gateSourceVoltageMax|.
    auto vgsmax = scalar_at(*elec, {"gateSourceVoltageMax"});
    auto vdrive = scalar_at(*elec, {"onResistanceVgs"});
    if (vdrive && vthf) {
        auto vthmax = scalar_at(*vthf, {"maximum"});
        if (!vthmax) vthmax = scalar_at(*vthf, {"nominal"});
        if (vthmax && std::fabs(*vdrive) <= std::fabs(*vthmax))
            emit(out, ctx, "MOS_VGS_VS_VTH", Severity::Suspicious, std::fabs(*vdrive),
                 std::fabs(*vthmax),
                 fmt("onResistanceVgs <= |Vth(max)|: Rds(on) drive could not enhance the device",
                     std::fabs(*vdrive), std::fabs(*vthmax)));
    }
    if (vdrive && vgsmax && std::fabs(*vdrive) > std::fabs(*vgsmax) + 1e-9)
        emit(out, ctx, "MOS_VGS_VS_VTH", Severity::Suspicious, std::fabs(*vdrive),
             std::fabs(*vgsmax),
             fmt("onResistanceVgs exceeds |gateSourceVoltageMax| (drive above abs-max rating)",
                 std::fabs(*vdrive), std::fabs(*vgsmax)));

    // CHECK: pulsed drain current must be >= continuous (magnitude — P-ch negative).
    auto idc = scalar_at(*elec, {"continuousDrainCurrent"});
    auto ipulse = scalar_at(*elec, {"pulsedDrainCurrent"});
    if (idc && ipulse && std::fabs(*ipulse) + 1e-9 < std::fabs(*idc))
        emit(out, ctx, "MOS_IPULSE_VS_IDC", Severity::Impossible, *ipulse, *idc,
             fmt("|pulsedDrainCurrent| < |continuousDrainCurrent|", std::fabs(*ipulse),
                 std::fabs(*idc)));

    // CHECK (NEW): body-diode / reverse-conduction forward drop.
    if (auto vf = scalar_at(*elec, {"bodyDiodeForwardVoltage"})) {
        if (*vf > 0 && (*vf < thr::MOS_BODY_VF_LO || *vf > thr::MOS_BODY_VF_HI))
            emit(out, ctx, "MOS_BODY_DIODE_VF", Severity::Impossible, *vf, 0,
                 fmt("body-diode forward voltage outside (0.2,5) V", *vf));
    }

    // CHECK (NEW): power vs thermal consistency Pdiss ~ (Tjmax-25)/Rth(j-c).
    auto pdiss = scalar_at(*elec, {"powerDissipation"});
    auto rthjc = scalar_at(datasheet, {"thermal", "thermalResistanceJunctionCase"});
    auto tjmax = scalar_at(datasheet, {"thermal", "junctionTemperatureMax"});
    if (pdiss && rthjc && tjmax && *rthjc > 0 && *pdiss > 0) {
        double pmax = (*tjmax - 25.0) / *rthjc;  // case held at 25 C
        if (pmax > 0) {
            double ratio = *pdiss / pmax;
            // Upper bound only: datasheets often rate Pdiss at an elevated case
            // temperature (giving ratio < 1), so the lower bound was a false-positive.
            if (ratio > thr::MOS_PTHERMAL_RATIO_SUS)
                emit(out, ctx, "MOS_POWER_THERMAL", Severity::Suspicious, *pdiss, pmax,
                     fmt("powerDissipation exceeds thermal limit (Tjmax-25)/Rth(j-c) [W]", *pdiss, pmax));
        }
    }

    // CHECK (NEW, advisory): specific-Ron floor proxy Ron*Vds^2 by technology.
    auto ron = scalar_at(*elec, {"onResistance"});
    auto vds = scalar_at(*elec, {"drainSourceVoltage"});
    if (ron && vds && *ron > 0 && *vds > 0) {
        double metric = *ron * (*vds) * (*vds);  // ohm*V^2
        double floor = thr::MOS_RON_VDS2_SI_SUS;
        if (tech_has(tech, "sic")) floor = thr::MOS_RON_VDS2_SIC_SUS;
        else if (tech_has(tech, "gan")) floor = thr::MOS_RON_VDS2_GAN_SUS;
        if (metric < floor)
            emit(out, ctx, "MOS_RON_FLOOR", Severity::Suspicious, metric, floor,
                 fmt("Ron*Vds^2 below silicon-limit proxy for technology", metric, floor));
    }
}

}  // namespace tas
