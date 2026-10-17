// SPDX-License-Identifier: MIT
// Resistor physics checks. `datasheet` is the datasheetInfo object:
//   electrical.{resistance,tolerance,temperatureCoefficient,powerRating,maxVoltage},
//   part.technology, mechanical.{length,width,height}.
#include "tas_validator/eseries.hpp"
#include "tas_validator/helpers.hpp"
#include "tas_validator/thresholds.hpp"
#include "tas_validator/validator.hpp"

#include <cctype>
#include <cmath>
#include <optional>
#include <regex>
#include <sstream>
#include <string>

namespace tas {
namespace {

std::string upper_of(const json* field) {
    if (field == nullptr || !field->is_string()) return "";
    std::string s = field->get<std::string>();
    for (char& c : s) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return s;
}

bool starts_with(const std::string& s, const char* prefix) {
    return s.rfind(prefix, 0) == 0;
}

// "12R5" / "1K45" / "96R250": digits with exactly one multiplier letter that
// is also the decimal point (R = x1, K = x1e3, M = x1e6).
std::optional<double> decode_letter_point(const std::string& code) {
    std::size_t pos = std::string::npos;
    double mult = 0.0;
    for (std::size_t i = 0; i < code.size(); ++i) {
        const char c = code[i];
        if (std::isdigit(static_cast<unsigned char>(c))) continue;
        if (pos != std::string::npos) return std::nullopt;  // two letters
        pos = i;
        if (c == 'R') mult = 1.0;
        else if (c == 'K') mult = 1e3;
        else if (c == 'M') mult = 1e6;
        else return std::nullopt;
    }
    if (pos == std::string::npos) return std::nullopt;
    const std::string digits = code.substr(0, pos) + "." + code.substr(pos + 1);
    if (digits == ".") return std::nullopt;
    return std::stod("0" + digits) * mult;
}

// MIL style "3483" (three significant figures + decade exponent) or "12R5"
// (R as decimal point); RL's two-figure "102" form when `figures` is 2.
std::optional<double> decode_mil_digits(const std::string& code, int figures) {
    if (code.find('R') != std::string::npos) return decode_letter_point(code);
    if (static_cast<int>(code.size()) != figures + 1) return std::nullopt;
    const double sig = std::stod(code.substr(0, figures));
    const int exp = code[figures] - '0';
    return sig * std::pow(10.0, exp);
}

// MIL-PRF-55342 value code: three digits and a letter that is both multiplier
// and decimal locator (Vishay datasheet 60018). Only the letters the catalogue
// was verified on are decoded (A/D/G/J = ohm, B/E/H/K = kilohm); any other
// letter leaves the code undecoded and the E-series check runs.
std::optional<double> decode_m55342(const std::string& code) {
    std::size_t pos = std::string::npos;
    double mult = 0.0;
    for (std::size_t i = 0; i < code.size(); ++i) {
        const char c = code[i];
        if (std::isdigit(static_cast<unsigned char>(c))) continue;
        if (pos != std::string::npos) return std::nullopt;
        pos = i;
        switch (c) {
            case 'A': case 'D': case 'G': case 'J': mult = 1.0; break;
            case 'B': case 'E': case 'H': case 'K': mult = 1e3; break;
            default: return std::nullopt;
        }
    }
    if (pos == std::string::npos) return std::nullopt;
    return std::stod("0" + code.substr(0, pos) + "." + code.substr(pos + 1)) * mult;
}

// The resistance encoded in the ordering code of a qualified ordering-code
// series (Vishay CMF, MIL RN/RNC/RNR/RL/RLR, M/D55342, Vishay TNPW). These are
// catalogued at any three-figure value in the style's range, so their value is
// whatever the code says. nullopt when the series is not one of these, the
// part number does not follow the series' code layout, or the code does not
// decode -- the caller then runs the E-series check as usual.
std::optional<double> ordering_code_value(const json& datasheet) {
    const std::string series = upper_of(at(datasheet, "part", "series"));
    const std::string pn = upper_of(at(datasheet, "part", "partNumber"));
    if (series.empty() || pn.empty()) return std::nullopt;
    std::smatch m;
    if (starts_with(series, "CMF")) {
        static const std::regex RE(R"(^CMF\d{2}([0-9RKM]{6})[A-Z])");
        if (std::regex_search(pn, m, RE)) return decode_letter_point(m[1].str());
    } else if (starts_with(series, "TNPW")) {
        static const std::regex RE(R"(^TNPW\d{4}([0-9RKM]{4})[A-Z])");
        if (std::regex_search(pn, m, RE)) return decode_letter_point(m[1].str());
    } else if (starts_with(series, "M55342") || starts_with(series, "D55342")) {
        static const std::regex RE(R"(^[MD]55342[A-Z]\d{2}[A-Z]([0-9A-Z]{4}))");
        if (std::regex_search(pn, m, RE)) return decode_m55342(m[1].str());
    } else if (starts_with(series, "RLR") || starts_with(series, "RN")) {
        static const std::regex RE(R"(^(RNC|RNR|RLR|RN)\d{2}[A-Z]([0-9R]{4})[A-Z])");
        if (std::regex_search(pn, m, RE)) return decode_mil_digits(m[2].str(), 3);
    } else if (starts_with(series, "RL")) {
        static const std::regex RE(R"(^RL\d{2}[A-Z]([0-9R]{3})[A-Z])");
        if (std::regex_search(pn, m, RE)) return decode_mil_digits(m[1].str(), 2);
    }
    return std::nullopt;
}

}  // namespace

void check_resistors(const json& datasheet, const Ctx& ctx, std::vector<Finding>& out,
                     std::vector<std::string>& skipped) {
    const json* elec = at(datasheet, "electrical");
    if (elec == nullptr) {
        skipped.push_back("RES_*");
        return;
    }
    auto R = scalar_at(*elec, {"resistance"});
    auto P = scalar_at(*elec, {"powerRating"});
    auto Vmax = scalar_at(*elec, {"maxVoltage"});

    // Geometry: length/width/height live directly under mechanical.
    const json* mech = at(datasheet, "mechanical");
    auto L = mech ? scalar_at(*mech, {"length"}) : std::nullopt;
    auto W = mech ? scalar_at(*mech, {"width"}) : std::nullopt;

    // CHECK: resistance range. resistance == 0 is a real 0-ohm jumper/link (Yageo
    // YC-series arrays, many vendors), so only a NEGATIVE resistance is impossible.
    if (R) {
        if (*R < 0)
            emit(out, ctx, "RES_R_RANGE", Severity::Impossible, *R, 0, "resistance < 0");
        else if (*R > 0 && *R < thr::RES_R_MIN_SUS)
            emit(out, ctx, "RES_R_RANGE", Severity::Suspicious, *R, thr::RES_R_MIN_SUS,
                 fmt("resistance below manufacturable floor [ohm]", *R, thr::RES_R_MIN_SUS));
        else if (*R > thr::RES_R_MAX_SUS)
            emit(out, ctx, "RES_R_RANGE", Severity::Suspicious, *R, thr::RES_R_MAX_SUS,
                 fmt("resistance above manufacturable ceiling [ohm]", *R, thr::RES_R_MAX_SUS));
    } else {
        skipped.push_back("RES_R_RANGE");
    }

    // CHECK: positivity of the power and voltage ratings (were silently accepted).
    if (P && *P <= 0)
        emit(out, ctx, "RES_POWER_SIZE", Severity::Impossible, *P, 0, "powerRating <= 0");
    if (Vmax && *Vmax <= 0)
        emit(out, ctx, "RES_MAXV_SIZE", Severity::Impossible, *Vmax, 0, "maxVoltage <= 0");

    // CHECK: power dissipation density over footprint.
    if (P && L && W && *L > 0 && *W > 0) {
        double area_mm2 = (*L * 1000.0) * (*W * 1000.0);
        double density = *P / area_mm2;  // W/mm^2
        if (density > thr::RES_POWER_PER_MM2_IMP)
            emit(out, ctx, "RES_POWER_SIZE", Severity::Impossible, density,
                 thr::RES_POWER_PER_MM2_IMP,
                 fmt("powerRating/footprint impossibly high [W/mm^2]", density,
                     thr::RES_POWER_PER_MM2_IMP));
        else if (density > thr::RES_POWER_PER_MM2_SUS)
            emit(out, ctx, "RES_POWER_SIZE", Severity::Suspicious, density,
                 thr::RES_POWER_PER_MM2_SUS, fmt("powerRating/footprint high [W/mm^2]", density,
                                                 thr::RES_POWER_PER_MM2_SUS));
    }

    // CHECK (NEW): max-voltage field over body length.
    if (Vmax && L && *L > 0) {
        double field = *Vmax / *L;  // V/m
        if (field > thr::RES_FIELD_VPM_IMP)
            emit(out, ctx, "RES_MAXV_SIZE", Severity::Impossible, field, thr::RES_FIELD_VPM_IMP,
                 fmt("maxVoltage/length implies impossible field [V/m]", field,
                     thr::RES_FIELD_VPM_IMP));
        else if (field > thr::RES_FIELD_VPM_SUS)
            emit(out, ctx, "RES_MAXV_SIZE", Severity::Suspicious, field, thr::RES_FIELD_VPM_SUS,
                 fmt("maxVoltage/length implies high field [V/m]", field, thr::RES_FIELD_VPM_SUS));
    }

    // (Removed RES_POWER_V_R: maxVoltage^2/R >> powerRating is normal for high-ohm
    // voltage-limited resistors, and ~76% of catalog parts back-compute maxVoltage as
    // sqrt(P*R) so the field carries no independent information. Real-vs-synthetic
    // discrimination for resistors belongs to the E-series preferred-value check.)

    // CHECK (NEW): temperature coefficient magnitude.
    if (auto tc = scalar_at(*elec, {"temperatureCoefficient"})) {
        if (std::fabs(*tc) > thr::RES_TEMPCO_PPM_SUS)
            emit(out, ctx, "RES_TEMPCO", Severity::Suspicious, *tc, thr::RES_TEMPCO_PPM_SUS,
                 fmt("|temperatureCoefficient| very large [ppm/C]", *tc, thr::RES_TEMPCO_PPM_SUS));
    }

    // CHECK (NEW): tolerance band.
    if (auto tol = scalar_at(*elec, {"tolerance"})) {
        if (*tol <= 0)
            emit(out, ctx, "RES_TOLERANCE", Severity::Impossible, *tol, 0, "tolerance <= 0");
        else if (*tol > thr::RES_TOL_MAX_SUS)
            emit(out, ctx, "RES_TOLERANCE", Severity::Suspicious, *tol, thr::RES_TOL_MAX_SUS,
                 fmt("tolerance fraction very large", *tol, thr::RES_TOL_MAX_SUS));
    }

    // CHECK (NEW, anti-synthesis): the nominal resistance should land on an IEC
    // 60063 E-series preferred value, and not carry more significant figures than a
    // real preferred value. Skip sub-0.1 ohm sense/shunt parts (non-E-series by
    // design). SUSPICIOUS only — a real-vs-fabricated signal, not a physics bound.
    if (const json* rf = at(*elec, "resistance")) {
        std::optional<double> rnom;
        if (rf->is_number())
            rnom = rf->get<double>();
        else if (rf->is_object() && rf->contains("nominal") && (*rf)["nominal"].is_number())
            rnom = (*rf)["nominal"].get<double>();
        std::string tech = norm_tech(at(datasheet, "part", "technology"));
        bool sense = tech_has(tech, "shunt") || tech_has(tech, "current") ||
                     tech_has(tech, "sense");
        // Wirewound and metal-oxide power resistors are catalogued at free
        // ordering-code values (Vishay RWR datasheet 30203: "see MIL-PRF-39007
        // QPL for available resistance values"; RWR/CW/RER/ROX/RNX/PTF/CPW and
        // TE CGS rows store 0.15, 25.2, 57, 3.7 ohm ...), not on an E-series.
        bool free_valued = tech_has(tech, "wirewound") || tech_has(tech, "metaloxide");
        if (rnom && *rnom >= 0.1 && !sense && !free_valued && !eseries::on_grid(*rnom)) {
            // E192 is the series of the <= 0.5 % classes; it needs the tolerance.
            const bool e192 = eseries::on_e192(*rnom, thr::RES_E192_WINDOW);
            const auto tol = scalar_at(*elec, {"tolerance"});
            // A qualified ordering-code series is accepted only when the stored
            // value is exactly the value its own ordering code encodes.
            const auto coded = ordering_code_value(datasheet);
            const bool code_match = coded && std::fabs(*coded - *rnom) <= 1e-9 * *rnom;
            if (code_match || (e192 && tol && *tol <= thr::RES_E192_TOLERANCE_MAX)) {
                // accepted
            } else if (e192 && !tol) {
                skipped.push_back("RES_E_SERIES");  // E192 value, tolerance class unknown
            } else {
                emit(out, ctx, "RES_E_SERIES", Severity::Suspicious, *rnom, 0,
                     fmt("resistance is not an IEC 60063 E-series preferred value [ohm]", *rnom));
            }
        }
        if (rnom && *rnom >= 0.1 && !sense) {
            if (eseries::sig_figs(*rnom) > 4)
                emit(out, ctx, "GEN_OVERPRECISION", Severity::Suspicious, *rnom, 0,
                     fmt("resistance nominal carries more significant figures than a preferred "
                         "value [ohm]",
                         *rnom));
        }
    }
}

}  // namespace tas
