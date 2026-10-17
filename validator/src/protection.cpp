// SPDX-License-Identifier: MIT
// Circuit-protection physics checks (EMAS protection.json). `datasheet` is the
// datasheetInfo object: part.{family,formFactor}, electrical (the family's own
// block, selected by part.family), thermal, mechanical.
//
// part.family is a pinned discriminator over a closed oneOf, so each family has
// its own electrical block and its own checks: fuse, resettableFuse (PPTC),
// gasDischargeTube and sparkGap (both carry the sparkover block),
// surgeProtectiveDevice, thermalCutoff, and fuseHolder (no physics check yet: a
// holder's ratings are independent of one another).
//
// Only IMPOSSIBLE checks are implemented: each is an ordering a real part cannot
// violate, so it needs no calibration against live rows. The catalogue had no
// protection.ndjson when this file was written (2026-10-18); the SUSPICIOUS
// screens of the proposal (cold-resistance power, speed vs I2t, GDT insulation
// resistance and capacitance, PPTC voltage ceiling, family vs description) wait
// for live rows to set their thresholds.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <algorithm>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace tas {

namespace {

// An array field: nullptr when absent; MalformedField when present but not an array.
const json* array_at(const json& obj, const char* key, const std::string& label) {
    const json* a = at(obj, key);
    if (a == nullptr || a->is_null()) return nullptr;
    if (!a->is_array()) throw MalformedField(label + " is not an array");
    return a;
}

// A string field: nullopt when absent; MalformedField when present but not a string.
std::optional<std::string> string_at(const json& obj, const char* key, const std::string& label) {
    const json* s = at(obj, key);
    if (s == nullptr || s->is_null()) return std::nullopt;
    if (!s->is_string()) throw MalformedField(label + " is not a string");
    return s->get<std::string>();
}

// One explicit end of a {nominal, minimum, maximum} object. The band checks below
// compare the ends the datasheet PRINTS, so this deliberately does not collapse
// the object to a scalar (no resolver): an absent end is absent, never inferred
// from the others.
std::optional<double> band_end(const json* dim, const char* end, const std::string& label) {
    if (dim == nullptr || dim->is_null()) return std::nullopt;
    if (dim->is_number()) return std::nullopt;  // a bare number has no printed ends
    if (!dim->is_object()) throw MalformedField(label + " is neither a number nor an object");
    return scalar(at(*dim, end), label + "." + end);
}

// ---------------------------------------------------------------- fuse
void check_fuse(const json& e, const Ctx& ctx, std::vector<Finding>& out,
                std::vector<std::string>& skipped) {
    const auto rated = scalar_at(e, {"ratedCurrent"});
    const auto v_ac = scalar_at(e, {"ratedVoltageAc"});
    const auto v_dc = scalar_at(e, {"ratedVoltageDc"});

    // CHECK PRT_FUSE_BREAK_VS_RATED / PRT_FUSE_BREAK_VOLTAGE. A breaking capacity
    // is the largest prospective current the link interrupts at a stated voltage.
    // Below the link's own rated current it would be unable to clear an ordinary
    // overload it is designed to carry (same physics as SWT_INTERRUPT_CAPACITY);
    // above the link's rated voltage for that kind of supply it was tested at a
    // voltage the part is not rated for.
    const json* bcs = array_at(e, "breakingCapacities", "electrical.breakingCapacities");
    if (bcs == nullptr) {
        skipped.push_back("PRT_FUSE_BREAK_VS_RATED");
        skipped.push_back("PRT_FUSE_BREAK_VOLTAGE");
    } else {
        for (std::size_t i = 0; i < bcs->size(); ++i) {
            const json& bc = (*bcs)[i];
            const std::string where = "electrical.breakingCapacities[" + std::to_string(i) + "]";
            if (!bc.is_object()) throw MalformedField(where + " is not an object");
            const auto cur = scalar_at(bc, {"current"});
            if (cur && rated && *cur < *rated)
                emit(out, ctx, "PRT_FUSE_BREAK_VS_RATED", Severity::Impossible, *cur, *rated,
                     fmt(where + ".current [A] is below the fuse's own ratedCurrent", *cur, *rated));
            const auto volt = scalar_at(bc, {"voltage"});
            if (!volt) continue;
            const auto type = string_at(bc, "voltageType", where + ".voltageType");
            std::optional<double> limit;
            if (type && *type == "ac")
                limit = v_ac;
            else if (type && *type == "dc")
                limit = v_dc;
            else if (v_ac && v_dc)  // acDc or unstated: above EVERY stated rating
                limit = std::max(*v_ac, *v_dc);
            else if (v_ac)
                limit = v_ac;
            else if (v_dc)
                limit = v_dc;
            if (!limit) {
                skipped.push_back("PRT_FUSE_BREAK_VOLTAGE");
                continue;
            }
            if (*volt > *limit)
                emit(out, ctx, "PRT_FUSE_BREAK_VOLTAGE", Severity::Impossible, *volt, *limit,
                     fmt(where + ".voltage [V] is above the fuse's rated voltage for that supply "
                                 "type",
                         *volt, *limit));
        }
    }

    // CHECK PRT_FUSE_OPENING_ORDER. Each openingTimes line is the band
    // [minimumTime, maximumTime] the link opens in at currentMultiple x I_n.
    //  (a) On one line the minimum cannot exceed the maximum.
    //  (b) Across lines, more current melts the element sooner -- but the bands
    //      of neighbouring lines OVERLAP on real tables, so "a higher multiple's
    //      maximum exceeds a lower multiple's minimum" is NOT a violation: the
    //      IEC 60127-2 time-lag (T) table allows up to 3 s at 4 x I_n while
    //      requiring at least 0.4 s at 2.75 x I_n, and that rule (the original
    //      proposal's) would condemn every T fuse. What no real link can do is
    //      open, at its FASTEST, at a higher multiple LATER than it opens, at its
    //      SLOWEST, at a lower one: min(I2) > max(I1) for I2 > I1. That is the
    //      only cross-line ordering asserted.
    const json* ots = array_at(e, "openingTimes", "electrical.openingTimes");
    if (ots == nullptr) {
        skipped.push_back("PRT_FUSE_OPENING_ORDER");
    } else {
        // multiple -> {smallest minimumTime, largest maximumTime} stated at it
        std::map<double, std::pair<std::optional<double>, std::optional<double>>> band;
        for (std::size_t i = 0; i < ots->size(); ++i) {
            const json& ot = (*ots)[i];
            const std::string where = "electrical.openingTimes[" + std::to_string(i) + "]";
            if (!ot.is_object()) throw MalformedField(where + " is not an object");
            const auto m = scalar_at(ot, {"currentMultiple"});
            const auto tmin = scalar_at(ot, {"minimumTime"});
            const auto tmax = scalar_at(ot, {"maximumTime"});
            if (tmin && tmax && *tmin > *tmax)
                emit(out, ctx, "PRT_FUSE_OPENING_ORDER", Severity::Impossible, *tmin, *tmax,
                     fmt(where + ".minimumTime [s] exceeds its maximumTime", *tmin, *tmax));
            if (!m) continue;
            auto& b = band[*m];
            if (tmin && (!b.first || *tmin < *b.first)) b.first = tmin;
            if (tmax && (!b.second || *tmax > *b.second)) b.second = tmax;
        }
        // std::map iterates in ascending multiple: compare every lower line with
        // every higher one (tables have a handful of lines, so O(n^2) is nothing).
        for (auto lo = band.begin(); lo != band.end(); ++lo) {
            if (!lo->second.second) continue;
            for (auto hi = std::next(lo); hi != band.end(); ++hi) {
                if (!hi->second.first) continue;
                if (*hi->second.first > *lo->second.second)
                    emit(out, ctx, "PRT_FUSE_OPENING_ORDER", Severity::Impossible,
                         *hi->second.first, *lo->second.second,
                         fmt("openingTimes: the fastest opening at " + std::to_string(hi->first) +
                                 " x I_n is slower than the slowest at " +
                                 std::to_string(lo->first) + " x I_n [s]",
                             *hi->second.first, *lo->second.second));
            }
        }
    }

    // CHECK PRT_FUSE_I2T_ORDER. Clearing I2t = melting (pre-arcing) I2t + arcing
    // I2t, so it cannot be smaller -- but only when both describe the same
    // short-circuit regime. A melting I2t stated at a long pre-arcing time
    // includes the heat conducted out of the element and grows with that time,
    // while the clearing I2t is measured at the breaking test in a few ms; see
    // thr::PRT_I2T_ADIABATIC_TIME_S. So the order is asserted only when the
    // melting I2t time is absent (datasheets that omit it print the nominal,
    // short-time value) or within that adiabatic limit.
    const auto melt = scalar_at(e, {"meltingI2t"});
    const auto clear = scalar_at(e, {"clearingI2t"});
    const auto melt_t = scalar_at(e, {"meltingI2tTime"});
    if (!melt || !clear) {
        skipped.push_back("PRT_FUSE_I2T_ORDER");
    } else if (melt_t && *melt_t > thr::PRT_I2T_ADIABATIC_TIME_S) {
        skipped.push_back("PRT_FUSE_I2T_ORDER");
    } else if (*clear < *melt) {
        emit(out, ctx, "PRT_FUSE_I2T_ORDER", Severity::Impossible, *clear, *melt,
             fmt("clearingI2t [A^2 s] is below the meltingI2t (clearing = melting + arcing)",
                 *clear, *melt));
    }
}

// ---------------------------------------------------------------- PPTC
void check_pptc(const json& e, const Ctx& ctx, std::vector<Finding>& out,
                std::vector<std::string>& skipped) {
    const auto hold = scalar_at(e, {"holdCurrent"});
    const auto trip = scalar_at(e, {"tripCurrent"});

    // CHECK PRT_PPTC_TRIP_HOLD: a PPTC carries I_hold indefinitely and must trip
    // at I_trip, so I_trip > I_hold (datasheets print 1.7-3 x).
    if (hold && trip) {
        if (*trip <= *hold)
            emit(out, ctx, "PRT_PPTC_TRIP_HOLD", Severity::Impossible, *trip, *hold,
                 fmt("tripCurrent [A] is not above holdCurrent", *trip, *hold));
    } else {
        skipped.push_back("PRT_PPTC_TRIP_HOLD");
    }

    // CHECK PRT_PPTC_RES_ORDER: the initial resistance band [R_min, R_max] is
    // ordered, and the post-trip resistance R1max -- the device after it has
    // tripped and cooled, which never fully recovers -- cannot be below the
    // smallest initial resistance.
    const auto rmin = scalar_at(e, {"minimumResistance"});
    const auto rmax = scalar_at(e, {"maximumResistance"});
    const auto r1 = scalar_at(e, {"maximumResistanceAfterTrip"});
    if (rmin && rmax && *rmin > *rmax)
        emit(out, ctx, "PRT_PPTC_RES_ORDER", Severity::Impossible, *rmin, *rmax,
             fmt("minimumResistance [Ohm] exceeds maximumResistance", *rmin, *rmax));
    if (rmin && r1 && *r1 < *rmin)
        emit(out, ctx, "PRT_PPTC_RES_ORDER", Severity::Impossible, *r1, *rmin,
             fmt("maximumResistanceAfterTrip [Ohm] is below minimumResistance", *r1, *rmin));
    if (!rmin || (!rmax && !r1)) skipped.push_back("PRT_PPTC_RES_ORDER");

    // CHECK PRT_PPTC_FAULT_VS_HOLD: I_max is the largest fault current the device
    // survives WHILE tripping; a fault smaller than I_trip never trips it, so an
    // I_max below I_trip is not a fault rating at all.
    const auto imax = scalar_at(e, {"maximumFaultCurrent"});
    if (imax && trip) {
        if (*imax < *trip)
            emit(out, ctx, "PRT_PPTC_FAULT_VS_HOLD", Severity::Impossible, *imax, *trip,
                 fmt("maximumFaultCurrent [A] is below tripCurrent", *imax, *trip));
    } else {
        skipped.push_back("PRT_PPTC_FAULT_VS_HOLD");
    }

    // CHECK PRT_PPTC_TTT_CURRENT: the time-to-trip is stated at a fault current
    // that trips the device; below I_trip it is not guaranteed to trip at all.
    const json* ttt = at(e, "timeToTrip");
    const auto ttt_i = ttt != nullptr ? scalar_at(*ttt, {"current"}) : std::nullopt;
    if (ttt_i && trip) {
        if (*ttt_i < *trip)
            emit(out, ctx, "PRT_PPTC_TTT_CURRENT", Severity::Impossible, *ttt_i, *trip,
                 fmt("timeToTrip.current [A] is below tripCurrent", *ttt_i, *trip));
    } else {
        skipped.push_back("PRT_PPTC_TTT_CURRENT");
    }
}

// ---------------------------------------------------------------- GDT / spark gap
void check_sparkover(const json& e, const Ctx& ctx, std::vector<Finding>& out,
                     std::vector<std::string>& skipped) {
    const json* dc = at(e, "dcSparkoverVoltage");
    const auto vmin = band_end(dc, "minimum", "electrical.dcSparkoverVoltage");
    const auto vnom = band_end(dc, "nominal", "electrical.dcSparkoverVoltage");
    const auto vmax = band_end(dc, "maximum", "electrical.dcSparkoverVoltage");

    // CHECK PRT_GDT_SPARKOVER_BAND: the printed DC sparkover tolerance band is
    // ordered, minimum <= nominal <= maximum.
    bool band_bad = false;
    double bv = 0.0, bt = 0.0;
    if (vmin && vmax && *vmin > *vmax) { band_bad = true; bv = *vmin; bt = *vmax; }
    else if (vmin && vnom && *vnom < *vmin) { band_bad = true; bv = *vnom; bt = *vmin; }
    else if (vmax && vnom && *vnom > *vmax) { band_bad = true; bv = *vnom; bt = *vmax; }
    if (band_bad)
        emit(out, ctx, "PRT_GDT_SPARKOVER_BAND", Severity::Impossible, bv, bt,
             fmt("dcSparkoverVoltage band is not ordered minimum <= nominal <= maximum [V]", bv,
                 bt));
    if (static_cast<int>(vmin.has_value()) + vnom.has_value() + vmax.has_value() < 2)
        skipped.push_back("PRT_GDT_SPARKOVER_BAND");

    // CHECK PRT_GDT_IMPULSE_VS_DC: a fast ramp breaks the gas down LATER, at a
    // higher voltage, than the slow DC ramp (the formative delay), so no impulse
    // sparkover can sit below the DC sparkover's guaranteed minimum. Only the
    // printed minimum is used: a nominal is not a bound.
    const json* imp = array_at(e, "impulseSparkoverVoltages", "electrical.impulseSparkoverVoltages");
    if (imp == nullptr || !vmin) {
        skipped.push_back("PRT_GDT_IMPULSE_VS_DC");
    } else {
        for (std::size_t i = 0; i < imp->size(); ++i) {
            const json& line = (*imp)[i];
            const std::string where =
                "electrical.impulseSparkoverVoltages[" + std::to_string(i) + "]";
            if (!line.is_object()) throw MalformedField(where + " is not an object");
            const auto v = scalar_at(line, {"voltage"});
            if (v && *v < *vmin)
                emit(out, ctx, "PRT_GDT_IMPULSE_VS_DC", Severity::Impossible, *v, *vmin,
                     fmt(where + ".voltage [V] is below the DC sparkover minimum", *v, *vmin));
        }
    }

    // CHECK PRT_GDT_ARC_GLOW: after sparkover the discharge passes from glow to
    // arc as the current rises, and the arc burns at a lower voltage than the
    // glow (tens of volts against roughly 60-200 V). The glow in turn sits below
    // the voltage that started the discharge -- compared with the printed DC
    // sparkover MINIMUM only, the one value that is a bound.
    const auto arc = scalar_at(e, {"arcVoltage"});
    const auto glow = scalar_at(e, {"glowVoltage"});
    if (arc && glow && *arc >= *glow)
        emit(out, ctx, "PRT_GDT_ARC_GLOW", Severity::Impossible, *arc, *glow,
             fmt("arcVoltage [V] is not below glowVoltage", *arc, *glow));
    if (glow && vmin && *glow >= *vmin)
        emit(out, ctx, "PRT_GDT_ARC_GLOW", Severity::Impossible, *glow, *vmin,
             fmt("glowVoltage [V] is not below the DC sparkover minimum", *glow, *vmin));
    if (!glow || (!arc && !vmin)) skipped.push_back("PRT_GDT_ARC_GLOW");

    // CHECK PRT_GDT_PULSE_ORDER: within ONE waveform, a larger surge wears the
    // electrodes faster, so the device cannot survive MORE operations at a
    // HIGHER current than at a lower one. Different waveforms are never compared
    // (10/700 and 5/320 are separate labels by design, see PEAS surgeWaveform).
    const json* pulses = array_at(e, "impulseDischargeCurrents", "electrical.impulseDischargeCurrents");
    if (pulses == nullptr) {
        skipped.push_back("PRT_GDT_PULSE_ORDER");
    } else {
        struct P { std::string wf; double i; double n; };
        std::vector<P> ps;
        for (std::size_t k = 0; k < pulses->size(); ++k) {
            const json& line = (*pulses)[k];
            const std::string where = "electrical.impulseDischargeCurrents[" + std::to_string(k) + "]";
            if (!line.is_object()) throw MalformedField(where + " is not an object");
            const auto wf = string_at(line, "waveform", where + ".waveform");
            const auto i = scalar_at(line, {"current"});
            const auto n = scalar_at(line, {"operations"});
            if (wf && i && n) ps.push_back({*wf, *i, *n});
        }
        for (const auto& a : ps)
            for (const auto& b : ps)
                if (a.wf == b.wf && b.i > a.i && b.n > a.n)
                    emit(out, ctx, "PRT_GDT_PULSE_ORDER", Severity::Impossible, b.n, a.n,
                         fmt("impulseDischargeCurrents at " + a.wf + ": more operations at " +
                                 std::to_string(b.i) + " A than at " + std::to_string(a.i) + " A",
                             b.n, a.n));
    }
}

// ---------------------------------------------------------------- SPD module
void check_spd(const json& e, const Ctx& ctx, std::vector<Finding>& out,
               std::vector<std::string>& skipped) {
    // CHECK PRT_SPD_IMAX_IN: IEC 61643-11 I_max is the largest single 8/20 surge
    // the module survives, I_n the one it survives repeatedly (15 times), so at
    // the same waveform I_max >= I_n.
    const json* in = at(e, "nominalDischargeCurrent");
    const json* im = at(e, "maximumDischargeCurrent");
    if (in != nullptr && im != nullptr && in->is_object() && im->is_object()) {
        const auto i_n = scalar_at(*in, {"current"});
        const auto i_m = scalar_at(*im, {"current"});
        const auto wf_n = string_at(*in, "waveform", "electrical.nominalDischargeCurrent.waveform");
        const auto wf_m = string_at(*im, "waveform", "electrical.maximumDischargeCurrent.waveform");
        if (i_n && i_m && wf_n && wf_m && *wf_n == *wf_m) {
            if (*i_m < *i_n)
                emit(out, ctx, "PRT_SPD_IMAX_IN", Severity::Impossible, *i_m, *i_n,
                     fmt("maximumDischargeCurrent [A] is below nominalDischargeCurrent at the "
                         "same waveform",
                         *i_m, *i_n));
        } else {
            skipped.push_back("PRT_SPD_IMAX_IN");
        }
    } else {
        skipped.push_back("PRT_SPD_IMAX_IN");
    }

    // CHECK PRT_SPD_UP_UC: the protection level U_p is the residual voltage the
    // module lets through while conducting a surge; it must clear the highest
    // continuous voltage U_c (whose AC peak is sqrt(2) higher still), or the
    // module would conduct on the mains it sits across.
    const auto up = scalar_at(e, {"voltageProtectionLevel"});
    const auto uc = scalar_at(e, {"maximumContinuousOperatingVoltage"});
    if (up && uc) {
        if (*up <= *uc)
            emit(out, ctx, "PRT_SPD_UP_UC", Severity::Impossible, *up, *uc,
                 fmt("voltageProtectionLevel [V] is not above maximumContinuousOperatingVoltage",
                     *up, *uc));
    } else {
        skipped.push_back("PRT_SPD_UP_UC");
    }
}

// ---------------------------------------------------------------- thermal cut-off
void check_tco(const json& e, const Ctx& ctx, std::vector<Finding>& out,
               std::vector<std::string>& skipped) {
    const auto tf = scalar_at(e, {"ratedFunctioningTemperature"});
    const auto th = scalar_at(e, {"holdingTemperature"});
    const auto tm = scalar_at(e, {"maximumTemperatureLimit"});

    // CHECK PRT_TCO_HOLD_VS_TF (IEC 60691): T_h is the highest temperature the
    // device holds for 168 h WITHOUT opening, so it lies below the rated
    // functioning temperature T_f at which it opens.
    if (tf && th) {
        if (*th >= *tf)
            emit(out, ctx, "PRT_TCO_HOLD_VS_TF", Severity::Impossible, *th, *tf,
                 fmt("holdingTemperature [degC] is not below ratedFunctioningTemperature", *th,
                     *tf));
    } else {
        skipped.push_back("PRT_TCO_HOLD_VS_TF");
    }

    // CHECK PRT_TCO_TM_VS_TF (IEC 60691): T_m is the temperature up to which the
    // OPENED device stays open without re-closing or breaking down, so it is at
    // or above T_f. (Optifuse K10B, SEFUSE SF/R and Multicomp BTT rows all print
    // T_h < T_f <= T_m.)
    if (tf && tm) {
        if (*tm < *tf)
            emit(out, ctx, "PRT_TCO_TM_VS_TF", Severity::Impossible, *tm, *tf,
                 fmt("maximumTemperatureLimit [degC] is below ratedFunctioningTemperature", *tm,
                     *tf));
    } else {
        skipped.push_back("PRT_TCO_TM_VS_TF");
    }
}

}  // namespace

void check_protection(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                      std::vector<std::string>& skipped) {
    const json* part = at(datasheet, "part");
    const auto family =
        part != nullptr ? string_at(*part, "family", "part.family") : std::nullopt;
    if (!family) {
        // The schema requires part.family once a part is described; without it the
        // electrical block cannot be read (each family has its own). Missing data
        // skips, it never reads as valid.
        skipped.push_back("PRT_*");
        return;
    }
    const json* e = at(datasheet, "electrical");
    if (e == nullptr || !e->is_object()) {
        skipped.push_back("PRT_*");
        return;
    }
    if (*family == "fuse")
        check_fuse(*e, ctx, out, skipped);
    else if (*family == "resettableFuse")
        check_pptc(*e, ctx, out, skipped);
    else if (*family == "gasDischargeTube" || *family == "sparkGap")
        check_sparkover(*e, ctx, out, skipped);
    else if (*family == "surgeProtectiveDevice")
        check_spd(*e, ctx, out, skipped);
    else if (*family == "thermalCutoff")
        check_tco(*e, ctx, out, skipped);
    else if (*family == "fuseHolder")
        skipped.push_back("PRT_HOLDER_*");  // no ordering between a holder's ratings
    else
        throw MalformedField("part.family '" + *family + "' is not an EMAS protection family");
}

}  // namespace tas
