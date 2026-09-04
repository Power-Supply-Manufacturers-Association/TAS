# TAS — Topology Agnostic Structure

> The universal data format for complete power converter designs — and the component catalog that powers them.

TAS is a JSON schema standard for describing power converter designs end-to-end: requirements, components, circuit connections, and computed results. It also hosts a curated component database of **960,000+ real parts** used by the Proteus AI design system.

## Quick Stats (measured 2026-09-04)

Counts are `wc -l` of the active `data/*.ndjson` files (one record per line). This
is a shared, continuously-updated tree — treat these as a point-in-time snapshot,
not a fixed constant.

| Category | Records | Largest Manufacturer | Share |
|----------|---------|----------------------|-------|
| Connectors | 391,073 | Molex | 24.1% |
| Capacitors | 253,831 | WIMA | 25.4% |
| Resistors | 149,255 | Panasonic | 55.7% |
| Magnetics | 82,467 | Vanguard Electronics | 16.0% |
| Timing devices | 23,634 | Abracon | 70.1% |
| Diodes | 17,789 | Vishay | 28.5% |
| MOSFETs | 9,800 | Infineon | 27.0% |
| BJTs | 3,668 | Nexperia | 52.5% |
| Analog ICs | 3,607 | Texas Instruments | 95.4% |
| Varistors | 3,198 | Bourns | 43.4% |
| IGBTs | 2,278 | STMicroelectronics | 20.8% |
| Controllers | 2,133 | Monolithic Power Systems | 29.3% |
| Thermistors | 535 | Vishay | 100.0% |
| Circuit bricks (CIAS) | 25,243 | — | — |
| Converters (full TAS docs) | 47 | — | — |
| **Active total** | **968,558** | — | — |
| Quarantine (single consolidated `data/quarantine.ndjson`) | 234,709 | — | — |

---

## For Engineers New to the Project

### What TAS Solves

A real power converter design involves dozens of interrelated decisions scattered across spreadsheets, simulation files, datasheets, and emails. TAS captures everything in one machine-readable document — from the original spec to the final loss budget.

```
┌──────────────────────────────────────────────────────────────────┐
│                        TAS Document                              │
├──────────────────────────────────────────────────────────────────┤
│  INPUTS — what you need                                          │
│    designRequirements:                                           │
│      efficiency: 0.88   inputType: "dc"                          │
│      inputVoltage: { minimum: 36, nominal: 48, maximum: 60 }     │
│      outputs: [{ name: "out1", voltage: { nominal: 12 },         │
│                  regulation: "voltage" }]                        │
│      switchingFrequency: { nominal: 200e3 }  isolationVoltage: … │
│    operatingPoints: [{ name: "full_load_Vin_min",                │
│      inputVoltage: 36, ambientTemperature: 25,                   │
│      outputs: [{ name: "out1", current: 2 }] }]                  │
│                                                                  │
│  TOPOLOGY — how you build it                                     │
│    stages[]: each instantiates one CIAS brick (a .subckt)        │
│      switchingCell → half-bridge brick                           │
│        Qh,Ql: IPD65R420CFD MOSFETs ← SAS/PEAS via brick component│
│      isolation    → transformer brick                            │
│        T1: E25/13/7 N87            ← MAS/PEAS via brick component│
│      outputFilter → rectifier+cap brick (D1 SAS, Cout CAS)       │
│    interStageConnections[]: wire the stage ports together        │
│                                                                  │
│  OUTPUTS — what you computed                                     │
│    metrics: { volume: 12e-6, mass: 0.05 }                        │
│    operatingPoints: [{ name: "full_load_Vin_min",                │
│      efficiency: 0.921,                                          │
│      stageResults: [{ stage: "inverter", loss: 1.1 }],           │
│      outputResults: [{ name: "out1",                             │
│        voltageRipplePkPk: 0.045 }] }]                            │
│                                                                  │
│  SIMULATION (optional) — stimulus + analyses, simulator-agnostic │
└──────────────────────────────────────────────────────────────────┘
```

`inputs` and `topology` are required; `outputs` and `simulation` are optional —
so a TAS document can be just a spec + intended assembly, or a fully analyzed
design with results and a simulation setup.

The spec side (`inputs.json`) in brief: `designRequirements` requires
`efficiency`, `inputType` (`dc` / `acSinglePhase` / `acThreePhase`),
`inputVoltage`, and `outputs[]`; each output is `{name, voltage, regulation}`
where `regulation` ∈ `voltage | current | both | constantPower | fixedRatio |
unregulated` (plus `ratio` for `fixedRatio`). Optional constraints:
`switchingFrequency`, `isolationVoltage`, `powerFactorMinimum`, `bidirectional`,
`holdUpTimeMinimum`, `lineFrequency` (AC-only fields are forbidden for `dc`).
`operatingPoints[]` gives per-test-condition Vin / Tamb / per-rail loads,
joined to the requirement rails by `name`; each load carries exactly one of
`current` or `power`.

### The Component Database

`data/` contains NDJSON files with real parts scraped from manufacturer datasheets and APIs. These are queried by Proteus agents during converter design.

Each record is a single-line JSON object wrapped in its PEAS discriminator, and every record is validated by `tests/test_data.py` against the matching sibling-repo schema:

| File | Records | Wrap | Validated against |
|------|---------|------|-------------------|
| `mosfets.ndjson` | 9,800 | `{"semiconductor": {"mosfet": …}}` | SAS `mosfet.json` |
| `diodes.ndjson` | 17,789 | `{"semiconductor": {"diode": …}}` | SAS `diode.json` |
| `igbts.ndjson` | 2,278 | `{"semiconductor": {"igbt": …}}` | SAS `igbt.json` |
| `bjts.ndjson` | 3,668 | `{"semiconductor": {"bjt": …}}` | SAS `bjt.json` |
| `capacitors.ndjson` | 253,831 | `{"capacitor": …}` | CAS `capacitor.json` |
| `resistors.ndjson` | 149,255 | `{"resistor": …}` | RAS `resistor.json` |
| `varistors.ndjson` | 3,198 | `{"varistor": …}` | RAS `varistor.json` |
| `magnetics.ndjson` | 82,467 | `{"magnetic": …}` | MAS `magnetic.json` |
| `controllers.ndjson` | 2,133 | `{"controller": …}` | CTAS `controller.json` |
| `analog_ics.ndjson` | 3,607 | `{"analog": …}` (e.g. `{"analog": {"operationalAmplifier": …}}`) | AAS `AAS.json` |
| `connectors.ndjson` | 391,073 | `{"connector": …}` | CONAS `connector.json` |
| `thermistors.ndjson` | 535 | `{"thermistor": …}` | RAS `thermistor.json` |
| `timing_devices.ndjson` | 23,634 | `{"timeBase": …}` (e.g. `{"timeBase": {"oscillator": …}}`) | TDAS `tdas.json` |
| `circuits.ndjson` | 25,243 | CIAS brick `{name, ports, components, connections}` | CIAS `CIAS.json` |
| `converters.ndjson` | 47 | full TAS documents (reference designs) | TAS `TAS.json` |

Records with verified data errors or incomplete data are excluded from queries by
moving them to the single consolidated `data/quarantine.ndjson` (234,709 records
as of this measurement) — see [Quarantine](#quarantine).

To search the database, grep/parse the NDJSON directly (never load a whole file — `capacitors.ndjson` is ~673 MB, `circuits.ndjson` ~1.2 GB):

```bash
grep -m5 '"partNumber": "C3M0' data/mosfets.ndjson | python3 -m json.tool
python3 -c '
import json
for line in open("data/mosfets.ndjson"):
    d = json.loads(line)["semiconductor"]["mosfet"]
    e = d["manufacturerInfo"]["datasheetInfo"]["electrical"]
    ...'
```

Beyond schema validation, catalog parts are physics-checked by the C++ **`tas_validator`** (`validator/` — a pybind11 module that flags IMPOSSIBLE/SUSPICIOUS parameter combinations; see `validator/BUILD.md`).

---

## Schema Overview

TAS uses [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/schema). All schemas are in `schemas/`.

TAS is a **v2** schema model: a converter is a tree of **stages**, each
instantiating one [CIAS](https://github.com/Power-Supply-Manufacturers-Association/CIAS)
circuit brick, wired together — analogous to a complete SPICE deck.

```
schemas/
├── TAS.json         Root — required: inputs, topology; optional: outputs, simulation
├── inputs.json      Design requirements + operating points (the spec to satisfy)
├── topology.json    stages[] + interStageConnections[] — the assembly of CIAS bricks
├── outputs.json     Per-design metrics + per-operating-point losses/stresses
└── utils.json       Shared types
```

- **`topology.json`** — `stages[]` (variants: `powerStage`, `isolationStage`,
  `virtualControl`, `physicalControl`) plus `interStageConnections[]` (variants:
  `wire`, `externalPort`). Each power stage references a CIAS brick (`circuit`:
  inline CIAS document or a URI string like
  `"TAS/data/circuits.ndjson?name=half-bridge"`), types its terminals
  (`portBinding`/`portType`), and control stages carry `senses`/`drives`
  (virtual) or electrically wired `ports` (physical) instead of power ports.
- **`simulation`** (in `TAS.json`) — simulator-agnostic stimulus + analyses
  (transient / ac / dcSweep / operatingPoint), model-library entries, and
  per-component model bindings/overrides. Translatable to SPICE, PLECS, etc.

### TAS sits at the top of the PEAS hierarchy

```
TAS   ← complete converter designs + finished component catalog
 └── PEAS   ← universal component container (abstract base)
      ├── MAS   ← magnetics (cores, windings, bobbins)
      ├── SAS   ← semiconductors (MOSFETs, diodes, IGBTs, BJTs)
      ├── CAS   ← capacitors
      └── RAS   ← resistors, varistors
```

(Sibling catalogs also draw on CTAS — controllers, AAS — analog ICs, CONAS — connectors,
TDAS — timing devices (oscillators/timers/latches), and CIAS — circuit bricks; RAS also
covers thermistors alongside resistors/varistors.)

**Rule:** Finished, orderable components (with part numbers) belong in `TAS/data/`. Manufacturing building blocks (raw cores, wire, die) belong in MAS/SAS/CAS/RAS.

### Topologies

v2 has **no fixed topology enum**: any converter expressible as a cascade of
typed stages (lineFilter → rectifier → pfc → bulkStorage → switchingCell /
inverter → isolation → outputRectifier → outputFilter, plus control stages) built
from CIAS bricks is representable — buck to LLC to dual active bridge and beyond.
The reusable brick library lives in `data/circuits.ndjson`.

Full schema reference: `docs/schema.md`

---

## For Contributors

### Repository Layout

```
schemas/        JSON Schema files — the normative spec.
                Edit these when the data model changes.

data/           NDJSON component databases. One JSON object per line.
                Scripts append here; never load the whole file at once
                (capacitors.ndjson is ~673 MB, connectors.ndjson ~601 MB,
                circuits.ndjson ~1.2 GB, magnetics.ndjson ~284 MB — measured
                2026-09-04, this is a live, actively-growing tree).

docs/           schema.md — human-readable schema reference.
                topology-stages-research.md — historical literature-review
                artifact that informed the stage design (not a live spec).

scripts/        Utility scripts for database maintenance (one-off ETL/repair).

validator/      C++/pybind11 physics validator (tas_validator).

librarian/      Enrichment pipeline for filling missing datasheet fields.

examples/       Example TAS documents (valid v2, used by the tests).

tests/          pytest suite — schema meta-validation + full data validation.
```

### Validation

```bash
pip install pytest jsonschema referencing
pytest tests/test_schemas.py -q   # schema meta-validation + negative cases (fast)
pytest tests/test_data.py -q      # every NDJSON record against its schema (slow)
```

Cross-repo `$ref`s resolve by absolute `$id` (`https://psma.com/<repo>/…`); the
tests build the registry from the sibling repos checked out alongside TAS
(PEAS, CIAS, MAS, CAS, RAS, SAS, CTAS, AAS, CONAS, TDAS).

### Data Format Rules

**All electrical values in SI units.** No exceptions.
- Resistance: Ω (not mΩ) → `0.045` not `45`
- Capacitance: F (not µF) → `100e-6` not `100`
- Inductance: H (not µH) → `10e-6` not `10`
- Voltage: V, Current: A, Frequency: Hz

**`dissipationFactor` is a fraction, not percent.**
X7R typical DF = 0.025 (not 2.5). This was a systematic error in early database population — all entries have been corrected.

**MOSFET structure (SAS path)** — note the two-level `semiconductor` → `mosfet` discriminator nesting:
```json
{
  "semiconductor": {
    "mosfet": {
      "manufacturerInfo": {
        "name": "Infineon",
        "reference": "…",
        "datasheetInfo": {
          "part": { "partNumber": "…", "technology": "Si", "subType": "nChannel" },
          "electrical": { "drainSourceVoltage": 100, "onResistance": 0.0018, ... },
          "thermal": { "thermalResistanceJunctionCase": 0.9, ... }
        }
      }
    }
  }
}
```

**Controller structure (CTAS path):**
```json
{
  "controller": {
    "manufacturerInfo": {
      "name": "Texas Instruments",
      "reference": "UCC256301",
      "datasheetInfo": {
        "part": { "partNumber": "UCC256301", "deviceType": "controller" },
        "function": { "category": "llcController", "intendedTopologies": ["llcResonantConverter"] },
        "electrical": { "gateDrive": { "sourceCurrentPeak": 0.5, "sinkCurrentPeak": 1.0 } }
      }
    }
  }
}
```
Each `controllers.ndjson` record is a single-key `{ "controller": { … } }` wrap validated against
[`CTAS/schemas/controller.json`](../CTAS/schemas/controller.json) by `tests/test_data.py`. The
required discriminator is `manufacturerInfo.datasheetInfo.function.category` (a `controllerCategory`
value: `pwmController`, `multiphaseController`, `llcController`, `pfcController`,
`syncRectifierController`, `gateDriver`, `digitalController`, `shuntRegulator`, …). Category-specific
electricals live in optional sub-objects under `electrical` (`gateDrive`, `isolation`, `currentMode`,
`shuntReference`, `hotSwap`, …). The legacy freeform records were migrated by
`scripts/port_controllers.py`; parts that are not control ICs (modules, EEPROMs, LDOs) and
controllers with no determinable category were moved into the consolidated
`data/quarantine.ndjson` (tagged `_quarantineSource: "controllers.quarantine_nonctrl.ndjson"` /
`"controllers.quarantine_sparse.ndjson"` respectively — the standalone per-catalog quarantine
files those names originally referred to no longer exist on disk, see
[Quarantine](#quarantine)).

**Magnetics structure (MAS path):** all magnetics — including Würth — now use the
standard MAS shape: specs live in `manufacturerInfo.datasheetInfo` (`part`,
`electrical[]` variant array, `mechanical`, `thermal`, `provenance[]`), with the
part number in `manufacturerInfo.reference`. (The old Würth-specific
`magnetic.commercialSpecs` shape and the `dataCompleteness` field have been
migrated out and no longer appear in the data.)

### Adding Parts to the Database

Use the `component-librarian` agent in Proteus (see also the standing enrichment
pipeline in `librarian/`):

```
Use the component-librarian agent to add these Infineon CoolMOS parts to the TAS database:
IPW60R099CP, IPW60R125CP, IPW60R165CP
```

The librarian searches for datasheets, extracts parameters, and appends to the appropriate NDJSON file. It will also flag entries for the `component-auditor` if values seem implausible.

**Never bulk-load a file and rewrite it when other processes may be appending.** Use line-number patching for in-place updates (grep for the part number to find its line, parse that line, modify, write back).

### Quarantine

Parts with data errors are moved to a single consolidated **`data/quarantine.ndjson`**
(234,709 records as of this measurement) rather than deleted. This preserves
traceability. Per-catalog `<catalog>.quarantine_<reason>.ndjson` sibling files
(e.g. `capacitors.quarantine_duplicates.ndjson`, `mosfets.quarantine_incomplete.ndjson`)
were historically how quarantined parts were split up, but those standalone files
no longer exist — every quarantined record now lives in `data/quarantine.ndjson`
and carries an `_quarantineSource` field naming the file/reason it was originally
filed under (e.g. `"connectors.quarantine_incomplete.ndjson"`,
`"magnetics.quarantine_fabricated.ndjson"`), plus typically a `quarantineReason` field:

```json
{ "quarantineReason": "Hallucinated part number — FCP021N60E does not exist on onsemi.com or any distributor" }
```

### Known Data Issues (Historical, Now Fixed)

| Issue | Affected entries | Fix applied |
|-------|-----------------|-------------|
| `dissipationFactor` stored as % instead of fraction | 10,088 MLCC/film caps | ÷100 applied to all |
| Coilcraft SRF in THz range (double ×1e6 error) | 159 entries | ÷1,000,000 applied |
| Wolfspeed SiC Qrr 2–5× too low (25°C values, not 175°C) | 8 corrected, 19 filled | Manual verification |
| ROHM SiC Vf at 1.35V (impossible — should be ~3.2V) | SCT3022/3030/3060/3120AL | Set to 3.2V |
| Hallucinated onsemi FCP/FCA parts | 4 entries | Moved to quarantine |

---

## License

MIT — see `LICENSE`.
