// SPDX-License-Identifier: MIT
// Diode physics checks. `datasheet` is the diode datasheetInfo object:
//   electrical.{reverseVoltage,forwardCurrent,surgeCurrent,forwardVoltage,
//     reverseLeakageCurrent,junctionCapacitance,junctionCapacitanceVr,
//     powerDissipation,reverseRecoveryCharge}, part.technology.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <algorithm>
#include <cmath>
#include <optional>
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
    // TVS / Zener / ESD parts ("protection diodes"). Their clamp, breakdown and
    // standoff voltages have their own fields; SAS defines forwardVoltage as V_F
    // for every subtype. The flag gates the checks whose OTHER inputs mean
    // something different on a protection part (powerDissipation is peak pulse
    // power, surgeCurrent is I_pp), and selects the protection V_F band below.
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

    // CHECK: forward voltage of a protection diode. This used to be skipped on
    // the belief that the field carries the clamp voltage; that belief is what
    // let 140 TI TVS/ESD rows store a 5.5-75 V clamp as V_F with no finding.
    // Forward conduction of a TVS/zener/ESD junction is ordinary PN conduction:
    // V_F = V_bi + I*R_s. Datasheets quote it at the surge current (SMAJ/SMBJ/
    // P6KE 3.5 V at 25-50 A; 1.5KE 3.5 V, or 5.0 V for the stacked-die
    // >200 V parts, at 100 A) and that is the top of the range: the same
    // (0.05, 5] V hard window every other diode is held to. The per-technology
    // SUSPICIOUS bands are NOT applied -- 3.5 V is a correct surge V_F (24 Vishay
    // SMAJ rows) and would sit outside the Si PN band. Measured 2026-09-23 on
    // the 356 protection rows carrying forwardVoltage: 352 in 0.7-3.5 V, and 4
    // Littelfuse SMAJ24A/24C/30A/30C at 7.5 V and 20 V (same JEDEC-series
    // parts Vishay rates 3.5 V; the C parts are bidirectional and have no
    // forward conduction at all).
    if (Vf && tvs) {
        skipped.push_back("DIO_VF_RANGE");
        if (*Vf < thr::DIO_VF_HARD_LO || *Vf > thr::DIO_VF_HARD_HI)
            emit(out, ctx, "DIO_VF_PROTECTION", Severity::Impossible, *Vf,
                 *Vf > thr::DIO_VF_HARD_HI ? thr::DIO_VF_HARD_HI : thr::DIO_VF_HARD_LO,
                 fmt("protection-diode forwardVoltage outside (0.05,5] V: a forward-"
                     "biased junction drops at most ~5 V even at its surge current; "
                     "tens of volts is a clamp or breakdown figure in the wrong field",
                     *Vf));
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

    // CHECK (NEW, adversarial physics review 2026-09-04): the 4H-SiC Schottky
    // BARRIER floor. The Ni/Ti-on-4H-SiC barrier height is 1.0-1.35 eV, so the
    // device cannot conduct its rated current at a drop below the barrier itself;
    // every traced SiC Schottky in this corpus lands at 1.2-2.0 V, and 198 rows
    // sit below 0.8 V. DIO_VF_RANGE's SiC band starts at 0.5 V and sees none of
    // them.
    //
    // THE TRAP, and the reason this keys on the TECHNOLOGY and not on "is a
    // Schottky": a SILICON Schottky legitimately drops 0.3-0.5 V, because its
    // barrier is ~0.7 eV. Widening this rule to all Schottkys would condemn every
    // correct silicon Schottky in the catalogue. Do not remove the sic gate.
    //
    // SUSPICIOUS: a Vf quoted at microamps genuinely can approach the barrier, so
    // this is a strong smell rather than a conservation law.
    if (Vf && !tvs && tech_has(tech, "sic") && tech_has(tech, "schottky")) {
        if (*Vf > 0 && *Vf < thr::DIO_VF_SIC_BARRIER_SUS)
            emit(out, ctx, "DIO_VF_SIC_BARRIER", Severity::Suspicious, *Vf,
                 thr::DIO_VF_SIC_BARRIER_SUS,
                 fmt("SiC Schottky forwardVoltage below the 4H-SiC Ni/Ti barrier "
                     "height (1.0-1.35 eV) the junction cannot conduct below [V]",
                     *Vf, thr::DIO_VF_SIC_BARRIER_SUS));
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
        // CHECK (NEW, 2026-10-30): the clamp's CEILING. At its rated pulse an
        // avalanche TVS clamps at ~1.3-1.6x breakdown (catalogue p99 1.55) and the
        // low-voltage ESD parts with large dynamic resistance reach 4.75x (Bourns
        // CDSOD323-T03C, 19 V on 4 V; esd array SP2502LBTG 6.06x on a minimum
        // breakdown); nothing real is beyond that. A local-model
        // extraction stored SM8S11A's I_PP column (363 A) as its clamp: 27x an
        // 13.5 V breakdown, which the ordering checks above all accept.
        if (vbr && vcl && *vbr > 0 && *vcl > *vbr * thr::DIO_TVS_CLAMP_VBR_SUS)
            emit(out, ctx, "DIO_TVS_CLAMP_RATIO", Severity::Suspicious, *vcl / *vbr,
                 thr::DIO_TVS_CLAMP_VBR_SUS,
                 fmt("clampingVoltage / breakdownVoltage above any real clamp (catalogue "
                     "max 6.06): the clamp is another column's value",
                     *vcl / *vbr, thr::DIO_TVS_CLAMP_VBR_SUS));
    }

    // CHECK (NEW, 2026-10-30): DIO_ZENER_TEST_POWER. V_Z is specified at I_ZT, so
    // the part dissipates V_Z x I_ZT during its own test; vendors pick I_ZT at a
    // fraction of the rating (PLZ: 5 mA x 39 V = 0.2 W on a 0.5 W part). Across
    // the 4,156 catalogue zeners carrying all three fields the ratio tops out at
    // 1.5 (small SOD-523 parts tested at 5 mA). A test current left in mA (5 for
    // 0.005) or read from the I_ZK / I_ZM column lands at 2x-800x. SUSPICIOUS:
    // the product says the three values disagree, not which one is wrong.
    if (tech_has(tech, "zener")) {
        auto vz = scalar_at(*elec, {"breakdownVoltage"});
        auto izt = scalar_at(*elec, {"zenerTestCurrent"});
        auto pd = scalar_at(*elec, {"powerDissipation"});
        if (vz && izt && pd && *vz > 0 && *izt > 0 && *pd > 0) {
            double ratio = (*vz) * (*izt) / (*pd);
            if (ratio > thr::DIO_ZENER_TEST_POWER_SUS)
                emit(out, ctx, "DIO_ZENER_TEST_POWER", Severity::Suspicious, ratio,
                     thr::DIO_ZENER_TEST_POWER_SUS,
                     fmt("breakdownVoltage x zenerTestCurrent / powerDissipation: the "
                         "part's own V_Z test would dissipate more than twice its rating "
                         "(test current in mA, or the I_ZK/I_ZM column)",
                         ratio, thr::DIO_ZENER_TEST_POWER_SUS));
        } else {
            skipped.push_back("DIO_ZENER_TEST_POWER");
        }
    }

    // ---- TVS pulse ratings and their stated waveforms (2026-10-17) ----------
    // SAS diode.json states each pulse rating AT an impulse (PEAS surgeWaveform):
    // peakPulsePower@peakPulsePowerWaveform, peakPulseCurrent@
    // peakPulseCurrentWaveform, clampingVoltage@clampingCurrent+clampingWaveform.
    // Absent waveform = NOT STATED. Neither check below ever assumes one (no
    // "TVS means 10/1000"), so a row that does not state every input it needs is
    // recorded as skipped, not judged.
    auto wave_of = [&](const char* key) -> std::optional<std::string> {
        const json* w = at(*elec, key);
        if (w == nullptr || w->is_null()) return std::nullopt;
        if (!w->is_string())
            throw MalformedField(std::string("electrical.") + key +
                                 " is not a surgeWaveform string");
        return w->get<std::string>();
    };
    auto Ppp = scalar_at(*elec, {"peakPulsePower"});
    auto Ipp = scalar_at(*elec, {"peakPulseCurrent"});
    auto Vc = scalar_at(*elec, {"clampingVoltage"});
    auto Ic = scalar_at(*elec, {"clampingCurrent"});
    auto wPpp = wave_of("peakPulsePowerWaveform");
    auto wIpp = wave_of("peakPulseCurrentWaveform");
    auto wVc = wave_of("clampingWaveform");

    // CHECK (NEW): DIO_TVS_PULSE_PRODUCT. When P_PP, I_PP and V_C are all stated
    // at ONE waveform and V_C is stated at I_PP (clampingCurrent equal to
    // peakPulseCurrent, or not given), the three describe a single pulse and the
    // datasheet's own definition P_PP = V_C x I_PP must close. Outside
    // [DIO_TVS_PULSE_PRODUCT_LO, _HI] one of the three belongs to a different
    // pulse (mixed waveform) or is wrong. SUSPICIOUS: rounding conventions are
    // vendor-specific (see thresholds.hpp) and the ratio cannot say which value
    // is off.
    if (Ppp && Ipp && Vc && wPpp && wIpp && wVc && *wPpp == *wIpp && *wIpp == *wVc &&
        *Ppp > 0 && *Ipp > 0 && *Vc > 0 &&
        (!Ic || std::fabs(*Ic - *Ipp) <= thr::DIO_TVS_SAME_CURRENT_REL * *Ipp)) {
        double ratio = (*Vc) * (*Ipp) / (*Ppp);
        if (ratio < thr::DIO_TVS_PULSE_PRODUCT_LO || ratio > thr::DIO_TVS_PULSE_PRODUCT_HI)
            emit(out, ctx, "DIO_TVS_PULSE_PRODUCT", Severity::Suspicious, ratio,
                 ratio < thr::DIO_TVS_PULSE_PRODUCT_LO ? thr::DIO_TVS_PULSE_PRODUCT_LO
                                                        : thr::DIO_TVS_PULSE_PRODUCT_HI,
                 fmt("clampingVoltage x peakPulseCurrent / peakPulsePower, all stated at "
                     "the " + *wIpp + " us pulse, outside [0.8, 1.25]: P_PP = V_C x I_PP "
                     "does not close, so one rating belongs to another pulse or is wrong",
                     ratio));
    } else if (Ppp || Ipp) {
        skipped.push_back("DIO_TVS_PULSE_PRODUCT");
    }

    // CHECK (NEW): DIO_TVS_WAVEFORM_ORDER. The same junction survives MORE peak
    // current in a short 8/20 us pulse than in a long 10/1000 us one (the
    // dissipated energy, ~V_C x I x pulse width, is ~50x smaller), so an 8/20
    // I_PP below the part's own 10/1000 I_PP is physically impossible.
    //
    // The 10/1000 I_PP is not stored when the I_PP field holds the 8/20 rating;
    // it is reconstructed from P_PP@10/1000 / V_C. That division is only a
    // guaranteed LOWER bound on the 10/1000 current when V_C is the 10/1000
    // clamp at a stated current I_c (clampingWaveform '10/1000' AND
    // clampingCurrent present): V_C(I) x I rises with I, so if I_c x V_C >= P_PP
    // then I_c is at or above the 10/1000 rating and P_PP/V_C <= I_10/1000, and
    // otherwise I_c itself is below it. Hence L = min(I_c, P_PP/V_C) never
    // exceeds the true 10/1000 rating. A V_C at an unstated current (a 1 A V_C
    // makes P_PP/V_C far too large) or another waveform is never used: skipped.
    if (Ppp && Ipp && Vc && Ic && wPpp && wIpp && wVc && *wIpp == "8/20" &&
        *wPpp == "10/1000" && *wVc == "10/1000" && *Ppp > 0 && *Ipp > 0 && *Vc > 0 &&
        *Ic > 0) {
        double lower_1000 = std::min(*Ic, *Ppp / *Vc);
        if (*Ipp < lower_1000 * thr::DIO_TVS_WAVEFORM_ORDER_ROUNDING)
            emit(out, ctx, "DIO_TVS_WAVEFORM_ORDER", Severity::Impossible, *Ipp, lower_1000,
                 fmt("peakPulseCurrent at 8/20 us below the part's own 10/1000 us peak "
                     "pulse current min(clampingCurrent, peakPulsePower/clampingVoltage) "
                     "[A]: the shorter pulse always allows more current",
                     *Ipp, lower_1000));
    } else if (wIpp || wPpp) {
        skipped.push_back("DIO_TVS_WAVEFORM_ORDER");
    }

    // CHECK (NEW): surge current must exceed continuous forward current.
    if (auto surge = scalar_at(*elec, {"surgeCurrent"})) {
        if (If && *surge < *If)
            emit(out, ctx, "DIO_SURGE_VS_IF", Severity::Impossible, *surge, *If,
                 fmt("surgeCurrent < forwardCurrent", *surge, *If));

        // CHECK (NEW, adversarial physics review 2026-09-04): the RATIO, not just
        // the ordering. Ifsm is set by the die's thermal mass over one half-cycle
        // and If by steady-state cooling, so their ratio is bounded by package
        // physics at 10-40x regardless of die size. BAS70VY reads 71,000 because
        // its forwardCurrent is stored 1000x low -- the ordering check above is
        // perfectly happy with that, which is why the whole 501-row Nexperia scale
        // block was invisible.
        //
        // TVS/Zener/ESD are excluded: their surgeCurrent is a peak PULSE current
        // (Ipp at 8/20 us) rated against a standoff voltage, a different pair
        // entirely, and their "forwardCurrent" is not an average rectified current.
        //
        // SUSPICIOUS: the ratio proves the two numbers disagree, not which one is
        // wrong.
        if (If && !tvs && *If > 0 && *surge > 0 &&
            *surge / *If > thr::DIO_SURGE_IF_RATIO_SUS)
            emit(out, ctx, "DIO_SURGE_RATIO", Severity::Suspicious, *surge / *If,
                 thr::DIO_SURGE_IF_RATIO_SUS,
                 fmt("surgeCurrent/forwardCurrent far above the 10-40x any real "
                     "rectifier reaches (one of the two current ratings is off by a "
                     "decimal scale factor)",
                     *surge / *If, thr::DIO_SURGE_IF_RATIO_SUS));
    } else {
        skipped.push_back("DIO_SURGE_RATIO");
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

    // CHECK (NEW, adversarial physics review 2026-09-04): powerDissipation
    // against the record's OWN thermal path -- the diode analogue of
    // MOS_POWER_THERMAL, which had no counterpart here at all. Wolfspeed
    // CSD06060A stores 360 W behind a thermal path good for 75 W.
    //
    // The ceiling comes from thermal_power_ceiling(), which requires a
    // junction-to-CASE resistance and takes the most generous reference the
    // record carries. See its comment: powerDissipation in this corpus mixes
    // case- and ambient-referenced values with no discriminator, and measuring a
    // case-referenced Pd against an ambient path is exactly how a correct record
    // gets accused.
    //
    // TVS/Zener/ESD are EXCLUDED, and this is not a convenience: their
    // powerDissipation field carries the PEAK PULSE power (Ppp over 10/1000 us),
    // which is three orders of magnitude above any steady-state rating by design.
    // Vishay P6KE200A really is a 600 W part with a 5 W steady-state rating, and
    // an un-gated version of this check accused it, Littelfuse SMBJ400A and six
    // SMAJ48 variants -- eight real parts out of ten hits. The surviving two are
    // Wolfspeed SiC rectifiers whose own thermal block contradicts their Pd.
    if (auto pd = scalar_at(*elec, {"powerDissipation"}); pd && !tvs) {
        if (*pd > 0) {
            auto ceiling = thermal_power_ceiling(datasheet);
            if (!ceiling) {
                skipped.push_back("DIO_POWER_THERMAL");
            } else if (*pd > *ceiling * thr::DIO_PTHERMAL_RATIO_SUS) {
                emit(out, ctx, "DIO_POWER_THERMAL", Severity::Suspicious, *pd, *ceiling,
                     fmt("powerDissipation contradicts the record's own thermal path "
                         "(Tjmax-25)/Rth [W]", *pd, *ceiling));
            }
        }
    } else {
        skipped.push_back("DIO_POWER_THERMAL");
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
