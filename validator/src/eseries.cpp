// SPDX-License-Identifier: MIT
#include "tas_validator/eseries.hpp"

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
    // Normalise mantissa to [100, 1000).
    double m = value / std::pow(10.0, std::floor(std::log10(value))) * 100.0;
    while (m < 100.0) m *= 10.0;
    while (m >= 1000.0) m /= 10.0;
    // Decade-boundary wrap: a value like 1e-5 stored as 9.9999e-6 normalises to
    // ~1000, which is 100 of the next decade — itself a preferred value.
    return nearest_rel(m, E24) <= 0.012 || nearest_rel(m, E96) <= 0.006 ||
           std::fabs(m - 1000.0) / 1000.0 <= 0.006;
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

}  // namespace tas::eseries
