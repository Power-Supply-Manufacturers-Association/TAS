// SPDX-License-Identifier: Apache-2.0
// IEC 60063 E-series preferred-value membership — the strongest real-vs-synthesized
// signal for passive components. Real resistor/capacitor nominal values are
// quantized to the E-series lattice (measured: 99.8% of live resistors, 99.9% of
// live capacitors land on a grid); a fabricated/random value (e.g. 4317.6 ohm) does
// not. This is advisory (SUSPICIOUS) — it feeds an authenticity signal, never an
// IMPOSSIBLE verdict, because rare legitimate exceptions exist (sense/shunt parts).
#pragma once

namespace tas::eseries {

// True if `value` (> 0) lands on a standard IEC 60063 preferred-value grid: the
// E6/E12/E24 family (2 significant figures, within 1.2%) or the E48/E96 family
// (3 significant figures, within 0.6%). E192 is intentionally excluded — at
// ~1.16% spacing it is too dense to discriminate fabricated values.
bool on_grid(double value);

// Minimum number of significant figures needed to represent `value` within a
// 1e-9 relative tolerance (capped at 7). Real preferred values are <= 3 sig figs;
// a nominal needing 5+ is an over-precision fingerprint of generated data.
int sig_figs(double value);

}  // namespace tas::eseries
