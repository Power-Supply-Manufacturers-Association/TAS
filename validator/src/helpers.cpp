// SPDX-License-Identifier: Apache-2.0
#include "tas_validator/helpers.hpp"

#include <cctype>
#include <cmath>
#include <sstream>

namespace tas {

std::optional<double> scalar(const json* field, const std::string& path) {
    if (field == nullptr || field->is_null()) return std::nullopt;

    if (field->is_number()) {
        double v = field->get<double>();
        if (!std::isfinite(v)) throw MalformedField(path + ": non-finite number");
        return v;
    }

    if (field->is_object()) {
        for (const char* key : {"nominal", "minimum", "maximum"}) {
            if (field->contains(key) && (*field)[key].is_number()) {
                double v = (*field)[key].get<double>();
                if (!std::isfinite(v)) throw MalformedField(path + "." + key + ": non-finite number");
                return v;
            }
        }
        // An object with no usable numeric member is malformed for a scalar field.
        throw MalformedField(path + ": object has no numeric nominal/minimum/maximum");
    }

    throw MalformedField(path + ": expected number or {nominal,minimum,maximum}, got " +
                         std::string(field->type_name()));
}

std::optional<double> scalar_at(const json& node, const std::vector<std::string>& path) {
    const json* cur = &node;
    std::string label;
    for (const auto& key : path) {
        if (!label.empty()) label += ".";
        label += key;
        if (cur == nullptr || !cur->is_object() || !cur->contains(key)) return std::nullopt;
        cur = &(*cur)[key];
    }
    return scalar(cur, label);
}

std::optional<double> box_volume_m3(const json& dims) {
    if (!dims.is_object()) return std::nullopt;
    auto l = scalar_at(dims, {"length"});
    auto w = scalar_at(dims, {"width"});
    auto h = scalar_at(dims, {"height"});
    if (!l || !w || !h) return std::nullopt;
    // A non-positive dimension is bad data, not a fatal type error: return nullopt
    // so the volume-dependent check skips while the caller surfaces it as a finding.
    if (*l <= 0 || *w <= 0 || *h <= 0) return std::nullopt;
    return (*l) * (*w) * (*h);
}

bool has_nonpositive_dimension(const json& dims) {
    if (!dims.is_object()) return false;
    for (const char* k : {"length", "width", "height"}) {
        auto v = scalar_at(dims, {k});  // throws MalformedField on wrong type (intended)
        if (v && *v <= 0) return true;
    }
    return false;
}

std::string fmt(const std::string& msg, double value) {
    std::ostringstream os;
    os << msg << " (value=" << value << ")";
    return os.str();
}

std::string fmt(const std::string& msg, double value, double threshold) {
    std::ostringstream os;
    os << msg << " (value=" << value << ", threshold=" << threshold << ")";
    return os.str();
}

std::string norm_tech(const json* field) {
    if (field == nullptr || !field->is_string()) return "";
    std::string out;
    for (char c : field->get<std::string>()) {
        if (std::isalnum(static_cast<unsigned char>(c)))
            out += static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return out;
}

bool tech_has(const std::string& normalised, const char* needle) {
    return normalised.find(needle) != std::string::npos;
}

}  // namespace tas
