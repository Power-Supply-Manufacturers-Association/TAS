// SPDX-License-Identifier: MIT
// Shared electromechanical contact checks.
//
// EMAS models a relay's and a switch's switched circuit with the SAME two
// types — `utils.json#/$defs/contactSet` and `utils.json#/$defs/isolation` —
// hung off `datasheetInfo.electrical.contacts` / `.isolation` in both families.
// The physics of a pair of metal contacts does not change with what moves them,
// so the checks live here once and both families call them, rather than being
// copy-pasted into relays.cpp and switches.cpp (this codebase's mirrored-branch
// trap: a copy is where `ratedCurrent` quietly becomes `carryCurrent` in one of
// the two and nobody notices, because both still compile and both still pass).
//
// `electrical` is the family's datasheetInfo.electrical object.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <string>
#include <vector>

namespace tas {

namespace {

// Flag a scalar that must be strictly positive to mean anything.
void require_positive(const json& obj, const char* key, const char* label, const Ctx& ctx,
                      std::vector<Finding>& out) {
    auto v = scalar_at(obj, {key});
    if (v && *v <= 0.0)
        emit(out, ctx, "CONTACT_POSITIVITY", Severity::Impossible, *v, 0,
             std::string(label) + " <= 0");
}

// NARM / IEC 60947 contact-form letters, and the `throw` each one IS. The letter
// and the throw are two spellings of one fact, printed side by side on every
// relay datasheet ("1 Form C SPDT"), so a record where they disagree answers
// "is this contact normally open or normally closed?" both ways at once.
//
// Only the letters whose throw is fixed by the standard are listed:
//   A = SPST-NO (make)              X = SPST-NO-DM (double make)
//   B = SPST-NC (break)             Y = SPST-NC-DB (double break)
//   C = SPDT break-before-make      Z = SPDT-DB-DM
//   D = SPDT make-before-break      E = SPDT break-make-before-break
// P (bridging), R, U, V and W are deliberately absent: they describe transfer
// and bridging arrangements the two-value `throw` enum cannot express, so no
// comparison against `throw` is meaningful for them.
struct FormThrow {
    const char* form;
    const char* thrw;
};
const FormThrow FORM_THROW[] = {
    {"A", "normallyOpen"},  {"X", "normallyOpen"},   {"B", "normallyClosed"},
    {"Y", "normallyClosed"}, {"C", "changeover"},    {"D", "changeover"},
    {"E", "changeover"},    {"Z", "changeover"},
};

void check_form(const json& contacts, const Ctx& ctx, std::vector<Finding>& out,
                std::vector<std::string>& skipped) {
    const json* form = at(contacts, "form");
    if (form == nullptr || !form->is_object()) {
        skipped.push_back("CONTACT_FORM_THROW");
        return;
    }
    if (auto poles = scalar_at(*form, {"poles"}); poles && *poles < 1.0)
        emit(out, ctx, "CONTACT_POSITIVITY", Severity::Impossible, *poles, 1,
             "contacts.form.poles < 1 — a contact set has at least one pole");

    const json* letter = at(*form, "form");
    const json* thrw = at(*form, "throw");
    if (letter == nullptr || !letter->is_string() || thrw == nullptr || !thrw->is_string()) {
        skipped.push_back("CONTACT_FORM_THROW");
        return;
    }
    const std::string l = letter->get<std::string>();
    const std::string t = thrw->get<std::string>();
    for (const auto& ft : FORM_THROW) {
        if (l != ft.form) continue;
        if (t == ft.thrw) return;
        emit(out, ctx, "CONTACT_FORM_THROW", Severity::Suspicious, 0, 0,
             "contacts.form.form '" + l + "' is " + ft.thrw +
                 " by the NARM/IEC contact-form letter, but contacts.form.throw says '" + t +
                 "' — the record states the opposite contact action twice");
        return;
    }
}

// EMAS utils isolation: the same block on relay and switch.
void check_isolation(const json& iso, const Ctx& ctx, std::vector<Finding>& out) {
    for (const auto& kv : {std::pair<const char*, const char*>{
                               "dielectricStrengthInputToContact",
                               "isolation.dielectricStrengthInputToContact"},
                           {"dielectricStrengthAcrossOpenContact",
                            "isolation.dielectricStrengthAcrossOpenContact"},
                           {"dielectricStrengthBetweenPoles",
                            "isolation.dielectricStrengthBetweenPoles"},
                           {"insulationResistance", "isolation.insulationResistance"},
                           {"creepage", "isolation.creepage"},
                           {"clearance", "isolation.clearance"}})
        require_positive(iso, kv.first, kv.second, ctx, out);

    // Creepage is measured ALONG the insulating surface between two conductors;
    // clearance is the shortest path THROUGH air between the same two. The
    // surface path can never be shorter than the straight line it detours
    // around, so creepage >= clearance holds on every part, in every standard
    // (IEC 60664-1). A record with clearance > creepage has the two swapped.
    auto creep = scalar_at(iso, {"creepage"});
    auto clear = scalar_at(iso, {"clearance"});
    if (creep && clear && *creep > 0.0 && *clear > 0.0 && *clear > *creep)
        emit(out, ctx, "CONTACT_ISOLATION", Severity::Impossible, *clear, *creep,
             fmt("isolation.clearance exceeds isolation.creepage [m] — the through-air path "
                 "cannot be longer than the over-surface path it short-cuts; the two are swapped",
                 *clear, *creep));
}

}  // namespace

void check_contact_block(const json& electrical, const Ctx& ctx, std::vector<Finding>& out,
                         std::vector<std::string>& skipped) {
    if (const json* iso = at(electrical, "isolation"); iso != nullptr && iso->is_object())
        check_isolation(*iso, ctx, out);

    const json* contacts = at(electrical, "contacts");
    if (contacts == nullptr || !contacts->is_object()) {
        skipped.push_back("CONTACT_*");
        return;
    }

    for (const auto& kv : {std::pair<const char*, const char*>{"ratedCurrent",
                                                               "contacts.ratedCurrent"},
                           {"ratedVoltage", "contacts.ratedVoltage"},
                           {"minimumVoltage", "contacts.minimumVoltage"},
                           {"carryCurrent", "contacts.carryCurrent"},
                           {"contactResistance", "contacts.contactResistance"},
                           {"maximumSwitchingPower", "contacts.maximumSwitchingPower"},
                           {"bounceTime", "contacts.bounceTime"},
                           {"electricalLife", "contacts.electricalLife"},
                           {"mechanicalLife", "contacts.mechanicalLife"}})
        require_positive(*contacts, kv.first, kv.second, ctx, out);

    check_form(*contacts, ctx, out, skipped);

    auto I = scalar_at(*contacts, {"ratedCurrent"});
    auto V = scalar_at(*contacts, {"ratedVoltage"});
    auto Vmin = scalar_at(*contacts, {"minimumVoltage"});
    auto Icarry = scalar_at(*contacts, {"carryCurrent"});
    auto Pmax = scalar_at(*contacts, {"maximumSwitchingPower"});
    auto Rc = scalar_at(*contacts, {"contactResistance"});
    auto life_e = scalar_at(*contacts, {"electricalLife"});
    auto life_m = scalar_at(*contacts, {"mechanicalLife"});

    // CHECK: the rated switching range runs FROM minimumVoltage UP TO
    // ratedVoltage (the schema's own words: "lower voltage bound of the rated
    // switching range"). A lower bound above the upper bound is an empty range.
    if (Vmin && V && *Vmin > 0.0 && *V > 0.0) {
        if (*Vmin > *V)
            emit(out, ctx, "CONTACT_VOLTAGE_RANGE", Severity::Impossible, *Vmin, *V,
                 fmt("contacts.minimumVoltage exceeds contacts.ratedVoltage [V] — the rated "
                     "switching range is empty",
                     *Vmin, *V));
    } else if (!Vmin || !V) {
        skipped.push_back("CONTACT_VOLTAGE_RANGE");
    }

    // CHECK: making and breaking a current is strictly harder on a contact than
    // carrying it once closed — the arc energy is what erodes the contact, and a
    // closed contact carrying its own switching current dissipates only I^2*Rc.
    // So carryCurrent >= ratedCurrent on every datasheet that states both.
    // SUSPICIOUS, not IMPOSSIBLE: the two figures are routinely quoted under
    // different standards, load types and ambients, which can invert them
    // slightly without either being wrong.
    if (I && Icarry && *I > 0.0 && *Icarry > 0.0 && *Icarry < *I)
        emit(out, ctx, "CONTACT_CARRY_VS_SWITCH", Severity::Suspicious, *Icarry, *I,
             fmt("contacts.carryCurrent is below contacts.ratedCurrent [A] — a contact that "
                 "makes and breaks a current necessarily carries it when closed",
                 *Icarry, *I));

    // CHECK: mechanicalLife is measured with NO load on the contacts; electrical
    // life is the same actuation WITH an arc eroding the contact faces every
    // operation. The loaded figure is therefore never the larger of the two.
    if (life_e && life_m && *life_e > 0.0 && *life_m > 0.0 && *life_e > *life_m)
        emit(out, ctx, "CONTACT_LIFE_ORDER", Severity::Suspicious, *life_e, *life_m,
             fmt("contacts.electricalLife exceeds contacts.mechanicalLife — loaded life cannot "
                 "outlast the same relay's unloaded life",
                 *life_e, *life_m));

    // CHECK: maximumSwitchingPower is the envelope of the contact's V-I derating
    // curve. Every point on that curve satisfies V <= ratedVoltage and
    // I <= ratedCurrent, so the envelope cannot exceed their product.
    if (Pmax && I && V && *Pmax > 0.0 && *I > 0.0 && *V > 0.0 && *Pmax > *I * *V)
        emit(out, ctx, "CONTACT_SWITCHING_POWER", Severity::Impossible, *Pmax, *I * *V,
             fmt("contacts.maximumSwitchingPower exceeds ratedVoltage * ratedCurrent [W] — no "
                 "point of the derating curve can lie outside the two headline maxima",
                 *Pmax, *I * *V));

    // CHECK: a closed metallic contact is milliohms (see CONTACT_R_IMP_HI).
    if (Rc && *Rc > thr::CONTACT_R_IMP_HI)
        emit(out, ctx, "CONTACT_RESISTANCE", Severity::Impossible, *Rc, thr::CONTACT_R_IMP_HI,
             fmt("contacts.contactResistance [ohm] is not a closed metallic contact", *Rc,
                 thr::CONTACT_R_IMP_HI));

    // Graded rating table: each line is a real (V, I) operating point.
    if (const json* ratings = at(*contacts, "ratings"); ratings != nullptr && ratings->is_array())
        for (const auto& line : *ratings) {
            if (!line.is_object()) continue;
            require_positive(line, "current", "contacts.ratings[].current", ctx, out);
            require_positive(line, "voltage", "contacts.ratings[].voltage", ctx, out);
            require_positive(line, "operations", "contacts.ratings[].operations", ctx, out);
        }
}

}  // namespace tas
