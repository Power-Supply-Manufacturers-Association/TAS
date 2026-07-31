#!/usr/bin/env python3
"""The ONE way to collapse a dimensionWithTolerance to a scalar in TAS Python code.

House rule: a {minimum, nominal, maximum} value is resolved with MKF's canonical
resolver — PyOpenMagnetics.resolve_dimension_with_tolerance, the binding over
OpenMagnetics::resolve_dimensional_values — never by hand-reading `.nominal` or
`.maximum`. Hand-rolled reads drift: three scripts in this directory each wrote
`cur.get("maximum", cur.get("nominal"))`, which prefers the MAXIMUM where the canonical
resolver prefers the NOMINAL. On Würth connectors that happened to be harmless (every
contactResistance carries exactly one of the two keys — 2,385 maximum, 1,442 nominal,
never both), but "happened to be harmless" is not a rule anyone can rely on.

Canonical semantics (preferred = NOMINAL):
    nominal -> (minimum+maximum)/2 -> maximum -> minimum
and it THROWS when none is present, rather than returning a silent 0.

There is deliberately NO fallback implementation here. If PyOpenMagnetics is missing this
raises at import, because a local re-implementation is exactly the drift this module
exists to prevent.
"""
from typing import Any

import PyOpenMagnetics as _pyom

_resolve = _pyom.resolve_dimension_with_tolerance


def resolve(value: Any) -> float:
    """dimensionWithTolerance | number -> float. Raises if unresolvable."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict):
        return float(_resolve(value))
    raise TypeError(f"not a dimensionWithTolerance or number: {value!r}")


def resolve_or_none(value: Any):
    """Same, but None for absent/unresolvable — for read paths that tolerate absence."""
    if value is None:
        return None
    try:
        return resolve(value)
    except Exception:
        return None
