# TAS Schema Reference

Complete JSON Schema documentation for the Topology Agnostic Structure (TAS).

All schemas use [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/schema) and are located in `schemas/`.

---

## File Overview

| File | $id | Purpose |
|------|-----|---------|
| `TAS.json` | `http://openconverters.com/schemas/TAS/TAS.json` | Root document schema |
| `inputs.json` | `http://openconverters.com/schemas/TAS/inputs.json` | Converter requirements and operating points |
| `outputs.json` | `http://openconverters.com/schemas/TAS/outputs.json` | Computed results per operating point |
| `components.json` | `http://openconverters.com/schemas/TAS/components.json` | Component list and circuit netlist |
| `utils.json` | `http://openconverters.com/schemas/TAS/utils.json` | Shared type definitions |

---

## TAS.json

Root document. `additionalProperties: false`.

```
TAS
 +-- inputs          (required)  -> $ref inputs.json
 +-- components      (optional)  -> $ref components.json
 +-- outputs         (optional)  -> array of $ref outputs.json
```

**Only `inputs` is required.** A TAS document can represent just requirements (inputs only), a partial design (inputs + components), or a complete analyzed design (all three sections).

---

## inputs.json

### designRequirements

**Required fields:** `topology`, `inputVoltage`, `outputVoltage`

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `name` | `string` | -- | Label for this design |
| `topology` | `string` | enum (20 values) | Must match MAS topology names exactly |
| `inputVoltage` | `dimensionWithTolerance` | -- | Volts |
| `outputVoltage` | `dimensionWithTolerance` | -- | Volts |
| `outputCurrent` | `dimensionWithTolerance` | -- | Amperes |
| `outputPower` | `dimensionWithTolerance` | -- | Watts |
| `efficiencyTarget` | `number` | 0 <= x <= 1 | Fraction, not percent |
| `isolationVoltage` | `number` or `null` | -- | Volts. Absent or null = non-isolated |
| `operatingMode` | `string` | enum: CCM, DCM, BCM, CrCM, QR | -- |
| `modulationType` | `string` | enum: PWM, PFM, Hysteretic, Phase-Shift | -- |
| `controlMode` | `string` | enum: Voltage Mode, Peak Current Mode, Average Current Mode, COT | -- |
| `maximumDutyCycle` | `number` | 0 <= x <= 1 | -- |
| `ambientTemperature` | `dimensionWithTolerance` | -- | Celsius |
| `market` | `string` | enum: Consumer, Commercial, Industrial, Automotive, Medical, Military, Space | -- |

**Topology enum (full list):**

```
Buck Converter                    Flyback Converter
Boost Converter                   Forward Converter
Buck-Boost Converter              Two-Switch Forward Converter
Inverting Buck-Boost Converter    Active Clamp Forward Converter
SEPIC Converter                   Push-Pull Converter
Cuk Converter                     Half-Bridge Converter
Zeta Converter                    Full-Bridge Converter
                                  Phase-Shifted Full-Bridge Converter
LLC Resonant Converter            Power Factor Correction Boost
CLLC Resonant Converter           Totem-Pole Bridgeless PFC
Dual Active Bridge
```

### operatingPoints

Array of operating point objects. `minItems: 1`.

**Required fields per element:** `name`

| Field | Type | Constraints | Notes |
|-------|------|-------------|-------|
| `name` | `string` | -- | e.g. "Full Load Vin_min", "50% Load Vin_nom" |
| `conditions.ambientTemperature` | `number` | -- | Celsius |
| `conditions.inputVoltage` | `number` | -- | Volts |
| `conditions.outputCurrent` | `number` | -- | Amperes |
| `switchingFrequency` | `number` | -- | Hz |
| `dutyCycle` | `number` | 0 <= x <= 1 | -- |
| `operatingMode` | `string` | enum: CCM, DCM, BCM, CrCM, QR | Actual mode at this point |
| `waveforms` | `object` | additionalProperties: signalDescriptor | Keyed by signal name (e.g. "inductorCurrent") |

---

## components.json

### componentList

Array of component objects.

**Required fields per element:** `name`, `role`, `data`

| Field | Type | Notes |
|-------|------|-------|
| `name` | `string` | Reference designator: T1, Q1, D1, C1, L1, R1, etc. |
| `role` | `componentRole` (enum) | See full list below |
| `quantity` | `integer` (>= 1) | Default: 1. Use for parallel components (e.g. 3x output caps). |
| `pins` | `string[]` | Pin names for netlist. Omit for default pins. |
| `data` | EAS document or `string` | Inline EAS or path to external file |

**componentRole enum (32 values):**

Inductors:
- `mainInductor` -- primary power stage inductor
- `resonantInductor` -- LLC/resonant tank inductor
- `pfcInductor` -- PFC stage inductor
- `filterInductor` -- output or EMI filter inductor

Transformers:
- `mainTransformer` -- power stage transformer
- `gateTransformer` -- isolated gate drive transformer
- `currentTransformer` -- current sensing transformer

Switches:
- `highSideSwitch` -- high-side MOSFET (buck, half-bridge)
- `lowSideSwitch` -- low-side MOSFET (buck, half-bridge)
- `primarySwitch` -- primary-side switch (flyback, forward)
- `secondarySwitch` -- secondary-side switch
- `clampSwitch` -- active clamp switch
- `synchronousRectifier` -- synchronous rectifier MOSFET
- `pfcSwitch` -- PFC stage switch

Diodes:
- `outputRectifier` -- output rectifier diode
- `freewheelDiode` -- freewheeling diode (buck)
- `clampDiode` -- clamp circuit diode
- `boostDiode` -- boost/PFC diode

Capacitors:
- `inputCapacitor` -- input filter capacitor
- `outputCapacitor` -- output filter capacitor
- `bulkCapacitor` -- bulk storage capacitor
- `resonantCapacitor` -- LLC/resonant tank capacitor
- `bootstrapCapacitor` -- bootstrap supply capacitor
- `decouplingCapacitor` -- IC decoupling capacitor
- `snubberCapacitor` -- snubber circuit capacitor
- `clampCapacitor` -- clamp circuit capacitor

Resistors:
- `currentSenseResistor` -- current sense shunt
- `gateResistor` -- gate drive resistor
- `feedbackResistor` -- voltage divider feedback resistor
- `bleederResistor` -- bleeder/preload resistor
- `snubberResistor` -- snubber circuit resistor
- `clampResistor` -- clamp circuit resistor

### data field (EAS document or reference)

The `data` field uses `oneOf` to accept either:

1. **Inline EAS document** -- a full object conforming to the EAS schema (`http://openconverters.com/schemas/EAS/eas.json`). This contains `inputs`, `outputs`, and one of `magnetic`, `semiconductor`, `capacitor`, or `resistor`.

2. **String reference** -- a file path or URI pointing to an external EAS document.

This follows the same indirection pattern used in MAS for core shapes and materials.

### netlist

**Required fields:** `nodes`, `connections`

#### nodes

Array of strings naming circuit nodes. Examples: `"Vin"`, `"sw_node"`, `"out"`, `"gnd"`, `"pri_gnd"`, `"sec_gnd"`.

#### connections

Array of connection objects. Each maps one component pin to one node.

**Required fields per connection:** `component`, `pin`, `node`

| Field | Type | Notes |
|-------|------|-------|
| `component` | `string` | Must match a `name` in componentList |
| `pin` | `string` | Must match a pin in the component's `pins` array |
| `node` | `string` | Must be listed in `nodes` |

Common pin names by component type:
- **MOSFETs:** drain, gate, source
- **Diodes:** anode, cathode
- **Capacitors:** positive, negative
- **Resistors:** 1, 2
- **Transformers:** pri_dot, pri_undot, sec_dot, sec_undot (or custom names)
- **Inductors:** 1, 2 (or dot, undot)

---

## outputs.json

Array of output objects, one per analyzed operating point.

| Field | Type | Notes |
|-------|------|-------|
| `operatingPointName` | `string` | Links to an operating point in `inputs.operatingPoints` by name |
| `origin` | `string` | enum: `simulation`, `measurement`, `analytical` |
| `methodUsed` | `string` | Free text: "Proteus converter-designer", "ngspice", "LTspice", etc. |
| `losses` | `lossBreakdown` | See below |
| `stresses` | `stressAnalysis` | See below |
| `kpis` | `kpis` | See below |
| `waveforms` | `object` | additionalProperties: signalDescriptor |

### lossBreakdown

All fields optional, all `number` type, all in **Watts**.

```
coreLosses              -- magnetic core losses (hysteresis + eddy current)
windingLosses           -- magnetic winding losses (DC + AC/proximity)
switchConduction        -- MOSFET/IGBT I^2*Rds(on) or Vce(sat)*Ic
switchSwitching         -- turn-on + turn-off overlap losses
switchCoss              -- Coss charging losses (0.5*Coss*Vds^2*fsw)
gateDrive               -- Qg * Vgs * fsw
diodeConduction         -- Vf * Iavg
diodeReverseRecovery    -- Qrr * Vr * fsw
capacitorEsr            -- Irms^2 * ESR
clamp                   -- RCD or active clamp dissipation
snubber                 -- snubber dissipation
controller              -- IC quiescent power
other                   -- anything not covered above
total                   -- sum of all loss terms
```

### stressAnalysis

All fields optional, all `number` type.

```
switchVoltageMax           (V)     -- worst-case drain-source voltage
switchVoltageRating        (V)     -- FET voltage rating
switchVoltageMargin        (frac)  -- (rating - max) / rating

diodeVoltageMax            (V)     -- worst-case reverse voltage
diodeVoltageRating         (V)     -- diode voltage rating
diodeVoltageMargin         (frac)

capacitorVoltageMax        (V)     -- worst-case capacitor voltage
capacitorVoltageRating     (V)
capacitorVoltageMargin     (frac)

inductorCurrentMax         (A)     -- peak inductor current
inductorSaturationCurrent  (A)
inductorCurrentMargin      (frac)

maxJunctionTemperature     (C)     -- hottest semiconductor junction
junctionTemperatureRating  (C)
thermalMargin              (frac)
```

### kpis

| Field | Type | Unit | Notes |
|-------|------|------|-------|
| `efficiency` | `number` | fraction | Pout/Pin, 0 to 1 |
| `outputRipple` | `number` | V | Peak-to-peak output voltage ripple |
| `inputPower` | `number` | W | Total input power |
| `outputPower` | `number` | W | Total output power |
| `powerDensity` | `number` or `null` | W/cm^3 | -- |
| `cost` | `number` or `null` | USD | Estimated BOM cost |

---

## utils.json -- Shared Definitions

### dimensionWithTolerance

Requires at least one of `minimum`, `nominal`, `maximum`.

```json
// Full range
{ "minimum": 36.0, "nominal": 48.0, "maximum": 60.0 }

// Nominal only
{ "nominal": 12.0 }

// Range without nominal
{ "minimum": 10.0, "maximum": 14.0 }
```

### signalDescriptor

MAS-compatible signal description. Must have either `waveform` or `processed` (enforced by `anyOf`).

#### waveform (raw data)

| Field | Type | Required |
|-------|------|----------|
| `data` | `number[]` | Yes |
| `time` | `number[]` | Yes |
| `numberPeriods` | `integer` | No (default: 1) |

#### processed (parameterized)

| Field | Type | Notes |
|-------|------|-------|
| `label` | `string` (enum) | Triangular, Sinusoidal, Rectangular, Custom, Unipolar Rectangular, Bipolar Rectangular, Flyback Primary, Flyback Secondary, Forward Primary, Forward Secondary |
| `dutyCycle` | `number` (0-1) | -- |
| `peakToPeak` | `number` | -- |
| `peak` | `number` | -- |
| `offset` | `number` | DC offset |
| `average` | `number` | -- |
| `rms` | `number` | -- |
| `effectiveFrequency` | `number` | Hz |

### curve

| Field | Type |
|-------|------|
| `xData` | `number[]` |
| `yData` | `number[]` |

### numberArray

`type: array`, `items: number`

---

## Cross-References to Sibling Schemas

The `data` field in each component references the EAS schema, which dispatches to:

| EAS key | Schema | Repo |
|---------|--------|------|
| `magnetic` | MAS `magnetic.json` | OpenMagnetics/MAS |
| `semiconductor` | SAS `semiconductor.json` | OpenConverters/SAS |
| `capacitor` | CAS `capacitor.json` | OpenConverters/CAS |
| `resistor` | RAS `resistor.json` | OpenConverters/RAS |

Each sibling schema defines the detailed structure for its component type (e.g., SAS defines MOSFET electrical parameters, thermal networks, SPICE models; CAS defines capacitance with tolerance, ESR curves, lifetime models; etc.). See the respective repositories for full documentation of those schemas.

---

## Validation

To validate a TAS document against the schema:

```bash
# Using ajv-cli (npm install -g ajv-cli)
ajv validate -s schemas/TAS.json -d examples/01_flyback_48v_to_12v.json \
  --spec=draft2020 \
  -r "schemas/inputs.json" \
  -r "schemas/outputs.json" \
  -r "schemas/components.json" \
  -r "schemas/utils.json"
```

Note: Full validation requires the EAS, SAS, CAS, RAS, and MAS schemas to be available for `$ref` resolution of inline component data.
