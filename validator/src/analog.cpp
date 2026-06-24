// SPDX-License-Identifier: Apache-2.0
// Analog IC (AAS) physics checks. `datasheet` is the AAS component datasheetInfo;
// the subtype is ctx.component (the top-level discriminator). Subtype families:
//   amplifier   (operationalAmplifier, buffer, differenceAmplifier,
//                instrumentationAmplifier, programmableGainAmplifier, sampleHold)
//                -> electrical extends utils.amplifierCommon (+supply)
//   comparator  -> own offset/bias/supply + propagationDelay (required)
//   converter   (adc, dac) -> resolution, sample/update rate, referenceVoltage, dynamics
//   switch      (analogSwitch, multiplexer) -> utils.switchCore (onResistance, ...)
//   behavioral  (integrator, summer, multiplier) -> no electrical; nothing to check
// Bounds: TI/ADI op-amp portfolios (GBW 50 MHz–8 GHz; slew ≤~3500 V/µs; CMRR/PSRR 60–140 dB;
// Vos µV–10 mV), ADC ≤32 bits / ~GSPS, comparator t_PD ns–µs.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cmath>
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

bool is_amplifier(const std::string& c) {
    return c == "operationalAmplifier" || c == "buffer" || c == "differenceAmplifier" ||
           c == "instrumentationAmplifier" || c == "programmableGainAmplifier" || c == "sampleHold";
}

// Shared dB-rating check (CMRR / PSRR / open-loop gain).
void check_db(const json& elec, const Ctx& ctx, std::vector<Finding>& out, const char* key,
              const char* code) {
    if (auto v = scalar_at(elec, {key})) {
        if (*v < 0 || *v > thr::ANA_DB_IMP)
            emit(out, ctx, code, Severity::Impossible, *v, thr::ANA_DB_IMP,
                 fmt(std::string(key) + " outside [0,200] dB", *v));
        else if (*v < thr::ANA_DB_SUS_LO || *v > thr::ANA_DB_SUS_HI)
            emit(out, ctx, code, Severity::Suspicious, *v, 0,
                 fmt(std::string(key) + " outside typical 20..180 dB", *v));
    }
}

// Shared supply checks (electrical.supply.*).
void check_supply(const json& elec, const Ctx& ctx, std::vector<Finding>& out) {
    auto lo = scalar_at(elec, {"supply", "minimumSupplyVoltage"});
    auto hi = scalar_at(elec, {"supply", "maximumSupplyVoltage"});
    if (lo && *lo <= 0)
        emit(out, ctx, "ANA_SUPPLY", Severity::Impossible, *lo, 0, "minimumSupplyVoltage <= 0");
    if (lo && hi && *lo > *hi)
        emit(out, ctx, "ANA_SUPPLY", Severity::Impossible, *lo, *hi,
             fmt("minimumSupplyVoltage > maximumSupplyVoltage", *lo, *hi));
    if (hi && *hi > 0) {
        if (*hi > thr::ANA_SUPPLY_IMP)
            emit(out, ctx, "ANA_SUPPLY", Severity::Impossible, *hi, thr::ANA_SUPPLY_IMP,
                 fmt("maximumSupplyVoltage implausibly high [V]", *hi, thr::ANA_SUPPLY_IMP));
        else if (*hi > thr::ANA_SUPPLY_SUS)
            emit(out, ctx, "ANA_SUPPLY", Severity::Suspicious, *hi, thr::ANA_SUPPLY_SUS,
                 fmt("maximumSupplyVoltage high for an analog IC [V]", *hi, thr::ANA_SUPPLY_SUS));
    }
    if (auto iq = scalar_at(elec, {"supply", "quiescentCurrentPerChannel"}))
        if (*iq < 0)
            emit(out, ctx, "ANA_SUPPLY", Severity::Impossible, *iq, 0,
                 "quiescentCurrentPerChannel < 0");
}

// Shared DC-input + channel-count checks (amplifiers + comparators).
void check_dc_input(const json& elec, const Ctx& ctx, std::vector<Finding>& out) {
    if (auto nch = scalar_at(elec, {"numberOfChannels"})) {
        if (*nch < 1 || *nch > thr::ANA_CHANNELS_IMP)
            emit(out, ctx, "ANA_CHANNELS", Severity::Impossible, *nch, thr::ANA_CHANNELS_IMP,
                 fmt("numberOfChannels out of range", *nch));
        else if (*nch > thr::ANA_CHANNELS_SUS)
            emit(out, ctx, "ANA_CHANNELS", Severity::Suspicious, *nch, thr::ANA_CHANNELS_SUS,
                 fmt("numberOfChannels unusually high", *nch));
    }
    if (auto vos = scalar_at(elec, {"inputOffsetVoltage"})) {
        double a = std::fabs(*vos);
        if (a > thr::ANA_VOS_IMP)
            emit(out, ctx, "ANA_VOS", Severity::Impossible, a, thr::ANA_VOS_IMP,
                 fmt("|inputOffsetVoltage| implausibly large [V]", a, thr::ANA_VOS_IMP));
        else if (a > thr::ANA_VOS_SUS)
            emit(out, ctx, "ANA_VOS", Severity::Suspicious, a, thr::ANA_VOS_SUS,
                 fmt("|inputOffsetVoltage| high [V]", a, thr::ANA_VOS_SUS));
    }
    if (auto ib = scalar_at(elec, {"inputBiasCurrent"}))
        if (std::fabs(*ib) > thr::ANA_IBIAS_SUS)
            emit(out, ctx, "ANA_IBIAS", Severity::Suspicious, std::fabs(*ib), thr::ANA_IBIAS_SUS,
                 fmt("|inputBiasCurrent| high for an analog input [A]", std::fabs(*ib),
                     thr::ANA_IBIAS_SUS));
}

void check_amplifier(const json& elec, const Ctx& ctx, std::vector<Finding>& out) {
    check_dc_input(elec, ctx, out);
    check_supply(elec, ctx, out);
    check_db(elec, ctx, out, "commonModeRejectionRatio", "ANA_CMRR");
    check_db(elec, ctx, out, "powerSupplyRejectionRatio", "ANA_PSRR");
    check_db(elec, ctx, out, "openLoopGain", "ANA_OL_GAIN");
    if (auto sr = scalar_at(elec, {"slewRate"})) {
        if (*sr <= 0)
            emit(out, ctx, "ANA_SLEW", Severity::Impossible, *sr, 0, "slewRate <= 0");
        else if (*sr > thr::ANA_SLEW_IMP)
            emit(out, ctx, "ANA_SLEW", Severity::Impossible, *sr, thr::ANA_SLEW_IMP,
                 fmt("slewRate implausibly high [V/s]", *sr, thr::ANA_SLEW_IMP));
        else if (*sr > thr::ANA_SLEW_SUS)
            emit(out, ctx, "ANA_SLEW", Severity::Suspicious, *sr, thr::ANA_SLEW_SUS,
                 fmt("slewRate very high [V/s]", *sr, thr::ANA_SLEW_SUS));
    }
    if (auto vn = scalar_at(elec, {"voltageNoiseDensity"})) {
        if (*vn <= 0)
            emit(out, ctx, "ANA_VNOISE", Severity::Impossible, *vn, 0, "voltageNoiseDensity <= 0");
        else if (*vn < thr::ANA_VNOISE_SUS_LO || *vn > thr::ANA_VNOISE_SUS_HI)
            emit(out, ctx, "ANA_VNOISE", Severity::Suspicious, *vn, 0,
                 fmt("voltageNoiseDensity outside 0.1 nV..10 µV/sqrtHz", *vn));
    }
    if (auto gbw = scalar_at(elec, {"gainBandwidthProduct"})) {  // op-amp
        if (*gbw <= 0)
            emit(out, ctx, "ANA_GBW", Severity::Impossible, *gbw, 0, "gainBandwidthProduct <= 0");
        else if (*gbw > thr::ANA_GBW_IMP)
            emit(out, ctx, "ANA_GBW", Severity::Impossible, *gbw, thr::ANA_GBW_IMP,
                 fmt("gainBandwidthProduct implausibly high [Hz]", *gbw, thr::ANA_GBW_IMP));
        else if (*gbw > thr::ANA_GBW_SUS)
            emit(out, ctx, "ANA_GBW", Severity::Suspicious, *gbw, thr::ANA_GBW_SUS,
                 fmt("gainBandwidthProduct very high [Hz]", *gbw, thr::ANA_GBW_SUS));
    }
}

void check_comparator(const json& elec, const Ctx& ctx, std::vector<Finding>& out) {
    check_dc_input(elec, ctx, out);
    check_supply(elec, ctx, out);
    if (auto tpd = scalar_at(elec, {"propagationDelay"})) {
        if (*tpd <= 0 || *tpd > thr::CMP_TPD_IMP)
            emit(out, ctx, "CMP_TPD", Severity::Impossible, *tpd, thr::CMP_TPD_IMP,
                 fmt("propagationDelay outside (0,1ms] [s]", *tpd, thr::CMP_TPD_IMP));
        else if (*tpd < thr::CMP_TPD_SUS_LO || *tpd > thr::CMP_TPD_SUS_HI)
            emit(out, ctx, "CMP_TPD", Severity::Suspicious, *tpd, 0,
                 fmt("propagationDelay outside typical 0.1 ns..100 µs [s]", *tpd));
    }
    if (auto h = scalar_at(elec, {"hysteresis"}))
        if (*h < 0)
            emit(out, ctx, "CMP_HYST", Severity::Impossible, *h, 0, "hysteresis < 0");
}

void check_converter(const json& elec, const Ctx& ctx, std::vector<Finding>& out) {
    check_supply(elec, ctx, out);
    if (auto res = scalar_at(elec, {"resolution"})) {
        if (*res < 1 || *res > thr::CONV_RES_IMP)
            emit(out, ctx, "CONV_RES", Severity::Impossible, *res, thr::CONV_RES_IMP,
                 fmt("resolution out of range [bits]", *res, thr::CONV_RES_IMP));
        else if (*res > thr::CONV_RES_SUS)
            emit(out, ctx, "CONV_RES", Severity::Suspicious, *res, thr::CONV_RES_SUS,
                 fmt("resolution unusually high [bits]", *res, thr::CONV_RES_SUS));
    }
    for (const char* k : {"sampleRate", "updateRate"}) {
        if (auto rate = scalar_at(elec, {k})) {
            if (*rate <= 0)
                emit(out, ctx, "CONV_RATE", Severity::Impossible, *rate, 0,
                     std::string(k) + " <= 0");
            else if (*rate > thr::CONV_RATE_IMP)
                emit(out, ctx, "CONV_RATE", Severity::Impossible, *rate, thr::CONV_RATE_IMP,
                     fmt(std::string(k) + " implausibly high [Sps]", *rate, thr::CONV_RATE_IMP));
            else if (*rate > thr::CONV_RATE_SUS)
                emit(out, ctx, "CONV_RATE", Severity::Suspicious, *rate, thr::CONV_RATE_SUS,
                     fmt(std::string(k) + " very high [Sps]", *rate, thr::CONV_RATE_SUS));
        }
    }
    if (auto vref = scalar_at(elec, {"referenceVoltage"}))
        if (*vref <= 0 || *vref > thr::ANA_SUPPLY_IMP)
            emit(out, ctx, "CONV_VREF", Severity::Impossible, *vref, 0,
                 fmt("referenceVoltage out of range [V]", *vref));
    if (auto snr = scalar_at(elec, {"dynamics", "signalToNoiseRatio"}))
        if (*snr < 0 || *snr > thr::ANA_DB_IMP)
            emit(out, ctx, "CONV_SNR", Severity::Impossible, *snr, thr::ANA_DB_IMP,
                 fmt("signalToNoiseRatio outside [0,200] dB", *snr));
}

void check_switch(const json& elec, const Ctx& ctx, std::vector<Finding>& out) {
    check_supply(elec, ctx, out);
    if (auto ron = scalar_at(elec, {"onResistance"})) {
        if (*ron <= 0 || *ron > thr::SW_RON_IMP)
            emit(out, ctx, "SW_RON", Severity::Impossible, *ron, thr::SW_RON_IMP,
                 fmt("onResistance out of range [Ohm]", *ron, thr::SW_RON_IMP));
        else if (*ron > thr::SW_RON_SUS)
            emit(out, ctx, "SW_RON", Severity::Suspicious, *ron, thr::SW_RON_SUS,
                 fmt("onResistance high for an analog switch [Ohm]", *ron, thr::SW_RON_SUS));
    }
    if (auto leak = scalar_at(elec, {"offLeakageCurrent"}))
        if (*leak < 0)
            emit(out, ctx, "SW_LEAK", Severity::Impossible, *leak, 0, "offLeakageCurrent < 0");
}

void check_multiplier(const json& elec, const Ctx& ctx, std::vector<Finding>& out) {
    check_supply(elec, ctx, out);
    if (auto sf = scalar_at(elec, {"scaleFactor"}))
        if (*sf <= 0)
            emit(out, ctx, "MULT_SCALE", Severity::Impossible, *sf, 0, "scaleFactor <= 0");
    if (auto te = scalar_at(elec, {"totalError"}))
        if (*te < 0 || *te > 1.0)
            emit(out, ctx, "MULT_ERROR", Severity::Impossible, *te, 1.0,
                 fmt("totalError outside [0,1] (fraction of full scale)", *te));
    if (auto bw = scalar_at(elec, {"bandwidth"})) {
        if (*bw <= 0)
            emit(out, ctx, "MULT_BW", Severity::Impossible, *bw, 0, "bandwidth <= 0");
        else if (*bw > 1.0e10)
            emit(out, ctx, "MULT_BW", Severity::Impossible, *bw, 1.0e10,
                 fmt("bandwidth implausibly high for an analog multiplier [Hz]", *bw, 1.0e10));
    }
}

}  // namespace

void check_analog(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                  std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    const std::string& c = ctx.component;
    // Behavioral-only blocks carry no electrical specs — nothing physical to check.
    if (c == "integrator" || c == "summer") {
        skipped.push_back("ANA_BEHAVIORAL");
        return;
    }
    if (elec == nullptr) {
        skipped.push_back("ANA_*");
        return;
    }
    if (is_amplifier(c)) check_amplifier(*elec, ctx, out);
    else if (c == "comparator") check_comparator(*elec, ctx, out);
    else if (c == "adc" || c == "dac") check_converter(*elec, ctx, out);
    else if (c == "analogSwitch" || c == "multiplexer") check_switch(*elec, ctx, out);
    else if (c == "multiplier") check_multiplier(*elec, ctx, out);
    else skipped.push_back("ANA_UNKNOWN_SUBTYPE");
}

}  // namespace tas
