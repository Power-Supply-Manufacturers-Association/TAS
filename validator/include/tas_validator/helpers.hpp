// SPDX-License-Identifier: MIT
// TAS Physics Validator — shared helpers for navigating MAS/TAS datasheet JSON.
//
// Records are draft-2020-12 documents. Physical scalars are either bare numbers
// or {nominal, minimum, maximum} objects. All values are SI (metre, henry,
// farad, ohm, hertz, volt, ampere, kelvin/celsius).
//
// Guardrail (global CLAUDE.md): no fallbacks. A *malformed* field (present but
// the wrong shape) throws MalformedField. A *missing* field yields std::nullopt
// so the calling check can skip itself — missing data must never read as valid.
#pragma once

#include <nlohmann/json.hpp>

#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace tas {

using json = nlohmann::json;

// Thrown when a required field is present but cannot be interpreted (wrong type,
// non-finite). Distinct from "absent", which is represented by std::nullopt.
struct MalformedField : std::runtime_error {
    explicit MalformedField(const std::string& what) : std::runtime_error(what) {}
};

// Walk a dotted path of object keys, e.g. at(part, "manufacturerInfo",
// "datasheetInfo", "electrical"). Returns nullptr if any segment is absent or
// not an object. Never throws.
template <typename... Keys>
const json* at(const json& node, Keys&&... keys) {
    const json* cur = &node;
    for (const std::string key : {std::string(keys)...}) {
        if (cur == nullptr || !cur->is_object() || !cur->contains(key)) return nullptr;
        cur = &(*cur)[key];
    }
    return cur;
}

// Extract a scalar from a field that is either a bare number or a
// {nominal|minimum|maximum} object (preferring nominal, then minimum, then
// maximum — mirrors the Proteus extract_scalar). Returns:
//   - std::nullopt        if the field is absent or JSON null
//   - the double          if it is a finite number (or such an object member)
// Throws MalformedField if present-but-uninterpretable (e.g. a string, or an
// object with no usable numeric member).
std::optional<double> scalar(const json* field, const std::string& path);

// Convenience: scalar(at(node, keys...)). The final key is also used in the
// error/skip path label.
std::optional<double> scalar_at(const json& node, const std::vector<std::string>& path);

// Volume in m^3 from three linear dimensions in metres. nullopt if any is absent
// OR non-positive (the caller may emit its own finding for a bad dimension);
// throws MalformedField only if a present dimension is the wrong TYPE.
std::optional<double> box_volume_m3(const json& mechanical_dims_or_node);

// True if `dims` is an object with a present length/width/height that is <= 0.
// Lets a family check surface a bad dimension as a finding instead of aborting.
bool has_nonpositive_dimension(const json& dims);

// Format a finding message: "<msg> (value=<a>)" or, with the 3-arg overload,
// "<msg> (value=<a>, threshold=<b>)". Two overloads (not a default arg) so a
// genuine threshold of 0 is still printed — no in-band sentinel.
std::string fmt(const std::string& msg, double value);
std::string fmt(const std::string& msg, double value, double threshold);

// Normalise a technology / material string to a lowercase, punctuation-stripped
// token for bucket lookups (e.g. "Alum. Electrolytic" -> "alumelectrolytic",
// "MLCC Class I" -> "mlccclassi"). Returns "" for absent/non-string.
std::string norm_tech(const json* field);

// True if `haystack` (already norm_tech'd or raw string) contains `needle`.
bool tech_has(const std::string& normalised, const char* needle);

// True if `url` is shaped like a search-engine query (?q=, /search?, ?search=)
// rather than a direct link to a document. The ONE definition: this used to be
// duplicated independently in corpus.cpp and validator.cpp -- exactly the trap
// ABT #397 is named for, applied to a predicate instead of a build: tightening
// one copy (e.g. to exempt a manufacturer's own product-finder) silently leaves
// the other one condemning under the old, looser rule.
bool is_search_query_url(const std::string& url);

// True if `component_obj`'s manufacturerInfo.datasheetUrl OR any
// datasheetInfo.provenance[].sourceUrl matches is_search_query_url. Used to
// corroborate a per-record statistical signature (e.g. MOS_CAP_FORMULA) with the
// citation shape most confirmed fabricated batches share -- NOT a standalone
// verdict (see GEN_CITATION_SEARCH_QUERY for that), and not load-bearing on its
// own for any cohort-shaped rule (citation practice is a property of a sourcing
// BATCH, not of the part -- see GEN_COHORT_LETTER_SUFFIX's own comment).
bool has_search_query_citation(const json& datasheet, const json* component_obj);

// The most GENEROUS steady-state power ceiling the record's own thermal block
// implies, in watts: max over the references it carries of (Tjmax - 25) / Rth,
// with the case held at 25 C. `datasheet` is a family datasheetInfo object; the
// thermal block is read from datasheet.thermal.
//
// Returns nullopt -- so the calling check SKIPS -- unless
// thermalResistanceJunctionCase is present and positive and
// junctionTemperatureMax exceeds 25 C.
//
// THE TRAP this exists for (adversarial physics review, 2026-09-04, and it
// produced that review's only near-miss false accusation): powerDissipation in
// this corpus mixes two incompatible references with NO discriminator field --
// some rows are the datasheet's Ptot at Tcase = 25 C, others the ambient-
// referenced P_D of a minimum-pad application. A case-referenced Pd measured
// against an ambient-referenced Rth(j-a) is off by the ratio of the two
// resistances (30x is ordinary), which condemns perfectly good records:
// Vishay SIHP065N60E-GE3 stores a real 250 W Ptot with only Rth(j-a) = 62 K/W,
// i.e. 124x the 2.0 W that reference implies. So:
//   * the j-c reference is REQUIRED -- an ambient-only record is skipped, never
//     accused;
//   * the ceiling is the MAXIMUM over the available references, which also
//     absorbs a record whose j-c and j-a values were written into each other's
//     fields (then the smaller resistance, whichever field it sits in, wins).
// Only a Pd that contradicts the record's own j-c path by a wide margin can then
// fire.
std::optional<double> thermal_power_ceiling(const json& datasheet);

}  // namespace tas
