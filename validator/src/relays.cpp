// SPDX-License-Identifier: MIT
// Relay physics checks (EMAS relay.json). `datasheet` is the datasheetInfo
// object: part.{technology,subType,actuationMode},
// electrical.{input,contacts,isolation,operateTime,releaseTime}, mechanical, thermal.
//
// The contacts/isolation half is shared with switches — see contacts.cpp. What
// is relay-specific is the INPUT: a coil (or an opto-isolated control input)
// whose voltage, resistance, power and current are bound to each other by Ohm's
// law, and whose pickup/dropout thresholds are bracketed by the rated voltage.
//
// Field presence measured over the live catalogue (4,693 rows, 2026-09-23):
// contacts 97.5%, contacts.form 95.6%, contacts.ratedCurrent 93.6%,
// input 85.4%, input.coilVoltage 81.3%, contacts.ratedVoltage 18.7%,
// input.coilPower 3.7%, input.coilResistance 0%. Checks are written so that the
// fields the catalogue actually carries are the ones that can fire; the rest are
// guards against what an import may bring in, and skip until then.
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
        emit(out, ctx, "REL_POSITIVITY", Severity::Impossible, *v, 0,
             std::string(label) + " <= 0");
}

std::string str_at(const json& node, const char* key) {
    const json* v = at(node, key);
    return (v != nullptr && v->is_string()) ? v->get<std::string>() : std::string();
}

// The coil's own voltage ordering. Every one of these is a definition, not a
// specification:
//   minimum <= nominal <= maximum          (a dimensionWithTolerance band)
//   mustRelease <= dropout <= pickup       (release is guaranteed BELOW operate)
//   pickup <= nominal                      (the relay must operate at its rated
//                                           coil voltage — that is what rated means)
//   nominal <= maximumContinuous           (it may be held at its rated voltage)
void check_coil_order(const json& input, const Ctx& ctx, std::vector<Finding>& out,
                      std::vector<std::string>& skipped) {
    const json* cv = at(input, "coilVoltage");
    std::optional<double> vnom, vmin, vmax;
    if (cv != nullptr && cv->is_object()) {
        vnom = scalar_at(*cv, {"nominal"});
        vmin = scalar_at(*cv, {"minimum"});
        vmax = scalar_at(*cv, {"maximum"});
    } else if (cv != nullptr && cv->is_number()) {
        vnom = cv->get<double>();
    }

    if (vmin && vmax && *vmin > *vmax)
        emit(out, ctx, "REL_COIL_ORDER", Severity::Impossible, *vmin, *vmax,
             fmt("input.coilVoltage.minimum exceeds its maximum [V]", *vmin, *vmax));
    if (vnom && vmin && *vnom < *vmin)
        emit(out, ctx, "REL_COIL_ORDER", Severity::Impossible, *vnom, *vmin,
             fmt("input.coilVoltage.nominal lies below its own minimum [V]", *vnom, *vmin));
    if (vnom && vmax && *vnom > *vmax)
        emit(out, ctx, "REL_COIL_ORDER", Severity::Impossible, *vnom, *vmax,
             fmt("input.coilVoltage.nominal lies above its own maximum [V]", *vnom, *vmax));

    auto pickup = scalar_at(input, {"pickupVoltage"});
    auto dropout = scalar_at(input, {"dropoutVoltage"});
    auto must_release = scalar_at(input, {"mustReleaseVoltage"});
    auto vcont = scalar_at(input, {"maximumContinuousVoltage"});

    if (pickup && dropout && *pickup > 0.0 && *dropout > 0.0 && *dropout > *pickup)
        emit(out, ctx, "REL_COIL_ORDER", Severity::Impossible, *dropout, *pickup,
             fmt("input.dropoutVoltage exceeds input.pickupVoltage [V] — the relay cannot be "
                 "guaranteed to release above the voltage at which it is guaranteed to operate",
                 *dropout, *pickup));
    if (must_release && pickup && *must_release > 0.0 && *pickup > 0.0 && *must_release > *pickup)
        emit(out, ctx, "REL_COIL_ORDER", Severity::Impossible, *must_release, *pickup,
             fmt("input.mustReleaseVoltage exceeds input.pickupVoltage [V]", *must_release,
                 *pickup));
    if (pickup && vnom && *pickup > 0.0 && *vnom > 0.0 && *pickup > *vnom)
        emit(out, ctx, "REL_COIL_ORDER", Severity::Impossible, *pickup, *vnom,
             fmt("input.pickupVoltage exceeds the rated input.coilVoltage [V] — a relay that "
                 "does not operate at its own rated coil voltage is not rated for it",
                 *pickup, *vnom));
    if (vcont && vnom && *vcont > 0.0 && *vnom > 0.0 && *vcont < *vnom)
        emit(out, ctx, "REL_COIL_ORDER", Severity::Impossible, *vcont, *vnom,
             fmt("input.maximumContinuousVoltage is below the rated input.coilVoltage [V] — the "
                 "coil may not be held at the voltage it is rated for",
                 *vcont, *vnom));

    if (!vnom && !pickup && !dropout) skipped.push_back("REL_COIL_ORDER");
}

// Ohm's law and Joule's law on a DC coil: I = V/R and P = V^2/R are identities.
// AC coils are excluded on purpose — an AC coil's steady current is set by its
// IMPEDANCE (the magnetising inductance of the closed magnetic circuit dominates
// the winding resistance, and the inrush with the armature open is several times
// the sealed current), so P = V^2/R_dc is simply the wrong relation there and
// applying it would condemn every correct AC-coil record.
void check_coil_ohms_law(const json& input, const Ctx& ctx, std::vector<Finding>& out,
                         std::vector<std::string>& skipped) {
    const std::string vtype = str_at(input, "coilVoltageType");
    if (vtype != "dc") {  // absent, "ac" or "acDc": the DC identity does not apply
        skipped.push_back("REL_COIL_OHMS_LAW");
        return;
    }
    const json* cv = at(input, "coilVoltage");
    std::optional<double> V;
    if (cv != nullptr && cv->is_object())
        V = scalar_at(*cv, {"nominal"});
    else if (cv != nullptr && cv->is_number())
        V = cv->get<double>();
    auto R = scalar_at(input, {"coilResistance"});
    if (!V || !R || *V <= 0.0 || *R <= 0.0) {
        skipped.push_back("REL_COIL_OHMS_LAW");
        return;
    }

    struct Claim {
        const char* field;
        double implied;
        std::optional<double> stated;
        const char* law;
    };
    const Claim CLAIMS[] = {
        {"input.coilPower", *V * *V / *R, scalar_at(input, {"coilPower"}), "V^2/R"},
        {"input.coilCurrent", *V / *R, scalar_at(input, {"coilCurrent"}), "V/R"},
    };
    bool any = false;
    for (const auto& c : CLAIMS) {
        if (!c.stated || *c.stated <= 0.0 || c.implied <= 0.0) continue;
        any = true;
        const double ratio = *c.stated > c.implied ? *c.stated / c.implied : c.implied / *c.stated;
        const std::string msg = std::string(c.field) + " and the " + c.law +
                                " the coil's own rated voltage and DC resistance imply disagree";
        if (ratio > thr::REL_COIL_OHMS_IMP)
            emit(out, ctx, "REL_COIL_OHMS_LAW", Severity::Impossible, ratio,
                 thr::REL_COIL_OHMS_IMP,
                 fmt(msg + " by a factor that cannot be the same coil", ratio,
                     thr::REL_COIL_OHMS_IMP));
        else if (ratio > thr::REL_COIL_OHMS_SUS)
            emit(out, ctx, "REL_COIL_OHMS_LAW", Severity::Suspicious, ratio,
                 thr::REL_COIL_OHMS_SUS, fmt(msg, ratio, thr::REL_COIL_OHMS_SUS));
    }
    if (!any) skipped.push_back("REL_COIL_OHMS_LAW");
}

}  // namespace

void check_relays(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                  std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr || !elec->is_object()) {
        skipped.push_back("REL_*");
        skipped.push_back("CONTACT_*");
        return;
    }

    // Shared contact-set / isolation physics (contacts.cpp).
    check_contact_block(*elec, ctx, out, skipped);

    // Transition times: a relay that operates in zero or negative time, or takes
    // longer than an hour to change state, is not reporting a transition time.
    for (const char* k : {"operateTime", "releaseTime"}) {
        auto t = scalar_at(*elec, {k});
        if (!t) continue;
        if (*t <= 0.0)
            emit(out, ctx, "REL_POSITIVITY", Severity::Impossible, *t, 0,
                 std::string("electrical.") + k + " <= 0");
        else if (*t > thr::REL_TIME_IMP_HI)
            emit(out, ctx, "REL_POSITIVITY", Severity::Impossible, *t, thr::REL_TIME_IMP_HI,
                 fmt(std::string("electrical.") + k + " [s] is not a contact transition time", *t,
                     thr::REL_TIME_IMP_HI));
    }

    const std::string tech = norm_tech(at(datasheet, "part", "technology"));

    // Reed contacts: sealed glass blades, ratings 0.25..3 A across the whole
    // published family (see REL_REED_CURRENT_*).
    if (tech == "reed") {
        const json* contacts = at(*elec, "contacts");
        auto I = contacts == nullptr ? std::nullopt : scalar_at(*contacts, {"ratedCurrent"});
        if (!I)
            skipped.push_back("REL_REED_CURRENT");
        else if (*I > thr::REL_REED_CURRENT_IMP)
            emit(out, ctx, "REL_REED_CURRENT", Severity::Impossible, *I,
                 thr::REL_REED_CURRENT_IMP,
                 fmt("contacts.ratedCurrent [A] on a reed relay — a glass-sealed reed blade pair "
                     "cannot switch a contactor's current",
                     *I, thr::REL_REED_CURRENT_IMP));
        else if (*I > thr::REL_REED_CURRENT_SUS)
            emit(out, ctx, "REL_REED_CURRENT", Severity::Suspicious, *I,
                 thr::REL_REED_CURRENT_SUS,
                 fmt("contacts.ratedCurrent [A] is above every published reed-relay rating", *I,
                     thr::REL_REED_CURRENT_SUS));
    }

    const json* input = at(*elec, "input");
    if (input == nullptr || !input->is_object()) {
        skipped.push_back("REL_COIL_ORDER");
        skipped.push_back("REL_COIL_OHMS_LAW");
        skipped.push_back("REL_TECH_INPUT");
        return;
    }

    for (const auto& kv : {std::pair<const char*, const char*>{"coilResistance",
                                                               "input.coilResistance"},
                           {"coilPower", "input.coilPower"},
                           {"coilCurrent", "input.coilCurrent"},
                           {"coilCount", "input.coilCount"},
                           {"pickupVoltage", "input.pickupVoltage"},
                           {"dropoutVoltage", "input.dropoutVoltage"},
                           {"mustReleaseVoltage", "input.mustReleaseVoltage"},
                           {"maximumContinuousVoltage", "input.maximumContinuousVoltage"},
                           {"controlCurrent", "input.controlCurrent"}})
        require_positive(*input, kv.first, kv.second, ctx, out);
    if (const json* cv = at(*input, "coilVoltage"); cv != nullptr && cv->is_object())
        for (const char* k : {"nominal", "minimum", "maximum"})
            if (auto v = scalar_at(*cv, {k}); v && *v <= 0.0)
                emit(out, ctx, "REL_POSITIVITY", Severity::Impossible, *v, 0,
                     std::string("input.coilVoltage.") + k + " <= 0");

    check_coil_order(*input, ctx, out, skipped);
    check_coil_ohms_law(*input, ctx, out, skipped);

    // CHECK: the input type has to be the one the technology physically has. A
    // solid-state relay is an opto-coupled semiconductor output stage — it has
    // no coil to energise; an electromechanical or reed relay IS a coil driving
    // an armature, and does not have an opto-isolated input in its place.
    // SUSPICIOUS: `technology` and `input.inputType` are separate vendor
    // parametric columns, so a disagreement identifies a mis-filed record rather
    // than an impossible device.
    const std::string itype = str_at(*input, "inputType");
    if (itype.empty()) {
        skipped.push_back("REL_TECH_INPUT");
    } else if (tech == "solidstate" && itype == "coil") {
        emit(out, ctx, "REL_TECH_INPUT", Severity::Suspicious, 0, 0,
             "part.technology is solidState but input.inputType is 'coil' — a solid-state relay "
             "has no coil");
    } else if ((tech == "electromechanical" || tech == "reed") && itype == "optoIsolated") {
        emit(out, ctx, "REL_TECH_INPUT", Severity::Suspicious, 0, 0,
             "part.technology is " + tech +
                 " but input.inputType is 'optoIsolated' — that input drives an armature coil");
    }
}

}  // namespace tas
