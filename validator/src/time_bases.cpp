// SPDX-License-Identifier: MIT
// Time-base (TBAS) physics checks: oscillators/crystals, 555-class timers and
// discrete SR latches. `datasheet` is the family datasheetInfo; the subtype is
// ctx.component ("oscillator" | "timer" | "latch", the timeBase sub-discriminator).
//
//   oscillator.electrical: technology, frequency [Hz], mode, outputType,
//     frequencyTolerance / frequencyStability / agingPerYear / pullRange
//     (DIMENSIONLESS fractions, 1 ppm = 1e-6), rmsPhaseJitter [s],
//     startupTime [s], loadCapacitance/equivalentSeriesResistance/
//     resonantImpedance/builtInCapacitance (bare resonators), supply{...},
//     enableFunction.
//   timer.electrical: technology, maximumFrequency [Hz], timingAccuracy
//     (fraction), numberOfChannels, supply{...}.
//   latch.electrical: technology, propagationDelay [s], numberOfChannels,
//     supply{...}.
//
// Vendor-sourced bounds live in thresholds.hpp (July-2026 catalog pass; every
// IMPOSSIBLE floor >10x beyond published best-in-class). Convention: a spec of
// exactly 0 for agingPerYear / rmsPhaseJitter / startupTime is vendor-CSV
// "missing data", not a value — those checks skip it.
//
// The behavioral atoms (ideal periodic source / one-shot / SR latch) are design
// intent, not physics claims: they get only unit-slip screening (TB_BEHAVIORAL,
// SUSPICIOUS), and a record may be a part-less behavioral-only document.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <string>

namespace tas {
namespace {

bool is_bare_resonator(const std::string& tech) {
    return tech == "quartzcrystal" || tech == "ceramicresonator";
}

// Packaged quartz-based oscillator classes (share the quartz XO frequency window).
bool is_quartz_xo(const std::string& tech) {
    return tech == "crystaloscillator" || tech == "vcxo" || tech == "programmable";
}

// --- oscillator: frequency window by technology + vibration mode -------------
void check_osc_frequency(const json& elec, const std::string& tech, const Ctx& ctx,
                         std::vector<Finding>& out, std::vector<std::string>& skipped) {
    auto f = scalar_at(elec, {"frequency"});
    if (!f) {
        skipped.push_back("TB_OSC_FREQ_TECH:frequency");
        return;
    }
    // Belt + braces under the schema's exclusiveMinimum: 0.
    if (*f <= 0) {
        emit(out, ctx, "TB_OSC_POSITIVITY", Severity::Impossible, *f, 0,
             fmt("frequency <= 0 [Hz]", *f));
        return;
    }

    if (tech == "quartzcrystal") {
        if (*f > thr::TB_F_QUARTZ_IMP)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Impossible, *f, thr::TB_F_QUARTZ_IMP,
                 fmt("quartz crystal above the overtone ceiling [Hz]", *f, thr::TB_F_QUARTZ_IMP));
        if (*f < thr::TB_F_QUARTZ_MIN_IMP)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Impossible, *f, thr::TB_F_QUARTZ_MIN_IMP,
                 fmt("quartz crystal below 1 kHz does not exist [Hz]", *f,
                     thr::TB_F_QUARTZ_MIN_IMP));
        // Vibration mode vs frequency (mode is quartz-only).
        const json* mode = at(elec, "mode");
        if (mode != nullptr && mode->is_string()) {
            const std::string m = mode->get<std::string>();
            if (m == "fundamental") {
                if (*f > thr::TB_F_QUARTZ_FUND_IMP)
                    emit(out, ctx, "TB_OSC_MODE_FREQ", Severity::Impossible, *f,
                         thr::TB_F_QUARTZ_FUND_IMP,
                         fmt("fundamental-mode quartz above the inverted-mesa HFF ceiling [Hz]",
                             *f, thr::TB_F_QUARTZ_FUND_IMP));
                else if (*f > thr::TB_F_QUARTZ_FUND_SUS)
                    emit(out, ctx, "TB_OSC_MODE_FREQ", Severity::Suspicious, *f,
                         thr::TB_F_QUARTZ_FUND_SUS,
                         fmt("fundamental-mode quartz above the standard AT-cut range [Hz]", *f,
                             thr::TB_F_QUARTZ_FUND_SUS));
            } else if (m == "overtone3" || m == "overtone5" || m == "overtone7") {
                if (*f < thr::TB_F_QUARTZ_OT_MIN_SUS)
                    emit(out, ctx, "TB_OSC_MODE_FREQ", Severity::Suspicious, *f,
                         thr::TB_F_QUARTZ_OT_MIN_SUS,
                         fmt("declared overtone crystal in fundamental territory [Hz]", *f,
                             thr::TB_F_QUARTZ_OT_MIN_SUS));
            }
        }
    } else if (tech == "ceramicresonator") {
        if (*f > thr::TB_F_CERAMIC_IMP)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Impossible, *f, thr::TB_F_CERAMIC_IMP,
                 fmt("ceramic resonator implausibly fast [Hz]", *f, thr::TB_F_CERAMIC_IMP));
        else if (*f < thr::TB_F_CERAMIC_SUS_LO || *f > thr::TB_F_CERAMIC_SUS_HI)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Suspicious, *f, 0,
                 fmt("ceramic resonator outside the 100 kHz..100 MHz catalog span [Hz]", *f));
    } else if (is_quartz_xo(tech)) {
        if (*f > thr::TB_F_XO_IMP)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Impossible, *f, thr::TB_F_XO_IMP,
                 fmt("packaged oscillator above 2 GHz [Hz]", *f, thr::TB_F_XO_IMP));
        else if (*f > thr::TB_F_XO_SUS)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Suspicious, *f, thr::TB_F_XO_SUS,
                 fmt("packaged oscillator above the 1.5 GHz catalog ceiling [Hz]", *f,
                     thr::TB_F_XO_SUS));
    } else if (tech == "mems") {
        if (*f > thr::TB_F_MEMS_IMP)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Impossible, *f, thr::TB_F_MEMS_IMP,
                 fmt("MEMS oscillator above 1 GHz [Hz]", *f, thr::TB_F_MEMS_IMP));
        else if (*f > thr::TB_F_MEMS_SUS)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Suspicious, *f, thr::TB_F_MEMS_SUS,
                 fmt("MEMS oscillator above the 725 MHz catalog ceiling [Hz]", *f,
                     thr::TB_F_MEMS_SUS));
        if (*f < thr::TB_F_MEMS_MIN_IMP)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Impossible, *f, thr::TB_F_MEMS_MIN_IMP,
                 fmt("MEMS oscillator below 1 Hz does not exist [Hz]", *f,
                     thr::TB_F_MEMS_MIN_IMP));
    } else if (tech == "siliconrc") {
        if (*f > thr::TB_F_SIRC_IMP)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Impossible, *f, thr::TB_F_SIRC_IMP,
                 fmt("silicon-RC oscillator implausibly fast [Hz]", *f, thr::TB_F_SIRC_IMP));
        else if (*f > thr::TB_F_SIRC_SUS)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Suspicious, *f, thr::TB_F_SIRC_SUS,
                 fmt("silicon-RC oscillator above the LTC6905-class ceiling [Hz]", *f,
                     thr::TB_F_SIRC_SUS));
    } else if (tech == "ocxo") {
        if (*f > thr::TB_F_OCXO_IMP)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Impossible, *f, thr::TB_F_OCXO_IMP,
                 fmt("OCXO above 1 GHz [Hz]", *f, thr::TB_F_OCXO_IMP));
        else if (*f > thr::TB_F_OCXO_SUS)
            emit(out, ctx, "TB_OSC_FREQ_TECH", Severity::Suspicious, *f, thr::TB_F_OCXO_SUS,
                 fmt("OCXO above the catalog ceiling [Hz]", *f, thr::TB_F_OCXO_SUS));
    }
    // tcxo has no dedicated frequency window (quartz TCXOs span kHz..~100 MHz;
    // the outputType and generic bounds still apply).
}

// --- oscillator: frequency stability over temperature, by technology ---------
void check_osc_stability(const json& elec, const std::string& tech, const Ctx& ctx,
                         std::vector<Finding>& out) {
    auto s = scalar_at(elec, {"frequencyStability"});
    if (!s) return;
    auto band = [&](double imp_lo, double sus_lo, double sus_hi, const char* cls) {
        if (*s < imp_lo)
            emit(out, ctx, "TB_OSC_STABILITY", Severity::Impossible, *s, imp_lo,
                 fmt(std::string(cls) +
                         " frequencyStability tighter than the class physics allows [fraction]",
                     *s, imp_lo));
        else if (*s < sus_lo || *s > sus_hi)
            emit(out, ctx, "TB_OSC_STABILITY", Severity::Suspicious, *s, 0,
                 fmt(std::string(cls) + " frequencyStability outside the class band [fraction]",
                     *s));
    };
    if (tech == "quartzcrystal" || tech == "crystaloscillator")
        band(thr::TB_STAB_XTAL_IMP, thr::TB_STAB_XTAL_SUS_LO, thr::TB_STAB_XTAL_SUS_HI,
             "plain quartz");
    else if (tech == "tcxo")
        band(thr::TB_STAB_TCXO_IMP, thr::TB_STAB_TCXO_SUS_LO, thr::TB_STAB_TCXO_SUS_HI, "TCXO");
    else if (tech == "ocxo")
        band(thr::TB_STAB_OCXO_IMP, thr::TB_STAB_OCXO_SUS_LO, thr::TB_STAB_OCXO_SUS_HI, "OCXO");
    else if (tech == "mems")
        band(thr::TB_STAB_MEMS_IMP, thr::TB_STAB_MEMS_SUS_LO, thr::TB_STAB_MEMS_SUS_HI, "MEMS");
    else if (tech == "siliconrc")
        band(thr::TB_STAB_SIRC_IMP, thr::TB_STAB_SIRC_SUS_LO, thr::TB_STAB_SIRC_SUS_HI,
             "silicon-RC");
    else if (tech == "ceramicresonator") {
        if (*s < thr::TB_STAB_CERAMIC_IMP)
            emit(out, ctx, "TB_OSC_STABILITY", Severity::Impossible, *s,
                 thr::TB_STAB_CERAMIC_IMP,
                 fmt("ceramic resonator with ppm-class stability (ceramic is percent-class) "
                     "[fraction]",
                     *s, thr::TB_STAB_CERAMIC_IMP));
        else if (*s > thr::TB_STAB_CERAMIC_SUS_HI)
            emit(out, ctx, "TB_OSC_STABILITY", Severity::Suspicious, *s,
                 thr::TB_STAB_CERAMIC_SUS_HI,
                 fmt("ceramic resonator stability worse than 5% [fraction]", *s,
                     thr::TB_STAB_CERAMIC_SUS_HI));
    }
    // vcxo / programmable: mixed resonator classes behind one enum value — no
    // stability band is asserted for them.
}

// --- oscillator: aging per year, by technology (0 = vendor-CSV missing) ------
void check_osc_aging(const json& elec, const std::string& tech, const Ctx& ctx,
                     std::vector<Finding>& out) {
    auto a = scalar_at(elec, {"agingPerYear"});
    if (!a || *a == 0.0) return;  // exactly 0 = missing data, not a value
    if (tech == "quartzcrystal" || tech == "crystaloscillator" || tech == "vcxo") {
        if (*a < thr::TB_AGE_XTAL_IMP_LO || *a > thr::TB_AGE_XTAL_IMP_HI)
            emit(out, ctx, "TB_OSC_AGING", Severity::Impossible, *a, 0,
                 fmt("quartz agingPerYear outside 0.1..30 ppm/yr [fraction/yr]", *a));
        else if (*a < thr::TB_AGE_XTAL_SUS_LO || *a > thr::TB_AGE_XTAL_SUS_HI)
            emit(out, ctx, "TB_OSC_AGING", Severity::Suspicious, *a, 0,
                 fmt("quartz agingPerYear outside the typical 1..10 ppm/yr [fraction/yr]", *a));
    } else if (tech == "tcxo") {
        if (*a < thr::TB_AGE_TCXO_IMP_LO)
            emit(out, ctx, "TB_OSC_AGING", Severity::Impossible, *a, thr::TB_AGE_TCXO_IMP_LO,
                 fmt("TCXO agingPerYear below 0.05 ppm/yr [fraction/yr]", *a,
                     thr::TB_AGE_TCXO_IMP_LO));
        else if (*a < thr::TB_AGE_TCXO_SUS_LO || *a > thr::TB_AGE_TCXO_SUS_HI)
            emit(out, ctx, "TB_OSC_AGING", Severity::Suspicious, *a, 0,
                 fmt("TCXO agingPerYear outside the typical 0.2..5 ppm/yr [fraction/yr]", *a));
    } else if (tech == "ocxo") {
        if (*a < thr::TB_AGE_OCXO_IMP_LO)
            emit(out, ctx, "TB_OSC_AGING", Severity::Impossible, *a, thr::TB_AGE_OCXO_IMP_LO,
                 fmt("OCXO agingPerYear below 0.0005 ppm/yr [fraction/yr]", *a,
                     thr::TB_AGE_OCXO_IMP_LO));
        else if (*a < thr::TB_AGE_OCXO_SUS_LO || *a > thr::TB_AGE_OCXO_SUS_HI)
            emit(out, ctx, "TB_OSC_AGING", Severity::Suspicious, *a, 0,
                 fmt("OCXO agingPerYear outside the typical 0.01..1 ppm/yr [fraction/yr]", *a));
    } else if (tech == "mems") {
        if (*a < thr::TB_AGE_MEMS_IMP_LO)
            emit(out, ctx, "TB_OSC_AGING", Severity::Impossible, *a, thr::TB_AGE_MEMS_IMP_LO,
                 fmt("MEMS agingPerYear below 0.01 ppm/yr [fraction/yr]", *a,
                     thr::TB_AGE_MEMS_IMP_LO));
        else if (*a < thr::TB_AGE_MEMS_SUS_LO)
            emit(out, ctx, "TB_OSC_AGING", Severity::Suspicious, *a, thr::TB_AGE_MEMS_SUS_LO,
                 fmt("MEMS agingPerYear below the catalog floor [fraction/yr]", *a,
                     thr::TB_AGE_MEMS_SUS_LO));
    }
}

// --- oscillator: RMS phase jitter (0 = vendor-CSV missing) -------------------
void check_osc_jitter(const json& elec, const std::string& tech, const Ctx& ctx,
                      std::vector<Finding>& out) {
    auto j = scalar_at(elec, {"rmsPhaseJitter"});
    if (!j || *j == 0.0) return;  // exactly 0 = missing data, not a value
    if (tech == "siliconrc") {
        if (*j < thr::TB_JIT_SIRC_IMP)
            emit(out, ctx, "TB_OSC_JITTER", Severity::Impossible, *j, thr::TB_JIT_SIRC_IMP,
                 fmt("silicon-RC rmsPhaseJitter below 500 fs [s]", *j, thr::TB_JIT_SIRC_IMP));
        else if (*j < thr::TB_JIT_SIRC_SUS)
            emit(out, ctx, "TB_OSC_JITTER", Severity::Suspicious, *j, thr::TB_JIT_SIRC_SUS,
                 fmt("silicon-RC rmsPhaseJitter below 5 ps [s]", *j, thr::TB_JIT_SIRC_SUS));
        return;
    }
    if (*j < thr::TB_JIT_IMP)
        emit(out, ctx, "TB_OSC_JITTER", Severity::Impossible, *j, thr::TB_JIT_IMP,
             fmt("rmsPhaseJitter below the 5 fs thermal floor [s]", *j, thr::TB_JIT_IMP));
    else if (*j < thr::TB_JIT_SUS)
        emit(out, ctx, "TB_OSC_JITTER", Severity::Suspicious, *j, thr::TB_JIT_SUS,
             fmt("rmsPhaseJitter below the 25 fs best-in-class floor [s]", *j, thr::TB_JIT_SUS));
    else {
        const json* ot = at(elec, "outputType");
        if (ot != nullptr && ot->is_string() && ot->get<std::string>() == "cmos" &&
            *j < thr::TB_JIT_CMOS_SUS)
            emit(out, ctx, "TB_OSC_JITTER", Severity::Suspicious, *j, thr::TB_JIT_CMOS_SUS,
                 fmt("sub-100 fs jitter claimed on a single-ended CMOS output [s]", *j,
                     thr::TB_JIT_CMOS_SUS));
    }
}

// --- oscillator: startup / warm-up time by class (0 = vendor-CSV missing) ----
void check_osc_startup(const json& elec, const std::string& tech, const Ctx& ctx,
                       std::vector<Finding>& out) {
    auto t = scalar_at(elec, {"startupTime"});
    if (!t || *t == 0.0) return;  // exactly 0 = missing data, not a value
    if (tech == "ocxo") {
        if (*t < thr::TB_START_OCXO_IMP)
            emit(out, ctx, "TB_OSC_STARTUP", Severity::Impossible, *t, thr::TB_START_OCXO_IMP,
                 fmt("OCXO warm-up under 1 s (the oven cannot settle) [s]", *t,
                     thr::TB_START_OCXO_IMP));
        else if (*t < thr::TB_START_OCXO_SUS_LO || *t > thr::TB_START_OCXO_SUS_HI)
            emit(out, ctx, "TB_OSC_STARTUP", Severity::Suspicious, *t, 0,
                 fmt("OCXO warm-up outside the typical 30 s..30 min [s]", *t));
    } else if (tech == "mems") {
        if (*t < thr::TB_START_MEMS_IMP)
            emit(out, ctx, "TB_OSC_STARTUP", Severity::Impossible, *t, thr::TB_START_MEMS_IMP,
                 fmt("MEMS startupTime under 1 us [s]", *t, thr::TB_START_MEMS_IMP));
        else if (*t < thr::TB_START_MEMS_SUS_LO || *t > thr::TB_START_MEMS_SUS_HI)
            emit(out, ctx, "TB_OSC_STARTUP", Severity::Suspicious, *t, 0,
                 fmt("MEMS startupTime outside the typical 100 us..50 ms [s]", *t));
    } else if (tech == "siliconrc") {
        if (*t < thr::TB_START_SIRC_IMP)
            emit(out, ctx, "TB_OSC_STARTUP", Severity::Impossible, *t, thr::TB_START_SIRC_IMP,
                 fmt("silicon-RC startupTime under 100 ns [s]", *t, thr::TB_START_SIRC_IMP));
        else if (*t > thr::TB_START_SIRC_SUS_HI)
            emit(out, ctx, "TB_OSC_STARTUP", Severity::Suspicious, *t, thr::TB_START_SIRC_SUS_HI,
                 fmt("silicon-RC startupTime over 10 ms [s]", *t, thr::TB_START_SIRC_SUS_HI));
    } else if (tech == "crystaloscillator" || tech == "vcxo" || tech == "tcxo") {
        // Quartz-based packaged oscillators; the tuning-fork kHz class rings up
        // orders slower than the MHz AT-cut class.
        auto f = scalar_at(elec, {"frequency"});
        if (f && *f < thr::TB_START_KHZ_F) {
            if (*t < thr::TB_START_KHZ_IMP)
                emit(out, ctx, "TB_OSC_STARTUP", Severity::Impossible, *t, thr::TB_START_KHZ_IMP,
                     fmt("kHz tuning-fork oscillator starting under 10 ms [s]", *t,
                         thr::TB_START_KHZ_IMP));
            else if (*t < thr::TB_START_KHZ_SUS_LO || *t > thr::TB_START_KHZ_SUS_HI)
                emit(out, ctx, "TB_OSC_STARTUP", Severity::Suspicious, *t, 0,
                     fmt("kHz tuning-fork startupTime outside 0.1..5 s [s]", *t));
        } else if (f) {
            if (*t < thr::TB_START_XO_IMP)
                emit(out, ctx, "TB_OSC_STARTUP", Severity::Impossible, *t, thr::TB_START_XO_IMP,
                     fmt("quartz MHz oscillator starting under 50 us [s]", *t,
                         thr::TB_START_XO_IMP));
            else if (*t < thr::TB_START_XO_SUS_LO || *t > thr::TB_START_XO_SUS_HI)
                emit(out, ctx, "TB_OSC_STARTUP", Severity::Suspicious, *t, 0,
                     fmt("quartz MHz oscillator startupTime outside 200 us..100 ms [s]", *t));
        }
    }
    // Bare resonators and programmable (mixed resonator classes): no band.
}

// --- oscillator: supply/power coherence ---------------------------------------
void check_osc_supply(const json& elec, const std::string& tech, const Ctx& ctx,
                      std::vector<Finding>& out) {
    const json* supply = at(elec, "supply");
    const bool has_enable = elec.is_object() && elec.contains("enableFunction");

    if (is_bare_resonator(tech)) {
        // A bare resonator has no active circuit: carrying supply or an
        // enable-pin function is a contradictory record (likely misfiled).
        if (supply != nullptr || has_enable)
            emit(out, ctx, "TB_OSC_RESONATOR_SUPPLY", Severity::Suspicious, 0, 0,
                 "bare resonator (" +
                     std::string(tech == "quartzcrystal" ? "quartzCrystal"
                                                         : "ceramicResonator") +
                     ") carries supply/enableFunction — contradictory record");
        return;
    }

    if (supply == nullptr) return;
    auto vmin = scalar_at(*supply, {"minimumSupplyVoltage"});
    auto vmax = scalar_at(*supply, {"maximumSupplyVoltage"});
    if (vmin && vmax && *vmin > *vmax)
        emit(out, ctx, "TB_OSC_SUPPLY", Severity::Impossible, *vmin, *vmax,
             fmt("minimumSupplyVoltage > maximumSupplyVoltage", *vmin, *vmax));

    if (auto i = scalar_at(*supply, {"currentConsumption"})) {
        if (*i < thr::TB_OSC_I_MIN_IMP)
            emit(out, ctx, "TB_OSC_SUPPLY", Severity::Impossible, *i, thr::TB_OSC_I_MIN_IMP,
                 fmt("packaged oscillator drawing under 0.1 uA [A]", *i, thr::TB_OSC_I_MIN_IMP));
        else if (tech == "ocxo" && *i < thr::TB_OCXO_I_IMP)
            emit(out, ctx, "TB_OSC_SUPPLY", Severity::Impossible, *i, thr::TB_OCXO_I_IMP,
                 fmt("OCXO steady-state current under 30 mA cannot keep an oven hot [A]", *i,
                     thr::TB_OCXO_I_IMP));
    }
    if (tech == "ocxo") {
        if (auto w = scalar_at(*supply, {"warmupPower"})) {
            if (*w < thr::TB_OCXO_WARMUP_IMP)
                emit(out, ctx, "TB_OSC_SUPPLY", Severity::Impossible, *w,
                     thr::TB_OCXO_WARMUP_IMP,
                     fmt("OCXO warmupPower under 100 mW [W]", *w, thr::TB_OCXO_WARMUP_IMP));
            else if (*w < thr::TB_OCXO_WARMUP_SUS_LO || *w > thr::TB_OCXO_WARMUP_SUS_HI)
                emit(out, ctx, "TB_OSC_SUPPLY", Severity::Suspicious, *w, 0,
                     fmt("OCXO warmupPower outside the typical 0.5..10 W [W]", *w));
        }
    }
}

// --- oscillator: pull range (VCXO / MEMS DCXO) --------------------------------
void check_osc_pull(const json& elec, const std::string& tech, const Ctx& ctx,
                    std::vector<Finding>& out) {
    auto p = scalar_at(elec, {"pullRange"});
    if (!p) return;
    if (tech == "vcxo") {
        if (*p > thr::TB_PULL_VCXO_IMP)
            emit(out, ctx, "TB_OSC_PULL_RANGE", Severity::Impossible, *p, thr::TB_PULL_VCXO_IMP,
                 fmt("quartz VCXO pullRange above 1000 ppm [fraction]", *p,
                     thr::TB_PULL_VCXO_IMP));
        else if (*p > thr::TB_PULL_VCXO_SUS_HI || *p < thr::TB_PULL_VCXO_SUS_LO)
            emit(out, ctx, "TB_OSC_PULL_RANGE", Severity::Suspicious, *p, 0,
                 fmt("quartz VCXO pullRange outside the typical 10..200 ppm [fraction]", *p));
    } else if (tech == "mems") {
        if (*p > thr::TB_PULL_MEMS_IMP)
            emit(out, ctx, "TB_OSC_PULL_RANGE", Severity::Impossible, *p, thr::TB_PULL_MEMS_IMP,
                 fmt("MEMS pullRange above 3200 ppm [fraction]", *p, thr::TB_PULL_MEMS_IMP));
        else if (*p > thr::TB_PULL_MEMS_SUS)
            emit(out, ctx, "TB_OSC_PULL_RANGE", Severity::Suspicious, *p, thr::TB_PULL_MEMS_SUS,
                 fmt("MEMS pullRange above the SiT3907 DCXO ceiling [fraction]", *p,
                     thr::TB_PULL_MEMS_SUS));
    }
}

// --- oscillator: output type vs technology / frequency ------------------------
void check_osc_output(const json& elec, const std::string& tech, const Ctx& ctx,
                      std::vector<Finding>& out) {
    const json* ot = at(elec, "outputType");
    const bool bare = is_bare_resonator(tech);
    if (ot == nullptr || !ot->is_string()) return;
    const std::string o = ot->get<std::string>();
    auto f = scalar_at(elec, {"frequency"});

    if (o == "none") {
        if (!bare && !tech.empty())
            emit(out, ctx, "TB_OSC_OUTPUT_TYPE", Severity::Suspicious, 0, 0,
                 "packaged oscillator (" + tech + ") with outputType none");
        return;
    }
    if (bare) {
        emit(out, ctx, "TB_OSC_OUTPUT_TYPE", Severity::Suspicious, 0, 0,
             "bare resonator with an active outputType (" + o + ")");
        return;
    }
    if (o == "cmos" && f && *f > 0) {
        if (*f > thr::TB_CMOS_F_IMP)
            emit(out, ctx, "TB_OSC_OUTPUT_TYPE", Severity::Impossible, *f, thr::TB_CMOS_F_IMP,
                 fmt("single-ended CMOS output above 500 MHz [Hz]", *f, thr::TB_CMOS_F_IMP));
        else if (*f > thr::TB_CMOS_F_SUS)
            emit(out, ctx, "TB_OSC_OUTPUT_TYPE", Severity::Suspicious, *f, thr::TB_CMOS_F_SUS,
                 fmt("single-ended CMOS output above the 250 MHz format ceiling [Hz]", *f,
                     thr::TB_CMOS_F_SUS));
    }
    if ((o == "lvds" || o == "lvpecl" || o == "hcsl") && f && *f > 0 &&
        *f < thr::TB_DIFF_F_MIN_SUS)
        emit(out, ctx, "TB_OSC_OUTPUT_TYPE", Severity::Suspicious, *f, thr::TB_DIFF_F_MIN_SUS,
             fmt("differential output format (" + o + ") below 1 MHz [Hz]", *f,
                 thr::TB_DIFF_F_MIN_SUS));
}

// --- oscillator: tolerance sanity + the 32.768 kHz watch class ----------------
void check_osc_tolerance(const json& elec, const std::string& tech, const Ctx& ctx,
                         std::vector<Finding>& out) {
    auto tol = scalar_at(elec, {"frequencyTolerance"});
    if (!tol) return;
    if (*tol > thr::TB_TOL_SUS)
        emit(out, ctx, "TB_OSC_TOLERANCE", Severity::Suspicious, *tol, thr::TB_TOL_SUS,
             fmt("frequencyTolerance above 10% [fraction]", *tol, thr::TB_TOL_SUS));
    auto f = scalar_at(elec, {"frequency"});
    if (tech == "quartzcrystal" && f && *f == thr::TB_WATCH_F && *tol < thr::TB_WATCH_TOL_SUS)
        emit(out, ctx, "TB_OSC_WATCH_TOL", Severity::Suspicious, *tol, thr::TB_WATCH_TOL_SUS,
             fmt("32.768 kHz watch crystal tighter than the +/-10/20 ppm class [fraction]", *tol,
                 thr::TB_WATCH_TOL_SUS));
}

}  // namespace

void check_oscillators(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                       std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr || !elec->is_object()) {
        skipped.push_back("TB_OSC_*");
        return;
    }
    const std::string tech = norm_tech(at(*elec, "technology"));
    if (tech.empty()) skipped.push_back("TB_OSC_FREQ_TECH:technology");

    check_osc_frequency(*elec, tech, ctx, out, skipped);
    check_osc_stability(*elec, tech, ctx, out);
    check_osc_aging(*elec, tech, ctx, out);
    check_osc_jitter(*elec, tech, ctx, out);
    check_osc_startup(*elec, tech, ctx, out);
    check_osc_supply(*elec, tech, ctx, out);
    check_osc_pull(*elec, tech, ctx, out);
    check_osc_output(*elec, tech, ctx, out);
    check_osc_tolerance(*elec, tech, ctx, out);
}

void check_timers(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                  std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr || !elec->is_object()) {
        skipped.push_back("TB_TMR_*");
        return;
    }
    const std::string tech = norm_tech(at(*elec, "technology"));
    const bool bip = (tech == "bipolar555");
    const bool cmos = (tech == "cmos555");

    if (auto f = scalar_at(*elec, {"maximumFrequency"})) {
        if (*f <= 0)
            emit(out, ctx, "TB_TMR_FREQ", Severity::Impossible, *f, 0,
                 fmt("maximumFrequency <= 0 [Hz]", *f));
        else if (bip) {
            if (*f > thr::TB_TMR_BIP_F_IMP)
                emit(out, ctx, "TB_TMR_FREQ", Severity::Impossible, *f, thr::TB_TMR_BIP_F_IMP,
                     fmt("bipolar 555 above 5 MHz [Hz]", *f, thr::TB_TMR_BIP_F_IMP));
            else if (*f > thr::TB_TMR_BIP_F_SUS)
                emit(out, ctx, "TB_TMR_FREQ", Severity::Suspicious, *f, thr::TB_TMR_BIP_F_SUS,
                     fmt("bipolar 555 above the NE555 500 kHz astable ceiling [Hz]", *f,
                         thr::TB_TMR_BIP_F_SUS));
        } else if (cmos) {
            if (*f > thr::TB_TMR_CMOS_F_IMP)
                emit(out, ctx, "TB_TMR_FREQ", Severity::Impossible, *f, thr::TB_TMR_CMOS_F_IMP,
                     fmt("CMOS 555 above 10 MHz [Hz]", *f, thr::TB_TMR_CMOS_F_IMP));
            else if (*f > thr::TB_TMR_CMOS_F_SUS)
                emit(out, ctx, "TB_TMR_FREQ", Severity::Suspicious, *f, thr::TB_TMR_CMOS_F_SUS,
                     fmt("CMOS 555 above the LMC555-class 3 MHz ceiling [Hz]", *f,
                         thr::TB_TMR_CMOS_F_SUS));
        }
    }

    if (const json* supply = at(*elec, "supply")) {
        auto vmin = scalar_at(*supply, {"minimumSupplyVoltage"});
        auto vmax = scalar_at(*supply, {"maximumSupplyVoltage"});
        if (vmin && vmax && *vmin > *vmax)
            emit(out, ctx, "TB_TMR_SUPPLY", Severity::Impossible, *vmin, *vmax,
                 fmt("minimumSupplyVoltage > maximumSupplyVoltage", *vmin, *vmax));
        auto window = [&](double imp_lo, double imp_hi, double sus_lo, double sus_hi,
                          const char* cls) {
            const bool imp = (vmin && *vmin < imp_lo) || (vmax && *vmax > imp_hi);
            const bool sus = (vmin && *vmin < sus_lo) || (vmax && *vmax > sus_hi);
            const double offending =
                (vmin && (*vmin < imp_lo || *vmin < sus_lo)) ? *vmin : (vmax ? *vmax : 0.0);
            if (imp)
                emit(out, ctx, "TB_TMR_SUPPLY", Severity::Impossible, offending, 0,
                     fmt(std::string(cls) + " supply window outside the process limits [V]",
                         offending));
            else if (sus)
                emit(out, ctx, "TB_TMR_SUPPLY", Severity::Suspicious, offending, 0,
                     fmt(std::string(cls) + " supply window outside the datasheet class [V]",
                         offending));
        };
        if (bip)
            window(thr::TB_TMR_BIP_V_IMP_LO, thr::TB_TMR_BIP_V_IMP_HI, thr::TB_TMR_BIP_V_SUS_LO,
                   thr::TB_TMR_BIP_V_SUS_HI, "bipolar 555");
        else if (cmos)
            window(thr::TB_TMR_CMOS_V_IMP_LO, thr::TB_TMR_CMOS_V_IMP_HI,
                   thr::TB_TMR_CMOS_V_SUS_LO, thr::TB_TMR_CMOS_V_SUS_HI, "CMOS 555");
    }

    if (auto acc = scalar_at(*elec, {"timingAccuracy"})) {
        if (bip || cmos) {
            const double sus_lo = bip ? thr::TB_TMR_BIP_ACC_SUS_LO : thr::TB_TMR_CMOS_ACC_SUS_LO;
            if (*acc < thr::TB_TMR_ACC_IMP_LO)
                emit(out, ctx, "TB_TMR_ACCURACY", Severity::Impossible, *acc,
                     thr::TB_TMR_ACC_IMP_LO,
                     fmt("555-class timingAccuracy below 0.1% (RC timing cannot) [fraction]",
                         *acc, thr::TB_TMR_ACC_IMP_LO));
            else if (*acc < sus_lo || *acc > thr::TB_TMR_ACC_SUS_HI)
                emit(out, ctx, "TB_TMR_ACCURACY", Severity::Suspicious, *acc, 0,
                     fmt("555-class timingAccuracy outside the datasheet class [fraction]",
                         *acc));
        }
    }

    if (auto nch = scalar_at(*elec, {"numberOfChannels"}))
        if (*nch > thr::TB_TMR_CH_SUS)
            emit(out, ctx, "TB_TMR_CHANNELS", Severity::Suspicious, *nch, thr::TB_TMR_CH_SUS,
                 fmt("more than 4 timers per package", *nch, thr::TB_TMR_CH_SUS));
}

void check_latches(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                   std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr || !elec->is_object()) {
        skipped.push_back("TB_LATCH_*");
        return;
    }
    if (auto tpd = scalar_at(*elec, {"propagationDelay"})) {
        if (*tpd < thr::TB_LATCH_TPD_IMP)
            emit(out, ctx, "TB_LATCH_TPD", Severity::Impossible, *tpd, thr::TB_LATCH_TPD_IMP,
                 fmt("propagationDelay below 100 ps (sub-100 ps discrete logic does not exist) "
                     "[s]",
                     *tpd, thr::TB_LATCH_TPD_IMP));
        else if (*tpd < thr::TB_LATCH_TPD_SUS_LO || *tpd > thr::TB_LATCH_TPD_SUS_HI)
            emit(out, ctx, "TB_LATCH_TPD", Severity::Suspicious, *tpd, 0,
                 fmt("propagationDelay outside the 1 ns..1 us discrete-logic band [s]", *tpd));
    }
    if (const json* supply = at(*elec, "supply")) {
        auto vmin = scalar_at(*supply, {"minimumSupplyVoltage"});
        auto vmax = scalar_at(*supply, {"maximumSupplyVoltage"});
        if (vmin && vmax && *vmin > *vmax)
            emit(out, ctx, "TB_LATCH_SUPPLY", Severity::Impossible, *vmin, *vmax,
                 fmt("minimumSupplyVoltage > maximumSupplyVoltage", *vmin, *vmax));
        if ((vmin && *vmin < thr::TB_LATCH_V_SUS_LO) || (vmax && *vmax > thr::TB_LATCH_V_SUS_HI))
            emit(out, ctx, "TB_LATCH_SUPPLY", Severity::Suspicious,
                 vmin && *vmin < thr::TB_LATCH_V_SUS_LO ? *vmin : *vmax, 0,
                 fmt("logic supply window outside 0.5..20 V [V]",
                     vmin && *vmin < thr::TB_LATCH_V_SUS_LO ? *vmin : *vmax));
    }
}

// Light screening of the ideal `behavioral` atom (may be the whole record —
// part-less behavioral documents are schema-valid). Design intent, not physics:
// only unit-slip magnitudes are flagged, always SUSPICIOUS.
void check_time_base_behavioral(const json& behavioral, const Ctx& ctx,
                                std::vector<Finding>& out, std::vector<std::string>& skipped) {
    if (!behavioral.is_object()) {
        skipped.push_back("TB_BEHAVIORAL");
        return;
    }
    if (ctx.component == "oscillator") {
        if (auto f = scalar_at(behavioral, {"frequency"}))
            if (*f > thr::TB_BEH_OSC_F_SUS)
                emit(out, ctx, "TB_BEHAVIORAL", Severity::Suspicious, *f, thr::TB_BEH_OSC_F_SUS,
                     fmt("behavioral oscillator frequency above 10 GHz [Hz]", *f,
                         thr::TB_BEH_OSC_F_SUS));
    } else if (ctx.component == "timer") {
        const json* mode = at(behavioral, "mode");
        if (mode != nullptr && mode->is_string() && mode->get<std::string>() == "monostable")
            if (auto t = scalar_at(behavioral, {"onTime"}))
                if (*t > thr::TB_BEH_TMR_ONTIME_SUS)
                    emit(out, ctx, "TB_BEHAVIORAL", Severity::Suspicious, *t,
                         thr::TB_BEH_TMR_ONTIME_SUS,
                         fmt("behavioral monostable onTime above one hour [s]", *t,
                             thr::TB_BEH_TMR_ONTIME_SUS));
    }
    // latch behavioral: nothing to screen — design intent only.
}

}  // namespace tas
