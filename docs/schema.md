# TAS Schema Reference

JSON Schema documentation for the **Topology Agnostic Structure (TAS)** and the
**Circuit Agnostic Structure (CIAS)** it composes.

All schemas use [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/schema).
Cross-repo `$ref`s resolve by absolute `$id` URI (`https://psma.com/<repo>/<file>.json`).

---

## The model in one picture

A converter is built bottom-up, each level referencing the level below **inline or
by URI** — the same indirection at every tier:

```
TAS   the minimal simulatable deck      (stages + wiring + stimulus + analyses)
 │  stage.circuit  →  inline CIAS  | "TAS/data/circuits.ndjson?name=half-bridge"
 ▼
CIAS  a reusable circuit brick          (ports + components + connections)   ·  a SPICE .subckt
 │  component.data →  inline PEAS  | "TAS/data/mosfets.ndjson?partNumber=…"
 ▼
PEAS  a single part                     (magnetic | semiconductor | capacitor | resistor)
```

The SPICE analogy is load-bearing: **CIAS = a `.subckt`** (a definition with a port
list and internal elements — not runnable alone); **TAS = the deck** (subckts
instantiated, wired at their terminals, with stimulus and analyses). A *full product
converter* would add more CIAS bricks (protection, aux, housekeeping) at a converter
level above TAS — out of scope for TAS.

| File | `$id` | Purpose |
|------|-------|---------|
| `TAS.json` | `…/tas/TAS.json` | Root: `inputs`, `topology`, `outputs?`, `simulation?` |
| `inputs.json` | `…/tas/inputs.json` | Design requirements + operating points |
| `topology.json` | `…/tas/topology.json` | The assembly: stages + inter-stage connections |
| `outputs.json` | `…/tas/outputs.json` | Computed metrics + per-operating-point results |
| `utils.json` | `…/tas/utils.json` | `dimensionWithTolerance` (re-exported from PEAS) |
| `CIAS.json` | `…/cias/CIAS.json` | The circuit brick (separate **CIAS** repo) |

---

## TAS.json

Root document, `additionalProperties: false`. **`inputs` and `topology` are required.**

```
TAS
 ├── inputs       (required)  → inputs.json
 ├── topology     (required)  → topology.json
 ├── outputs      (optional)  → outputs.json
 └── simulation   (optional)  → simulator-agnostic analysis setup
```

---

## inputs.json

### designRequirements — `required: [efficiency, inputType, inputVoltage, outputs]`

| Field | Type | Notes |
|-------|------|-------|
| `efficiency` | `number` (0–1) | Target efficiency, fraction |
| `inputType` | `string` | `dc` / `acSinglePhase` / `acThreePhase` |
| `inputVoltage` | `dimensionWithTolerance` | Volts |
| `lineFrequency` | `dimensionWithTolerance` | Hz (ac input) |
| `switchingFrequency` | `dimensionWithTolerance` | Hz |
| `isolationVoltage` | `number` | Volts; absent = non-isolated |
| `powerFactorMinimum` | `number` | PFC designs |
| `bidirectional` | `boolean` | — |
| `holdUpTimeMinimum` | `number` | s |
| `outputs[]` | array | Per-rail `{name, voltage, regulation, ratio?}`; `regulation` ∈ `voltage \| current \| both \| constantPower \| fixedRatio \| unregulated`; `ratio` (Vout/Vin) required for `fixedRatio`, optional for `unregulated`, forbidden otherwise |

### operatingPoints — `minItems: 1`, each `required: [name, inputVoltage, ambientTemperature, outputs]`

| Field | Type | Notes |
|-------|------|-------|
| `name` | `string` | e.g. "full_load_Vin_min" |
| `inputVoltage` | `number` | Volts at this point |
| `ambientTemperature` | `number` | °C |
| `outputs[]` | array | Per-rail `{name, power|current}` |

---

## topology.json — the assembly

`{ stages[], interStageConnections[] }`, `additionalProperties: false`.

### stage — `oneOf` (closed variants, discriminated by `role`)

| Variant | role | Shape |
|---------|------|-------|
| `powerStage` | `lineFilter`, `rectifier`, `pfc`, `bulkStorage`, `switchingCell`, `inverter`, `outputRectifier`, `outputFilter` | `{name, role, phaseCount?, circuit, inputPort, outputPort}` — single in, single out |
| `isolationStage` | `isolation` | `{name, role, phaseCount?, circuit, inputPort, outputPorts[]}` — the **only** multi-output stage |
| `virtualControl` | `control` | `{name, role, controlImplementation:"virtual", model?, senses[], drives[]}` — software/behavioural; **no circuit/ports** |
| `physicalControl` | `control` | `{name, role, controlImplementation:"physical", circuit, ports[]}` — real controller brick, wired electrically; **no senses/drives** |

- `circuit` = `oneOf[ inline CIAS document | URI string ]` (the Lego brick).
- `portBinding` = `{port, type}` where `port` names a terminal on the stage's brick and
  `type` ∈ `portType`. Direction is implied by `inputPort` vs `outputPort`.
- `portType` = `acLine | pulsatingDc | dcBus | hfAc | dcOutput | control`
  (`control` is for a physical controller's gate-drive / feedback / enable nets).
- `sense` = `oneOf[ componentSense{stage,component,signal} | netSense{net,signal} ]`.
- `drive` = `{stage, component, signal}` (signal ∈ `gate|enable`).

**Cascade type-signatures:** lineFilter acLine→acLine · rectifier acLine→pulsatingDc ·
pfc pulsatingDc→dcBus · bulkStorage/switchingCell dcBus→dcBus · inverter dcBus→hfAc
(owns the resonant tank) · isolation hfAc→hfAc[] · outputRectifier hfAc→pulsatingDc ·
outputFilter pulsatingDc→dcOutput.

### interStageConnection — `oneOf` (discriminated by `kind`)

| Variant | kind | Shape |
|---------|------|-------|
| `internalNet` | `wire` | `{name, kind, endpoints[≥2]}` joins stage terminals |
| `externalNet` | `externalPort` | `{name, kind, direction, endpoints[≥1]}` — a converter terminal (Vin/Vout/GND) |

Endpoints are **stage-qualified**: `stagePortEndpoint = {stage, port}` — exactly how a
SPICE parent references a subckt instance's nodes. There is **no coupling variant**:
magnetic coupling lives inside a single multi-winding PEAS component, never across bricks.

---

## CIAS.json — the brick (separate repo)

`{ name, ports[], components[], connections[] }`, all required, `additionalProperties: false`.
Names (port / component / connection) are **local to the brick**.

| Field | Shape |
|-------|-------|
| `name` | brick type name (the lookup key for `?name=…` references) |
| `ports[]` | `{name, description?}` — external terminals (the studs) |
| `components[]` | `{name, data: oneOf[ PEAS doc | URI ]}` |
| `connections[]` | `{name, endpoints[≥2]}` — one electrical net |

`endpoint` = `oneOf[ pinEndpoint{component,pin} | portEndpoint{port} ]`. A net that
includes a `portEndpoint` is **exposed** at that brick terminal. CIAS has no `kind`
discriminator and no coupling edges — a coupled inductor/transformer is one
multi-winding PEAS part whose coupling lives in its own (MAS) model.

Reusable bricks live in **`TAS/data/circuits.ndjson`** and are referenced by
`"TAS/data/circuits.ndjson?name=<brick>"`.

---

## simulation — simulator-agnostic

`{ analyses[], models[], overrides[], stimulus[], initialConditions[] }`. Carries *what* to
analyse and *which* models to bind — never simulator-specific command syntax. Translatable to
SPICE, PLECS, Modelica, etc.

| Field | Shape |
|-------|-------|
| `analyses[]` | `oneOf[ transient{stopTime,maximumTimeStep?,startTime?} \| ac{sweep,startFrequency,stopFrequency,pointsPerInterval} \| dcSweep{sweptComponent,start,stop,step} \| operatingPoint ]` |
| `models[]` | `{name, format, definition}` — `format` ∈ `spice-model \| spice-subcircuit \| modelica \| verilog-a \| plecs \| table` (the tag lets each backend pick what it understands) |
| `overrides[]` | `{stage, component, model?, parameters[]}` — bind a model / override params on one brick component |
| `stimulus[]` | `{stage, component, signal, waveform}` — **open-loop** drive; `waveform` = `oneOf[ pwm{frequency,dutyCycle,phase?,deadTime?} \| constant{value} ]` |
| `initialConditions[]` | `{node, voltage}` — nodes that begin **pre-charged** at t=0 instead of solved from a cold DC operating point (SPICE: `.ic` + UIC). Lets converters whose steady state is unreachable from a cold start — resonant tanks with ill-conditioned DC points, or active synchronous rectifiers that can't self-start into 0 V — begin near steady state. |

A TAS may carry **both** a `virtualControl` stage (closed loop) and `simulation.stimulus`
(open loop); a stimulus on a switch takes precedence over a drive for that switch.

---

## outputs.json

`{ metrics, operatingPoints[] }` — design-level metrics plus per-operating-point
losses / stresses / waveforms. See the schema for field detail.

---

## Validation

```bash
pip install pytest jsonschema referencing
pytest tests/                         # schema + data + referential-integrity tests
python3 scripts/validate_topology.py  # cross-reference integrity (examples + brick library)
```

Structural validation (JSON Schema) is complemented by
`scripts/validate_topology.py`, which checks the references JSON Schema cannot express:
that a `portBinding` names a port the brick declares, that every `{stage, …}` reference
resolves, that no brick port is left unwired, and that names are unique. Bricks
referenced by URI are opaque to the integrity pass (reported as a note).

Full `$ref` resolution requires the sibling repos (PEAS, CIAS, MAS, CAS, SAS, RAS)
checked out alongside TAS in the `PSMA/` layout.
