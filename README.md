# TAS — Topology Agnostic Structure

> The universal data format for complete power converter designs — and the component catalog that powers them.

TAS is a JSON schema standard for describing power converter designs end-to-end: requirements, components, circuit connections, and computed results. It also hosts a curated component database of 22,000+ real parts used by the Proteus AI design system.

---

## For Engineers New to the Project

### What TAS Solves

A real power converter design involves dozens of interrelated decisions scattered across spreadsheets, simulation files, datasheets, and emails. TAS captures everything in one machine-readable document — from the original spec to the final loss budget.

```
┌──────────────────────────────────────────────────────────────────┐
│                        TAS Document                              │
├──────────────────────────────────────────────────────────────────┤
│  INPUTS — what you need                                          │
│    topology: "Flyback Converter"                                  │
│    inputVoltage: { minimum: 36, nominal: 48, maximum: 60 }       │
│    outputVoltage: { nominal: 12 }  outputCurrent: { nominal: 2 } │
│    efficiencyTarget: 0.88                                        │
│                                                                  │
│  COMPONENTS — what you build with                                │
│    T1: E25/13/7 N87 transformer  ← full MAS/EAS document        │
│    Q1: IPD65R420CFD 650V MOSFET  ← full SAS/EAS document        │
│    D1: STPS8L40B Schottky        ← full SAS/EAS document        │
│    Cout: 2× 220µF polymer        ← full CAS/EAS document        │
│    Netlist: pin-to-node connections                              │
│                                                                  │
│  OUTPUTS — what you computed                                     │
│    losses: { core: 0.4W, winding: 0.6W, switch: 1.1W, ... }    │
│    kpis: { efficiency: 0.921, outputRipple: 0.045 }             │
│    stresses: { switchVoltageMargin: 0.48, ... }                  │
└──────────────────────────────────────────────────────────────────┘
```

Only `inputs` is required — a TAS document can represent just a spec (inputs only), a partial design (inputs + components), or a fully analyzed design (all three sections).

### The Component Database

`data/` contains NDJSON files with real parts scraped from manufacturer datasheets. These are queried by Proteus agents during converter design.

| File | Parts | What's in it |
|------|-------|-------------|
| `mosfets.ndjson` | 1,271 | Si, SiC, GaN — Rds_on(Tj), Coss(Vds), Eon/Eoff curves |
| `diodes.ndjson` | 1,051 | Schottky, ultrafast, SiC — Qrr verified at TJ=175°C |
| `igbts.ndjson` | 12 | With Eon/Eoff vs Ic switching energy curves |
| `capacitors.ndjson` | 13,179 | MLCC, electrolytic, film — dissipationFactor as fraction |
| `resistors.ndjson` | 1,439 | Sense, feedback, snubber types |
| `magnetics.ndjson` | 5,071 | Power inductors — WE-Aplan + TAS formats |
| `converters.ndjson` | — | Full converter TAS documents (reference designs) |
| `quarantine.ndjson` | ~111 | Parts with verified data errors, excluded from queries |

To search the database:

```bash
# From the Proteus directory:
python3 scripts/component_query.py mosfets --min-vds 600 --max-rds-on 0.1 --top 5
python3 scripts/component_query.py diodes --min-vrrm 200 --max-vf 0.6 --top 5
python3 scripts/component_query.py capacitors --min-cap 100e-6 --min-voltage 50 --top 5
python3 scripts/component_query.py magnetics --min-inductance 10e-6 --min-isat 5 --top 5
```

---

## Schema Overview

TAS uses [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/schema). All schemas are in `schemas/`.

```
schemas/
├── TAS.json         Root document — inputs (required), components, outputs (optional)
├── inputs.json      Design requirements + operating points
├── outputs.json     Loss breakdown, stress analysis, KPIs
├── components.json  Component list (EAS references) + circuit netlist
└── utils.json       Shared types: dimensionWithTolerance, signalDescriptor
```

### TAS sits at the top of the EAS hierarchy

```
TAS   ← complete converter designs + finished component catalog
 └── EAS   ← universal component container (abstract base)
      ├── MAS   ← magnetics (cores, windings, bobbins)
      ├── SAS   ← semiconductors (MOSFETs, diodes, IGBTs)
      ├── CAS   ← capacitors
      └── RAS   ← resistors
```

**Rule:** Finished, orderable components (with part numbers) belong in `TAS/data/`. Manufacturing building blocks (raw cores, wire, die) belong in MAS/SAS/CAS/RAS.

### Supported Topologies (20)

Buck, Boost, Buck-Boost, Inverting Buck-Boost, SEPIC, Cuk, Zeta, Flyback, Forward, Two-Switch Forward, Active Clamp Forward, Push-Pull, Half-Bridge, Full-Bridge, Phase-Shifted Full-Bridge, LLC Resonant, CLLC Resonant, Dual Active Bridge, Power Factor Correction Boost, Totem-Pole Bridgeless PFC.

Full schema reference: `docs/schema.md`

---

## For Contributors

### Repository Layout

```
schemas/        JSON Schema files — the normative spec.
                Edit these when the data model changes.

data/           NDJSON component databases. One JSON object per line.
                Scripts append here; never load the whole file at once
                (capacitors.ndjson is 26 MB).

docs/           schema.md — human-readable schema reference.

scripts/        Utility scripts for database maintenance.

examples/       Example TAS documents.
```

### Data Format Rules

**All electrical values in SI units.** No exceptions.
- Resistance: Ω (not mΩ) → `0.045` not `45`
- Capacitance: F (not µF) → `100e-6` not `100`
- Inductance: H (not µH) → `10e-6` not `10`
- Voltage: V, Current: A, Frequency: Hz

**`dissipationFactor` is a fraction, not percent.**
X7R typical DF = 0.025 (not 2.5). This was a systematic error in early database population — all entries have been corrected.

**MOSFET structure (SAS path):**
```json
{
  "semiconductor": {
    "manufacturerInfo": {
      "name": "Infineon",
      "datasheetInfo": {
        "part": { "partNumber": "...", "technology": "Si", "subType": "nChannel" },
        "electrical": { "drainSourceVoltage": 100, "onResistance": 0.0018, ... },
        "thermal": { "thermalResistanceJunctionCase": 0.9, ... }
      }
    }
  }
}
```

**Wurth magnetics structure (WE-Aplan path, different from standard SAS):**
```json
{
  "magnetic": {
    "manufacturerInfo": {
      "name": "Wurth Elektronik",
      "reference": "7443641000"
    },
    "commercialSpecs": {
      "inductance": 1e-5,
      "dcResistance": 0.0024,
      "saturationCurrent": 37.0,
      "ratedCurrent": 30.0,
      "selfResonantFrequency": 13000000.0
    }
  }
}
```
Note: For Wurth entries, inductance and electrical specs are in `magnetic.commercialSpecs`, not in `manufacturerInfo.datasheetInfo.electrical`. The part number is in `manufacturerInfo.reference`.

**`dataCompleteness` field (magnetics only):**
- `"complete"` — all key parameters present
- `"partial"` — some parameters missing (e.g. DCR but no Isat)
- `"skeleton"` — part number only, no electrical data yet
- `"not_found"` — datasheet not locatable

### Adding Parts to the Database

Use the `component-librarian` agent in Proteus:

```
Use the component-librarian agent to add these Infineon CoolMOS parts to the TAS database:
IPW60R099CP, IPW60R125CP, IPW60R165CP
```

The librarian searches for datasheets, extracts parameters, and appends to the appropriate NDJSON file. It will also flag entries for the `component-auditor` if values seem implausible.

**Never bulk-load a file and rewrite it when other processes may be appending.** Use line-number patching for in-place updates (grep for the part number to find its line, parse that line, modify, write back). The librarian uses this pattern for `usageNotes` updates.

### `usageNotes` Field

Components can carry a top-level `usageNotes` array recording cross-reference validation history, known caveats, and application lessons:

```json
{
  "usageNotes": [{
    "date": "2026-04-04",
    "source": "cross-referencer agent (EPC9195 Wurth cross-reference)",
    "note": "Validated in 216W GaN buck @ 750kHz. DCR=2.4mΩ gives 1.97× Isat margin at worst case (Vin=60V, L-20%, T=125°C). WE-MAPI 74436411000 rejected — only 1.15× margin. APPROVED."
  }]
}
```

The Proteus cross-referencer agent writes these back after every substitution analysis.

### Quarantine

Parts with data errors go to `quarantine.ndjson` rather than deletion. This preserves traceability. A quarantined entry includes a `quarantineReason` field:

```json
{ "quarantineReason": "Hallucinated part number — FCP021N60E does not exist on onsemi.com or any distributor" }
```

### Known Data Issues (Historical, Now Fixed)

| Issue | Affected entries | Fix applied |
|-------|-----------------|-------------|
| `dissipationFactor` stored as % instead of fraction | 10,088 MLCC/film caps | ÷100 applied to all |
| Coilcraft SRF in THz range (double ×1e6 error) | 159 entries lines 2818–2976 | ÷1,000,000 applied |
| Wolfspeed SiC Qrr 2–5× too low (25°C values, not 175°C) | 8 corrected, 19 filled | Manual verification |
| ROHM SiC Vf at 1.35V (impossible — should be ~3.2V) | SCT3022/3030/3060/3120AL | Set to 3.2V |
| Hallucinated onsemi FCP/FCA parts | 4 entries | Moved to quarantine |

---

## License

MIT — see `LICENSE` if present, otherwise all rights reserved pending formal license assignment.
