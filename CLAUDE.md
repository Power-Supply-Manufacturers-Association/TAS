# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

TAS ("Topology Agnostic Structure") is two things in one repo:

1. **A JSON Schema standard** (`schemas/`) describing a complete power converter design.
2. **A component database** (`data/`, NDJSON) of ~660k real, orderable parts used by the
   Proteus / Heaviside AI design system.

TAS is a git repo of its own (`OpenConverters/TAS`). It is checked out in two places:
standalone inside the PSMA schema workspace at `/home/alf/PSMA/TAS/`, and inside the
Heaviside workspace at `/home/alf/OpenConverters/Heaviside/TAS/`. Either works: the
sibling schema repos TAS depends on — **PEAS, CIAS, SAS, CAS, RAS, MAS, CTAS, AAS,
CONAS** — are resolved from the checkout's **parent directory** (`REPO.parent` in the
tests, historically named `PROTEUS`). TAS schemas `$ref` into those siblings by absolute
`$id` (`https://psma.com/<repo>/…`); nothing here is self-contained.

## Schemas are the source of truth (docs are current)

The **v2 schema model is fully landed**. `schemas/*.json` are authoritative, and
`README.md` and `docs/schema.md` were brought up to date with them (July 2026) — they now
describe the same model. If they ever disagree again, trust the schema files and fix the
docs.

### v2 architecture

- **`TAS.json`** — root. Required: `inputs`, `topology`. Optional: `outputs`,
  `simulation` (simulator-agnostic analyses/models/stimulus/initialConditions).
- **`inputs.json`** — `designRequirements` (required: `efficiency`, `inputType`
  [`dc`/`acSinglePhase`/`acThreePhase`], `inputVoltage`, `outputs[]`; each output
  `{name, voltage, regulation}` with `regulation` ∈ voltage/current/both/constantPower/
  fixedRatio/unregulated and `ratio` required for fixedRatio) + `operatingPoints[]`
  (name-joined to the requirement rails; each load carries exactly one of
  `current`/`power`).
- **`topology.json`** — `stages[]` + `interStageConnections[]`. Stage variants
  (closed `oneOf`, discriminated by `role` / `controlImplementation`): `powerStage`
  (roles lineFilter, rectifier, pfc, bulkStorage, switchingCell, inverter,
  outputRectifier, outputFilter; `{inputPort, outputPort}`), `isolationStage`
  (role isolation, the only multi-output stage: `outputPorts[]`), `virtualControl`
  (software/behavioural, `senses[]`/`drives[]`, no circuit), `physicalControl`
  (a real controller brick with typed `ports[]`). Port bindings are `{port, type}`
  with `portType` ∈ acLine/pulsatingDc/dcBus/hfAc/dcOutput/control.
- **`stage.circuit` is a CIAS brick** — `oneOf[ inline CIAS document
  (https://psma.com/cias/CIAS.json) | URI string ]`, e.g.
  `"TAS/data/circuits.ndjson?name=half-bridge"`. CIAS is a **separate repo**; a brick is
  `{name, ports[], components[], connections[]}` — a SPICE `.subckt`. Each brick
  component's `data` is either an inline PEAS document or a URI into a data file
  (`TAS/data/mosfets.ndjson?partNumber=C3M0032120K`). **CIAS connections are plain
  nets**: `{name, endpoints[]}` where an endpoint is `{component, pin}` or `{port}` —
  there is **no `kind` discriminator and no coupling edge**. Magnetic coupling lives
  inside a single multi-winding PEAS magnetic component, never across bricks.
- **`interStageConnections[]`** DO have a `kind`: `wire` (internal net, ≥2
  stage-qualified `{stage, port}` endpoints) or `externalPort` (converter terminal
  Vin/Vout/GND, with `direction`).
- **PEAS hierarchy:** TAS → PEAS (abstract component container) → MAS (magnetic) / SAS
  (semiconductor: mosfet/diode/igbt/bjt) / CAS (capacitor) / RAS (resistor/varistor);
  plus CTAS (controllers), AAS (analog ICs), CONAS (connectors). Rule: finished,
  orderable parts live in `TAS/data/`; manufacturing building blocks (raw cores, wire,
  die) live in MAS/SAS/CAS/RAS.

`examples/01_flyback_48v_to_12v.json` and `02_buck_12v_to_5v.json` are valid v2
documents — good references for document shape.

## Component database (`data/`)

NDJSON, one JSON object per line. Files are **huge** (`capacitors.ndjson` ~263 MB /
230k records, `magnetics.ndjson` ~276 MB / 92k, `connectors.ndjson` ~138 MB / 138k,
`resistors.ndjson` ~123 MB / 161k, `circuits.ndjson` ~95 MB / 12k CIAS bricks). Active
catalogs: `mosfets`, `diodes`, `igbts`, `bjts`, `capacitors`, `resistors`, `varistors`,
`magnetics`, `controllers`, `analog_ics`, `connectors`, plus `circuits` (CIAS bricks)
and `converters` (47 full TAS documents). Per-catalog `*.quarantine_*.ndjson` siblings
(and the legacy `quarantine.ndjson`) hold parts with verified data errors or incomplete
data — excluded from queries, typically carrying a `quarantineReason`. `*.bak` /
`*.backup` files are historical artifacts — leave them alone.

Hard rules when touching data:

- **Never bulk-load and rewrite a data file.** Other processes append concurrently. For
  in-place edits, find the line by grepping the part number, parse/modify/write back that line.
- **All electrical values in SI base units.** Ω not mΩ (`0.045`), F not µF (`100e-6`),
  H not µH (`10e-6`), V/A/Hz. No exceptions.
- **`dissipationFactor` is a fraction, not percent** (X7R ≈ `0.025`, not `2.5`).
- **Every record is wrapped in its discriminator**: `{"semiconductor": {"mosfet": …}}`,
  `{"capacitor": …}`, `{"magnetic": …}`, `{"analog": {"operationalAmplifier": …}}`, etc.
  Magnetics (including Würth) use the standard MAS shape — specs under
  `manufacturerInfo.datasheetInfo` (`electrical[]` variant array, `provenance[]`); the
  old Würth `commercialSpecs` shape and the `dataCompleteness` field are gone.
- **Quarantine, don't delete.** Bad records move to the matching
  `*.quarantine_*.ndjson` file with a reason — traceability is preserved.

## Commands

There is no pyproject/venv inside TAS. Tests need only:

```bash
pip install pytest jsonschema referencing
```

(any Python ≥3.10 works; in the Heaviside checkout you may also use the parent
`.venv-web` venv). Then, from the repo root:

```bash
pytest tests/test_schemas.py -q   # schema meta-validation + negative cases — 70 tests, all pass, <1 s
pytest tests/test_data.py -q      # validate every NDJSON record (slow — hundreds of MB)
```

- `tests/test_data.py` — wraps each `data/*.ndjson` record in its discriminator and
  validates against the matching sibling schema (mosfets→SAS/mosfet.json,
  capacitors→CAS/capacitor.json, magnetics→MAS/magnetic.json,
  controllers→CTAS/controller.json, analog_ics→AAS/AAS.json,
  connectors→CONAS/connector.json, circuits→CIAS/CIAS.json, …); validates converter
  docs and examples against `TAS.json`. It builds the `$id` registry from the sibling
  repos at `<checkout>/../{PEAS,CIAS,SAS,CAS,RAS,MAS,CTAS,AAS,CONAS}/schemas` at runtime.
- `tests/test_schemas.py` — asserts the current `https://psma.com/tas/…` `$id`s,
  meta-validates every schema, and exercises the role-conditional negative cases.
  **It passes** (70/70) — if it fails for you, something is actually broken; surface it,
  don't paper over it.
- `scripts/validate_topology.py` — referential-integrity pass JSON Schema cannot
  express (port bindings resolve, no brick port left unwired, unique names).
- `validator/` — the C++/pybind11 **physics** validator (`tas_validator`, a.k.a.
  "Blade Runner"): flags physically impossible/suspicious catalog parts. Build per
  `validator/BUILD.md` (CMake + Ninja, C++20); `tests/test_validator_py.py` covers the
  Python binding.

`scripts/` is a large pile of one-off ETL/repair scripts (sourcing, recovery, schema
patches `patch_schema_vN.py`, SPICE round-trip `spice_to_tas.py` / `tas_to_spice.py`).
They are historical campaign tools, not a maintained library — read before reusing.
`librarian/` is the standing enrichment pipeline (missing-field queue + promotion).

## Adding / editing parts

Use the **component-librarian** agent in Proteus rather than hand-editing data files; it
finds datasheets, extracts SI-unit parameters, appends to the right NDJSON via line
patching, and flags implausible values for the component-auditor. Errors go to the
per-catalog quarantine files with a `quarantineReason` (preserve traceability — don't
delete).
