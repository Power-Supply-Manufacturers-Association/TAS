// SPDX-License-Identifier: MIT
// Junction FET physics checks (SAS jfet.json). `datasheet` is the datasheetInfo
// object: part.subType (nChannel / pChannel), then EITHER one package-level
// electrical block OR a dies[] array (a monolithic matched dual) whose entries
// each carry their own electrical block. The same checks run on every block.
//
// Only IMPOSSIBLE checks are implemented, each a sign convention or ordering
// fixed by how a depletion-mode junction FET works. TAS had no jfets.ndjson when
// this file was written (2026-10-18); SUSPICIOUS screens (g_fs vs I_DSS/V_P,
// R_DS(on) vs I_DSS/V_P, noise floors) wait for live rows to set thresholds.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cmath>
#include <optional>
#include <string>
#include <vector>

namespace tas {

namespace {

std::optional<std::string> jfet_subtype(const json& obj, const std::string& label) {
    const auto st = string_at(obj, "subType", label);
    if (!st) return std::nullopt;
    if (*st == "nChannel" || *st == "pChannel") return st;
    throw MalformedField(label + " '" + *st + "' is not a SAS jfet subType");
}

void check_electrical(const json& e, const std::string& where,
                      const std::optional<std::string>& channel, const Ctx& ctx,
                      std::vector<Finding>& out, std::vector<std::string>& skipped) {
    if (!e.is_object()) throw MalformedField(where + " is not an object");

    // CHECK JFET_IDSS_POSITIVE. I_DSS is the drain current at V_GS = 0 -- the
    // full-conduction current of a depletion-mode device -- stored as a magnitude
    // (schema). A JFET whose channel carries no current at V_GS = 0 is not a
    // JFET, so every printed end is > 0.
    const json* idss = at(e, "saturationDrainCurrent");
    if (idss == nullptr || idss->is_null()) {
        skipped.push_back("JFET_IDSS_POSITIVE");
    } else {
        for (const auto& [end, v] : printed_ends(idss, where + ".saturationDrainCurrent"))
            if (v <= 0)
                emit(out, ctx, "JFET_IDSS_POSITIVE", Severity::Impossible, v, 0,
                     fmt(where + ".saturationDrainCurrent." + end + " [A] is not positive", v));
    }

    // CHECK JFET_VGSOFF_SIGN. V_GS(off) (stored SIGNED as printed) is the gate
    // bias that depletes the channel. The gate junction must be REVERSE-biased to
    // do that: negative gate for an n-channel part (p+ gate on n channel),
    // positive for a p-channel one. Zero would mean the channel is already off at
    // V_GS = 0, contradicting the depletion mode that defines a JFET. Every
    // printed end therefore carries the channel's sign (JFE150: -1.5..-0.9 V).
    const json* voff = at(e, "gateSourceCutoffVoltage");
    const auto voff_ends =
        printed_ends(voff, where + ".gateSourceCutoffVoltage");
    if (voff_ends.empty() || !channel) {
        skipped.push_back("JFET_VGSOFF_SIGN");
    } else {
        const bool n = *channel == "nChannel";
        for (const auto& [end, v] : voff_ends)
            if (n ? v >= 0 : v <= 0)
                emit(out, ctx, "JFET_VGSOFF_SIGN", Severity::Impossible, v, 0,
                     fmt(where + ".gateSourceCutoffVoltage." + end + " [V] is " +
                             (n ? "not negative on an n-channel" : "not positive on a p-channel") +
                             " JFET",
                         v));
    }

    // CHECK JFET_VGSOFF_VS_VGSS. The channel must pinch off before the gate
    // junction breaks down: gateSourceVoltageMax is V_GSS / V_(BR)GSS, the
    // largest reverse gate-source voltage the junction withstands (a magnitude).
    // A part whose cut-off voltage lies beyond it could not be turned off within
    // its own rating, so no printed |V_GS(off)| exceeds it (JFE150: 1.5 V vs
    // 40 V; J111: 10 V vs 35 V).
    const auto vgss = scalar_at(e, {"gateSourceVoltageMax"});
    if (voff_ends.empty() || !vgss) {
        skipped.push_back("JFET_VGSOFF_VS_VGSS");
    } else {
        for (const auto& [end, v] : voff_ends)
            if (std::fabs(v) > *vgss)
                emit(out, ctx, "JFET_VGSOFF_VS_VGSS", Severity::Impossible, std::fabs(v), *vgss,
                     fmt(where + ".gateSourceCutoffVoltage." + end +
                             " magnitude [V] exceeds gateSourceVoltageMax",
                         std::fabs(v), *vgss));
    }
}

}  // namespace

void check_jfets(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                 std::vector<std::string>& skipped) {
    const json* part = at(datasheet, "part");
    const std::optional<std::string> part_ch =
        part != nullptr ? jfet_subtype(*part, "part.subType") : std::nullopt;

    const json* e = at(datasheet, "electrical");
    const json* dies = array_at(datasheet, "dies", "dies");
    if (e != nullptr && !e->is_null())
        check_electrical(*e, "electrical", part_ch, ctx, out, skipped);
    if (dies != nullptr) {
        for (std::size_t i = 0; i < dies->size(); ++i) {
            const json& d = (*dies)[i];
            const std::string where = "dies[" + std::to_string(i) + "]";
            if (!d.is_object()) throw MalformedField(where + " is not an object");
            // A die states its channel only when it differs from the package's
            // (a complementary pair); otherwise the part's subType describes it.
            const auto die_ch = jfet_subtype(d, where + ".subType");
            const json* de = at(d, "electrical");
            if (de == nullptr || de->is_null()) {
                skipped.push_back("JFET_*:" + where + ":no-electrical");
                continue;
            }
            check_electrical(*de, where + ".electrical", die_ch ? die_ch : part_ch, ctx, out,
                             skipped);
        }
    }
    if ((e == nullptr || e->is_null()) && dies == nullptr) skipped.push_back("JFET_*");
}

}  // namespace tas
