// SPDX-License-Identifier: MIT
// IEC 60063 E-series preferred-value membership — the strongest real-vs-synthesized
// signal for passive components. Real resistor/capacitor nominal values are
// quantized to the E-series lattice (measured: 99.8% of live resistors, 99.9% of
// live capacitors land on a grid); a fabricated/random value (e.g. 4317.6 ohm) does
// not. This is advisory (SUSPICIOUS) — it feeds an authenticity signal, never an
// IMPOSSIBLE verdict, because rare legitimate exceptions exist (sense/shunt parts).
#pragma once

#include <string>
#include <vector>

namespace tas::eseries {

// True if `value` (> 0) lands on a standard IEC 60063 preferred-value grid: the
// E6/E12/E24 family (2 significant figures, within 1.2%) or the E48/E96 family
// (3 significant figures, within 0.6%). E192 is intentionally excluded — at
// ~1.16% spacing it is too dense to discriminate fabricated values.
bool on_grid(double value);

// True if `value` (> 0) lies within `window` (relative) of an official IEC 60063
// E192 mantissa (the 192-value table, including its 920 exception). On its own
// E192 is too dense to discriminate fabricated values, so callers gate it on the
// part's tolerance class (E192 is the series of the <= 0.5 % classes).
bool on_e192(double value, double window);

// Minimum number of significant figures needed to represent `value` within a
// 1e-9 relative tolerance (capped at 7). Real preferred values are <= 3 sig figs;
// a nominal needing 5+ is an over-precision fingerprint of generated data.
int sig_figs(double value);

// Part-number value codes. A catalogue value that is off every E-series grid is
// still a real value when it is exactly what the part's own ordering code spells
// (Vishay PTF6511K600 = 11.6 kohm, Coilcraft XAL1580-452 = 4.5 uH); these decode
// the tokens so a check can compare them to the stored value. They return every
// candidate, and the caller accepts only an exact match (same_value), so a token
// that merely resembles a value code accepts nothing.

// Letter-point tokens: one or more digits, one letter of `letters` that is both
// decimal point and multiplier, then zero or more digits ("12R5" = 12.5,
// "11K600" = 11.6e3, "0R6" = 0.6, "8R00" = 8, "8K" = 8e3). R = 1, K = 1e3,
// M = 1e6 (other letters are not multipliers and are rejected). The token must
// end at a non-digit (or the end of the code) and must hold at least one digit
// before the letter, so a series prefix such as "RT0603" or "M55342" is never
// read as "R0603". With `cut_digit_run` the token may start inside the digit run
// before the letter (ordering codes glue the value to a series or packaging
// digit: "PTF65" + "11K600", Yageo "-07" + "0R6"); without it the token is the
// whole digit run.
std::vector<double> letter_point_values(const std::string& code, const std::string& letters,
                                        bool cut_digit_run);

// Three-digit codes (two significant figures and a decade exponent, "452" =
// 45e2), returned unscaled: the caller applies the unit. The run must be exactly
// three digits, preceded by a separator (not a letter, digit or the start of the
// code) and followed by a non-digit, so series numbers ("XAL1580", "CDEP105")
// are not read as value codes.
std::vector<double> three_digit_code_values(const std::string& code);

// a == b within 1e-9 relative: the stored value is the decoded value.
bool same_value(double a, double b);

}  // namespace tas::eseries
