# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

TAS ("Topology Agnostic Structure") is two things in one repo:

1. **A JSON Schema standard** (`schemas/`) describing a complete power converter design.
2. **A component database** (`data/`, NDJSON) of ~64k real, orderable parts used by the
   Proteus / Heaviside AI design system.

TAS is a git repo of its own (`OpenConverters/TAS`) that lives *inside* the parent Heaviside
workspace at `/home/alf/OpenConverters/Heaviside/` (referred to in scripts as `PROTEUS`).
That parent contains the sibling schema repos TAS depends on: **PEAS, SAS, CAS, RAS, MAS**
(and COAS). TAS schemas `$ref` into those; nothing here is self-contained.

## ⚠️ Schemas are the source of truth — README and docs are STALE

The repo is mid-migration to a **v2 schema model**. The actual `schemas/*.json` files are
authoritative. `README.md` and `docs/schema.md` still describe the *old* model and are wrong
in important ways. Do not trust them; read the schema files directly.

| Concept | OLD model (README/docs/schema.md — STALE) | v2 model (actual `schemas/`) |
|---|---|---|
| `$id` namespace | `http://openconverters.com/schemas/TAS/…` | `https://psma.com/tas/…` |
| Top-level sections | `inputs` (req), `components`, `outputs` | `inputs`+`topology` (req), `outputs`, `simulation` |
| Circuit description | flat `componentList` + `netlist` | `topology` = tree of **stages**, each owning a **circuit** |
| Component wiring | `connections` (component/pin/node) | `circuit.connections` with `kind`: wire / coupling / externalPort |

### v2 architecture (the real one)

- **`TAS.json`** — root. Required: `inputs`, `topology`. Optional: `outputs`, `simulation`
  (global SPICE models/commands).
- **`topology.json`** — `stages[]` + `interStageCircuit[]`. A stage has a `role` (stageRole
  enum: lineFilter, rectifier, pfc, bulkStorage, switchingCell, inverter, isolation,
  outputRectifier, outputFilter, control), `inputPort`/`outputPorts` (typed by portType:
  acLine/pulsatingDc/dcBus/hfAc/dcOutput), and one `circuit`. `control` stages are special:
  they have `senses`/`drives` instead of ports. Only `isolation` stages may have >1 output port.
- **`circuit.json`** — reusable sub-netlist: `components[]` + `connections[]`. Each component's
  `data` is either an inline PEAS document (`https://psma.com/peas/peas.json`) or a URI string
  into a data file (e.g. `TAS/data/mosfets.ndjson?partNumber=C3M0032120K`). Connections have
  `kind`: `wire` (shared net), `coupling` (magnetic K, needs `couplingCoefficient`, endpoints
  have no pin), `externalPort` (terminal, needs `direction`).
- **`inputs.json`**, **`outputs.json`**, **`utils.json`** — spec/operating-points, computed
  results, shared types.
- **PEAS hierarchy:** TAS → PEAS (abstract component container) → MAS (magnetic) / SAS
  (semiconductor: mosfet/diode/igbt) / CAS (capacitor) / RAS (resistor). Rule: finished,
  orderable parts live in `TAS/data/`; manufacturing building blocks (raw cores, wire, die)
  live in MAS/SAS/CAS/RAS.

`examples/01_flyback_48v_to_12v.json` and `02_buck_12v_to_5v.json` are valid v2 documents —
use them as the reference for document shape, not the docs.

## Component database (`data/`)

NDJSON, one JSON object per line. Files are **huge** (`capacitors.ndjson` ~98 MB,
`magnetics.ndjson` ~47 MB). Active catalogs: `mosfets`, `diodes`, `igbts`, `capacitors`,
`resistors`, `magnetics`, `controllers`, `converters`. `quarantine*.ndjson` hold parts with
verified data errors (excluded from queries; carry a `quarantineReason`). `*.bak` /
`*.quarantine_*` / `*.backup` files are historical artifacts — leave them alone.

Hard rules when touching data:

- **Never bulk-load and rewrite a data file.** Other processes append concurrently. For
  in-place edits, find the line by grepping the part number, parse/modify/write back that line.
- **All electrical values in SI base units.** Ω not mΩ (`0.045`), F not µF (`100e-6`),
  H not µH (`10e-6`), V/A/Hz. No exceptions.
- **`dissipationFactor` is a fraction, not percent** (X7R ≈ `0.025`, not `2.5`).
- **Würth magnetics use a different shape:** electrical specs live in
  `magnetic.commercialSpecs` (inductance, dcResistance, saturationCurrent, …) and the part
  number is in `manufacturerInfo.reference`, NOT in `manufacturerInfo.datasheetInfo.electrical`.
- Magnetics records carry `dataCompleteness`: complete / partial / skeleton / not_found.
- Components may carry a top-level `usageNotes[]` array (cross-reference validation history);
  the Proteus cross-referencer agent appends to it.

## Commands

There is no pyproject/venv inside TAS. Tests import sibling repos by relative path and need
`jsonschema` + `referencing`. Run them with the **parent Heaviside venv**:

```bash
# from anywhere; use absolute paths so the parent venv resolves
/home/alf/OpenConverters/Heaviside/.venv-web/bin/python -m pytest \
    /home/alf/OpenConverters/Heaviside/TAS/tests/test_data.py -q     # validate every NDJSON record (slow, ~90s)

/home/alf/OpenConverters/Heaviside/.venv-web/bin/python -m pytest \
    /home/alf/OpenConverters/Heaviside/TAS/tests/test_schemas.py -q  # schema meta-validation + negative cases
```

- `tests/test_data.py` — wraps each `data/*.ndjson` record in its discriminator and validates
  against the matching sibling schema (mosfets→SAS/mosfet.json, capacitors→CAS/capacitor.json,
  magnetics→MAS/magnetic.json, …); validates converter docs and examples against `TAS.json`.
  It reads sibling `$id`s from `PROTEUS/{PEAS,SAS,CAS,RAS,MAS}/schemas` at runtime.
- `tests/test_schemas.py` — **currently fails**: it still asserts the old
  `openconverters.com` `$id`s and old field names (`operatingPoints`, `dutyCycle`, `cost`…).
  `test_data.py` also currently fails to resolve `https://psma.com/peas/peas.json` because the
  sibling PEAS repo still publishes the old `$id`. These failures are the *migration in
  progress*, not your regression — confirm against a clean checkout before assuming you broke
  anything, and per the workspace policy, surface a still-broken path rather than papering over it.

`scripts/` is a large pile of one-off ETL/repair scripts (sourcing, recovery, schema patches
`patch_schema_vN.py`, SPICE round-trip `spice_to_tas.py` / `tas_to_spice.py`). They are
historical campaign tools, not a maintained library — read before reusing.

## Adding / editing parts

Use the **component-librarian** agent in Proteus rather than hand-editing data files; it finds
datasheets, extracts SI-unit parameters, appends to the right NDJSON via line patching, and
flags implausible values for the component-auditor. Errors go to `quarantine.ndjson` with a
`quarantineReason` (preserve traceability — don't delete).
