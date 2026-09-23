// SPDX-License-Identifier: MIT
// Connector-accessory physics checks (CONAS connectorAccessory.json).
// `datasheet` is the datasheetInfo object: accessoryDetails.{kind,
// wireGaugeRange,cableDiameterRange,...}, electrical.{ratedCurrentPerContact,
// ratedVoltage,characteristicImpedance,contactResistance}, mechanical, hostSystem.
//
// Most accessory kinds are not electrical parts at all (a marker strip, a coding
// key, a crimp tool), so the family's completeness manifest is scored off
// accessoryDetails and hostSystem rather than electrical, and carries no sparse
// floor — see core_fields() and sparse_floor() in validator.cpp. The
// two checks with real teeth both live on the CONTACT kinds, which are 3,324 of
// the 18,227 live rows and the only ones that carry both a conductor range and a
// current rating:
//
//   * AWG and cross-sectional area are two spellings of ONE conductor size, tied
//     by the gauge's own definition, so a record publishing both can be tested
//     against itself (52 live rows disagree by more than two gauge steps);
//   * a contact's continuous current rating cannot exceed the fusing current of
//     the largest conductor it accepts (27 live rows do).
//
// Field presence over the live catalogue (18,227 rows, 2026-09-23):
// accessoryDetails.kind 100%, electrical 19.2%, wireGaugeRange 16.3%,
// cableDiameterRange 17.4%, mechanical.positions 16.2%.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cmath>
#include <optional>
#include <string>
#include <vector>

namespace tas {

namespace {

// Cross-sectional area [m^2] of an AWG gauge. The gauge is DEFINED as a
// geometric series: AWG 36 is 0.005 inch (0.127 mm) diameter and each 39 gauges
// down multiplies the diameter by 92, so
//     d(n) = 0.127 mm * 92^((36-n)/39),  A = pi*d^2/4.
// (AWG 0 -> 53.5 mm^2, AWG 18 -> 0.823 mm^2, AWG 24 -> 0.205 mm^2 — the printed
// table.) 39.0 and 4.0 are spelled as doubles deliberately: an integer 39 here
// would truncate the exponent to 0 for every gauge and silently return one
// single wire size for the whole table.
double awg_area_m2(double gauge) {
    const double d = 0.127e-3 * std::pow(92.0, (36.0 - gauge) / 39.0);
    return M_PI * d * d / 4.0;
}

void require_positive(const json& obj, const char* key, const char* label, const Ctx& ctx,
                      std::vector<Finding>& out) {
    auto v = scalar_at(obj, {key});
    if (v && *v <= 0.0)
        emit(out, ctx, "ACC_POSITIVITY", Severity::Impossible, *v, 0,
             std::string(label) + " <= 0");
}

// Preece's fusing current for a round copper conductor in free air:
//     I_fuse [A] = 80 * d[mm]^1.5
// (the classical result: a 1 mm copper wire fuses at 80 A). Returns the current
// at which the conductor of cross-section `area_m2` melts.
double preece_fusing_current(double area_m2) {
    const double d_mm = std::sqrt(4.0 * area_m2 / M_PI) * 1e3;
    return thr::ACC_PREECE_COPPER_K * std::pow(d_mm, thr::ACC_PREECE_EXPONENT);
}

}  // namespace

void check_connector_accessories(const json& datasheet, const Ctx& ctx,
                                 std::vector<Finding>& out,
                                 std::vector<std::string>& skipped) {
    if (const json* mech = at(datasheet, "mechanical"); mech != nullptr && mech->is_object()) {
        if (auto pos = scalar_at(*mech, {"positions"}); pos && *pos < 1.0)
            emit(out, ctx, "ACC_POSITIVITY", Severity::Impossible, *pos, 1,
                 "mechanical.positions < 1");
        require_positive(*mech, "pitch", "mechanical.pitch", ctx, out);
    }

    const json* elec = at(datasheet, "electrical");
    std::optional<double> I, Z;
    if (elec != nullptr && elec->is_object()) {
        for (const auto& kv :
             {std::pair<const char*, const char*>{"ratedCurrentPerContact",
                                                  "electrical.ratedCurrentPerContact"},
              {"ratedVoltage", "electrical.ratedVoltage"},
              {"characteristicImpedance", "electrical.characteristicImpedance"},
              {"contactResistance", "electrical.contactResistance"}})
            require_positive(*elec, kv.first, kv.second, ctx, out);
        I = scalar_at(*elec, {"ratedCurrentPerContact"});
        Z = scalar_at(*elec, {"characteristicImpedance"});
    }

    // CHECK: a characteristic impedance outside the range transmission lines are
    // built in. Real coaxial and twinaxial lines span ~25 ohm (high-power, low
    // loss) to ~125 ohm (air-spaced); the catalogue's own RF adapters are 50 and
    // 75 ohm. Outside 25..150 the number is not a line impedance — most often a
    // contact resistance or an insulation resistance in the wrong field.
    if (!Z)
        skipped.push_back("ACC_RF_IMPEDANCE");
    else if (*Z > 0.0 && (*Z < thr::ACC_IMPEDANCE_SUS_LO || *Z > thr::ACC_IMPEDANCE_SUS_HI))
        emit(out, ctx, "ACC_RF_IMPEDANCE", Severity::Suspicious, *Z,
             *Z < thr::ACC_IMPEDANCE_SUS_LO ? thr::ACC_IMPEDANCE_SUS_LO
                                            : thr::ACC_IMPEDANCE_SUS_HI,
             fmt("electrical.characteristicImpedance [ohm] is outside the range real "
                 "transmission lines are built in",
                 *Z,
                 *Z < thr::ACC_IMPEDANCE_SUS_LO ? thr::ACC_IMPEDANCE_SUS_LO
                                                : thr::ACC_IMPEDANCE_SUS_HI));

    const json* det = at(datasheet, "accessoryDetails");
    if (det == nullptr || !det->is_object()) {
        skipped.push_back("ACC_CABLE_RANGE");
        skipped.push_back("ACC_WIRE_GAUGE_RANGE");
        skipped.push_back("ACC_AWG_AREA");
        skipped.push_back("ACC_CONTACT_FUSING");
        return;
    }

    // --- accepted cable outer diameter -------------------------------------
    if (const json* cd = at(*det, "cableDiameterRange"); cd != nullptr && cd->is_object()) {
        require_positive(*cd, "minimum", "accessoryDetails.cableDiameterRange.minimum", ctx, out);
        require_positive(*cd, "maximum", "accessoryDetails.cableDiameterRange.maximum", ctx, out);
        auto lo = scalar_at(*cd, {"minimum"});
        auto hi = scalar_at(*cd, {"maximum"});
        if (lo && hi && *lo > 0.0 && *hi > 0.0 && *lo > *hi)
            emit(out, ctx, "ACC_CABLE_RANGE", Severity::Impossible, *lo, *hi,
                 fmt("accessoryDetails.cableDiameterRange.minimum exceeds its maximum [m] — the "
                     "accepted cable range is empty",
                     *lo, *hi));
        else if (!lo || !hi)
            skipped.push_back("ACC_CABLE_RANGE");
    } else {
        skipped.push_back("ACC_CABLE_RANGE");
    }

    // --- accepted conductor size -------------------------------------------
    const json* wg = at(*det, "wireGaugeRange");
    if (wg == nullptr || !wg->is_object()) {
        skipped.push_back("ACC_WIRE_GAUGE_RANGE");
        skipped.push_back("ACC_AWG_AREA");
        skipped.push_back("ACC_CONTACT_FUSING");
        return;
    }
    require_positive(*wg, "minimumArea", "accessoryDetails.wireGaugeRange.minimumArea", ctx, out);
    require_positive(*wg, "maximumArea", "accessoryDetails.wireGaugeRange.maximumArea", ctx, out);

    auto awg_lo = scalar_at(*wg, {"minimumAwg"});
    auto awg_hi = scalar_at(*wg, {"maximumAwg"});
    auto area_lo = scalar_at(*wg, {"minimumArea"});
    auto area_hi = scalar_at(*wg, {"maximumArea"});

    if (awg_lo && awg_hi && *awg_lo > *awg_hi)
        emit(out, ctx, "ACC_WIRE_GAUGE_RANGE", Severity::Impossible, *awg_lo, *awg_hi,
             fmt("accessoryDetails.wireGaugeRange.minimumAwg exceeds its maximumAwg", *awg_lo,
                 *awg_hi));
    if (area_lo && area_hi && *area_lo > 0.0 && *area_hi > 0.0 && *area_lo > *area_hi)
        emit(out, ctx, "ACC_WIRE_GAUGE_RANGE", Severity::Impossible, *area_lo, *area_hi,
             fmt("accessoryDetails.wireGaugeRange.minimumArea exceeds its maximumArea [m^2]",
                 *area_lo, *area_hi));
    if ((!awg_lo || !awg_hi) && (!area_lo || !area_hi))
        skipped.push_back("ACC_WIRE_GAUGE_RANGE");

    // CHECK: AWG and area are the SAME conductor size published twice. A LARGER
    // gauge number is a SMALLER wire, so minimumArea pairs with maximumAwg and
    // maximumArea with minimumAwg — the crossed pairing, and the reason this
    // block reads the two ends separately instead of copying one branch.
    if (awg_lo && awg_hi && area_lo && area_hi && *area_lo > 0.0 && *area_hi > 0.0) {
        struct End {
            const char* area_field;
            const char* awg_field;
            double area;
            double gauge;
        };
        const End ENDS[] = {{"minimumArea", "maximumAwg", *area_lo, *awg_hi},
                            {"maximumArea", "minimumAwg", *area_hi, *awg_lo}};
        for (const auto& e : ENDS) {
            const double implied = awg_area_m2(e.gauge);
            if (implied <= 0.0) continue;
            const double ratio = e.area > implied ? e.area / implied : implied / e.area;
            if (ratio > thr::ACC_AWG_AREA_RATIO_SUS) {
                emit(out, ctx, "ACC_AWG_AREA", Severity::Suspicious, ratio,
                     thr::ACC_AWG_AREA_RATIO_SUS,
                     fmt("accessoryDetails.wireGaugeRange." + std::string(e.area_field) + " (" +
                             std::to_string(e.area * 1e6) + " mm^2) and ." + e.awg_field + " (AWG " +
                             std::to_string(static_cast<long long>(e.gauge)) + " = " +
                             std::to_string(implied * 1e6) +
                             " mm^2) are more than two gauge steps apart — the two published "
                             "conductor ranges are not the same wire",
                         ratio, thr::ACC_AWG_AREA_RATIO_SUS));
                break;  // one finding per record; both ends are the same defect
            }
        }
    } else {
        skipped.push_back("ACC_AWG_AREA");
    }

    // CHECK: the contact and the conductor crimped into it carry the same
    // current. The MOST GENEROUS conductor the record admits is used — the
    // larger of the published maximumArea and the area of the published
    // minimumAwg — so a record is only accused when NO wire it accepts could
    // carry its own rating. Preece's free-air fusing current is itself generous:
    // the same conductor inside a connector housing melts at less.
    double best_area = 0.0;  // seeded at 0 and only ever raised by a positive candidate
    if (area_hi && *area_hi > 0.0) best_area = *area_hi;
    if (awg_lo) {
        const double from_gauge = awg_area_m2(*awg_lo);
        if (from_gauge > best_area) best_area = from_gauge;
    }
    if (!I || *I <= 0.0 || best_area <= 0.0) {
        skipped.push_back("ACC_CONTACT_FUSING");
        return;
    }
    const double i_fuse = preece_fusing_current(best_area);
    if (*I > i_fuse)
        emit(out, ctx, "ACC_CONTACT_FUSING", Severity::Impossible, *I, i_fuse,
             fmt("electrical.ratedCurrentPerContact [A] exceeds the free-air fusing current of "
                 "the largest conductor the contact accepts (" +
                     std::to_string(best_area * 1e6) + " mm^2) — every wire it can be crimped "
                                                       "onto melts below its own rating",
                 *I, i_fuse));
}

}  // namespace tas
