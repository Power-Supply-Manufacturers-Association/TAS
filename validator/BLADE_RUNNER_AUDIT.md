# Blade Runner Audit & "Real Replicant Detector" Plan
*TAS C++ physics validator (`tas_validator`) — audit 2026-06-24*

This is the output of a deep audit of every check in `tas_validator`, verified against the
**live catalog** (per-check fire rates measured on random 4,000-record samples and full-catalog
runs) and adversarially cross-checked. It covers (1) what's broken, (2) what to recalibrate,
(3) the missing **anti-synthesis layer** that turns Blade Runner from a loose bounds-checker
into a real "is this part real or fabricated?" detector.

No JSON-schema edits are proposed. Two items need user sign-off (flagged ⚠).

---

## STATUS — P0 + P1 + P2 implemented (2026-06-24)

All of §2 (the confirmed bugs/recalibrations), §2's coverage-gap invariants, and the §4
contamination/coherence detectors that don't need new verdict dimensions are **implemented and
verified** (23 C++ tests pass; 91 check codes). Measured impact on 4,000-record samples:

| family | INVALID before→after | key de-noise |
|---|---|---|
| mosfets | **5.8% → 0.00%** | `MOS_RON_FLOOR` 28.6%→0.08%; `MOS_VGS_VS_VTH` IMPOSSIBLE 5.8%→0 (now SUSPICIOUS via `onResistanceVgs`) |
| capacitors | 0% → 0% | `CAP_INSULATION_RC` 8.3%→0; `CAP_ENERGY_DENSITY` 6.2%→1% |
| diodes | 0% → 0% | `DIO_VF_RANGE` 7%→1% (Schottky detection re-enabled); `DIO_QRR_SCHOTTKY` now live |
| resistors | 0% → 0.12% | `RES_POWER_V_R` removed; new catch = real `maxVoltage=0` bad data |
| igbts | 0% → 0.04% | new catch = the 16,001,200 A PN-leak garbage |
| magnetics | 0.1% → 0% | `MAG_SRF_L` no longer invalidates real beads; `MAG_RATED_LE_SAT` now live |

Newly-caught genuine bad data (previously silent): zero-`maxVoltage` resistors, the 16 MA IGBT,
negative analog supply, non-zero Qrr on Schottkys. The ↑ rates are *correct* catches, not noise.

**Provenance warning (your request):** `GEN_PROVENANCE_MISSING` (SUSPICIOUS) fires on every PEAS
child whose `datasheetInfo.provenance` array is absent/empty. It currently fires on **98.7–100%**
of every family — *except* analog ICs (0.1%), because only the TI analog import populated
provenance. The warning never invalidates a part; it surfaces the governance gap so it can be
back-filled. Also added: `GEN_FAMILY_MISMATCH` (description names a foreign component family),
`GEN_MULTI_DISCRIMINATOR` (>1 discriminator).

**P3 — E-series anti-synthesis layer — DONE (2026-06-24).** New `eseries` module (`src/eseries.cpp`)
+ `RES_E_SERIES` / `CAP_E_SERIES` / `GEN_OVERPRECISION` checks, SUSPICIOUS-only (never invalidate).
A nominal resistance/capacitance that does not land on an IEC 60063 E24/E96 grid, or carries >4
significant figures, is flagged — the strongest real-vs-fabricated signal. Measured on live data:
**resistors 0.14% off-grid, capacitors 0.46% off-grid** (sub-0.1 Ω sense/shunt parts allow-listed;
float-boundary values like 10 µF stored as 9.999…e-6 correctly recognised). Zero false positives on
known preferred values (verified across E6–E96 + E96 3-sig values). The off-grid residue is genuine
anomalies (800 Ω, 9.4 kΩ, 8.6 µF, zero-capacitance bad data). 28 C++ tests pass; 94 check codes.

**P4 — cross-parameter physics correlations — DONE (2026-06-24).** Each candidate was *calibrated
on live data first* — which correctly **rejected 3 of 5** before they could ship noise: MOSFET
`figureOfMerit` (ambiguously defined — FOM/(Rds·Qg) spans 2 decades in real data), capacitor
DF=ESR·2πfC (doesn't hold even at matched frequencies — DF and ESR are specified at different
operating points, ratio spans 7 decades), and ADC ENOB≤resolution (the ENOB field does not exist
in the data). The three that survived and shipped (31 tests, 97 codes), all 0–0.04% with zero
false positives on real parts:
- `DIO_TVS_ORDERING` (IMPOSSIBLE) — TVS standoff < breakdown < clamp. Caught 2 genuine errors:
  a ±24 V TVS with `breakdown.min 21 < standoff 24`, and an ESD diode with `clamping == standoff`.
- `IGBT_VCESAT_RATIO` (SUSPICIOUS) — Vce(sat)/Vces outside the real 0.0003–0.02 band (p1–p99 =
  0.0009–0.0042); armed, 0 real violations.
- `ANA_SLEW_GBW` (SUSPICIOUS) — op-amp slewRate/GBW outside 0.05–100 V (real p1–p99 = 0.23–23);
  armed, 0 real violations.

**P5 — completeness/authenticity verdict dimension — DONE (2026-06-24).** `Verdict` gains a
`completeness` field (0..1, or -1 if no manifest) and a `GEN_SPARSE` SUSPICIOUS finding fires when
a record carries <50% of its family's core datasheet fields — closing FW-5 (a near-empty fake now
scores e.g. `completeness=0.25` + `GEN_SPARSE`, but stays `valid=True`, preserving the binary
contract). Per-family core-field manifests were derived from live field-presence statistics; the
manifest is **0% false-positive** on magnetics/capacitors/resistors/mosfets/igbts/varistors.
**Diodes were deliberately excluded** — their subtypes (rectifier/Schottky/TVS/Zener/ESD) carry
disjoint field sets, so any single core manifest false-flags ~45% of real parts.

**P6 — corpus batch layer — DONE (2026-06-24).** New `validate_corpus(records)` API +
`GEN_COHORT_OUTLIER`: within each (manufacturer, component, technology/series) cohort, a numeric
field that is a robust-z (median/MAD, log-space) outlier is surfaced — a typo or fabricated value
that per-record bounds pass (e.g. caught `IRFB4310 gateThresholdVoltage=4e-6`, a real µV-for-V unit
slip, at z=−120). Sub-cohorting by technology/series was essential: (manufacturer, component) alone
lumps different dielectrics/voltage classes together and explodes z-scores (3.4%→0.31% caps after
the fix). **Cross-manufacturer "clone" detection was deliberately omitted** — measured on the live
catalog, identical spec blocks are dominated by legitimate second-source equivalents and
part-family variants (33–51% of parts share a block), so it is not a reliable synthesis signal.
Screening-only; kept entirely out of per-record `validate()`.

**Magnetics E-series (`MAG_E_SERIES`) — DONE** (gated to L ≥ 1 µH power inductors; 2.9% off-grid,
all genuine custom/odd values). Varistor voltage E-series was **evaluated and rejected** — MOV
voltages follow an MOV-specific ladder, not IEC 60063 (6% of real parts off-grid).

**`connectors.ndjson` data fix — DONE.** The 3 stray git-LFS pointer lines were stripped (131,268 →
131,265 lines); Blade Runner now ingests all 131,265 connector records cleanly.

**Per-family completeness floor — DONE (2026-06-24).** `GEN_SPARSE` now uses a per-family floor
(magnetics 0.40, igbt 0.50, all complete families 0.60) set safely below each family's measured
real-part minimum completeness — 0% false positives, and now catches a record missing even one core
field on always-complete families (a capacitor missing `ratedVoltage` fires).

**CTAS controller validation — DONE (2026-06-24).** New `check_controllers` + `controller`
discriminator (the validator now covers the CIAS umbrella: AAS analog ICs were already handled;
CTAS controllers are the new half). 12 `CTL_*` codes covering the structural invariants every real
control IC obeys regardless of category (PWM/LLC/PFC/multiphase/phase-shift/sync-rect controllers,
gate drivers, references, shunt regulators, hot-swap): `CTL_UVLO_ORDER` (start > stop),
`CTL_ISO_ORDER` (surge ≥ withstand ≥ working), `CTL_ISO_CREEP`, `CTL_FREQ_ORDER` (fmin ≤ fmax),
`CTL_SUPPLY_ABSMAX`/`CTL_SUPPLY_ORDER`, `CTL_SHUNT_CATHODE`, `CTL_SR_THRESHOLD` (negative VDS),
`CTL_DUTY_RANGE`, `CTL_THERMAL_ORDER` (θJC ≤ θJA), `CTL_PHASE_COUNT` (maxPhaseCount ≥ channelCount),
`CTL_POSITIVITY`. Plus magnitude-bound codes (`CTL_SUPPLY_RANGE`, `CTL_FREQ_RANGE`, `CTL_REF_RANGE`,
`CTL_CS_THRESHOLD`, `CTL_GATE_DRIVE`, `CTL_ISO_RANGE`, `CTL_TJMAX`) and a `CTL_DEADTIME` invariant
(dead time must fit inside the switching period). Every bound is **datasheet-calibrated** from a
~60-parameter survey of real TI/ADI/onsemi/Infineon/ST/Renesas/Skyworks parts, cross-checked against
the live catalog's populated fields (which capped tightening — e.g. real gate drivers reach 30 A and
real propagation delays reach 4 µs, so those bounds stay wide). Two false-positive traps the survey
flagged were fixed: a **negative-rail VEE UVLO** (rising −3.1 V / falling −2.6 V) no longer trips
`CTL_UVLO_ORDER` (gated to positive rails), and **isolation withstand-vs-working is not enforced**
(it decouples on wide-body parts; withstand is RMS, working is peak) — only `surge ≥ withstand` and
`surge ≥ working` are. All 1,831 real controllers validate cleanly and pass every CTL check (0% false
positives); the checks fire on synthetic violations (200 A driver, surge < working, deadTime ≥ period,
40-phase) and correctly pass the two real edge-cases above.

**PN value-encoding decode (EIA-198/EIA-96/IEC-60062) — EVALUATED AND DEFERRED.** Prototyped the full
decoders (with the correct standard tables) and measured them on the live catalog: even tightly
gated (ceramic-only, bounded tokens, on-grid sanity) the false-mismatch rate is **4.5–9.4%**, and
"EIA-96 only" is 92% — because without per-vendor part-number grammars the decoder cannot
distinguish a value token from a lot/date/packaging code (e.g. the `M404` lot suffix parses as
0.4 MΩ), the R-notation `10R` (10 Ω) collides with the EIA-96 reading (1.24 Ω), electrolytics don't
use the pF code, and normalized internal PNs don't follow EIA. The E-series check already provides
the value-authenticity signal, so PN decode is deferred until per-vendor PN-location rules exist.
**Byproduct — a real data bug found:** ~26+ Yageo CFR carbon-film resistors store resistance in mΩ
instead of MΩ (`CFR-12JB-52-1M` = 1 MΩ but spec = 0.001 Ω, a 10⁹ unit error) — worth quarantining.

**Full-catalog scan + data repair — DONE (2026-06-24).** Ran the validator over all 651,571
records: 204 invalid (0.031%). Verification first caught a validator **false positive** — 108
`resistance == 0` records are real **0 Ω jumper arrays** (Yageo YC-series), so the check now flags
only negative resistance (==0 is a valid jumper; +2 regression tests). The genuine single-bad-field
records were then **field-fixed in place** (streaming + atomic replace, line counts unchanged):
64 connector `insulationResistance = 0.5` nulled (Würth import placeholder), 21 resistor
`maxVoltage`/`powerRating = 0` nulled, **26 Yageo CFR carbon-film resistances decoded from the part
number and corrected mΩ→MΩ**, the 16,001,200 A IGBT current nulled, the 37 A op-amp Iq unit-fixed.
22 misfiled inductors were quarantined out of `connectors.ndjson` →
`connectors.quarantine_misfiled_inductors.ndjson` (re-import to magnetics pending). **9 records were
deliberately left for manual review** — 6 magnetics (rated-current ≫ Isat, where it is genuinely
ambiguous whether `Isat` or `ratedCurrent` is the wrong field — e.g. SRP1265A-R56M's Isat is the
error, not its rating) and 3 TVS diodes (`standoff ≥ clamp`). **Catalog INVALID is now 9 (0.0014%).**

**Final state:** 120 check codes, 39 C++ tests passing, 0 crashes across all 10 families
(magnetics/capacitors/resistors/mosfets/diodes/igbts/varistors/connectors/analog/**controllers**)
on the live catalog, INVALID 0–0.014% everywhere. `MOS_RON_FLOOR` was **neutered to a sub-physical backstop**
(per your choice), not removed. The full P0→P6 roadmap is implemented. Remaining future ideas: a
per-family completeness floor tuned per family (currently a flat 0.5), and PN value-encoding
self-consistency (EIA-198/EIA-96 decode) from the P3 design, not yet built.

---

## 1. Diagnosis — two opposite failure modes, both defeating the goal

**(A) Silent on the majority.** On random 4,000-record samples the validator produces **zero
findings** for:

| family | zero-findings | INVALID | family | zero-findings | INVALID |
|---|---|---|---|---|---|
| magnetics | 94.8% | 0.1% | diodes | 93.0% | 0.0% |
| capacitors | 84.3% | 0.0% | igbts | 99.1% | 0.0% |
| resistors | 93.4% | 0.0% | varistors | 99.8% | 0.0% |
| mosfets | 55.3% | 5.8% | analog | 99.9% | 0.0% |

Because the verdict is purely `valid = !any(IMPOSSIBLE)`, a fabricated record that supplies a
handful of plausible numbers and omits everything else **passes as valid with 0 findings**.
Many present-in-data fields are never read at all: mosfet `continuous/pulsedDrainCurrent`,
diode `reverseLeakageCurrent`, cap `rippleCurrent`, varistor `clampingCurrent`/`energyAbsorption`,
magnetics chip-bead `impedancePoints`.

**(B) Noisy where it fires — and on the *wrong* parts.** The high-firing checks overwhelmingly
flag the **most real** parts, and two checks **mass-invalidate real orderable parts**:

- `MOS_VGS_VS_VTH` → **IMPOSSIBLE on 5.67% of MOSFETs** (the entire family INVALID rate). All
  real: every ROHM SCT-series + Infineon IMW/AIMW SiC part, because `gateThresholdVoltage` is
  polluted with the **gate-drive window** (`{9,15,19.5}` V) not the true ~3 V threshold, and the
  check compares the wrong pairing.
- `MOS_RON_FLOOR` → fires on **28.6%** of MOSFETs; every example is a normal low-voltage Si part.
  The `Ron·Vds²` proxy omits die area, so it structurally trips for all low-voltage parts.
- `CAP_INSULATION_RC` 8.3%, `RES_POWER_V_R` 6.5%, `DIO_VF_RANGE` 7% — all flag real parts (small
  ceramics, high-megohm HV resistors, fast-recovery Si rectifiers).

Net: the checks that fire are **anti-correlated with fakeness**, eroding trust while adding ~0
anti-synthesis value.

---

## 2. Confirmed bugs (50 verified; 3 critical)

### CRITICAL — mass false-positives / invalidators (fix first)

| ID | Check | Problem | Fix |
|---|---|---|---|
| **MOS-1** | `MOS_VGS_VS_VTH` | IMPOSSIBLE on 417 real SiC/Si parts; wrong invariant (`\|VgsMax\|>\|Vth.max\|`) + `gateThresholdVoltage` holds the drive window | Remove the IMPOSSIBLE comparison. Replace with SUSPICIOUS-only data-available invariants: `onResistanceVgs ≤ \|Vth.max\|` ("drive can't turn device on") and `\|onResistanceVgs\| > \|VgsMax\|` ("drive exceeds abs-max", fires on only 7/2148 real). Drops family INVALID 5.67%→~0. |
| **MAG-2** | `MAG_SRF_L` | IMPOSSIBLE (1e6) invalidates 25 real parts (Würth WE-CBF/CHSA beads, TDK/Würth common-mode chokes); IMP branch lacks the `L>1nH` gate the SUS branch has | `MAG_SRF_L_IMP` 1e6→**3e6** (real max 2.6e6) + add `L>1nH` gate; `MAG_SRF_L_SUS` 5e5→~8e5 (p99=6.25e5; currently fires 5.5%) |
| **DIO-1** | `DIO_VF_RANGE` | **Schottky detection is dead code**: device type is in `part.subType` (schottky/sicSchottky/tvs/zener) but the check reads only `part.technology` (only ever `Si`/`SiC`). So real Schottkys (Vf 0.26–0.40 V) fall into the Si-PN band and get flagged; the `Qrr`/majority logic never runs. | Build the token from **both**: `tech = norm_tech(technology)+norm_tech(subType)`. Add `DIO_VF_SCHOTTKY_LO=0.2`. Re-enables the dead Qrr logic. |

Plus the structural critical: **FW-5** — no completeness/coherence verdict dimension (a near-empty
fake passes as valid). See §4.

### HIGH-VALUE recalibrations (de-noise, restore trust — all constant/branch edits)

- **MOS-3 `MOS_RON_FLOOR`** (28.6% FP): ⚠ correct version needs die area (schema). **Recommend
  remove**; replace its role with the tech-aware `Rds·BV`-too-good band in §4. (If kept as a
  unit-error backstop, drop floors to sub-physical 0.15/0.5/0.1.)
- **MOS-2 `MOS_VTH_WINDOW`**: ordering done in *magnitude* inverts for P-channel → 6 real TI
  P-ch parts wrongly IMPOSSIBLE. Do **ordering signed** (`min≤nom≤max`), keep magnitude only for
  the technology-band check.
- **MOS-4 `MOS_POWER_THERMAL`** (11.6%): drop the lower-bound clause (`ratio<1/3`) — datasheets
  rate Pdiss at elevated case temp, not Tc=25 °C. Keep the upper bound only.
- **CAP-01 `CAP_INSULATION_RC`** (8.3%): the Riso·C spec applies to bulk caps, not ceramics. Gate
  on `C>1µF`, lower `SUS_LOW` 1.0→0.3 s.
- **CAP-02 `CAP_ENERGY_DENSITY`** (6.2%): SUS bands are 1–3 decades below reality. `SUS_ALUM`
  0.1e6→3e6, `SUS_CERAMIC` 0.5e6→5e6, `SUS_FILM` 0.3e6→0.5e6 (comment "alum 0.01–0.05" is wrong —
  real is 0.2–2.5 J/cm³).
- **RES-1 `RES_POWER_V_R`** (6.5%): **remove.** Neither over- nor under-direction ratio
  discriminates real from fake (high-megohm HV resistors are voltage-limited; 76% of parts have
  `maxVoltage==√(P·R)` exactly, so the field carries no independent info). Move discrimination to
  the E-series check.
- **DIO-2 `DIO_VF_SI_HI`**: 1.3 V too tight — fast/ultrafast Si rectifiers reach 1.7–2.5 V. Raise
  to 2.0 (or scale upper bound with `reverseVoltage`); widen SiC band to (0.5, 3.5).
- **MAG-4 `MAG_SRF_SANE`**: `srf==0` is a placeholder, not impossible physics — treat `srf≤0` as
  *skip*, keep the positive sub-floor as the real signal; add an upper SRF sanity (>1e11 Hz).
- **IGBT VCESAT** SUS ceiling 4.5 V too tight for 1600–1700 V parts (flags 22 real IXYS) — make it
  Vces-dependent (7.5 V when Vces>1200).

### COVERAGE GAPS — free, near-zero-FP structural invariants (close the silent holes)

These hold for **every** real part regardless of vendor; a synthesizer rarely satisfies all of them:

- **MAG-1 `MAG_RATED_LE_SAT` is dead code** — `ratedCurrents` elements are bare numbers but the
  check only reads object members `rms`/`current`, so it never fires. Read bare numbers; flag
  SUSPICIOUS at `rated/Isat>5`, IMPOSSIBLE at `>50` (20% of real parts legitimately have
  `rated>Isat` — don't flag directly). Catches genuine unit-errors (SRP1265A-R56M: 37 A vs 0.058 A).
- **MAG-3** — a single zero/missing dimension throws `MalformedField` and **aborts all magnetics
  checks for 633 parts**. Make `box_volume_m3` return `nullopt` (not throw) on non-positive dims +
  emit `MAG_DIM_NONPOSITIVE` SUSPICIOUS; keep the malformed-vs-missing distinction.
- **MOSFET** `pulsedDrainCurrent ≥ continuousDrainCurrent` (magnitude — 3 P-ch parts store
  negative signed values). Never-false-firing on real data.
- **Diode/TVS** standoff < breakdown < clamp ordering; **Varistor** `clampingCurrent ≤ peakSurgeCurrent`,
  `energyAbsorption` positivity + magnitude (1389/1514 records carry these, never checked).
- **IGBT** magnitude bounds — a **16,000,000 A** part currently passes (PN digits leaking into the
  field). Add `IGBT_IC_IMP=2e4`, `IGBT_VCES_IMP=2e4`.
- **Positivity gaps that pass as valid today**: cap `insulationResistance<0`, cap `leakageCurrent<0`,
  resistor `powerRating≤0`/`maxVoltage≤0`, connector `clearance`/`creepage`/`DWV ≤0`, BJT
  `Vce(sat)==0`, analog `maximumSupplyVoltage≤0` (when no minimum present), analog gain ordering
  (`minGain>maxGain`), ADC/DAC/switch `numberOfChannels`. All should be IMPOSSIBLE.
- **GEN_TEMP_ORDER** (FW-1): fires IMPOSSIBLE on `min==max` and on non-temperature thermal
  sub-objects (`tcc`, `temperatureRise`). Restrict to real temperature keys + strict `min>max`.

### Data-integrity (data fixes, not validator)

- **connectors.ndjson** has a 3-line git-LFS pointer header prepended to real JSON (lines 1–3
  unparseable; the other 131,265 records are fine). `sed -i '1,3d'` or re-run `git lfs checkout`.
- **22 power-inductors misclassified as connectors** (TE records, mA-read-as-A). Route back to
  magnetics or quarantine. Caught only because `CONN_CURRENT_RANGE` tripped — a contamination
  detector (below) would catch these directly.

### Cosmetic / low-priority

- Shared `fmt()` drops the threshold from the message when it's exactly 0 (`if (b != 0)` in-band
  sentinel — violates the no-in-band-sentinel guardrail). `MOS_CAP_HIERARCHY` message is garbled by
  chained `fmt()` calls and passes `value=0,threshold=0` so the structured fields are useless.
  Hoist one shared `fmt_value(value, std::optional<double> threshold)`.
- NaN/Infinity via the Python dict path raise an opaque `parse_error`, never `MalformedField`
  (`scalar()`'s `isfinite` check is dead for that path). Use `json.dumps(allow_nan=False)`.

**Refuted during verification:** 2 analog claims (ANA-05, ANA-06) did not survive — not real bugs.

---

## 3. Research-backed calibration (extremes that real 2026 parts actually reach)

- **MOSFET Vth (true):** Si 1–4 V, SiC 1.8–4 V (NOT 15 V — that's the drive window), GaN 0.7–2 V.
  SiC/GaN/SJ beat the Si `Rds·BV` limit by 100–870×, so any `Rds·BV` band must be wide + tech-aware.
- **Diode Vf:** Schottky 0.26–1.2 V, Si PN to ~1.8 V, fast/ultrafast Si to ~2.5–2.9 V, SiC to ~3.0 V hot.
- **Cap energy density (J/cm³):** alum-elec 0.2–2.5, film ≤0.45, X7R/C0G ceramic ≤2.9, tantalum to ~9,
  advanced MLCC to ~22. Tan δ ceilings: C0G 0.001, X7R 0.025, film 0.01, tantalum to 0.25, electrolytic 0.30.
- **Magnetics:** real in-core energy density ~0.05 J/cm³ ferrite (current SUS bands are 3+ decades
  too loose → inert); SRF floor 300 Hz–1 kHz fine for wound parts.
- **Op-amp:** slew/GBW ratio spans ~3 decades (don't over-tighten); CMRR/PSRR 60–150 dB.
- **ADC:** ENOB ≤ resolution is a *hard* identity; but **do not** enforce `SNR ≤ 6.02N+1.76`
  (false-positives delta-sigma) or `SFDR>SNR` (22 real violations).

---

## 4. The anti-synthesis layer — the "real replicant detector"

Real catalogs live on a **discrete, standards-coded, physics-bound lattice**; synthesizers sample a
continuous/naive distribution. **Design rule:** anti-synthesis tells are rarely hard physics
violations → they emit **SUSPICIOUS only** (never flip `valid=false`) and feed a **new non-binary
authenticity/coherence score**, preserving the existing `IMPOSSIBLE⇒invalid` contract.

1. **E-series preferred-value membership** (strongest signal). *Empirically validated:* **99.8% of
   real resistors and 99.9% of real capacitors land exactly on IEC 60063 grids**; the ~0.2%
   off-grid residue already surfaces real bad data (zero-capacitance records). Pick the coarsest
   series whose tolerance ≥ declared, compute snap-distance, flag >0.5%. **Must be family-scoped** —
   measured: varistor V₁ₘₐ follows an MOV-specific ladder (6.3% "off-grid" = false alarms there), so
   apply to R/C (and Zener breakdown) only, with a shunt/precision-resistor allowlist.
2. **Part-number value-encoding self-consistency** (EIA-198 / EIA-96 / 3–4-digit codes): the value
   encoded in the PN must equal the spec (cap `473`→47 nF; resistor `4992`→49.9 kΩ). Decodable-but-
   inconsistent = strong tell. Gate on recognized grammars; "undecodable" ≠ fake.
3. **Over-precision-vs-tolerance fingerprint:** a 5% part quoted to 5 sig-figs (4.7012 kΩ), or a
   `minimum==nominal*0.8` float artifact (`2.64e-7`), is a generator tell. Generic `check_generic` pass.
4. **Cross-parameter physics correlations:** `figureOfMerit==Ron·Qg` recompute; DF`==`ESR·2πfC (gated
   on equal frequency); TVS `peakPulsePower==Vclamp·Ipp`; gate-drive chain `Vth(max)<onResistanceVgs≤VgsMax`;
   tech-aware `Rds·BV`-too-good; ENOB≤resolution (hard). Individually in-range, jointly impossible.
5. **Cross-family contamination / discriminator coherence** (highest signal-per-line): noun-scan
   `part.description` for wrong-family component words ("inductor" under a `connector`) — this catches
   the 22 mislabeled inductors that *every physics bound passed*. Plus `GEN_MULTI_DISCRIMINATOR`
   (>1 top-level discriminator) and `GEN_PN_DIGIT_LEAK` (a numeric field is a substring of the PN).
6. **Completeness / presence-fingerprint score (new verdict dimension):** ⚠ per-family core-field
   manifest needs your sign-off. First cut needs no manifest — expose `skipped/expected` thinness ratio.
   Emit `GEN_SPARSE` SUSPICIOUS below the empirical real-part floor; respect magnetics `dataCompleteness`.
   **Does not** flip `valid`.
7. **Corpus-level batch layer** (separate `validate_corpus()` API, screening only): robust within-cohort
   MAD/IQR outliers (log-space, n≥8) and MinHash+LSH near-duplicate detection. ⚠ **Naive clone
   detection is a trap** — measured 33.5% of MOSFETs / 51.5% of caps share identical electrical blocks,
   but these are *legit* part-family variants (current bins, ESR grades). Must exclude variant axes and
   key on cross-manufacturer identity. Highest FP risk → last; never auto-reject.

---

## 5. Priority roadmap (highest value / lowest risk first)

| Phase | What | Risk | Effort |
|---|---|---|---|
| **P0** | Recalibration: fix the 2 mass-invalidators (MOS-1, MOS-2) + the high-firing FPs (MOS-3/4, CAP-01/02, RES-1, DIO-1/2, MAG-2/4). Pure threshold/branch edits; measure each against live data before/after. Drops mosfet INVALID 5.67%→~0. | low | ~1 day |
| **P1** | Free ordering/positivity invariants (§2 coverage gaps). Cheap, exact, anti-synthesis value. | low | ~1 day |
| **P2** | Cross-family contamination + framework hygiene (`GEN_FAMILY_MISMATCH`, `GEN_MULTI_DISCRIMINATOR`, `GEN_PN_DIGIT_LEAK`, connectors LFS fix, shared `fmt()`/non-finite). | low | ~0.5 day |
| **P3** | E-series + over-precision layer (the core real-vs-fake discriminator). Verify tolerance→series mapping on live data first. SUSPICIOUS-only. | low–med | ~2 days |
| **P4** | Cross-parameter physics correlations (tech-aware, wide bands). | med | ~2–3 days |
| **P5** | ⚠ New verdict dimensions (`completeness`, authenticity score) — needs `Verdict` struct extension (no schema edit) + per-family manifest sign-off. | med | ~2 days |
| **P6** | Corpus batch layer (`validate_corpus`): cohort outliers + dedup. Highest FP risk → last; review report only. | med–high | ~3 days |

⚠ **Two items need your decision:** (a) `MOS_RON_FLOOR` done correctly needs die area = a schema
field — remove for now, or add the field? (b) the per-family **core-field manifest** for the
completeness score.
