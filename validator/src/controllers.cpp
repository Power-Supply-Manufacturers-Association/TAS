// SPDX-License-Identifier: Apache-2.0
// Controller (CTAS) physics checks. `datasheet` is the controller datasheetInfo:
//   function.{category,channelCount,maxPhaseCount}, electrical.{supplyVoltage,
//   switchingFrequencyMin/Max, uvlo[], isolation, gateDrive, shuntReference,
//   syncRectifier, ...}, thermal.{thetaJA,thetaJC,maximumJunctionTemperature}.
// CTAS is the unified schema for every control IC (PWM / LLC / PFC / multiphase /
// phase-shift / sync-rect controllers, isolated & non-isolated gate drivers,
// references, shunt regulators, sense amps, hot-swap/eFuse). Most checks are
// STRUCTURAL ordering invariants every real controller obeys regardless of category;
// the magnitude bounds are deliberately wide (control ICs span huge parameter ranges).
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <string>

namespace tas {

void check_controllers(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                       std::vector<std::string>& skipped) {
    // --- function-level invariant: maxPhaseCount >= channelCount ---
    if (const json* fn = at(datasheet, "function")) {
        auto ch = scalar_at(*fn, {"channelCount"});
        auto ph = scalar_at(*fn, {"maxPhaseCount"});
        if (ch && ph && *ph < *ch)
            emit(out, ctx, "CTL_PHASE_COUNT", Severity::Impossible, *ph, *ch,
                 fmt("maxPhaseCount < channelCount", *ph, *ch));
        if (ph && *ph > thr::CTL_PHASE_IMP)
            emit(out, ctx, "CTL_PHASE_COUNT", Severity::Impossible, *ph, thr::CTL_PHASE_IMP,
                 fmt("maxPhaseCount implausibly high", *ph, thr::CTL_PHASE_IMP));
        else if (ph && *ph > thr::CTL_PHASE_SUS)
            emit(out, ctx, "CTL_PHASE_COUNT", Severity::Suspicious, *ph, thr::CTL_PHASE_SUS,
                 fmt("maxPhaseCount high (real max ~20)", *ph, thr::CTL_PHASE_SUS));
    }

    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("CTL_*");
        // thermal can still be checked below.
    }

    if (elec != nullptr) {
        // --- supply-voltage ordering + absolute-max coherence ---
        auto svmin = scalar_at(*elec, {"supplyVoltage", "minimum"});
        auto svmax = scalar_at(*elec, {"supplyVoltage", "maximum"});
        auto svabs = scalar_at(*elec, {"supplyVoltageAbsoluteMax"});
        if (svmin && svmax && *svmin > *svmax)
            emit(out, ctx, "CTL_SUPPLY_ORDER", Severity::Impossible, *svmin, *svmax,
                 fmt("supplyVoltage minimum > maximum", *svmin, *svmax));
        if (svabs && *svabs <= 0)
            emit(out, ctx, "CTL_POSITIVITY", Severity::Impossible, *svabs, 0,
                 "supplyVoltageAbsoluteMax <= 0");
        if (svabs && svmax && *svabs > 0 && *svabs < *svmax)
            emit(out, ctx, "CTL_SUPPLY_ABSMAX", Severity::Impossible, *svabs, *svmax,
                 fmt("supplyVoltageAbsoluteMax below the operating-maximum VCC", *svabs, *svmax));

        // --- switching-frequency ordering ---
        auto fmin = scalar_at(*elec, {"switchingFrequencyMin"});
        auto fmax = scalar_at(*elec, {"switchingFrequencyMax"});
        if (fmin && fmax && *fmin > *fmax)
            emit(out, ctx, "CTL_FREQ_ORDER", Severity::Impossible, *fmin, *fmax,
                 fmt("switchingFrequencyMin > switchingFrequencyMax", *fmin, *fmax));

        // --- UVLO: turn-on (start) must sit above turn-off (stop). Only for a
        // positive (VCC-side) rail: a negative-rail VEE UVLO (e.g. UCC21737 rising
        // -3.1 V / falling -2.6 V) has more-negative = on, so the raw ordering flips
        // and would false-fire. Gate on both thresholds > 0. ---
        if (elec->contains("uvlo") && (*elec)["uvlo"].is_array()) {
            for (const auto& u : (*elec)["uvlo"]) {
                if (!u.is_object()) continue;
                auto start = scalar_at(u, {"startThreshold"});
                auto stop = scalar_at(u, {"stopThreshold"});
                if (start && stop && *start > 0 && *stop > 0 && *start <= *stop)
                    emit(out, ctx, "CTL_UVLO_ORDER", Severity::Impossible, *start, *stop,
                         fmt("UVLO startThreshold <= stopThreshold (turn-on must exceed turn-off)",
                             *start, *stop));
            }
        }

        // --- isolation barrier ordering. Only surge >= withstand and surge >= working
        // are universal: surge & working are peak, withstand is RMS (×√2 to peak), and
        // on wide-body parts withstand DECOUPLES from working (withstand can sit below
        // the peak working voltage), so withstand-vs-working is NOT enforced. ---
        if (const json* iso = at(*elec, "isolation")) {
            auto work = scalar_at(*iso, {"workingVoltage"});
            auto withs = scalar_at(*iso, {"withstandVoltageRms"});
            auto surge = scalar_at(*iso, {"surgeVoltage"});
            if (surge && withs && *surge < *withs)  // peak surge below RMS withstand: impossible
                emit(out, ctx, "CTL_ISO_ORDER", Severity::Impossible, *surge, *withs,
                     fmt("surgeVoltage below withstandVoltageRms", *surge, *withs));
            if (surge && work && *surge < *work)
                emit(out, ctx, "CTL_ISO_ORDER", Severity::Impossible, *surge, *work,
                     fmt("surgeVoltage below workingVoltage", *surge, *work));
            auto creep = scalar_at(*iso, {"creepage"});
            auto clear = scalar_at(*iso, {"clearance"});
            if (creep && clear && *creep > 0 && *clear > 0 && *creep < *clear)
                emit(out, ctx, "CTL_ISO_CREEP", Severity::Impossible, *creep, *clear,
                     fmt("creepage < clearance (surface path shorter than air path)", *creep,
                         *clear));
        }

        // --- shunt-reference cathode-current window ordering ---
        if (const json* sh = at(*elec, "shuntReference")) {
            auto imin = scalar_at(*sh, {"minCathodeCurrent"});
            auto imax = scalar_at(*sh, {"maxCathodeCurrent"});
            if (imin && imax && *imin > *imax)
                emit(out, ctx, "CTL_SHUNT_CATHODE", Severity::Impossible, *imin, *imax,
                     fmt("minCathodeCurrent > maxCathodeCurrent", *imin, *imax));
        }

        // --- sync-rectifier VDS turn-on threshold is negative by construction ---
        if (const json* sr = at(*elec, "syncRectifier")) {
            if (auto ton = scalar_at(*sr, {"turnOnThreshold"}))
                if (*ton >= 0)
                    emit(out, ctx, "CTL_SR_THRESHOLD", Severity::Suspicious, *ton, 0,
                         fmt("sync-rectifier turnOnThreshold should be negative (VDS sense) [V]",
                             *ton));
        }

        // --- maximum duty cycle is a fraction in [0,1] ---
        if (auto dmax = scalar_at(*elec, {"maxDutyCycle"}))
            if (*dmax < 0 || *dmax > 1)
                emit(out, ctx, "CTL_DUTY_RANGE", Severity::Impossible, *dmax, 1.0,
                     fmt("maxDutyCycle outside [0,1]", *dmax));

        // --- dead time must fit inside the switching period (deadTime < 1/fmax) ---
        if (fmax && *fmax > 0)
            if (auto dt = scalar_at(*elec, {"deadTime"}))
                if (*dt > 0 && *dt >= 1.0 / *fmax)
                    emit(out, ctx, "CTL_DEADTIME", Severity::Impossible, *dt, 1.0 / *fmax,
                         fmt("deadTime >= switching period (1/switchingFrequencyMax) [s]", *dt,
                             1.0 / *fmax));

        // --- magnitude bounds (wide; catch unit-error / fabricated values) ---
        if (svabs && *svabs > 0) {
            if (*svabs > thr::CTL_VABSMAX_IMP)
                emit(out, ctx, "CTL_SUPPLY_RANGE", Severity::Impossible, *svabs,
                     thr::CTL_VABSMAX_IMP, fmt("supplyVoltageAbsoluteMax implausibly high [V]",
                                               *svabs, thr::CTL_VABSMAX_IMP));
            else if (*svabs > thr::CTL_VABSMAX_SUS)
                emit(out, ctx, "CTL_SUPPLY_RANGE", Severity::Suspicious, *svabs,
                     thr::CTL_VABSMAX_SUS, fmt("supplyVoltageAbsoluteMax high for a controller [V]",
                                               *svabs, thr::CTL_VABSMAX_SUS));
        }
        if (fmax && *fmax > 0) {
            if (*fmax > thr::CTL_FREQ_IMP)
                emit(out, ctx, "CTL_FREQ_RANGE", Severity::Impossible, *fmax, thr::CTL_FREQ_IMP,
                     fmt("switchingFrequencyMax implausibly high [Hz]", *fmax, thr::CTL_FREQ_IMP));
            else if (*fmax > thr::CTL_FREQ_SUS)
                emit(out, ctx, "CTL_FREQ_RANGE", Severity::Suspicious, *fmax, thr::CTL_FREQ_SUS,
                     fmt("switchingFrequencyMax very high [Hz]", *fmax, thr::CTL_FREQ_SUS));
        }
        if (auto vref = scalar_at(*elec, {"referenceVoltage"})) {
            if (*vref <= 0 || *vref > thr::CTL_VREF_IMP)
                emit(out, ctx, "CTL_REF_RANGE", Severity::Impossible, *vref, thr::CTL_VREF_IMP,
                     fmt("referenceVoltage out of range [V]", *vref));
            else if (*vref < thr::CTL_VREF_SUS_LO || *vref > thr::CTL_VREF_SUS_HI)
                emit(out, ctx, "CTL_REF_RANGE", Severity::Suspicious, *vref, 0,
                     fmt("referenceVoltage outside typical 0.4..12 V", *vref));
        }
        if (const json* cm = at(*elec, "currentMode"))
            if (auto cs = scalar_at(*cm, {"maxThresholdVoltage"})) {
                if (*cs <= 0 || *cs > thr::CTL_CS_THRESH_IMP)
                    emit(out, ctx, "CTL_CS_THRESHOLD", Severity::Impossible, *cs,
                         thr::CTL_CS_THRESH_IMP,
                         fmt("current-mode maxThresholdVoltage out of range [V]", *cs,
                             thr::CTL_CS_THRESH_IMP));
                else if (*cs > thr::CTL_CS_THRESH_SUS)
                    emit(out, ctx, "CTL_CS_THRESHOLD", Severity::Suspicious, *cs,
                         thr::CTL_CS_THRESH_SUS,
                         fmt("current-mode maxThresholdVoltage high [V]", *cs,
                             thr::CTL_CS_THRESH_SUS));
            }

        // --- gate-drive magnitude bounds ---
        if (const json* gd = at(*elec, "gateDrive")) {
            for (const char* k : {"sourceCurrentPeak", "sinkCurrentPeak"})
                if (auto i = scalar_at(*gd, {k})) {
                    if (*i > thr::CTL_GATE_I_IMP)
                        emit(out, ctx, "CTL_GATE_DRIVE", Severity::Impossible, *i,
                             thr::CTL_GATE_I_IMP,
                             fmt(std::string("gateDrive.") + k + " implausibly high [A]", *i,
                                 thr::CTL_GATE_I_IMP));
                    else if (*i > thr::CTL_GATE_I_SUS)
                        emit(out, ctx, "CTL_GATE_DRIVE", Severity::Suspicious, *i,
                             thr::CTL_GATE_I_SUS,
                             fmt(std::string("gateDrive.") + k + " high for a driver [A]", *i,
                                 thr::CTL_GATE_I_SUS));
                }
            if (auto dv = scalar_at(*gd, {"driveVoltage"})) {
                if (*dv > thr::CTL_DRIVE_V_IMP)
                    emit(out, ctx, "CTL_GATE_DRIVE", Severity::Impossible, *dv, thr::CTL_DRIVE_V_IMP,
                         fmt("gateDrive.driveVoltage implausibly high [V]", *dv,
                             thr::CTL_DRIVE_V_IMP));
                else if (*dv > thr::CTL_DRIVE_V_SUS)
                    emit(out, ctx, "CTL_GATE_DRIVE", Severity::Suspicious, *dv, thr::CTL_DRIVE_V_SUS,
                         fmt("gateDrive.driveVoltage high [V]", *dv, thr::CTL_DRIVE_V_SUS));
            }
            if (auto pd = scalar_at(*gd, {"propagationDelay"})) {
                if (*pd > thr::CTL_PROP_DELAY_IMP)
                    emit(out, ctx, "CTL_GATE_DRIVE", Severity::Impossible, *pd,
                         thr::CTL_PROP_DELAY_IMP,
                         fmt("gateDrive.propagationDelay implausibly long [s]", *pd,
                             thr::CTL_PROP_DELAY_IMP));
                else if (*pd > thr::CTL_PROP_DELAY_SUS)
                    emit(out, ctx, "CTL_GATE_DRIVE", Severity::Suspicious, *pd,
                         thr::CTL_PROP_DELAY_SUS,
                         fmt("gateDrive.propagationDelay long [s]", *pd, thr::CTL_PROP_DELAY_SUS));
            }
        }

        // --- isolation magnitude bounds ---
        if (const json* iso = at(*elec, "isolation")) {
            if (auto viso = scalar_at(*iso, {"withstandVoltageRms"})) {
                if (*viso > thr::CTL_ISO_VISO_IMP)
                    emit(out, ctx, "CTL_ISO_RANGE", Severity::Impossible, *viso,
                         thr::CTL_ISO_VISO_IMP,
                         fmt("withstandVoltageRms implausibly high [V]", *viso,
                             thr::CTL_ISO_VISO_IMP));
                else if (*viso > thr::CTL_ISO_VISO_SUS)
                    emit(out, ctx, "CTL_ISO_RANGE", Severity::Suspicious, *viso,
                         thr::CTL_ISO_VISO_SUS,
                         fmt("withstandVoltageRms very high [V]", *viso, thr::CTL_ISO_VISO_SUS));
            }
            if (auto cmti = scalar_at(*iso, {"cmti"})) {
                if (*cmti > thr::CTL_CMTI_IMP)
                    emit(out, ctx, "CTL_ISO_RANGE", Severity::Impossible, *cmti, thr::CTL_CMTI_IMP,
                         fmt("cmti implausibly high [V/s]", *cmti, thr::CTL_CMTI_IMP));
                else if (*cmti > thr::CTL_CMTI_SUS)
                    emit(out, ctx, "CTL_ISO_RANGE", Severity::Suspicious, *cmti, thr::CTL_CMTI_SUS,
                         fmt("cmti very high [V/s]", *cmti, thr::CTL_CMTI_SUS));
            }
        }

        // --- gate-drive positivity (peak currents / edge times are non-negative) ---
        if (const json* gd = at(*elec, "gateDrive"))
            for (const char* k : {"sourceCurrentPeak", "sinkCurrentPeak", "riseTime", "fallTime",
                                  "propagationDelay", "minPulseWidth"})
                if (auto v = scalar_at(*gd, {k}))
                    if (*v < 0)
                        emit(out, ctx, "CTL_POSITIVITY", Severity::Impossible, *v, 0,
                             std::string("gateDrive.") + k + " < 0");
    }

    // --- thermal ordering: junction-to-case <= junction-to-ambient ---
    auto jc = scalar_at(datasheet, {"thermal", "thetaJC"});
    auto ja = scalar_at(datasheet, {"thermal", "thetaJA"});
    if (jc && ja && *jc > *ja)
        emit(out, ctx, "CTL_THERMAL_ORDER", Severity::Impossible, *jc, *ja,
             fmt("thetaJC > thetaJA (case path cannot be worse than ambient path)", *jc, *ja));
    if (auto tj = scalar_at(datasheet, {"thermal", "maximumJunctionTemperature"})) {
        if (*tj > thr::CTL_TJMAX_IMP)
            emit(out, ctx, "CTL_TJMAX", Severity::Impossible, *tj, thr::CTL_TJMAX_IMP,
                 fmt("maximumJunctionTemperature implausibly high [degC]", *tj, thr::CTL_TJMAX_IMP));
        else if (*tj > thr::CTL_TJMAX_SUS)
            emit(out, ctx, "CTL_TJMAX", Severity::Suspicious, *tj, thr::CTL_TJMAX_SUS,
                 fmt("maximumJunctionTemperature high [degC]", *tj, thr::CTL_TJMAX_SUS));
    }
}

}  // namespace tas
