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
tas_validator.validate_json(text)    # same, JSON string only
tas_validator.check_codes()          # every check id the validator can emit
```

Verdict model: each check emits a `Finding` only when it fires. A part is
**INVALID** iff it has at least one `IMPOSSIBLE` finding; `SUSPICIOUS` findings
are warnings. Missing input for a check → the check is **skipped** (recorded in
`v.skipped`), never silently treated as valid. A field that is present but
malformed (wrong type) raises `MalformedField`.
