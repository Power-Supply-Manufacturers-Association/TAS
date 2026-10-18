// SPDX-License-Identifier: MIT
#include "tas_validator/eseries.hpp"

#include <cctype>
#include <cmath>

namespace tas::eseries {
namespace {

// 3-significant-figure mantissas normalised to [100, 1000).
// E24 (covers E6/E12/E24): the 2-sig-fig grid scaled by 10.
constexpr double E24[] = {100, 110, 120, 130, 150, 160, 180, 200, 220, 240, 270, 300,
                          330, 360, 390, 430, 470, 510, 560, 620, 680, 750, 820, 910};
// E96 (covers E48/E96): the 3-sig-fig grid.
constexpr double E96[] = {
    100, 102, 105, 107, 110, 113, 115, 118, 121, 124, 127, 130, 133, 137, 140, 143,
    147, 150, 154, 158, 162, 165, 169, 174, 178, 182, 187, 191, 196, 200, 205, 210,
    215, 221, 226, 232, 237, 243, 249, 255, 261, 267, 274, 280, 287, 294, 301, 309,
    316, 324, 332, 340, 348, 357, 365, 374, 383, 392, 402, 412, 422, 432, 442, 453,
    464, 475, 487, 499, 511, 523, 536, 549, 562, 576, 590, 604, 619, 634, 649, 665,
    681, 698, 715, 732, 750, 768, 787, 806, 825, 845, 866, 887, 909, 931, 953, 976};

// E192 (IEC 60063 table; E96 is every other entry). 920 is the standard's
// exception to 10^(i/192) rounding (919).
constexpr double E192[] = {
    100, 101, 102, 104, 105, 106, 107, 109, 110, 111, 113, 114, 115, 117, 118, 120,
    121, 123, 124, 126, 127, 129, 130, 132, 133, 135, 137, 138, 140, 142, 143, 145,
    147, 149, 150, 152, 154, 156, 158, 160, 162, 164, 165, 167, 169, 172, 174, 176,
    178, 180, 182, 184, 187, 189, 191, 193, 196, 198, 200, 203, 205, 208, 210, 213,
    215, 218, 221, 223, 226, 229, 232, 234, 237, 240, 243, 246, 249, 252, 255, 258,
    261, 264, 267, 271, 274, 277, 280, 284, 287, 291, 294, 298, 301, 305, 309, 312,
    316, 320, 324, 328, 332, 336, 340, 344, 348, 352, 357, 361, 365, 370, 374, 379,
    383, 388, 392, 397, 402, 407, 412, 417, 422, 427, 432, 437, 442, 448, 453, 459,
    464, 470, 475, 481, 487, 493, 499, 505, 511, 517, 523, 530, 536, 542, 549, 556,
    562, 569, 576, 583, 590, 597, 604, 612, 619, 626, 634, 642, 649, 657, 665, 673,
    681, 690, 698, 706, 715, 723, 732, 741, 750, 759, 768, 777, 787, 796, 806, 816,
    825, 835, 845, 856, 866, 876, 887, 898, 909, 920, 931, 942, 953, 965, 976, 988};
static_assert(sizeof(E192) / sizeof(E192[0]) == 192, "IEC 60063 E192 has 192 values");

// Mantissa normalised to [100, 1000).
double mantissa(double value) {
    double m = value / std::pow(10.0, std::floor(std::log10(value))) * 100.0;
    while (m < 100.0) m *= 10.0;
    while (m >= 1000.0) m /= 10.0;
    return m;
}

template <std::size_t N>
double nearest_rel(double m, const double (&grid)[N]) {
    double best = 1e9;
    for (double g : grid) {
        double d = std::fabs(m - g) / g;
        if (d < best) best = d;
    }
    return best;
}

}  // namespace

bool on_grid(double value) {
    if (!(value > 0) || !std::isfinite(value)) return false;
    const double m = mantissa(value);
    // Decade-boundary wrap: a value like 1e-5 stored as 9.9999e-6 normalises to
    // ~1000, which is 100 of the next decade — itself a preferred value.
    return nearest_rel(m, E24) <= 0.012 || nearest_rel(m, E96) <= 0.006 ||
           std::fabs(m - 1000.0) / 1000.0 <= 0.006;
}

bool on_e192(double value, double window) {
    if (!(value > 0) || !std::isfinite(value)) return false;
    const double m = mantissa(value);
    return nearest_rel(m, E192) <= window || std::fabs(m - 1000.0) / 1000.0 <= window;
}

int sig_figs(double value) {
    value = std::fabs(value);
    if (!(value > 0) || !std::isfinite(value)) return 1;
    double decade = std::floor(std::log10(value));
    for (int k = 1; k <= 6; ++k) {
        double scale = std::pow(10.0, k - 1 - decade);
        double rounded = std::round(value * scale) / scale;
        if (std::fabs(rounded - value) <= value * 1e-9) return k;
    }
    return 7;
}

namespace {
bool is_digit(char c) { return std::isdigit(static_cast<unsigned char>(c)) != 0; }
bool is_alnum(char c) { return std::isalnum(static_cast<unsigned char>(c)) != 0; }
}  // namespace

std::vector<double> letter_point_values(const std::string& code, const std::string& letters,
                                        bool cut_digit_run) {
    std::vector<double> out;
    for (std::size_t p = 1; p < code.size(); ++p) {
        const char letter = static_cast<char>(std::toupper(static_cast<unsigned char>(code[p])));
        if (letters.find(letter) == std::string::npos || !is_digit(code[p - 1])) continue;
        double mult = 0.0;
        if (letter == 'R') mult = 1.0;
        else if (letter == 'K') mult = 1e3;
        else if (letter == 'M') mult = 1e6;
        else continue;
        std::size_t a = p;
        while (a > 0 && is_digit(code[a - 1])) --a;
        std::size_t b = p + 1;
        while (b < code.size() && is_digit(code[b])) ++b;
        const std::string frac = code.substr(p + 1, b - p - 1);
        const std::size_t last_start = cut_digit_run ? p - 1 : a;
        for (std::size_t s = a; s <= last_start; ++s)
            out.push_back(std::stod("0" + code.substr(s, p - s) + "." + frac + "0") * mult);
    }
    return out;
}

std::vector<double> three_digit_code_values(const std::string& code) {
    std::vector<double> out;
    for (std::size_t a = 1; a + 3 <= code.size(); ++a) {
        if (is_alnum(code[a - 1])) continue;
        if (!is_digit(code[a]) || !is_digit(code[a + 1]) || !is_digit(code[a + 2])) continue;
        if (a + 3 < code.size() && is_digit(code[a + 3])) continue;
        const double sig = (code[a] - '0') * 10.0 + (code[a + 1] - '0');
        out.push_back(sig * std::pow(10.0, code[a + 2] - '0'));
    }
    return out;
}

bool same_value(double a, double b) {
    return std::isfinite(a) && std::isfinite(b) && std::fabs(a - b) <= 1e-9 * std::fabs(b);
}

}  // namespace tas::eseries
