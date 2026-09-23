// SPDX-License-Identifier: MIT
// Potentiometer physics checks (RAS potentiometer.json). `datasheet` is the
// datasheetInfo object: electrical.{totalResistance,resistanceTolerance,
// powerRating,maximumWorkingVoltage,taper,wiper,endResistance,gangs,...},
// mechanical.{actuation,shaft,rotationalLife,...}, part.technology.
//
// A potentiometer is a resistor with a moving tap, so the resistor's own
// energy relations hold on the track and are what these checks use: the whole
// track dissipates V^2/R at its maximum working voltage, and carries at most
// sqrt(P/R) in a rheostat connection. The rest are definitions the schema
// itself states (electrical travel never exceeds mechanical travel; the end
// resistance is a residue of the track, not the track).
//
// Field presence over the live catalogue (106 rows, 2026-09-23):
// electrical.totalResistance.nominal, .resistanceTolerance, .powerRating and
// .gangs on 100%; nothing else populated yet. The remaining checks are guards
// for what an import brings in, and skip until then.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cmath>
#include <string>
#include <vector>

namespace tas {

namespace {

void require_positive(const json& obj, const char* key, const char* label, const Ctx& ctx,
                      std::vector<Finding>& out) {
    auto v = scalar_at(obj, {key});
    if (v && *v <= 0.0)
        emit(out, ctx, "POT_POSITIVITY", Severity::Impossible, *v, 0,
             std::string(label) + " <= 0");
}

// A field the schema defines as a FRACTION of something (0.005 = 0.5%). A value
// at or above 1 is a percent written into a fraction field: 3% stored as 3 is
// "300% of the total resistance", which no measurable quantity of a real track
// can be. (This is the dissipationFactor trap from the capacitor catalogue, in
// a family that has five more fields shaped the same way.)
void check_fraction(const json& obj, const char* key, const char* label, const Ctx& ctx,
                    std::vector<Finding>& out) {
    auto v = scalar_at(obj, {key});
    if (!v) return;
    if (*v < 0.0)
        emit(out, ctx, "POT_FRACTION_RANGE", Severity::Impossible, *v, 0,
             std::string(label) + " is a fraction and cannot be negative");
    else if (*v >= 1.0)
        emit(out, ctx, "POT_FRACTION_RANGE", Severity::Impossible, *v, 1.0,
             fmt(std::string(label) + " is a fraction of the total resistance but is >= 1 — a "
                                      "percentage written into a fraction field",
                 *v, 1.0));
}

}  // namespace

void check_potentiometers(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                          std::vector<std::string>& skipped) {
    const json* mech = at(datasheet, "mechanical");
    if (mech != nullptr && mech->is_object()) {
        require_positive(*mech, "rotationalLife", "mechanical.rotationalLife", ctx, out);
        const json* act = at(*mech, "actuation");
        if (act != nullptr && act->is_object()) {
            for (const auto& kv :
                 {std::pair<const char*, const char*>{"turns", "mechanical.actuation.turns"},
                  {"mechanicalTravel", "mechanical.actuation.mechanicalTravel"},
                  {"electricalTravel", "mechanical.actuation.electricalTravel"},
                  {"detents", "mechanical.actuation.detents"}})
                require_positive(*act, kv.first, kv.second, ctx, out);
            // CHECK: the wiper is on the resistive track over the ELECTRICAL
            // travel and moves over the MECHANICAL travel, which also contains
            // the dead bands beyond both ends of the track. The schema states
            // the relation in both branches: "Always <= mechanicalTravel".
            auto mt = scalar_at(*act, {"mechanicalTravel"});
            auto et = scalar_at(*act, {"electricalTravel"});
            if (mt && et && *mt > 0.0 && *et > 0.0 && *et > *mt)
                emit(out, ctx, "POT_TRAVEL_ORDER", Severity::Impossible, *et, *mt,
                     fmt("mechanical.actuation.electricalTravel exceeds mechanicalTravel — the "
                         "wiper cannot be on the track over more travel than it has",
                         *et, *mt));
            else if (!mt || !et)
                skipped.push_back("POT_TRAVEL_ORDER");
        } else {
            skipped.push_back("POT_TRAVEL_ORDER");
        }
    } else {
        skipped.push_back("POT_TRAVEL_ORDER");
    }

    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr || !elec->is_object()) {
        skipped.push_back("POT_*");
        return;
    }

    auto R = scalar_at(*elec, {"totalResistance"});
    auto P = scalar_at(*elec, {"powerRating"});
    auto Vmax = scalar_at(*elec, {"maximumWorkingVoltage"});
    auto Rend = scalar_at(*elec, {"endResistance"});
    auto gangs = scalar_at(*elec, {"gangs"});

    if (R && *R <= 0.0)
        emit(out, ctx, "POT_POSITIVITY", Severity::Impossible, *R, 0,
             "electrical.totalResistance <= 0 — a track with no resistance is a wire");
    for (const auto& kv : {std::pair<const char*, const char*>{"powerRating",
                                                               "electrical.powerRating"},
                           {"maximumWorkingVoltage", "electrical.maximumWorkingVoltage"},
                           {"insulationResistance", "electrical.insulationResistance"},
                           {"resistanceTolerance", "electrical.resistanceTolerance"}})
        require_positive(*elec, kv.first, kv.second, ctx, out);
    if (Rend && *Rend < 0.0)
        emit(out, ctx, "POT_POSITIVITY", Severity::Impossible, *Rend, 0,
             "electrical.endResistance < 0");
    if (gangs && *gangs < 1.0)
        emit(out, ctx, "POT_POSITIVITY", Severity::Impossible, *gangs, 1,
             "electrical.gangs < 1 — a potentiometer has at least one resistive section");

    // CHECK: track resistance within the range a resistive element can be made
    // in. Below ~1 ohm the wiper contact resistance alone exceeds the track;
    // above 100 Mohm the element is not a track (see POT_R_SUS_*).
    if (!R)
        skipped.push_back("POT_R_RANGE");
    else if (*R > 0.0 && *R < thr::POT_R_SUS_LO)
        emit(out, ctx, "POT_R_RANGE", Severity::Suspicious, *R, thr::POT_R_SUS_LO,
             fmt("electrical.totalResistance [ohm] is below the wiper contact resistance of any "
                 "real track",
                 *R, thr::POT_R_SUS_LO));
    else if (*R > thr::POT_R_SUS_HI)
        emit(out, ctx, "POT_R_RANGE", Severity::Suspicious, *R, thr::POT_R_SUS_HI,
             fmt("electrical.totalResistance [ohm] is above any manufacturable track", *R,
                 thr::POT_R_SUS_HI));

    // CHECK: resistanceTolerance is a FRACTION (0.2 = +/-20%).
    if (auto tol = scalar_at(*elec, {"resistanceTolerance"})) {
        if (*tol >= thr::POT_TOL_IMP_HI)
            emit(out, ctx, "POT_TOLERANCE", Severity::Impossible, *tol, thr::POT_TOL_IMP_HI,
                 fmt("electrical.resistanceTolerance is a fraction but is >= 1 — a percentage "
                     "written into a fraction field",
                     *tol, thr::POT_TOL_IMP_HI));
        else if (*tol > thr::POT_TOL_SUS_HI)
            emit(out, ctx, "POT_TOLERANCE", Severity::Suspicious, *tol, thr::POT_TOL_SUS_HI,
                 fmt("electrical.resistanceTolerance is wider than the loosest real grade",
                     *tol, thr::POT_TOL_SUS_HI));
    } else {
        skipped.push_back("POT_TOLERANCE");
    }

    // CHECK: at the maximum working voltage the WHOLE track carries V/R and
    // dissipates V^2/R. That may not exceed the track's own power rating —
    // the "critical resistance" point of every resistive element: above it the
    // part is voltage-limited and the stated Vmax is LOWER than sqrt(P*R),
    // never higher.
    if (Vmax && P && R && *Vmax > 0.0 && *P > 0.0 && *R > 0.0) {
        const double p_at_vmax = *Vmax * *Vmax / *R;
        if (p_at_vmax > *P * thr::POT_ENERGY_ROUNDING)
            emit(out, ctx, "POT_VOLTAGE_POWER", Severity::Impossible, p_at_vmax, *P,
                 fmt("maximumWorkingVoltage across totalResistance dissipates more than the "
                     "track's own powerRating [W], beyond any rounding of the two figures",
                     p_at_vmax, *P));
    } else {
        skipped.push_back("POT_VOLTAGE_POWER");
    }

    // CHECK: the residual resistance between the wiper and the end terminal at
    // full travel is a RESIDUE of the track (ohms, or a few tenths of a percent
    // of it). It cannot be the whole track or more.
    if (Rend && R && *Rend > 0.0 && *R > 0.0 && *Rend >= *R)
        emit(out, ctx, "POT_END_RESISTANCE", Severity::Impossible, *Rend, *R,
             fmt("electrical.endResistance is not below electrical.totalResistance [ohm] — the "
                 "residue at the end of travel cannot be the entire track",
                 *Rend, *R));
    else if (!Rend || !R)
        skipped.push_back("POT_END_RESISTANCE");

    for (const auto& kv :
         {std::pair<const char*, const char*>{"independentLinearity",
                                              "electrical.independentLinearity"},
          {"resolution", "electrical.resolution"},
          {"gangTracking", "electrical.gangTracking"}})
        check_fraction(*elec, kv.first, kv.second, ctx, out);

    const json* wiper = at(*elec, "wiper");
    if (wiper != nullptr && wiper->is_object()) {
        if (auto rw = scalar_at(*wiper, {"resistance"}); rw && *rw < 0.0)
            emit(out, ctx, "POT_POSITIVITY", Severity::Impossible, *rw, 0,
                 "electrical.wiper.resistance < 0");
        require_positive(*wiper, "maximumCurrent", "electrical.wiper.maximumCurrent", ctx, out);
        check_fraction(*wiper, "contactResistanceVariation",
                       "electrical.wiper.contactResistanceVariation", ctx, out);

        // CHECK: in a rheostat connection the wiper current flows through the
        // whole track, which may dissipate at most powerRating — i.e. the track
        // itself limits the current to sqrt(P/R). The schema's own words for
        // this field are "usually far below the track's own current capability",
        // so a wiper rating ABOVE the track limit is a contradiction.
        //
        // SUSPICIOUS, not IMPOSSIBLE, and the fixture for this check is why:
        // vendors publish ONE absolute wiper-current limit for a whole trimmer
        // family (a heating limit of the moving contact, e.g. 100 mA) across
        // every resistance code in it, and on the high-ohm codes that figure is
        // simply unreachable — sqrt(0.5 W / 10 kohm) is 7 mA. Such a record is
        // not describing an impossible part; it is carrying a family headline in
        // a per-part field, exactly as the "200 VDC or sqrt(P*R), whichever is
        // less" convention does for the voltage. Worth flagging, not condemning.
        auto Iw = scalar_at(*wiper, {"maximumCurrent"});
        if (Iw && P && R && *Iw > 0.0 && *P > 0.0 && *R > 0.0) {
            const double i_track = std::sqrt(*P / *R);
            if (*Iw > i_track * thr::POT_ENERGY_ROUNDING)
                emit(out, ctx, "POT_WIPER_CURRENT", Severity::Suspicious, *Iw, i_track,
                     fmt("electrical.wiper.maximumCurrent exceeds sqrt(powerRating/"
                         "totalResistance) [A] — that current in the track alone already exceeds "
                         "the track's power rating; the figure is probably the trimmer family's "
                         "absolute wiper limit rather than this resistance code's",
                         *Iw, i_track));
        } else {
            skipped.push_back("POT_WIPER_CURRENT");
        }
    } else {
        skipped.push_back("POT_WIPER_CURRENT");
    }

    const json* taper = at(*elec, "taper");
    if (taper != nullptr && taper->is_object()) {
        check_fraction(*taper, "centreResistanceFraction", "electrical.taper."
                                                           "centreResistanceFraction",
                       ctx, out);
        // CHECK: `exponent` is the fitted power law of the taper NAMED by `law`
        // (R_cw/totalResistance = position^n). n == 1 IS the linear law, so a
        // linear taper with n != 1 and a logarithmic taper with n == 1 each
        // state two different curves in one object. SUSPICIOUS: the exponent is
        // a fitted convenience, not a datasheet primary.
        const json* law = at(*taper, "law");
        auto n = scalar_at(*taper, {"exponent"});
        if (law != nullptr && law->is_string() && n) {
            const std::string l = law->get<std::string>();
            if (*n <= 0.0)
                emit(out, ctx, "POT_POSITIVITY", Severity::Impossible, *n, 0,
                     "electrical.taper.exponent <= 0");
            else if (l == "linear" && std::fabs(*n - 1.0) > thr::POT_TAPER_EXP_TOL)
                emit(out, ctx, "POT_TAPER", Severity::Suspicious, *n, 1.0,
                     fmt("electrical.taper.law is 'linear' but the fitted exponent is not 1",
                         *n, 1.0));
            else if (l == "logarithmic" && std::fabs(*n - 1.0) <= thr::POT_TAPER_EXP_TOL)
                emit(out, ctx, "POT_TAPER", Severity::Suspicious, *n, 1.0,
                     fmt("electrical.taper.law is 'logarithmic' but the fitted exponent is 1, "
                         "which is the linear law",
                         *n, 1.0));
        } else {
            skipped.push_back("POT_TAPER");
        }
    } else {
        skipped.push_back("POT_TAPER");
    }
}

}  // namespace tas
