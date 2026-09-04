// SPDX-License-Identifier: MIT
#include "tas_validator/helpers.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <regex>
#include <sstream>
#include <vector>

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

namespace {

// ASCII transliteration of Latin-1 Supplement (U+00C0..U+00FF) and Latin
// Extended-A (U+0100..U+017F). norm_tech used to DELETE every non-ASCII byte,
// which made an accented brand name and its ASCII-folded twin normalise to
// different strings ("Wurth Elektronik" -> "wurthelektronik" but
// "Wurth Elektronik" -> "wrthelektronik"). Every equality test built on
// norm_tech silently lost the pair - GEN_SERIES_IS_MANUFACTURER above all,
// whose original defect (ABT #506) was an importer copying a vendor feed's
// brand column, and feeds routinely ASCII-fold. Transliterating instead of
// deleting repairs every consumer at once.
const char* translit_latin1(unsigned cp) {
    switch (cp) {
        case 0xC0: case 0xC1: case 0xC2: case 0xC3: case 0xC4: case 0xC5:
        case 0xE0: case 0xE1: case 0xE2: case 0xE3: case 0xE4: case 0xE5: return "a";
        case 0xC6: case 0xE6: return "ae";
        case 0xC7: case 0xE7: return "c";
        case 0xC8: case 0xC9: case 0xCA: case 0xCB:
        case 0xE8: case 0xE9: case 0xEA: case 0xEB: return "e";
        case 0xCC: case 0xCD: case 0xCE: case 0xCF:
        case 0xEC: case 0xED: case 0xEE: case 0xEF: return "i";
        case 0xD0: case 0xF0: return "d";
        case 0xD1: case 0xF1: return "n";
        case 0xD2: case 0xD3: case 0xD4: case 0xD5: case 0xD6: case 0xD8:
        case 0xF2: case 0xF3: case 0xF4: case 0xF5: case 0xF6: case 0xF8: return "o";
        case 0xD9: case 0xDA: case 0xDB: case 0xDC:
        case 0xF9: case 0xFA: case 0xFB: case 0xFC: return "u";
        case 0xDD: case 0xFD: case 0xFF: return "y";
        case 0xDE: case 0xFE: return "th";
        case 0xDF: return "ss";
        default: return nullptr;
    }
}

// U+0100..U+017F, one entry per code point, base letter only.
const char* const LATIN_EXT_A[0x80] = {
    "a","a","a","a","a","a",              // 0100-0105
    "c","c","c","c","c","c","c","c",      // 0106-010D
    "d","d","d","d",                      // 010E-0111
    "e","e","e","e","e","e","e","e","e","e", // 0112-011B
    "g","g","g","g","g","g","g","g",      // 011C-0123
    "h","h","h","h",                      // 0124-0127
    "i","i","i","i","i","i","i","i","i","i", // 0128-0131
    "ij","ij",                            // 0132-0133
    "j","j",                              // 0134-0135
    "k","k","k",                          // 0136-0138
    "l","l","l","l","l","l","l","l","l","l", // 0139-0142
    "n","n","n","n","n","n","n","n","n",  // 0143-014B
    "o","o","o","o","o","o",              // 014C-0151
    "oe","oe",                            // 0152-0153
    "r","r","r","r","r","r",              // 0154-0159
    "s","s","s","s","s","s","s","s",      // 015A-0161
    "t","t","t","t","t","t",              // 0162-0167
    "u","u","u","u","u","u","u","u","u","u","u","u", // 0168-0173
    "w","w",                              // 0174-0175
    "y","y","y",                          // 0176-0178
    "z","z","z","z","z","z",              // 0179-017E
    "s"                                   // 017F
};

// Minimal UTF-8 decoder. Invalid bytes yield no code point (they are skipped),
// which preserves the old behaviour for genuinely undecodable input.
std::vector<unsigned> utf8_codepoints(const std::string& s) {
    std::vector<unsigned> cps;
    size_t i = 0;
    while (i < s.size()) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        unsigned cp = 0;
        size_t extra = 0;
        if (c < 0x80) { cp = c; extra = 0; }
        else if ((c & 0xE0) == 0xC0) { cp = c & 0x1Fu; extra = 1; }
        else if ((c & 0xF0) == 0xE0) { cp = c & 0x0Fu; extra = 2; }
        else if ((c & 0xF8) == 0xF0) { cp = c & 0x07u; extra = 3; }
        else { ++i; continue; }                       // stray continuation byte
        if (i + extra >= s.size()) break;             // truncated sequence
        bool ok = true;
        for (size_t k = 1; k <= extra; ++k) {
            const unsigned char cc = static_cast<unsigned char>(s[i + k]);
            if ((cc & 0xC0) != 0x80) { ok = false; break; }
            cp = (cp << 6) | (cc & 0x3Fu);
        }
        if (!ok) { ++i; continue; }
        cps.push_back(cp);
        i += extra + 1;
    }
    return cps;
}

}  // namespace

std::string norm_tech(const json* field) {
    if (field == nullptr || !field->is_string()) return "";
    std::string out;
    for (unsigned cp : utf8_codepoints(field->get<std::string>())) {
        if (cp < 0x80) {
            if (std::isalnum(static_cast<unsigned char>(cp)))
                out += static_cast<char>(std::tolower(static_cast<int>(cp)));
            continue;
        }
        const char* t = nullptr;
        if (cp >= 0xC0 && cp <= 0xFF) t = translit_latin1(cp);
        else if (cp >= 0x100 && cp <= 0x17F) t = LATIN_EXT_A[cp - 0x100];
        if (t != nullptr) out += t;   // unmapped code points are dropped, as before
    }
    return out;
}

bool tech_has(const std::string& normalised, const char* needle) {
    return normalised.find(needle) != std::string::npos;
}

bool is_search_query_url(const std::string& url) {
    static const std::regex RE(R"(\?q=|/search\?|\?search=)", std::regex::icase);
    return std::regex_search(url, RE);
}

bool has_search_query_citation(const json& datasheet, const json* component_obj) {
    if (component_obj != nullptr) {
        const json* mi = at(*component_obj, "manufacturerInfo");
        if (mi != nullptr && mi->contains("datasheetUrl") && (*mi)["datasheetUrl"].is_string() &&
            is_search_query_url((*mi)["datasheetUrl"].get<std::string>()))
            return true;
    }
    const json* prov = at(datasheet, "provenance");
    if (prov != nullptr && prov->is_array())
        for (const auto& p : *prov)
            if (p.is_object() && p.contains("sourceUrl") && p["sourceUrl"].is_string() &&
                is_search_query_url(p["sourceUrl"].get<std::string>()))
                return true;
    return false;
}

std::optional<double> thermal_power_ceiling(const json& datasheet) {
    const json* th = at(datasheet, "thermal");
    if (th == nullptr) return std::nullopt;
    auto tjmax = scalar_at(*th, {"junctionTemperatureMax"});
    auto rjc = scalar_at(*th, {"thermalResistanceJunctionCase"});
    if (!tjmax || *tjmax <= 25.0) return std::nullopt;
    // Junction-to-case is REQUIRED: without it we cannot tell an ambient-
    // referenced Rth from a case-referenced Pd, and accusing that pair is the
    // documented false positive this helper exists to prevent.
    if (!rjc || *rjc <= 0) return std::nullopt;
    double ceiling = (*tjmax - 25.0) / *rjc;
    if (auto rja = scalar_at(*th, {"thermalResistanceJunctionAmbient"})) {
        if (*rja > 0) ceiling = std::max(ceiling, (*tjmax - 25.0) / *rja);
    }
    return ceiling;
}

}  // namespace tas
