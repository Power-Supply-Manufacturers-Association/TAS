// SPDX-License-Identifier: MIT
// Optocoupler physics checks (SAS optocoupler.json). `datasheet` is the
// datasheetInfo object: part.subType (the output stage), electrical (LED input,
// output stage, CTR, switching, and the shared PEAS isolationRating block).
//
// Only IMPOSSIBLE checks are implemented: band orderings and the positivity of
// ratings that define the part. TAS had no optocouplers.ndjson when this file
// was written (2026-10-18); SUSPICIOUS screens (CTR range per output stage, LED
// forward voltage window, isolation vs package creepage, switching time vs load
// resistance) wait for live rows to set thresholds.
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <optional>
#include <string>
#include <vector>

namespace tas {

void check_optocouplers(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                        std::vector<std::string>& skipped) {
    if (const json* part = at(datasheet, "part")) {
        if (const auto st = string_at(*part, "subType", "part.subType")) {
            static const char* KNOWN[] = {"phototransistor", "photodarlington", "logicOutput",
                                          "gateDriver",      "photoThyristor",  "photovoltaic",
                                          "linear"};
            bool known = false;
            for (const char* k : KNOWN)
                if (*st == k) known = true;
            if (!known)
                throw MalformedField("part.subType '" + *st +
                                     "' is not a SAS optocoupler subType");
        }
    }
    const json* e = at(datasheet, "electrical");
    if (e == nullptr || e->is_null()) {
        skipped.push_back("OPT_*");
        return;
    }
    if (!e->is_object()) throw MalformedField("electrical is not an object");

    // CHECK OPT_CTR_ORDER. A CTR band (or the bin a CTR-graded part is sold in)
    // is [minimum, maximum] around its typical: minimum <= nominal <= maximum is
    // what the three words mean. Compared on the ends the datasheet prints.
    const json* ctr = at(*e, "currentTransferRatio");
    if (ctr == nullptr || ctr->is_null()) {
        skipped.push_back("OPT_CTR_ORDER");
    } else {
        const std::string l = "electrical.currentTransferRatio";
        const auto mn = band_end(ctr, "minimum", l);
        const auto nom = band_end(ctr, "nominal", l);
        const auto mx = band_end(ctr, "maximum", l);
        if (mn && mx && *mn > *mx)
            emit(out, ctx, "OPT_CTR_ORDER", Severity::Impossible, *mn, *mx,
                 fmt(l + ".minimum exceeds its maximum", *mn, *mx));
        if (mn && nom && *mn > *nom)
            emit(out, ctx, "OPT_CTR_ORDER", Severity::Impossible, *mn, *nom,
                 fmt(l + ".minimum exceeds its nominal (typical)", *mn, *nom));
        if (nom && mx && *nom > *mx)
            emit(out, ctx, "OPT_CTR_ORDER", Severity::Impossible, *nom, *mx,
                 fmt(l + ".nominal (typical) exceeds its maximum", *nom, *mx));
    }

    // CHECK OPT_ISOLATION_POSITIVE. V_ISO (withstandVoltageRms) is the voltage
    // the barrier withstands for the qualification test -- the rating that makes
    // the part an isolator at all. The shared PEAS isolationRating admits 0
    // (minimum 0, written for blocks that are optional elsewhere); on an
    // optocoupler, whose schema requires V_ISO, a zero barrier is no barrier.
    const json* iso = at(*e, "isolation");
    const auto viso = iso != nullptr ? scalar_at(*iso, {"withstandVoltageRms"}) : std::nullopt;
    if (!viso) {
        skipped.push_back("OPT_ISOLATION_POSITIVE");
    } else if (*viso <= 0) {
        emit(out, ctx, "OPT_ISOLATION_POSITIVE", Severity::Impossible, *viso, 0,
             fmt("electrical.isolation.withstandVoltageRms [V] is not positive", *viso));
    }

    // CHECK OPT_LED_CURRENT_POSITIVE. inputForwardCurrentMax is the LED's
    // absolute-maximum continuous forward current; the coupler works by driving
    // that LED, so a rating of zero or below admits no operating point. (The
    // schema's exclusiveMinimum says the same; this guards records handed to the
    // validator without schema validation.)
    const auto ifmax = scalar_at(*e, {"inputForwardCurrentMax"});
    if (!ifmax) {
        skipped.push_back("OPT_LED_CURRENT_POSITIVE");
    } else if (*ifmax <= 0) {
        emit(out, ctx, "OPT_LED_CURRENT_POSITIVE", Severity::Impossible, *ifmax, 0,
             fmt("electrical.inputForwardCurrentMax [A] is not positive", *ifmax));
    }
}

}  // namespace tas
