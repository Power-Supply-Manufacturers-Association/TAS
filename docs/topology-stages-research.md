# Stage-based topology decomposition — literature review

> **Historical research artifact.** This document captures the literature review that
> *informed* the stage design; it does not describe the current schema. For the live
> CIAS/TAS model (bricks, stages, agnostic simulation), see [`schema.md`](schema.md).

Background research before extending TAS to break a converter topology into named stages
(e.g. *EMI filter → rectifier → PFC → bulk → inverter → isolation → output rectifier → output filter*).

The goal of this document is **not** to propose a schema yet — it is to make sure that whatever
"stage" abstraction we add is consistent with the way the power-electronics literature has been
formalising "what is a converter made of" for the last 50 years. Anything we invent that conflicts
with that body of work will eventually bite us (synthesis, modelling, control-loop derivation,
loss accounting, EMI analysis all depend on the same primitives).

---

## 1. The reference work: Maksimović, *Synthesis of PWM and Quasi-Resonant DC-to-DC Power Converters* (Caltech PhD, 1989)

- Author: **Dragan Maksimović**, defended 12 Jan 1989
- Advisor: **Slobodan Ćuk**; co-advisor: **R. D. Middlebrook**
- DOI: [10.7907/B8XA-2R90](https://doi.org/10.7907/B8XA-2R90), full PDF at <https://thesis.caltech.edu/626/>
- Companion paper: D. Maksimović & S. Ćuk, *"General properties and synthesis of PWM DC-to-DC
  converters"*, IEEE PESC 1989, pp. 515–525.
- Follow-up: D. Maksimović & S. Ćuk, *"A unified analysis of PWM converters in discontinuous
  modes"*, IEEE TPEL 6(3), Jul 1991, pp. 476–490.

### What the thesis actually says

Maksimović's central abstraction (which is the one we should adopt) is:

> A PWM converter is **two linear time-invariant (LTI) networks** built from capacitors and
> inductors, plus a source and a load, that are switched at constant frequency with duty ratio
> *D* between two configurations.

From that single defining assumption he derives **general properties** that bound *any* PWM
converter regardless of how it is drawn:

1. **Reactance count ↔ conversion ratio.** A PWM converter with *n* reactive elements (L+C) can
   only realise a DC conversion ratio *M(D)* of bounded complexity (specifically, *M(D)* is a
   ratio of polynomials in *D* whose degree is bounded by *n* and the number of switches).
2. **Switch count constraint.** The number of switches in the two switched networks is fixed by
   the topology — not free.
3. **Continuous-terminal-current ("non-pulsating") constraint.** Whether *Iin* and/or *Iout* can
   be made continuous depends on whether an inductor sits at the corresponding terminal in
   *both* switched states; this is a topological property, not a sizing question.
4. **Inductor coupling constraint.** Inductors that carry identical voltage waveforms in both
   switched states *can* be coupled (Ćuk, SEPIC, Zeta — coupled-inductor variants); inductors
   that don't, can't.

He then turns this into a **synthesis algorithm**: given a spec
`(target M(D), # reactances, # switches, # transistors, terminal-current properties,
coupling allowed?)`, the algorithm enumerates **all** PWM topologies that satisfy it. Buck,
boost, buck-boost, Ćuk, SEPIC, Zeta, and a handful of previously-unidentified topologies fall
out as the complete enumeration for small *n*.

The QR (quasi-resonant) half of the thesis does the same trick for soft-switched converters:
take a 2-switch PWM "parent", add resonant L and/or C, enumerate the topologically distinct
positions, and you get exactly **6 QR classes** (ZV, ZC, ZV-QSW, ZC-QSW, plus two new ones he
named Q<sub>n</sub>-PWM / Q<sub>f</sub>-PWM). Multi-resonant (MR) families fall out by the same
construction.

### Why this matters for our "stages" idea

Maksimović's framing **does not have "stages"** as a primitive. His primitive is
**(switched LTI network, source, load, duty)**. The "stages" we want to expose
(filter / rectifier / PFC / bulk / inverter / isolation / output rectifier / output filter)
are an **engineering decomposition** of a converter — convenient for humans, BOM groupings,
loss accounting, EMI partitioning — but they are not what defines a converter mathematically.

The implication is concrete: **a "stage" must be definable as a port-wise sub-network of the
overall switched network**, otherwise we will end up with stages that overlap (one inductor
belonging to two stages) or stages that have no physical meaning (a "PFC stage" that includes
half the bulk capacitor). The clean criterion is *2-port (or n-port) cuts of the netlist along
constant-current / constant-voltage interfaces*.

---

## 2. Surrounding work — the abstractions that already exist

Power electronics has been generalising topologies for half a century. There are **four**
distinct levels of abstraction in the literature, and a stage-based TAS schema needs to know
where it sits in this stack.

### Level 0 — State-space averaging (Middlebrook & Ćuk, 1976)

> R. D. Middlebrook & S. Ćuk, *"A general unified approach to modelling switching-converter
> power stages"*, IEEE PESC 1976, pp. 18–34.

Defines the *averaged model*: two state-space descriptions
*ẋ = A₁x + B₁u* (switch closed) and *ẋ = A₂x + B₂u* (switch open), combined as
*ẋ = [DA₁ + (1−D)A₂]x + [DB₁ + (1−D)B₂]u*. This is what makes "average inductor current",
"average switch loss", and small-signal control-to-output transfer functions well-defined.

For TAS this is the layer that **outputs** documents will reference — average waveforms, loss
totals, transfer functions all live here.

### Level 1 — Canonical circuit model (Middlebrook & Ćuk, 1977)

> R. D. Middlebrook & S. Ćuk, *Advances in Switched-Mode Power Conversion*, vol. I, 1981
> (collected reprints of the 1977 IEEE TIE / TPEL papers).

Every two-switch PWM converter, after averaging, reduces to **one canonical equivalent
circuit**:

```
            ideal DC transformer       low-pass LC filter
   Vg ──┤ M(D) : 1 ├────────[ Le ]────┬──── Vout
                                       │
                                      [Ce]
                                       │
                                       ⏚
        + duty-modulated voltage and current sources j(d), e(d) for small-signal
```

Buck, boost, buck-boost, Ćuk, SEPIC, Zeta all collapse to this form — they only differ in
*M(D)*, *Le*, *Ce*, and the source coefficients.

**This is the strongest argument for a stage decomposition**: the canonical model itself
has natural *parts* — (a) the **switching cell** that realises *M(D)*, and (b) the
**effective output filter** *(Le, Ce)*. Those are two stages. Adding a transformer adds an
**isolation stage**. Adding an input EMI filter adds a **filter stage**. Etc.

### Level 2 — Canonical switching cells (Ćuk's three-terminal cells)

The three primitive PWM cells, each containing one inductor and the active+passive switch pair:

| Cell | M(D) | Used in |
|---|---|---|
| **Buck cell** | *D* | buck, forward, half-/full-bridge buck, buck-derived |
| **Boost cell** | *1/(1−D)* | boost, PFC boost, boost-derived |
| **Buck-boost cell** | *D/(1−D)* | buck-boost, flyback, Ćuk, SEPIC, Zeta (after rearrangement) |

Cascading two cells gives quadratic converters (e.g. *D²/(1−D)²* for cascaded buck-boost).
Stacking, paralleling, transformer-coupling, and inverting these cells generates **the entire
isolated converter family** (forward = buck cell + transformer; flyback = buck-boost cell with
the inductor split into a coupled pair; PSFB = buck cell + bridge + transformer; LLC = boost
cell + resonant tank + rectifier; etc.).

This is the level where a **stage-based decomposition becomes natural and unambiguous**:
- *front-end EMI filter* — passive 2-port LC, no switching
- *bridge rectifier* — passive 2-port, line-frequency switching
- *PFC stage* — boost cell (or interleaved boost cells)
- *bulk storage* — passive 1-port (the bulk cap) — debatably part of either neighbour
- *inverter / primary bridge* — switching network, no DC conversion on its own
- *isolation* — transformer (a 2- or n-port LTI block)
- *output rectifier* — switch network operating at the secondary
- *output filter* — passive 2-port LC

Each stage is a **port-wise sub-network**. Connections between stages are exactly what TAS
already calls `wire` / `externalPort` connections — what changes is that we **group**
components, not that we add a new connection type.

### Level 3 — PWM-switch model (Vorpérian, 1990; Tymerski & Vorpérian, 1986)

> V. Vorpérian, *"Simplified analysis of PWM converters using model of PWM switch
> Parts I & II"*, IEEE TAES 26(3), May 1990, pp. 490–505.
> R. Tymerski & V. Vorpérian, *"Generation, classification and analysis of switched-mode
> DC-to-DC converters by the use of converter cells"*, INTELEC 1986.

Vorpérian's PWM switch is a **3-terminal nonlinear element** (active switch a, passive switch
p, common c) that, once averaged, has a small-signal equivalent independent of the
surrounding linear network. This is the dual of Ćuk's cell view: instead of *"the cell is the
LCL block + switches"*, it's *"the switches are the nonlinear primitive, everything else is
linear and time-invariant"*.

Practically: if a stage in TAS is *"a switching cell that performs one M(D) operation"*, the
PWM-switch model is what lets us derive the stage's small-signal model **independent of the
upstream/downstream stages** — exactly the property we want for compositional analysis,
control-loop design, and per-stage loss attribution.

### Level 4 — Modern cell-based / graph-based synthesis

- **Liu & Lee** (1988, IEEE TPEL): graph-based enumeration, complementary to Maksimović's
  matrix approach.
- **B. W. Williams**, *"Generation and analysis of canonical switching cell DC-DC converters"*,
  IEEE TIE 61(1), Jan 2014. Catalogues hundreds of converters as combinations of canonical
  cells with rotation/reflection/inversion operators. Closest to a "stage algebra".
- **Erickson & Maksimović**, *Fundamentals of Power Electronics*, 3rd ed., Springer 2020.
  Chapter 6 is exactly the textbook formalisation of "stages": converter synthesis by
  *(a)* inversion, *(b)* cascade connection, *(c)* differential connection, *(d)* isolation
  transformer insertion, *(e)* synchronous-rectifier replacement of diodes. Each operation
  preserves the canonical-model structure.

**These five operations are the complete generative grammar** for the converter families we
care about. If our TAS "stage" abstraction can express them, it's expressive enough. If it
can't, it's broken.

---

## 3. Translating this into a TAS stage abstraction (sketch — for discussion, not yet a schema)

What the literature tells us a stage **is**:

1. A **port-wise sub-network** of the topology graph — i.e. a set of components plus the
   internal wires between them, with a small set of *external* nets (typically 2 or 3) that
   connect it to neighbouring stages.
2. Exactly one of the following **functional roles**, each of which corresponds to a
   well-defined averaged-model behaviour:
   - `lineFilter` — passive LC, no active switching (EMI / differential / common-mode)
   - `rectifier` — diode/SR bridge, no DC conversion ratio of its own (just |·|)
   - `pfc` — switching cell that shapes input current to follow *Vin*
   - `bulkStorage` — single capacitor or bank between two switching stages
   - `inverter` — DC→AC switching network (half-bridge, full-bridge, push-pull, …)
   - `isolation` — transformer (1:N, possibly multi-winding, possibly with leakage modelled)
   - `outputRectifier` — secondary-side rectifier (diode, SR, centre-tap, current-doubler, …)
   - `outputFilter` — passive LC after the output rectifier
   - `auxiliary` — bias supply, snubber, clamp, gate-drive supply (does not carry main power)
   - `control` — controller IC + sensing + feedback (no power flow)
3. An **ordered position** in the main power chain (auxiliary/control stages have no position),
   so that *stage[i].outputPort = stage[i+1].inputPort* is enforceable.
4. A **declared M(D)** (or *M(D, fs/fr)* for resonant stages, or `1` for transparent
   stages like rectifiers and filters) that lets us check the cascade against the spec
   `Vout/Vin` without actually running a SPICE simulation.

What the literature warns us **not** to do:

- Don't make stages overlap. Every component belongs to **exactly one** stage. A bulk cap
  is *either* part of `pfc.outputFilter` *or* a standalone `bulkStorage` stage — pick one and
  stick with it.
- Don't model parasitics as stages. Snubbers, RC dampers, leakage inductance — these stay as
  components in the stage they parasitise (consistent with the existing TAS rule that
  parasitic L/C/R are real BOM components, not connection kinds).
- Don't conflate the **role of a stage** (`pfc`) with the **role of a component**
  (`pfcSwitch`, `pfcInductor`). They are at different levels — stage role is structural,
  component role is per-part. We already have component roles in `topology.json:42`, and
  many of them (`pfcSwitch`, `pfcInductor`, `outputRectifier`, `bulkCapacitor`,
  `resonantInductor`) are *already* shadowing stage names. After we add stages, those
  component roles can either (a) be derived from `stage.role + position-in-stage`, or
  (b) stay as a redundant cross-check. Worth deciding before we write the schema.
- Don't make `circuit` a flat list once stages exist. Either:
  - *Option A*: Stages **own** their internal components and internal wires. Top-level
    `circuit` only contains the inter-stage wires + couplings + external ports. This is the
    cleanest mapping to the canonical-model picture and to Erickson-Maksimović Ch. 6.
  - *Option B*: Components and wires stay flat; each stage just lists the **names** of the
    components/wires it owns. Less structurally rigid, easier to refactor.
  - The literature doesn't pick for us. Option A is more faithful to the port-wise-subnetwork
    definition; Option B is friendlier to incremental migration of existing TAS docs.

---

## 4. Open questions before touching the schema

1. **Is "stage" the right name?** Erickson-Maksimović uses *"power stage"* for the entire
   switching network (everything except the controller), and *"converter cell"* for what
   we're calling a stage. Williams 2014 uses *"canonical switching cell"*. If `stage` means
   different things in different parts of the docs, that will hurt. Candidate names:
   `stage`, `block`, `cell`, `section`. My weak preference is `stage` (matches your wording
   and is the most common term in industry datasheets — *"two-stage PFC + LLC"*).
2. **Cascading rule enforcement**. Should the schema *enforce* port matching between
   consecutive stages (input port of stage *i+1* = output port of stage *i*), or just
   recommend it? Enforcing requires giving each stage a typed input/output port (DC voltage,
   AC line, rectified DC, isolated DC, ...).
3. **Multi-output converters** (e.g. flyback with 12 V + 5 V + −12 V outputs) — a single
   isolation stage feeds *N* output-rectifier + output-filter chains. Stages then form a
   **tree**, not a chain. The schema must allow that from day 1; retrofitting later is
   painful.
4. **Resonant tanks** (LLC, LCC, series-resonant) — is the resonant tank its own stage, or
   part of the inverter, or part of the isolation stage? Maksimović's QR analysis treats
   the resonant L and C as belonging to the **switching cell**, not as separate. Suggest:
   `inverter` stage owns the resonant tank for series-resonant; `isolation` stage owns Lm
   and any parallel-resonant cap for LLC. Needs a documented convention.
5. **Interleaved / multi-phase stages** (interleaved boost PFC, multi-phase buck) — one
   logical stage, *N* parallel physical sub-stages. Probably modelled as a stage with a
   `phaseCount` parameter and per-phase component lists, *not* as N sibling stages (because
   they share a controller and a port pair).
6. **Where does the controller live?** A `control` stage with no power-chain position, that
   *references* the components it senses/drives. This is the only graceful way to model
   things like a current-mode controller that senses the PFC inductor *and* drives the
   inverter switches.

---

## 5. Decisions taken

| # | Question | Decision |
|---|---|---|
| 1 | Naming | **`stage`** |
| 2 | Cascade enforcement | **enforced** (typed `inputPort` / `outputPorts[]`, `portType` enum) |
| 3 | Chain vs tree | **tree** — only the `isolation` stage may have `outputPorts.length > 1` |
| 4 | Resonant tank ownership | **Maksimović convention** — tank lives inside the `inverter` stage (and `Lm` etc. inside `isolation` for LLC) |
| 5 | Interleaved phases | **one logical stage** with `phaseCount` integer field |
| 6 | Controller | **`control` stage** with no power-chain position; references components via `senses[]` / `drives[]` |
| 7 | Component ownership | **Option A** — each stage owns its `components[]` and internal `connections[]`; `topology.interStageCircuit` carries only cross-stage wires + `externalPort`s |

Implemented in commit/PR alongside this doc (see `schemas/topology.json`,
`schemas/circuit.json`, `schemas/inputs.json`, `schemas/outputs.json`,
`schemas/utils.json`, `schemas/TAS.json`).

### Notes on what was *not* added (and why)

- **No `auxiliary` stage role.** Bias supplies are uncommon and add cross-stage wire
  ownership awkwardness. Add later if a real design needs it.
- **No per-stage `parameters` blob.** Component values come from PEAS docs; stage role +
  port types carry the structural meaning. There was nothing left for a `parameters` field
  to hold that wouldn't be a generic dict — so it's gone.
- **No component-level `role` enum.** Removed (was previously a 30-entry enum:
  `pfcSwitch`, `mainInductor`, …). Recoverable from (stage role + PEAS document type +
  pin connectivity) when needed. Easier to add back than to live with overlap and drift.
- **No SPICE `simulation` block under `topology`.** Per-component SPICE overrides moved
  onto each component (`circuit.json#/$defs/component.simulationOverride`); global SPICE
  artefacts (`models[]`, `commands[]`) lifted to `TAS.simulation` so the same topology can
  be simulated multiple ways without duplication.
- **Cascade graph topology** (root has no upstream port; leaves all have `dcOutput`
  outputPort; every stage's `inputPort.wire` matches some upstream `outputPorts[*].wire`)
  is **not** enforced by the schema — it requires whole-document consistency across
  multiple `$defs/port` instances, which JSON Schema can't express cleanly. A separate
  `validate_topology.py` script will own that check.

---

## References

- Maksimović, D. *Synthesis of PWM and Quasi-Resonant DC-to-DC Power Converters.* PhD
  thesis, Caltech, 1989. <https://thesis.caltech.edu/626/>, DOI 10.7907/B8XA-2R90.
- Maksimović, D. & Ćuk, S. *General properties and synthesis of PWM DC-to-DC converters.*
  IEEE PESC 1989, 515–525.
- Maksimović, D. & Ćuk, S. *A unified analysis of PWM converters in discontinuous modes.*
  IEEE TPEL 6(3), Jul 1991, 476–490.
- Middlebrook, R. D. & Ćuk, S. *A general unified approach to modelling switching-converter
  power stages.* IEEE PESC 1976, 18–34.
- Middlebrook, R. D. & Ćuk, S. *Advances in Switched-Mode Power Conversion*, vols. I–III.
  TESLAco, 1981–83.
- Vorpérian, V. *Simplified analysis of PWM converters using model of PWM switch, Parts I &
  II.* IEEE TAES 26(3), May 1990, 490–505.
- Tymerski, R. & Vorpérian, V. *Generation, classification and analysis of switched-mode
  DC-to-DC converters by the use of converter cells.* INTELEC 1986.
- Liu, K.-H. & Lee, F. C. *Topological constraints on basic PWM converters.* IEEE PESC 1988.
- Williams, B. W. *Generation and analysis of canonical switching cell DC-DC converters.*
  IEEE TIE 61(1), Jan 2014.
- Erickson, R. W. & Maksimović, D. *Fundamentals of Power Electronics*, 3rd ed. Springer,
  2020. (Ch. 6 — Converter Circuits.)
