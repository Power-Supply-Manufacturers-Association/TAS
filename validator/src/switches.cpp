// SPDX-License-Identifier: MIT
// Switch physics checks (EMAS switch.json). `datasheet` is the datasheetInfo
// object: part.{technology,subType}, electrical.{contacts,isolation,trip},
// mechanical.{actuator*,positions,positionSequence,...}, thermal.
//
// The contacts/isolation half is shared with relays — see contacts.cpp. What is
// switch-specific is the ACTUATOR (positions and the sequence they run in) and,
// for the circuitBreaker technology, the overcurrent TRIP element.
//
// Field presence over the live catalogue (2,914 rows, 2026-09-23):
// contacts 99.5%, contacts.form 75.8%, contacts.ratedVoltage 39.7%,
// contacts.ratedCurrent 26.8%, mechanical.positions 14.2%, trip 6.5%
// (188 circuit breakers, and technology == 'circuitBreaker' on exactly those
// 188 rows), trip.tripCurve 3.7%.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cmath>
#include <map>
#include <string>
#include <vector>

namespace tas {

namespace {

void require_positive(const json& obj, const char* key, const char* label, const Ctx& ctx,
                      std::vector<Finding>& out) {
    auto v = scalar_at(obj, {key});
    if (v && *v <= 0.0)
        emit(out, ctx, "SWT_POSITIVITY", Severity::Impossible, *v, 0,
             std::string(label) + " <= 0");
}

// The published time-current band of an overcurrent trip.
//
// NOTE, and the reason this is not the obvious "time must fall as current
// rises": the live catalogue stores the two EDGES of a band at ONE overload as
// two array entries with the SAME currentMultiple (e.g. 2x -> 4 s and 2x ->
// 40 s, "4 to 40 seconds at 200%"). All 108 rows carrying a tripCurve have that
// shape, so a naive monotonicity test over the raw arrays fires on every one of
// them. Monotonicity is therefore asserted only BETWEEN DISTINCT multiples, and
// between the matching edges of each multiple's band (fastest against fastest,
// slowest against slowest) — which is the property every published curve has and
// the band shape cannot fake.
void check_trip_curve(const json& tc, const Ctx& ctx, std::vector<Finding>& out,
                      std::vector<std::string>& skipped) {
    const json* mult = at(tc, "currentMultiple");
    const json* time = at(tc, "time");
    if (mult == nullptr || time == nullptr || !mult->is_array() || !time->is_array()) {
        skipped.push_back("SWT_TRIP_CURVE");
        return;
    }
    if (mult->size() != time->size()) {
        emit(out, ctx, "SWT_TRIP_CURVE", Severity::Impossible,
             static_cast<double>(mult->size()), static_cast<double>(time->size()),
             "trip.tripCurve.currentMultiple and .time are index-aligned but have different "
             "lengths");
        return;
    }
    // Per distinct overload multiple, the fastest and the slowest trip time.
    std::map<double, std::pair<double, double>> band;  // multiple -> {min t, max t}
    for (std::size_t i = 0; i < mult->size(); ++i) {
        const json& m = (*mult)[i];
        const json& t = (*time)[i];
        if (!m.is_number() || !t.is_number())
            throw MalformedField("trip.tripCurve entry " + std::to_string(i) + " is not numeric");
        const double mv = m.get<double>();
        const double tv = t.get<double>();
        if (mv <= 0.0)
            emit(out, ctx, "SWT_TRIP_CURVE", Severity::Impossible, mv, 0,
                 fmt("trip.tripCurve.currentMultiple <= 0", mv));
        if (tv <= 0.0)
            emit(out, ctx, "SWT_TRIP_CURVE", Severity::Impossible, tv, 0,
                 fmt("trip.tripCurve.time [s] <= 0", tv));
        if (mv <= 0.0 || tv <= 0.0) continue;
        auto it = band.find(mv);
        if (it == band.end())
            band.emplace(mv, std::make_pair(tv, tv));
        else {
            if (tv < it->second.first) it->second.first = tv;
            if (tv > it->second.second) it->second.second = tv;
        }
    }
    if (band.size() < 2) {
        skipped.push_back("SWT_TRIP_CURVE_MONOTONIC");
        return;
    }
    // std::map iterates in ascending key order, so `prev` is always the LOWER
    // overload. Both edges must fall (or hold) as the overload rises: more
    // current through the same bimetal or solenoid never trips it later.
    auto prev = band.begin();
    for (auto cur = std::next(band.begin()); cur != band.end(); ++cur) {
        const bool fast_rises = cur->second.first > prev->second.first;
        const bool slow_rises = cur->second.second > prev->second.second;
        if (fast_rises || slow_rises)
            emit(out, ctx, "SWT_TRIP_CURVE", Severity::Impossible,
                 fast_rises ? cur->second.first : cur->second.second,
                 fast_rises ? prev->second.first : prev->second.second,
                 fmt("trip.tripCurve trips LATER at " + std::to_string(cur->first) +
                         "x rated current than at " + std::to_string(prev->first) +
                         "x [s] — a time-current curve cannot rise with overload",
                     fast_rises ? cur->second.first : cur->second.second,
                     fast_rises ? prev->second.first : prev->second.second));
        prev = cur;
    }
}

}  // namespace

void check_switches(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                    std::vector<std::string>& skipped) {
    const json* mech = at(datasheet, "mechanical");
    if (mech != nullptr && mech->is_object()) {
        for (const auto& kv : {std::pair<const char*, const char*>{"positions",
                                                                   "mechanical.positions"},
                               {"terminalPitch", "mechanical.terminalPitch"},
                               {"terminalCount", "mechanical.terminalCount"},
                               {"angleBetweenPositions", "mechanical.angleBetweenPositions"}})
            require_positive(*mech, kv.first, kv.second, ctx, out);
        for (const char* k : {"actuationForce", "actuationTravel", "actuationTorque"})
            if (auto v = scalar_at(*mech, {k}); v && *v <= 0.0)
                emit(out, ctx, "SWT_POSITIVITY", Severity::Impossible, *v, 0,
                     std::string("mechanical.") + k + " <= 0");

        // CHECK: positionSequence lists the positions IN PHYSICAL ORDER, so it
        // has exactly `positions` entries. A mismatch means one of the two
        // describes a different switch (SUSPICIOUS: the sequence may simply be
        // partially transcribed, which is a provenance defect, not physics).
        const json* seq = at(*mech, "positionSequence");
        auto positions = scalar_at(*mech, {"positions"});
        if (seq != nullptr && seq->is_array() && positions && *positions > 0.0 &&
            static_cast<double>(seq->size()) != *positions)
            emit(out, ctx, "SWT_POSITION_SEQUENCE", Severity::Suspicious,
                 static_cast<double>(seq->size()), *positions,
                 fmt("mechanical.positionSequence lists a different number of positions than "
                     "mechanical.positions",
                     static_cast<double>(seq->size()), *positions));
        else if (seq == nullptr || !positions)
            skipped.push_back("SWT_POSITION_SEQUENCE");
    } else {
        skipped.push_back("SWT_POSITION_SEQUENCE");
    }

    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr || !elec->is_object()) {
        skipped.push_back("SWT_*");
        skipped.push_back("CONTACT_*");
        return;
    }

    // Shared contact-set / isolation physics (contacts.cpp).
    check_contact_block(*elec, ctx, out, skipped);

    const std::string tech = norm_tech(at(datasheet, "part", "technology"));
    const json* trip = at(*elec, "trip");
    const bool has_trip = trip != nullptr && trip->is_object() && !trip->empty();

    // CHECK: the trip element is what makes a circuit breaker a circuit breaker
    // (and what an ordinary switch does not have). A record carrying one label
    // without the other has lost half of itself in extraction. SUSPICIOUS: the
    // two come from separate vendor parametric columns.
    if (tech == "circuitbreaker" && !has_trip)
        emit(out, ctx, "SWT_BREAKER_TRIP", Severity::Suspicious, 0, 0,
             "part.technology is circuitBreaker but electrical.trip — the overcurrent element "
             "that defines a breaker — is absent");
    else if (tech != "circuitbreaker" && !tech.empty() && has_trip)
        emit(out, ctx, "SWT_BREAKER_TRIP", Severity::Suspicious, 0, 0,
             "electrical.trip (an overcurrent trip element) is present but part.technology is '" +
                 tech + "', not circuitBreaker");

    if (!has_trip) {
        skipped.push_back("SWT_TRIP_VS_CONTACT");
        skipped.push_back("SWT_INTERRUPT_CAPACITY");
        skipped.push_back("SWT_TRIP_CURVE");
        return;
    }

    require_positive(*trip, "ratedCurrent", "trip.ratedCurrent", ctx, out);
    require_positive(*trip, "interruptingCapacity", "trip.interruptingCapacity", ctx, out);

    auto trip_I = scalar_at(*trip, {"ratedCurrent"});
    auto icap = scalar_at(*trip, {"interruptingCapacity"});

    // CHECK: interrupting capacity is the largest PROSPECTIVE FAULT current the
    // breaker can safely break. A breaker is required to clear, at minimum, a
    // fault on the circuit it protects — so it is always a large multiple of the
    // handle rating, and can never be below it: a breaker that cannot interrupt
    // its own rated current cannot open under any overload at all.
    if (trip_I && icap && *trip_I > 0.0 && *icap > 0.0 && *icap < *trip_I)
        emit(out, ctx, "SWT_INTERRUPT_CAPACITY", Severity::Impossible, *icap, *trip_I,
             fmt("trip.interruptingCapacity is below trip.ratedCurrent [A] — a breaker that "
                 "cannot interrupt its own handle rating cannot protect anything",
                 *icap, *trip_I));
    else if (!trip_I || !icap)
        skipped.push_back("SWT_INTERRUPT_CAPACITY");

    // CHECK: the breaker's own contacts carry the handle current continuously,
    // so the contact rating cannot be below the trip rating. SUSPICIOUS rather
    // than IMPOSSIBLE: the contact figure may be published for a different load
    // type or system voltage than the handle rating (a UL resistive line and an
    // IEC AC-3 motor line on the same part differ by a factor of several).
    if (const json* contacts = at(*elec, "contacts"); contacts != nullptr && trip_I) {
        auto cI = scalar_at(*contacts, {"ratedCurrent"});
        if (cI && *cI > 0.0 && *trip_I > *cI)
            emit(out, ctx, "SWT_TRIP_VS_CONTACT", Severity::Suspicious, *trip_I, *cI,
                 fmt("trip.ratedCurrent exceeds contacts.ratedCurrent [A] — the breaker trips "
                     "above what its own contacts are rated to carry",
                     *trip_I, *cI));
        else if (!cI)
            skipped.push_back("SWT_TRIP_VS_CONTACT");
    } else {
        skipped.push_back("SWT_TRIP_VS_CONTACT");
    }

    if (const json* tc = at(*trip, "tripCurve"); tc != nullptr && tc->is_object())
        check_trip_curve(*tc, ctx, out, skipped);
    else
        skipped.push_back("SWT_TRIP_CURVE");
}

}  // namespace tas
