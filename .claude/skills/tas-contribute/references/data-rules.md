# Data rules

## Shape: copy the live catalogue

Each catalogue line is ONE JSON object wrapped in its PEAS discriminator, validated against
the family schema. Copy the structure of existing records of the same manufacturer and family
in `data/<catalogue>.ndjson` (stream it, e.g. `grep -m5 '"<manufacturer>"' data/x.ndjson`).

| catalogue | wrapper | schema |
|---|---|---|
| `capacitors.ndjson` | `{"capacitor": ...}` | CAS/capacitor.json |
| `resistors.ndjson` | `{"resistor": ...}` | RAS/resistor.json |
| `varistors.ndjson` | `{"varistor": ...}` | RAS/varistor.json |
| `thermistors.ndjson` | `{"thermistor": ...}` | RAS/thermistor.json |
| `potentiometers.ndjson` | `{"potentiometer": ...}` | RAS/potentiometer.json |
| `magnetics.ndjson` | `{"magnetic": ...}` | MAS/magnetic.json |
| `mosfets.ndjson` | `{"semiconductor": {"mosfet": ...}}` | SAS/mosfet.json |
| `diodes.ndjson` | `{"semiconductor": {"diode": ...}}` | SAS/diode.json |
| `igbts.ndjson` | `{"semiconductor": {"igbt": ...}}` | SAS/igbt.json |
| `bjts.ndjson` | `{"semiconductor": {"bjt": ...}}` | SAS/bjt.json |
| `thyristors.ndjson` | `{"semiconductor": {"thyristor": ...}}` | SAS/thyristor.json |
| `jfets.ndjson` | `{"semiconductor": {"jfet": ...}}` | SAS/jfet.json |
| `optocouplers.ndjson` | `{"semiconductor": {"optocoupler": ...}}` | SAS/optocoupler.json |
| `controllers.ndjson` | `{"controller": ...}` | CTAS/controller.json |
| `analog_ics.ndjson` | `{"analog": {"<kind>": ...}}` (operationalAmplifier, comparator, ...) | AAS/<kind>.json |
| `timing_devices.ndjson` | `{"timeBase": {"<kind>": ...}}` (oscillator, ...) | TDAS/<kind>.json |
| `connectors.ndjson` | `{"connector": ...}` | CONAS/connector.json |
| `connector_accessories.ndjson` | `{"connectorAccessory": ...}` | CONAS/connectorAccessory.json |
| `relays.ndjson` | `{"relay": ...}` | EMAS/relay.json |
| `switches.ndjson` | `{"switch": ...}` | EMAS/switch.json |
| `protection.ndjson` | `{"protection": ...}` | EMAS/protection.json |
| `thermal_management.ndjson` | `{"thermalManagement": ...}` | TMAS/thermalManagement.json |

`circuits.ndjson` (CIAS bricks) and `converters.ndjson` (whole designs) are not part
catalogues and are out of scope for this skill.

Specs live under `manufacturerInfo.datasheetInfo` (`part`, `electrical`, `thermal`,
`mechanical`, ..., and `provenance`); `manufacturerInfo.name` is the manufacturer.

**Every object you write must be schema-valid, including intermediate files.** No extra
keys, ever (the schemas are closed). Auxiliary data the schema has no place for (raw text,
your notes) stays in your own work files, never inside a record.

## Values

- **SI base units, plain numbers.** Ohm not mOhm (`0.045`), F not uF (`100e-6`), H not uH
  (`10e-6`), V, A, W, Hz, m (millimetres become `0.00483`), temperatures in degC as plain
  numbers. Tolerances are fractions (1 % -> `0.01`). `dissipationFactor` is a fraction
  (X7R about `0.025`, not `2.5`). Documented exceptions only (acoustic noise in dBA,
  `pollutionDegree` as `PD1..PD4`, and whatever the field's own schema description says).
- **No invented values.** No defaults, "typical" fill-ins, family-wide constants, values
  from a similar part, or placeholders. If the document does not print it, omit the field.
- **Derived values** only when the formula is exact and the inputs are printed (e.g.
  capacitance min/max from nominal and the tolerance code), with a `derived` provenance entry
  whose `derivation` is a one-line formula.
- **Nullable vs omit:** where a family's schema allows `null` and live records of that family
  use it, follow the family; otherwise omit an unknown field. Never write `0`, `""`, `{}` or
  `[]` to mean unknown (the gate counts an empty required object as missing).
- **Categorical fields come from each part's own datasheet** (technology, type, polarity,
  dielectric, subType). Never set them per decoder, per prefix or per family: a whole
  trimmer family was once mislabelled cermet when it was wirewound.
- **Rating conditions follow the schema's definition**, not the datasheet's first row. Read
  each field's schema description. SAS defines `continuousDrainCurrent` and
  `powerDissipation` at T_C = 25 degC: take the T_C row. If a sheet prints only T_A ratings,
  do not put them in the T_C field (MOSFETs have `electrical.ambientRatings` for that); never
  mix conditions in one record.
- **Store test conditions** wherever the schema has a slot (e.g. RDS(on)'s V_GS and I_D).
- **TYP/MAX pairs:** store the MAX for limits such as V_F, leakage, RDS(on).
- **Ranges:** `minimum <= maximum` numerically. P-channel V_GS(th) -3 ... -1 V is minimum -3,
  maximum -1, whatever column order the sheet uses.
- **Signs:** MOSFET ratings are stored signed as the datasheet prints them (P-channel V_DS,
  I_D, V_GS(th) negative); a sign that disagrees with `part.subType` is refused.
- **`manufacturerInfo.status`** (lifecycle) only from a vendor page that states it, and that
  provenance entry lists `manufacturerInfo.status` in its `fields`. A datasheet does not say
  "production"; an unsourced status is refused.
- **Codes the current datasheet does not offer stay out.** Dual devices are stored per
  device as the family does (e.g. dual diodes per diode).

## Identity

- Identity is **(part number, manufacturer)**. The same number from another manufacturer is
  a second source, not a duplicate.
- Store the device name the datasheet title prints (Si7942DP), not a packaging/ordering
  variant (Si7942DP-T1-E3), unless the vendor sells the codes as electrically different parts.
  The gate refuses an ordering-code variant of a live part: that means the part is already
  live; report it, never re-spell it to get past the gate.
- Part numbers that differ by a dot are different parts (SMCJ90A is not SMCJ9.0A). Spaces,
  `-`, `_`, `/` are usually spelling noise, BUT some vendors use a hyphen as the decimal
  point (Eaton 10BJ6-0A is 6.0 V, 10BJ60A is 60 V). If the vendor prints both spellings as
  different parts, keep its exact spelling and flag the pair in REPORT.md.
- A part number is an identifier, never a description: no grid-row text, no series name
  followed by prose (the gate refuses identities over 40 characters and series+prose).

## Provenance: a reference, not an essay

Each record carries `manufacturerInfo.datasheetInfo.provenance`, a list of entries with
**exactly** these keys:

```json
{"source": "manufacturerDatasheet", "sourceName": "KEMET MIL-PRF-20 ceramic molded datasheet F3101",
 "sourceUrl": "https://...", "retrievedDate": "2026-10-25",
 "fields": ["electrical.capacitance.nominal", "electrical.ratedVoltage"],
 "verification": "seriesConfirmed", "verificationDate": "2026-10-25"}
```

A `derived` entry has `source`, `sourceName`, `sourceUrl`, `retrievedDate`, `fields` and
`derivation` (the formula), and may omit `verification`.

One optional key, **`verificationMethod`**, holds facts read off the source that qualify the
value: the measurement condition or the bound, e.g. `"Measured at 10 mA Max."`,
`"1000 VRMS Min"`, `"200 V AC for 1 minute"`. One short line. It never says how the row
was obtained (HTTP GET, pdftotext, parsed, matched, export, script, archive, agent, ticket
numbers): `validate_submission.py` rejects that. Any other key (`note`, `comment`, ...)
is rejected.

- `source`: `manufacturerParametric` | `manufacturerDatasheet` | `manufacturerDatabase` | `derived`.
- `verification`: `partNamed` (the cited document prints the exact part number),
  `valuesReadFromSource` (you read the stored values of `fields` from it), `seriesConfirmed`
  (the document covers the series, not this exact code), `existenceConfirmed` (an
  authoritative vendor catalogue confirms the part exists, no values read). Never claim more
  than you did.
- `retrievedDate` is the real fetch date. `fields` lists the fields THIS source provided,
  as paths relative to `datasheetInfo` (`electrical.capacitance.nominal`), and nothing else.
- `sourceName` is a noun phrase naming the document or table. No reasoning, no "why", no
  how-it-was-scraped, no script names, no ticket numbers, no agent or person names, no
  history. Length is not the test: a long precise document title is fine; a second sentence
  or a finite verb ("was", "is") means it has become prose.
- Prefer a citation URL that names the exact part (the vendor product page) over a series PDF.

Name fields hold names: never put caveats, provenance or notes in `partNumber`, `series`,
`description`, `commercialName` or similar. If the schema has nowhere for a fact, leave it
out and propose a schema change (pull-request.md).

## Quarantine vs delete (fixes to live records)

- **Real part, broken data** (a value disagrees with the document, data incomplete): fix it
  with `set`/`unset`, or `quarantine` it with a reason stating the evidence.
- **Fabricated part or fabricated values** (the part does not exist, the values match
  nothing printed): `delete`. Invented data is removed, never kept in quarantine. Only with
  existence evidence AND the controls in checks.md; when the evidence is medium (vendor
  search blocked, no archived listing), quarantine instead.
- A "withdrawn" row leaves the live catalogue.

## Traps (read before mapping any vendor column)

- **Wrong field, not wrong unit.** A column off by a clean power of ten is usually the WRONG
  COLUMN, not a unit slip. Before rescaling, test whether the number equals another field of
  the same record (557 capacitor ESLs once equalled the can diameter in mm, exactly), with a
  control group of healthy rows. Rescaling would fabricate plausible values nobody can
  detect afterwards.
- **Headers lie.** A parametric column's heading or unit can be wrong (a "mA" column holding
  uA; a column labelled one quantity holding another; ids that mean different things in
  different grid views). Verify every column against 3+ datasheets before mapping it.
- **Units inside cells** ("40 uA", "650 V"): a bare number regex drops the prefix. Read the
  unit from what remains after the number, never by substring ("ma" is a substring of
  "max (a)").
- **Multi-channel strings** ("Q1=Q2=95", "Q1: 62.0, Q2: 62.0"): a naive regex returns the
  1 of Q1. Duals publish one row per channel: tie a record to a row on its own value, not on
  the part number alone.
- **Charge columns:** a "Qg" column may hold Q_gs; cross-check Q_gd < Q_g and plausibility
  against C_iss.
- **Capped APIs:** some vendor result lists silently cap at a fixed row count; check you got
  the whole family.
- **Join keys:** a prefix or derived key that is a bijection on your sample can collide in
  the vendor's full catalogue. Test keys against the vendor's catalogue, prefer exact
  identifiers, anchor matches at both ends (a truncated code prefix-matches a real longer one).
- **Mechanical dimensions have no axis convention** across vendors: copy the convention of
  live records of the same family, and never write checks that assume one axis is height.
- **Capacitor `thermal.tcc`** has three conventions: class-1 ppm/K
  (`{nominal: -750, minimum: -870, maximum: -630, unit: "ppm/K"}`), class-2 percent band
  (`{nominal: 0, minimum: -15, maximum: 15}`, where 0 is the real midpoint) and a bare
  magnitude (`{nominal: 15}`). Follow the family's existing convention; never write
  `{nominal: 0}` alone as a placeholder.
- **Multi-connection magnetics:** when a sheet prints ratings per connection (series /
  parallel, all windings / one winding), store one `datasheetInfo.electrical[]` entry per
  connection, `name` = the sheet's own column header; Isat in
  `saturationCurrents{percentInductanceDrop, ...}`, Irms in
  `ratedCurrentPoints{current, temperatureRise}`. Never mix columns in one entry.
- **Material names** (cores, dielectrics) name the material class and its distinguishing
  property, never a product family or part.
