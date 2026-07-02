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

}  // namespace tas
