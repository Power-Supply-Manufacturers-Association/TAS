// SPDX-License-Identifier: MIT
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

    // CHECK (NEW): the gate rating is a gate-oxide rating. Magnitude, so the
    // P-channel sign convention does not matter.
    if (vgsmax && std::fabs(*vgsmax) > thr::MOS_VGS_MAX_ABS_IMP)
        emit(out, ctx, "MOS_VGS_MAX_RATING", Severity::Impossible, std::fabs(*vgsmax),
             thr::MOS_VGS_MAX_ABS_IMP,
             fmt("|gateSourceVoltageMax| exceeds any gate-oxide rating [V] (a watt or "
                 "another column, not a gate limit)",
                 std::fabs(*vgsmax), thr::MOS_VGS_MAX_ABS_IMP));

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
    auto ron = scalar_at(*elec, {"onResistance"});
    auto rthjc = scalar_at(datasheet, {"thermal", "thermalResistanceJunctionCase"});
    auto tjmax = scalar_at(datasheet, {"thermal", "junctionTemperatureMax"});

    // CHECK (NEW): a Coss/Crss pair generated by a closed-form formula instead of
    // measured (ABT #531, 67 rows, 2026-09-04 fabrication sweep): outputCapacitance
    // = onResistance^2 * 10^k EXACTLY, for an integer k, AND
    // reverseTransferCapacitance = outputCapacitance/10 EXACTLY.
    //
    // ABT (adversarial review, 2026-09-04): the original comment here overclaimed.
    // Of the 12 "cleared by the conjunction" rows the first version cited as proof,
    // 8 had NO reverseTransferCapacitance field AT ALL -- they were cleared by a
    // missing field the librarian exists to backfill, not by a genuinely different
    // ratio. Of the remaining 4 that DID carry Crss, only some sat far from 0.1;
    // one (onsemi NTBG050N120SC1) sits at 0.088, one datasheet-rounding away from
    // firing. And a false positive was CONSTRUCTED, not hypothetical: Ron=0.1 ohm,
    // Coss=100 pF, Crss=10 pF are ordinary 1-significant-figure datasheet typicals
    // for a small 100 V/100 mOhm SMD part, and they satisfy both conditions EXACTLY
    // -- three round numbers rounded to one sig fig can coincide with this formula
    // by pure chance, and nothing in the record's own Ron/Coss/Crss triplet can
    // tell that apart from a generator's output. The fix is the SAME one used
    // elsewhere in this fabrication sweep: require a search-query citation (see
    // has_search_query_citation, helpers.hpp) as a second, independent signal --
    // real datasheet typicals essentially never ALSO carry a search-engine-query
    // "citation". This drops recall from 67/67 to 66/67 (the one loss, Wolfspeed
    // C3M0016120D, was independently disproven by other means already) while
    // closing the constructed false positive (a real IRF/onsemi-style typical with
    // a real manufacturer citation no longer qualifies).
    //
    // Also: this check currently fires on ZERO live mosfet records (all 67 known
    // matches are already quarantined) -- it is a REGRESSION GUARD against this
    // exact generator reappearing, not an active detector proven against today's
    // corpus. Say so plainly: a clean sweep of live data is not evidence the rule
    // generalizes, only that this specific batch is gone.
    //
    // SUSPICIOUS: a generative/statistical signature, not a physics violation.
    if (ron && coss && *ron > 0 && *coss > 0) {
        double log_ratio = std::log10(*coss / (*ron * *ron));
        double nearest_k = std::round(log_ratio);
        if (std::fabs(log_ratio - nearest_k) < 1e-6 && crss && *crss > 0) {
            double crss_ratio = *crss / *coss;
            if (std::fabs(crss_ratio - 0.1) < 1e-6 &&
                has_search_query_citation(datasheet, ctx.component_obj))
                emit(out, ctx, "MOS_CAP_FORMULA", Severity::Suspicious, *coss, *ron,
                     fmt("outputCapacitance = onResistance^2*10^k and "
                         "reverseTransferCapacitance = outputCapacitance/10 EXACTLY, AND the "
                         "citation is a search query -- a generated pair, not two independent "
                         "measurements",
                         *coss, *ron));
        }
    }
    // The ceiling comes from thermal_power_ceiling(), not from Rth(j-c) alone:
    // it is the MAXIMUM over the references the record carries, so a row whose
    // j-c and j-a values sit in each other's fields is judged by the smaller
    // resistance rather than accused, and a row carrying only Rth(j-a) is
    // SKIPPED outright (Vishay SIHP065N60E-GE3 stores a real 250 W Ptot with
    // only a 62 K/W j-a path -- 124x the 2.0 W that reference implies -- and was
    // the adversarial review's near-miss false accusation).
    if (pdiss && *pdiss > 0) {
        auto ceiling = thermal_power_ceiling(datasheet);
        if (!ceiling) {
            skipped.push_back("MOS_POWER_THERMAL");
        } else {
            double pmax = *ceiling;
            double ratio = *pdiss / pmax;
            // Upper bound only: datasheets often rate Pdiss at an elevated case
            // temperature (giving ratio < 1), so the lower bound was a false-positive.
            if (ratio > thr::MOS_PTHERMAL_RATIO_SUS)
                emit(out, ctx, "MOS_POWER_THERMAL", Severity::Suspicious, *pdiss, pmax,
                     fmt("powerDissipation exceeds thermal limit (Tjmax-25)/Rth(j-c) [W]", *pdiss, pmax));
        }
    }

    // CHECK (NEW): the rated continuous drain current against the record's own
    // thermal path. ABT #500: a TO-247 record carrying a sibling package's thermal
    // table is internally consistent between Ptot and Rth(j-c) -- both come from the
    // same wrong row -- so MOS_POWER_THERMAL above cannot see it. What gives it away
    // is that the die cannot conduct its own rated current through that thermal path:
    // IPW80R280P7 stored 16 A through 0.28 ohm (71.7 W cold) behind a 3.5 K/W path
    // good for 35.7 W, where the TO-247 datasheet says 1.2 K/W / 101 W.
    if (idc && ron && rthjc && tjmax && *ron > 0 && *rthjc > 0 && *tjmax > 25.0) {
        double pcond = (*idc) * (*idc) * (*ron);  // conduction loss at the 25 C Rds(on)
        double pmax = (*tjmax - 25.0) / *rthjc;   // case held at 25 C
        // Isolated packages (FullPAK / TO-220F / TO-3PF) are rated by vendors at
        // the NON-isolated sibling's silicon current with an explicit duty-cycle
        // footnote ("Limited by Tj max. Maximum duty cycle D=0.5" -- Infineon
        // IPA60R120P7's own front page: 26 A behind 4.49 K/W, a 2.9x cold
        // overcommit), so on those a 2-3x excess is the vendor's rating
        // convention, not broken data. The impossible bar moves to 4x for them
        // (ABT #500 calibration, datasheet-verified 2026-08-02); the band in
        // between stays visible as Suspicious. Bare packages keep the 2x bar --
        // the #500 exhibit, a TO-247 wearing FullPak thermals, is exactly what
        // this check exists to catch, and it stays caught.
        std::string pkg = norm_tech(at(datasheet, "part", "case"));
        bool isolated = tech_has(pkg, "fullpak") || tech_has(pkg, "fullpack") ||
                        tech_has(pkg, "to220f") || tech_has(pkg, "to3pf");
        double bar = isolated ? thr::MOS_IDC_THERMAL_RATIO_ISO_IMP
                              : thr::MOS_IDC_THERMAL_RATIO_IMP;
        if (pcond > pmax * bar)
            emit(out, ctx, "MOS_IDC_VS_THERMAL", Severity::Impossible, pcond, pmax,
                 fmt("conduction loss at the rated continuous drain current exceeds "
                     "(Tjmax-25)/Rth(j-c) [W]", pcond, pmax));
        else if (isolated && pcond > pmax * thr::MOS_IDC_THERMAL_RATIO_IMP)
            emit(out, ctx, "MOS_IDC_VS_THERMAL", Severity::Suspicious, pcond, pmax,
                 fmt("conduction loss at rated Id exceeds (Tjmax-25)/Rth(j-c) on an "
                     "isolated package (vendor silicon-rated Id) [W]", pcond, pmax));
    }

    // CHECK (NEW): powerDissipation that is really an on-resistance. The May-2026
    // Vishay import wrote the grid's r_DS(on)-at-4.5-V column (P7016) into the
    // watt field (P7008) on 961 records -- SiRS4300DP, a 680 A part, stored
    // 0.00068 "W". Ohms in a watt field land orders of magnitude below any
    // package rating, so test against the package the drain rating implies rather
    // than against the ohm value itself.
    if (pdiss && idc && std::fabs(*idc) >= thr::MOS_PD_IDC_A && *pdiss < thr::MOS_PD_IDC_W)
        emit(out, ctx, "MOS_PD_VS_IDC", Severity::Impossible, *pdiss, thr::MOS_PD_IDC_W,
             fmt("powerDissipation below any package able to carry the rated continuous "
                 "drain current [W]", *pdiss, thr::MOS_PD_IDC_W));

    // CHECK (NEW): a totalGateCharge that is not gate charge. ABT #512 -- 2 nC on a
    // Vishay 80 V / 5.5 mohm / 72 A die (the grid's real Qg is 55 nC), and onsemi's
    // parametric export publishes Q_gs under its "Qg Typ @ VGS = 10 V" heading.
    // Ron and Qg both scale with channel width in opposite directions, so their
    // product is a die-area-independent technology constant; see the thresholds.
    if (qg && ron && *qg > 0 && *ron > 0) {
        double fom = *ron * (*qg);  // ohm*C
        double floor = tech_has(tech, "gan") ? thr::MOS_QG_RON_FOM_GAN_IMP
                                             : thr::MOS_QG_RON_FOM_SI_IMP;
        if (fom < floor)
            emit(out, ctx, "MOS_QG_VS_RON", Severity::Impossible, fom, floor,
                 fmt("onResistance*totalGateCharge below the switching figure of merit "
                     "any real die reaches [ohm*C] (the charge field holds another "
                     "quantity)", fom, floor));
    } else {
        skipped.push_back("MOS_QG_VS_RON");
    }

    // CHECK (NEW, advisory): specific-Ron floor proxy Ron*Vds^2 by technology.
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

    // CHECK (NEW, adversarial physics review 2026-09-04): the SAME Ron*Qg figure
    // of merit as MOS_QG_VS_RON above, but floored PER VOLTAGE CLASS. That check
    // uses one global silicon floor, which is necessarily set by the best 30 V
    // logic-level die and is therefore ~100x too permissive for a 600 V part --
    // FDP22N50 (55 mohm*nC on a 500 V part), STL240N6F7 (a 60 V family recorded
    // at 650 V) and IXTX3N250L (2500 V recorded at 8.3 mohm against a datasheet
    // that says under 10 OHMS) all cleared it.
    //
    // The FOM is die-area independent but NOT voltage independent: specific Ron
    // and gate charge per unit width both climb steeply with blocking voltage, so
    // no amount of die area lets a 600 V superjunction reach a 30 V die's FOM.
    // See the thresholds header for the calibrated per-class population.
    //
    // SUSPICIOUS, never Impossible. Three of the eight live hits are Navitas
    // GaNFast parts whose ELECTRICALS are right and whose technology field wrongly
    // reads "Si" -- the record IS wrong, but the wrong field is the label, so this
    // must not block a shard build.
    if (qg && ron && vds && *qg > 0 && *ron > 0 && *vds >= thr::MOS_FOM_VCLASS_MIN_VDS) {
        double fom = *ron * (*qg);  // ohm*C
        double floor;
        if (tech_has(tech, "gan")) {
            floor = thr::MOS_FOM_GAN_SUS;
        } else if (tech_has(tech, "sic")) {
            floor = thr::MOS_FOM_SIC_SUS;
        } else if (*vds < 400) {
            floor = thr::MOS_FOM_SI_200_SUS;
        } else if (*vds <= 700) {
            floor = thr::MOS_FOM_SI_400_SUS;
        } else if (*vds <= 1200) {
            floor = thr::MOS_FOM_SI_700_SUS;
        } else {
            floor = thr::MOS_FOM_SI_1200_SUS;
        }
        if (fom < floor)
            emit(out, ctx, "MOS_FOM_VCLASS", Severity::Suspicious, fom, floor,
                 fmt("onResistance*totalGateCharge below the switching figure of merit "
                     "this technology reaches at this blocking voltage [ohm*C] (one of "
                     "Ron, Qg, Vds or the technology label is wrong)", fom, floor));
    } else if (!(qg && ron && vds)) {
        skipped.push_back("MOS_FOM_VCLASS");
    }
}

}  // namespace tas
