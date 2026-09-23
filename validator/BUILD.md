# Building the TAS physics validator

A C++20 library + pybind11 module that decides whether a single TAS catalog part
is *physically* valid. Mirrors the MAS C++ binding's CMake/FetchContent style.

## Dependencies

Fetched automatically by CMake (network required on first configure):

- [nlohmann/json](https://github.com/nlohmann/json) v3.11.2 (same tag as MAS)
- [pybind11](https://github.com/pybind/pybind11) v2.13.6
- [Catch2](https://github.com/catchorg/Catch2) v3.8.1 (tests only; same tag as CAS)

Host tools: a C++20 compiler, CMake ≥ 3.18, Ninja (or Make), and Python 3 dev
headers (`python3-dev`) for the module.

## Build

```bash
cd TAS/validator
cmake -B build -G Ninja
cmake --build build
```

Produces:

- `build/tas_validator.*.so` — the Python module
- `build/tas_validator_tests` — the C++ unit-test binary
- `build/libtas_validator_core.a` — the pure-C++ core library

## Run

C++ unit tests:

```bash
./build/tas_validator_tests
```

Python:

```bash
PYTHONPATH=TAS/validator/build python3 -c "
import tas_validator, json
rec = json.load(open('TAS/examples/some_part.json'))   # or a dict
v = tas_validator.validate(rec)
print(v.valid, [(f.code, f.severity, f.message) for f in v.findings])
"
```

`pytest TAS/tests/test_validator_py.py` exercises the module against samples of
each `data/*.ndjson`.

## API

```python
import tas_validator
v = tas_validator.validate(record)   # record: dict OR JSON string
v.valid       # bool — False iff any IMPOSSIBLE finding
v.findings    # list of Finding: .code .severity .component .reference .message .value .threshold
v.skipped     # list of check codes skipped because required input data was absent
v.completeness  # fraction of the family's core datasheet fields present, or -1.0
                # for a family with NO manifest (the AAS integrator and summer
                # subtypes, which are behavioural atoms with no electrical block).
                # -1 is a SENTINEL meaning "not scored", not a low score — never
                # average it into a mean. Diodes are scored per part.subType
                # (zener/tvs/esd/everything-else, mirroring the SAS schema's own
                # conditional required sets); connector accessories are scored off
                # accessoryDetails/hostSystem rather than electrical, and carry no
                # GEN_SPARSE floor because real tooling rows legitimately have
                # nothing but a kind.
tas_validator.validate_json(text)    # same, JSON string only
tas_validator.check_codes()          # every check id the validator can emit
tas_validator.build_fingerprint()    # content hash of the src this .so was built from (ABT #397)
```

### `Finding.severity`

`severity` is a member of `tas_validator.Severity`, which is a **str-derived
enum** (`class Severity(str, Enum)`), so BOTH spellings compare equal:

```python
f.severity == tas_validator.Severity.Impossible   # True
f.severity == "IMPOSSIBLE"                        # True
str(f.severity)                                   # "IMPOSSIBLE"
f.severity in ("SUSPICIOUS", "IMPOSSIBLE")        # True
isinstance(f.severity, str)                       # True
```

It used to be a plain uppercase `str` while the module ALSO exported a
pybind11 enum whose members stringify as `'Severity.Impossible'` — so the
obvious gate, `f.severity == Severity.Impossible`, was silently `False` for
every record and "do we have any impossible parts?" answered "no" vacuously.
The str mixin is deliberate: a pure enum fixes that spelling and breaks the
other one, producing the same silent false pass for every consumer written
against the string.

### Dispatch

`validate()` accepts the PEAS discriminators `magnetic`, `capacitor`,
`resistor`, `varistor`, `thermistor`, `connector`, `controller`, `relay`,
`switch`, `potentiometer`, `connectorAccessory`, `semiconductor`
(mosfet/diode/igbt/bjt), `timeBase` and the AAS analog subtypes. Two record
kinds are NOT parts and say so by name rather than as "unknown discriminator":

- a **CIAS brick** (`ports[]` + `components[]`, `TAS/data/circuits.ndjson`) →
  use `validate_circuit()`;
- a **TAS converter document** (`topology`, `TAS/data/converters.ndjson`) → a
  design, not a catalogue part; validate its stages' bricks individually.

## The revert counter-check harness

`build/tas_validator_tests_revert` is the same suite built with
`TAS_VALIDATOR_REVERT_HARNESS`, where `emit()` honours a
`TAS_VALIDATOR_SUPPRESS` environment variable listing check codes that must
behave as though the check had never been written:

```bash
TAS_VALIDATOR_SUPPRESS=REL_COIL_ORDER ./build/tas_validator_tests_revert "[relay]"
```

That check's must-fire test must go RED and its must-stay-quiet test must stay
GREEN. It is the mechanical form of "revert the fix and confirm the test fails"
— a test that passes in both states proves nothing and looks identical to one
that works. The shipped library and the Python module never see the define, so
nothing in production can be switched off from the environment.

## Build freshness (ABT #397) — verify before you trust an import

This repo routinely has several out-of-source build directories at once
(`build/`, `build-ninja/`, ad-hoc `build-<task>/` trees other sessions use),
all configuring against the one `validator/src`. Each is only as fresh as its
own last rebuild — importing a stale one does not fail, it just silently
behaves like an older validator (a missing check reads as "this part passed",
not as an error). This has already cost a real investigation (ABT #552) and
broke a same-day rollout (ABT #549's `validate_circuit()`) with nothing to
tell the trees apart at import time.

**Never trust a build directory by name or mtime.** Before relying on an
imported `tas_validator` module (a gate, a bulk audit, an interactive
session), verify it by CONTENT:

```bash
python3 validator/tools/check_freshness.py \
    --module-dir validator/build \
    --validator-dir validator
# -> "FRESH <sha256>" on success; non-zero exit + a diagnosis naming both
#    hashes if the module was built from different source than what's on
#    disk right now.
```

This works for ANY build directory, not just `build/` — point `--module-dir`
at whichever tree you're about to import from. `tests/test_validator.cpp`
runs the equivalent check on every build via the
`"Framework: build_fingerprint matches on-disk source"` test case.

Verdict model: each check emits a `Finding` only when it fires. A part is
**INVALID** iff it has at least one `IMPOSSIBLE` finding; `SUSPICIOUS` findings
are warnings. Missing input for a check → the check is **skipped** (recorded in
`v.skipped`), never silently treated as valid. A field that is present but
malformed (wrong type) raises `MalformedField`.
