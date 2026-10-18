// SPDX-License-Identifier: MIT
// Thyristor physics checks (SAS thyristor.json): SCR, triac, sidac, diac and the
// thyristor surge protective device (TSPD). `datasheet` is the datasheetInfo
// object: part.subType, then EITHER one package-level electrical block OR a
// dies[] array whose entries each carry their own electrical block (a dual or
// array protector). The same checks run on every electrical block found.
//
// Only IMPOSSIBLE checks are implemented: each follows from a definition or an
// inequality no real device can violate, so none needs calibration against live
// rows. TAS had no thyristors.ndjson when this file was written (2026-10-18);
// SUSPICIOUS screens (dv/dt vs V_DRM, gate power vs trigger figures, surge vs
// I2t consistency, on-state voltage ranges) wait for live rows to set thresholds.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <algorithm>
#include <cmath>
#include <optional>
#include <string>
#include <vector>

namespace tas {

namespace {

// part.subType / dies[].subType: absent is nullopt; a value outside the SAS enum
// is malformed (no guessing at what a misspelled member was meant to be).
std::optional<std::string> thyristor_subtype(const json& obj, const std::string& label) {
    const auto st = string_at(obj, "subType", label);
    if (!st) return std::nullopt;
    static const char* KNOWN[] = {"scr", "triac", "sidac", "diac", "surgeProtector"};
    for (const char* k : KNOWN)
        if (*st == k) return st;
    throw MalformedField(label + " '" + *st + "' is not a SAS thyristor subType");
}

void check_electrical(const json& e, const std::string& where,
                      const std::optional<std::string>& subtype, const Ctx& ctx,
                      std::vector<Finding>& out, std::vector<std::string>& skipped) {
    if (!e.is_object()) throw MalformedField(where + " is not an object");

    // CHECK THY_DIRECTION_SUBTYPE. conductionDirection is inherent to three
    // members (the schema's own definition): an SCR blocks in reverse by
    // construction (one p-n-p-n path, anode to cathode), and a triac and a diac
    // are by definition the bidirectional members. A sidac or a surge protector
    // comes in both and is not judged.
    const auto dir = string_at(e, "conductionDirection", where + ".conductionDirection");
    if (dir && subtype) {
        const char* inherent = nullptr;
        if (*subtype == "scr")
            inherent = "unidirectional";
        else if (*subtype == "triac" || *subtype == "diac")
            inherent = "bidirectional";
        if (inherent != nullptr && *dir != inherent)
            emit(out, ctx, "THY_DIRECTION_SUBTYPE", Severity::Impossible, 0, 0,
                 where + ".conductionDirection is '" + *dir + "' on a " + *subtype +
                     ", which is " + inherent + " by definition");
    } else {
        skipped.push_back("THY_DIRECTION_SUBTYPE");
    }

    // CHECK THY_BLOCKING_POSITIVE. V_DRM and V_RRM are magnitudes of the voltage
    // the device blocks; a zero or negative blocking rating is not a rating.
    // (The schema's exclusiveMinimum says the same; this guards records handed
    // to the validator without schema validation.)
    const auto vdrm = scalar_at(e, {"repetitivePeakOffStateVoltage"});
    const auto vrrm = scalar_at(e, {"repetitivePeakReverseVoltage"});
    if (vdrm && *vdrm <= 0)
        emit(out, ctx, "THY_BLOCKING_POSITIVE", Severity::Impossible, *vdrm, 0,
             fmt(where + ".repetitivePeakOffStateVoltage [V] is not positive", *vdrm));
    if (vrrm && *vrrm <= 0)
        emit(out, ctx, "THY_BLOCKING_POSITIVE", Severity::Impossible, *vrrm, 0,
             fmt(where + ".repetitivePeakReverseVoltage [V] is not positive", *vrrm));
    if (!vdrm && !vrrm) skipped.push_back("THY_BLOCKING_POSITIVE");

    // CHECK THY_AV_VS_RMS. For ANY current waveform the mean of |i| cannot exceed
    // its RMS (Cauchy-Schwarz: (mean|i|)^2 <= mean(i^2)); for the half-sine the
    // ratings are defined on, I_AV = I_pk/pi and I_RMS = I_pk/2, so I_T(AV) =
    // (2/pi) x I_T(RMS) = 0.64 x I_T(RMS) (BT151: 7.5 A vs 12 A). Both are rated
    // at the one
    // onStateCurrentTemperature, so an average above the RMS is impossible.
    const auto irms = scalar_at(e, {"rmsOnStateCurrent"});
    const auto iav = scalar_at(e, {"averageOnStateCurrent"});
    if (irms && iav) {
        if (*iav > *irms)
            emit(out, ctx, "THY_AV_VS_RMS", Severity::Impossible, *iav, *irms,
                 fmt(where + ".averageOnStateCurrent [A] exceeds rmsOnStateCurrent", *iav,
                     *irms));
    } else {
        skipped.push_back("THY_AV_VS_RMS");
    }

    // CHECK THY_SURGE_VS_CONTINUOUS. I_TSM is the PEAK of a non-repetitive surge
    // the device survives on top of rated operation; within one line cycle
    // (thr::THY_SURGE_CYCLE_MAX_S) it is many times the continuous rating. Even
    // a sustained sine at I_T(RMS) already peaks at sqrt(2) x I_T(RMS), so a
    // sub-cycle surge peak below I_T(RMS) -- or below I_T(AV), which is smaller
    // still -- would mean the device cannot survive one cycle of the current it
    // carries continuously.
    const json* surges = array_at(e, "surgeOnStateCurrent", where + ".surgeOnStateCurrent");
    if (surges == nullptr || (!irms && !iav)) {
        skipped.push_back("THY_SURGE_VS_CONTINUOUS");
    } else {
        for (std::size_t i = 0; i < surges->size(); ++i) {
            const json& s = (*surges)[i];
            const std::string sw = where + ".surgeOnStateCurrent[" + std::to_string(i) + "]";
            if (!s.is_object()) throw MalformedField(sw + " is not an object");
            const auto cur = scalar_at(s, {"current"});
            const auto dur = scalar_at(s, {"duration"});
            if (!cur || !dur || *dur > thr::THY_SURGE_CYCLE_MAX_S) continue;
            if (irms && *cur < *irms)
                emit(out, ctx, "THY_SURGE_VS_CONTINUOUS", Severity::Impossible, *cur, *irms,
                     fmt(sw + ".current [A] (a sub-cycle surge peak) is below "
                              "rmsOnStateCurrent",
                         *cur, *irms));
            else if (iav && *cur < *iav)
                emit(out, ctx, "THY_SURGE_VS_CONTINUOUS", Severity::Impossible, *cur, *iav,
                     fmt(sw + ".current [A] (a sub-cycle surge peak) is below "
                              "averageOnStateCurrent",
                         *cur, *iav));
        }
    }

    // CHECK THY_GATE_TRIGGER_SIGN. I_GT and V_GT are the gate current and voltage
    // that turn the device on. The package-level figures are an SCR's (gate
    // positive with respect to the cathode, always) or a triac's single figure
    // for all quadrants (a magnitude), so every printed end is > 0. Per-quadrant
    // figures: Q1 and Q4 are the G+ quadrants, so > 0; in Q2 and Q3 the gate is
    // driven negative and a datasheet may print the figure signed, so only zero
    // is impossible there (a device that triggers on no gate drive is not
    // gate-triggered).
    bool any_gate = false;
    for (const char* key : {"gateTriggerCurrent", "gateTriggerVoltage"}) {
        const json* f = at(e, key);
        if (f == nullptr || f->is_null()) continue;
        any_gate = true;
        for (const auto& [end, v] : printed_ends(f, where + "." + key))
            if (v <= 0)
                emit(out, ctx, "THY_GATE_TRIGGER_SIGN", Severity::Impossible, v, 0,
                     fmt(where + "." + key + "." + end + " is not positive", v));
    }
    const json* quads = array_at(e, "quadrantRatings", where + ".quadrantRatings");
    if (quads != nullptr) {
        for (std::size_t i = 0; i < quads->size(); ++i) {
            const json& q = (*quads)[i];
            const std::string qw = where + ".quadrantRatings[" + std::to_string(i) + "]";
            if (!q.is_object()) throw MalformedField(qw + " is not an object");
            const auto quadrant = string_at(q, "quadrant", qw + ".quadrant");
            if (!quadrant) throw MalformedField(qw + ".quadrant is missing");
            bool gate_positive;
            if (*quadrant == "Q1" || *quadrant == "Q4")
                gate_positive = true;
            else if (*quadrant == "Q2" || *quadrant == "Q3")
                gate_positive = false;
            else
                throw MalformedField(qw + ".quadrant '" + *quadrant + "' is not Q1..Q4");
            for (const char* key : {"gateTriggerCurrent", "gateTriggerVoltage"}) {
                const json* f = at(q, key);
                if (f == nullptr || f->is_null()) continue;
                any_gate = true;
                for (const auto& [end, v] : printed_ends(f, qw + "." + key)) {
                    if (gate_positive ? v <= 0 : v == 0)
                        emit(out, ctx, "THY_GATE_TRIGGER_SIGN", Severity::Impossible, v, 0,
                             fmt(qw + "." + key + "." + end +
                                     (gate_positive ? " is not positive in a G+ quadrant"
                                                    : " is zero"),
                                 v));
                }
            }
        }
    }
    if (!any_gate) skipped.push_back("THY_GATE_TRIGGER_SIGN");

    // CHECK THY_HOLD_VS_LATCH. Latching current I_L is the anode current needed
    // to stay on the moment the gate drive is removed; holding current I_H is
    // the current below which an already-latched device turns off. Every device
    // has I_H <= I_L (a device that stays on above I_L but drops out above I_H
    // would have to drop out while latching). Over a population this survives
    // only for ends that bound the same side: a guaranteed lower bound or the
    // typical of I_H above a guaranteed upper bound or the typical of I_L is
    // impossible (every device's I_H >= I_H.min, median I_H <= median I_L <=
    // I_L.max). max-vs-max is NOT compared: two loose guarantees need not be
    // ordered. A triac prints one I_H for both directions and I_L per quadrant,
    // so I_L's upper bound is the largest of the package figure and every
    // quadrant's maximum (as a magnitude -- a Q3 figure may be printed signed).
    const json* ih = at(e, "holdingCurrent");
    std::optional<double> ih_lo;  // the largest lower-or-central end of I_H
    for (const char* end : {"minimum", "nominal"})
        if (const auto v = band_end(ih, end, where + ".holdingCurrent"))
            ih_lo = ih_lo ? std::max(*ih_lo, *v) : *v;
    if (ih != nullptr && ih->is_number()) ih_lo = *scalar(ih, where + ".holdingCurrent");
    std::optional<double> il_hi;  // the largest upper-or-central bound on I_L
    auto take_il = [&](const json* il, const std::string& label, bool nominal_too) {
        if (il != nullptr && il->is_number()) {
            const double v = std::fabs(*scalar(il, label));
            il_hi = il_hi ? std::max(*il_hi, v) : v;
            return;
        }
        for (const char* end : {"maximum", "nominal"}) {
            if (!nominal_too && std::string(end) == "nominal") continue;
            if (const auto v = band_end(il, end, label))
                il_hi = il_hi ? std::max(*il_hi, std::fabs(*v)) : std::fabs(*v);
        }
    };
    take_il(at(e, "latchingCurrent"), where + ".latchingCurrent", true);
    if (quads != nullptr)
        for (std::size_t i = 0; i < quads->size(); ++i)
            // A quadrant's typical is not compared: the median of the per-device
            // maximum over quadrants can exceed every quadrant's own median.
            take_il(at((*quads)[i], "latchingCurrent"),
                    where + ".quadrantRatings[" + std::to_string(i) + "].latchingCurrent",
                    false);
    if (ih_lo && il_hi) {
        if (*ih_lo > *il_hi)
            emit(out, ctx, "THY_HOLD_VS_LATCH", Severity::Impossible, *ih_lo, *il_hi,
                 fmt(where + ".holdingCurrent [A] (its minimum or typical) exceeds every "
                             "printed upper bound of the latching current",
                     *ih_lo, *il_hi));
    } else {
        skipped.push_back("THY_HOLD_VS_LATCH");
    }

    // CHECK THY_BREAKOVER_VS_STANDOFF. V_DRM is the largest off-state voltage the
    // device blocks repetitively without switching; V_(BO) is where it switches
    // on (a protector's clamp, a sidac's trigger, an SCR's forward breakover).
    // Every device therefore breaks over above its V_DRM, and every printed end
    // of the slow-ramp breakoverVoltage -- a guaranteed maximum included -- is at
    // or above V_DRM (SMCP0080SC: 6 V stand-off, 15 V maximum breakover;
    // TISP4350H3BJ: 275 V, 350 V). The impulse breakover is not compared: on a
    // fast ramp an SCR can switch below V_DRM through its dv/dt limit.
    const json* vbo = at(e, "breakoverVoltage");
    if (vdrm && vbo != nullptr && !vbo->is_null()) {
        for (const auto& [end, v] : printed_ends(vbo, where + ".breakoverVoltage"))
            if (v < *vdrm)
                emit(out, ctx, "THY_BREAKOVER_VS_STANDOFF", Severity::Impossible, v, *vdrm,
                     fmt(where + ".breakoverVoltage." + end +
                             " [V] is below repetitivePeakOffStateVoltage",
                         v, *vdrm));
    } else {
        skipped.push_back("THY_BREAKOVER_VS_STANDOFF");
    }
}

}  // namespace

void check_thyristors(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                      std::vector<std::string>& skipped) {
    const json* part = at(datasheet, "part");
    const std::optional<std::string> part_st =
        part != nullptr ? thyristor_subtype(*part, "part.subType") : std::nullopt;

    const json* e = at(datasheet, "electrical");
    const json* dies = array_at(datasheet, "dies", "dies");
    if (e != nullptr && !e->is_null())
        check_electrical(*e, "electrical", part_st, ctx, out, skipped);
    if (dies != nullptr) {
        for (std::size_t i = 0; i < dies->size(); ++i) {
            const json& d = (*dies)[i];
            const std::string where = "dies[" + std::to_string(i) + "]";
            if (!d.is_object()) throw MalformedField(where + " is not an object");
            // A die states its own member only when it differs from the
            // package's; otherwise the part's subType describes it.
            const auto die_st = thyristor_subtype(d, where + ".subType");
            const json* de = at(d, "electrical");
            if (de == nullptr || de->is_null()) {
                skipped.push_back("THY_*:" + where + ":no-electrical");
                continue;
            }
            check_electrical(*de, where + ".electrical", die_st ? die_st : part_st, ctx, out,
                             skipped);
        }
    }
    if ((e == nullptr || e->is_null()) && dies == nullptr) skipped.push_back("THY_*");
}

}  // namespace tas
