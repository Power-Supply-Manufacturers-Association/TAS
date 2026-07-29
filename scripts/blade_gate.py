#!/usr/bin/env python3
"""Blade Runner gate for harvester write steps — shared by every campaign script.

RULE (user, 2026-07-29): every new/changed catalogue record must pass Blade Runner
(the TAS C++ tas_validator), not only JSON Schema. They are DIFFERENT gates —
schema says the shape is legal, Blade Runner says the physics is possible. A units
error is perfectly valid JSON: 4,415 Murata bias curves were written 1e6 too large
(10 pF stored as 10 uF) and all 274,131 capacitors passed CAS validation.

Usage in a writer, per record, BEFORE accepting the patch:

    from blade_gate import BladeGate
    gate = BladeGate("capacitor")          # discriminator key for the wrap
    ok, why = gate.check(patched_component)
    if not ok:
        rejected += 1; out_lines.append(original_line); continue

IMPOSSIBLE findings block the write. SUSPICIOUS findings are counted and reported
but do not block — they are frequently pre-existing catalogue conditions
(GEN_SPARSE, CAP_E_SERIES) and blocking on them would stall every campaign.
Call gate.summary() at the end and LOG IT, so the suspicious tail is never silent.

If the validator binding is unavailable the gate FAILS LOUD rather than silently
passing everything: a gate that cannot run is not a gate.
"""
import collections
import os
import sys

_VALIDATOR_BUILD = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "validator", "build")


class BladeGate:
    def __init__(self, discriminator, required=True):
        """discriminator: the key a record is wrapped in ("capacitor", "connector",
        "magnetic", ...). Nested wraps take a tuple, e.g. ("semiconductor","mosfet").
        required=True means an unavailable validator raises instead of no-opping."""
        self.disc = (discriminator,) if isinstance(discriminator, str) else tuple(discriminator)
        self.impossible = collections.Counter()
        self.suspicious = collections.Counter()
        self.checked = 0
        self.blocked = 0
        if _VALIDATOR_BUILD not in sys.path:
            sys.path.insert(0, _VALIDATOR_BUILD)
        try:
            import tas_validator
            self.tv = tas_validator
        except ImportError as e:
            if required:
                raise RuntimeError(
                    f"Blade Runner (tas_validator) unavailable at {_VALIDATOR_BUILD}: {e}. "
                    "Build it per validator/BUILD.md — refusing to write catalogue data "
                    "without the physics gate.") from e
            self.tv = None

    def check(self, component):
        """Return (ok, reason). ok=False only for IMPOSSIBLE findings."""
        if self.tv is None:
            return True, None
        rec = component
        for k in reversed(self.disc):
            rec = {k: rec}
        self.checked += 1
        try:
            verdict = self.tv.validate(rec)
        except Exception as e:  # a record the validator cannot parse is not a pass
            self.blocked += 1
            return False, f"validator error: {str(e)[:100]}"
        worst = None
        for f in verdict.findings:
            if "IMPOSSIBLE" in str(f.severity).upper():
                self.impossible[f.code] += 1
                worst = worst or f
            else:
                self.suspicious[f.code] += 1
        if worst is not None:
            self.blocked += 1
            return False, f"{worst.code}: {getattr(worst, 'message', '')[:120]}"
        return True, None

    def summary(self):
        parts = [f"blade-runner: {self.checked} checked, {self.blocked} BLOCKED (impossible)"]
        if self.impossible:
            parts.append("  impossible: " + ", ".join(
                f"{c}x{n}" for c, n in self.impossible.most_common(5)))
        if self.suspicious:
            parts.append("  suspicious (not blocking): " + ", ".join(
                f"{c}x{n}" for c, n in self.suspicious.most_common(5)))
        return "\n".join(parts)
