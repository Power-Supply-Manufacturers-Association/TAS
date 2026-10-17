// SPDX-License-Identifier: MIT
// emscripten module: the same quality score in the browser.
//
//   const s = new Module.Scorer(specJsonText);
//   JSON.parse(s.score(recordJsonText, "mosfets", true, 0.8))   // {score, checks, ...} or null
#include "tas_metrics/metrics.hpp"

#include <emscripten/bind.h>

using namespace tas::metrics;

namespace {

std::string score_json(const Scorer& s, const std::string& record, const std::string& catalogue, bool schema_ok,
                       double url_alive) {
    const auto ps = s.score(json::parse(record), catalogue, schema_ok, url_alive);
    if (!ps) return "null";
    const Checks& c = ps->checks;
    json checks = {{"existence", c.existence}, {"traced", c.traced}, {"fields", c.fields},
                   {"url", c.url},             {"valid", c.valid}};
    checks["curves"] = c.curves ? json(*c.curves) : json(nullptr);
    return json{{"score", ps->score},
                {"checks", checks},
                {"kind", ps->kind},
                {"pn", ps->identity_pn},
                {"manufacturer", ps->identity_mfr},
                {"blade_codes", ps->blade_codes},
                {"blade_error", ps->blade_error}}
        .dump();
}

Scorer make_scorer(const std::string& spec) { return Scorer(json::parse(spec)); }
Scorer make_aliased_scorer(const std::string& spec, const std::string& aliases) {
    return Scorer(json::parse(spec), json::parse(aliases));
}

}  // namespace

EMSCRIPTEN_BINDINGS(tas_metrics) {
    emscripten::class_<Scorer>("Scorer")
        .constructor(&make_scorer)
        .constructor(&make_aliased_scorer)
        .function("score", &score_json);
    emscripten::function("normalisePn", &normalise_pn);
}
