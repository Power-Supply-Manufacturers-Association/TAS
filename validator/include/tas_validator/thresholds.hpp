// SPDX-License-Identifier: MIT
// TAS Physics Validator — all numeric bounds in one auditable place.
//
// Each constant carries the physical/datasheet rationale. Bounds are
// deliberately generous: an IMPOSSIBLE bound flags only physics-violating data
// (large safety margin over the most extreme real part); a SUSPICIOUS bound
// flags "almost certainly a data-entry error" while tolerating exotic parts.
//
// Sources for the NEW bounds (see plan): TI SLUP124 (core characteristics);
// PSMA CWS magnetics properties; Rohm SiC app-notes + MDPI Si/SJ/SiC/GaN MOSFET
// review (specific-Ron vs BV); Nature Comms MLCC energy-storage + industry
// J/cm^3 figures for capacitor energy density.
#pragma once

namespace tas::thr {

// ---- Magnetics --------------------------------------------------------------
// DCR x size_mm^2 / (L_uH)  — geometric constraint (ported from Proteus).
inline constexpr double MAG_DCR_GEOM_IMP = 1000.0;
inline constexpr double MAG_DCR_GEOM_SUS = 100.0;
inline constexpr double MAG_DCR_GEOM_SUS_LOW = 1e-6;
// DCR / L  [ohm/H] — material limit (ported).
inline constexpr double MAG_DCR_PER_H_IMP = 1e9;
inline constexpr double MAG_DCR_PER_H_SUS = 1e6;  // only applied when L > 1 uH
// Isat^2 * DCR  [W] — peak conduction dissipation (ported).
inline constexpr double MAG_ISAT_POWER_IMP = 500.0;
inline constexpr double MAG_ISAT_POWER_SUS = 50.0;
// SRF * sqrt(L) — parasitic resonance (ported). Real catalog tail reaches 2.6e6
// (Würth WE-CBF/CHSA beads, TDK/Würth common-mode chokes); IMP raised above it so
// the impossible tier is a unit-error backstop, not a flag on real sub-uH chip parts.
inline constexpr double MAG_SRF_L_IMP = 3e6;
inline constexpr double MAG_SRF_L_SUS = 8e5;  // above p99 (6.25e5); only when L > 1 nH
// Stored-energy density  E = 1/2 L Isat^2 over device volume  [J/m^3].
// Real WE-MAPI part ~ 0.8 mJ/cm^3 = 800 J/m^3; ferrite materials are limited by
// B_sat^2/(2 mu); powder/metal-alloy cores store far more. 1 J/cm^3 = 1e6 J/m^3.
inline constexpr double MAG_ENERGY_DENSITY_IMP = 2e6;          // 2 J/cm^3
inline constexpr double MAG_ENERGY_DENSITY_SUS_FERRITE = 2e5;  // 0.2 J/cm^3
inline constexpr double MAG_ENERGY_DENSITY_SUS_POWDER = 1e6;   // 1 J/cm^3
// Inductor self-resonant frequency floor.
inline constexpr double MAG_SRF_FLOOR_HZ = 1e3;
// Inductance tolerance band width (max/min) suspicious above this.
inline constexpr double MAG_L_TOL_RATIO_SUS = 3.0;
// Rated-current / saturation-current ratio. ~20% of real parts have rated>Isat
// (Isat is a peak L-drop spec, rated is RMS thermal); only a gross ratio is a
// unit error (e.g. SRP1265A-R56M rated 37 A vs Isat 0.058 A = 638x).
inline constexpr double MAG_RATED_ISAT_SUS = 5.0;
inline constexpr double MAG_RATED_ISAT_IMP = 50.0;
// Upper self-resonant-frequency sanity (catches Hz/MHz swaps the other way).
inline constexpr double MAG_SRF_CEIL_HZ = 1e11;

// ---- Capacitors -------------------------------------------------------------
// Stored-energy density 1/2 C V^2 over volume [J/m^3]. Industry figures:
// alum-elec ~0.01-0.05, film ~0.02-0.2, conventional ceramic <0.2, tantalum up
// to ~9, advanced MLCC up to ~22 J/cm^3. Hard ceiling 50 J/cm^3.
// SUS bands track real-part distributions (aluminum-electrolytic 0.2-2.5 J/cm^3,
// not the 0.01-0.05 the old comment claimed); these were the #1/#2 noise source.
inline constexpr double CAP_ENERGY_DENSITY_IMP = 100e6;      // 100 J/cm^3 (advanced MLCC dielectric ~22 leaves margin)
inline constexpr double CAP_ENERGY_DENSITY_SUS_ALUM = 3e6;   // 3 J/cm^3 (above real max ~2.5)
inline constexpr double CAP_ENERGY_DENSITY_SUS_TANT = 9e6;    // 9 J/cm^3
inline constexpr double CAP_ENERGY_DENSITY_SUS_FILM = 0.5e6;  // 0.5 J/cm^3 (above real max ~0.45)
inline constexpr double CAP_ENERGY_DENSITY_SUS_CERAMIC = 5e6;  // 5 J/cm^3 (above p99 ~2.9)
// Inductance magnitude [H] — dimension-free sanity catching uH/mH/H unit slips.
// The largest practical wound inductors are ~10 H; nothing in a parts catalog
// reaches 100 H (most power magnetics are uH-mH).
inline constexpr double MAG_L_MAGNITUDE_IMP = 100.0;     // > 100 H impossible
inline constexpr double MAG_L_MAGNITUDE_SUS = 1.0;       // > 1 H suspicious

// DCR x Irated^2 / box-surface-area [W/cm^2] — dissipation the part claims to
// survive at its own thermal rating, per unit of cooling surface. Calibrated on
// the ABT #351 campaign: the largest legitimate parts (41 mm three-phase CMCs at
// their 40 C-rise rating, 100+ A busbar chokes) reach ~0.7 W/cm^2, while every
// vendor-confirmed defect sat at 4.5 W/cm^2 or (far) above. The absolute floor
// exists because the surface model diverges for tiny parts: below a few watts,
// pad conduction dominates and an 0402 at its vendor-rated current is fine
// despite a "high" areal number. Exclusions live in the check, not here: the
// product DCR*I^2 is not a physical quantity at all for current-sense parts
// (primary current x winding R), Isat-quoted molded parts, or chip beads (whose
// datasheets pair IR at dT=40K with a non-simultaneous small-signal RDC max —
// WE 7427920 prints 9600 mA next to 0.15 ohm on one page).
inline constexpr double MAG_DISS_POWER_FLOOR_W = 5.0;
inline constexpr double MAG_DISS_DENSITY_SUS = 2.5;      // W/cm^2
inline constexpr double MAG_DISS_DENSITY_IMP = 25.0;     // W/cm^2
// Capacitance magnitude [F] — dimension-free sanity that catches uF/F unit
// errors (e.g. a 100 F MLCC/electrolytic). Only supercapacitors exceed ~1 F;
// the largest commercial EDLC is ~3400 F.
inline constexpr double CAP_MAGNITUDE_IMP = 10.0;        // non-super: > 10 F impossible
inline constexpr double CAP_MAGNITUDE_SUS = 1.0;         // non-super: > 1 F suspicious
inline constexpr double CAP_MAGNITUDE_SUPER_IMP = 1e5;   // supercap: > 100 kF impossible
inline constexpr double CAP_MAGNITUDE_SUPER_SUS = 5e3;   // supercap: > 5 kF suspicious
// Dissipation factor (tan delta) upper bounds by dielectric family.
inline constexpr double CAP_DF_CERAMIC_NPO = 0.001;
inline constexpr double CAP_DF_CERAMIC_X7R = 0.025;
inline constexpr double CAP_DF_CERAMIC_Y5V = 0.05;
inline constexpr double CAP_DF_FILM = 0.01;
inline constexpr double CAP_DF_TANTALUM = 0.25;  // solid Ta reaches 0.10-0.25 at 120 Hz
inline constexpr double CAP_DF_ELECTROLYTIC = 0.30;
inline constexpr double CAP_DF_POLYMER = 0.10;
inline constexpr double CAP_DF_DEFAULT = 0.5;  // generic upper sanity
// Leakage: I_leak / (C*V)  [1/s] — fraction of charge bled per second.
// Electrolytics ~1e-2..1e-1; film/ceramic far lower. Physically impossible above.
inline constexpr double CAP_LEAKAGE_PER_CV_IMP = 10.0;
inline constexpr double CAP_LEAKAGE_PER_CV_SUS = 1.0;
// Insulation time constant Riso*C [s]: electrolytics short (~50-1000s), film/
// ceramic long (>1e4 s). Suspicious outside a very wide band.
// Riso*C low bound applies to BULK caps only (the check now gates on C > 1 uF);
// ceramics legitimately compute sub-second RC and must not be flagged.
inline constexpr double CAP_RC_SECONDS_SUS_LOW = 0.3;
inline constexpr double CAP_RC_SECONDS_SUS_HIGH = 1e9;
inline constexpr double CAP_RC_GATE_FARAD = 1e-6;  // only apply the low bound above this C

// ---- Resistors --------------------------------------------------------------
// Power dissipation density over footprint [W/mm^2]. SMD chips dissipate ~0.05-
// 0.5 W/mm^2; even high-power packages stay under a few W/mm^2.
inline constexpr double RES_POWER_PER_MM2_IMP = 20.0;
inline constexpr double RES_POWER_PER_MM2_SUS = 3.0;
// Manufacturable resistance window [ohm].
inline constexpr double RES_R_MIN_SUS = 1e-4;   // 0.1 mohm — below this is exotic shunt territory
inline constexpr double RES_R_MAX_SUS = 1e12;   // 1 Tohm
// Working-voltage field over body length [V/m]. Air/coating breakdown ~3 MV/m;
// rated working voltage stays well under, so flag field above ~1e7 V/m.
inline constexpr double RES_FIELD_VPM_SUS = 1e7;
inline constexpr double RES_FIELD_VPM_IMP = 1e8;
// Temperature coefficient |ppm/C|.
inline constexpr double RES_TEMPCO_PPM_SUS = 10000.0;
// Tolerance fraction upper sanity.
inline constexpr double RES_TOL_MAX_SUS = 0.5;

// ---- MOSFETs ----------------------------------------------------------------
// Charge component sum may exceed total gate charge by at most this factor
// (datasheet rounding / different test conditions).
inline constexpr double MOS_QG_SUM_SLACK = 1.05;
// Gate threshold windows by technology [V].
inline constexpr double MOS_VTH_SI_LO = 1.0, MOS_VTH_SI_HI = 5.0;
inline constexpr double MOS_VTH_SIC_LO = 1.5, MOS_VTH_SIC_HI = 6.0;
inline constexpr double MOS_VTH_GAN_LO = 0.7, MOS_VTH_GAN_HI = 2.5;
// Body-diode / reverse-conduction forward drop [V].
inline constexpr double MOS_BODY_VF_LO = 0.2, MOS_BODY_VF_HI = 5.0;
// Power-vs-thermal consistency factor: Pdiss should track (Tjmax-25)/Rth(j-c).
inline constexpr double MOS_PTHERMAL_RATIO_SUS = 3.0;
// A rated continuous drain current that the record's OWN thermal path cannot
// carry (ABT #500: TO-247 records built from a sibling package's thermal table).
// The datasheet derives ID at TC=25 C from Id^2*Rds(on)@Tjmax <= (Tjmax-25)/RthJC,
// and Rds(on) rises ~2-2.5x from 25 C to Tjmax, so a consistent record sits near
// ratio 0.4-0.5 when the COLD Rds(on) the catalogue stores is used. Ratios just
// above 1 are real (ID is often bond-wire- rather than thermally-limited, and the
// stored Rds(on) is a max while ID is derived from typ), so only a cold-loss
// overcommit of this factor is called impossible: at 2x cold the part is 4-5x
// over its own ceiling hot, which no rounding explains.
inline constexpr double MOS_IDC_THERMAL_RATIO_IMP = 2.0;
// Isolated packages (FullPAK/TO-220F/TO-3PF): vendors publish the bare sibling's
// silicon Id with a duty-cycle footnote, so 2-3x cold overcommit is the rating
// convention, not a defect. Datasheet-verified on Infineon IPA60R120P7 (2.9x)
// and IPA60R190P6 (2.3x) during the ABT #500 campaign; 4x still catches wrong
// thermal tables (the pre-repair records sat at 3-13x with FABRICATED pairs).
inline constexpr double MOS_IDC_THERMAL_RATIO_ISO_IMP = 4.0;
// A powerDissipation that is really an on-resistance (ABT #482/#494: Vishay
// serves "On-resistance at 4.5 V" one column from "Power dissipation (max.)").
// Anchored on the PACKAGE, not on the ohm value: a die rated to carry
// MOS_PD_IDC_A [A] continuously sits behind a thermal path good for several
// watts, and even the ambient-referenced minimum-pad P_D some vendors publish
// for such a die stays above MOS_PD_IDC_W [W] (worst real case in this catalogue
// is 0.69 W at 46 A, an SO-8FL steady-state figure). Below it, the number is not
// a power rating at all.
inline constexpr double MOS_PD_IDC_A = 20.0, MOS_PD_IDC_W = 0.5;
// A gateSourceVoltageMax that is really a power dissipation (ABT #501: the mirror
// of the above -- the same Vishay grid serves "Power dissipation (max.)" one
// column from "Gate-to-source voltage", so 60 W lands as a 60 V gate rating).
// V_GS(max) is a gate-OXIDE breakdown rating, and the oxide sets a hard ceiling
// no process crosses: Si tops out at +-30 V, SiC at +-25, GaN at +-7 (+-20 for
// cascodes); the highest real rating in this catalogue is 30 V. Anything past
// this is not a gate rating at all -- at 156 V a ~50-100 nm oxide has punched
// through, so the number came from another column.
inline constexpr double MOS_VGS_MAX_ABS_IMP = 40.0;
// Specific-Ron floor proxy: Ron*Vds^2 [ohm*V^2] minimum for a single die by
// technology. Silicon obeys Ron,sp ~ k*BV^2.5; with die area unknown this is an
// advisory (SUS) lower bound only. Calibrated so a 600 V Si part with Ron < a
// few mohm or a 1200 V SiC part with Ron < ~0.1 ohm gets flagged.
// NEUTERED to a sub-physical unit-error backstop (was 50/5/1, which fired on
// 28.6% of real low-voltage Si parts — the proxy omits die area so it cannot be a
// real specific-Ron bound). These floors now only catch Ron≈0 / unit-error entries.
inline constexpr double MOS_RON_VDS2_SI_SUS = 0.15;    // Si / superjunction
inline constexpr double MOS_RON_VDS2_SIC_SUS = 0.5;    // SiC
inline constexpr double MOS_RON_VDS2_GAN_SUS = 0.1;    // GaN
// A totalGateCharge that is not a gate charge (ABT #512: Vishay's grid served
// Q_g one column from another quantity, onsemi's export publishes Q_gs under the
// "Qg Typ @ VGS = 10 V" heading, and 2 nC landed on an 80 V / 5.5 mohm / 72 A die).
// Ron*Qg [ohm*C] is the switching figure of merit. Both factors scale with the
// same channel width -- Ron ~ 1/W, Qg ~ W -- so their PRODUCT is a technology
// constant independent of die area, which is exactly what makes it a usable floor
// when the die area is unknown. Nothing in the field is below it: the lowest of
// TI's 177 published silicon MOSFETs is 24.2 pOhm*C (CSD16415Q5) and the lowest of
// EPC's 97 published GaN parts is 8.6 pOhm*C (EPC2370) -- GaN gets its own floor
// because a better FOM is the whole point of the technology. Each floor sits ~1.6x
// below its technology's verified best, so only a value that is not gate charge
// at all can reach it.
inline constexpr double MOS_QG_RON_FOM_SI_IMP = 1.5e-11;   // Si / SiC [ohm*C]
inline constexpr double MOS_QG_RON_FOM_GAN_IMP = 5.0e-12;  // GaN [ohm*C]

// The SAME figure of merit, but as a VOLTAGE-CLASS floor (adversarial physics
// review, 2026-09-04). MOS_QG_RON_FOM_*_IMP above is one global floor for all
// silicon, so it is set by the best 30 V logic-level die (24 pOhm*C) and is
// therefore ~100x too permissive for a 600 V part: FDP22N50 stored 22 mohm with
// 2.5 nC (55 mohm*nC) and sailed through, as did STL240N6F7 (a 60 V family
// recorded at 650 V, where 6 mohm / 240 A is coherent at 60 V and impossible at
// 650) and IXTX3N250L (a 2500 V part recorded at 8.3 mohm where IXYS's own
// datasheet says under 10 OHMS -- a 1000x slip).
//
// Ron*Qg is die-area independent (Ron ~ 1/W, Qg ~ W), but it is NOT
// voltage-independent: both the specific on-resistance and the gate charge per
// unit width climb steeply with the blocking voltage, so a 600 V superjunction
// die cannot reach a 30 V die's FOM however large it is. That makes a per-class
// floor a strictly stronger, still die-area-free bound.
//
// CALIBRATED 2026-09-04 over all 8,563 live mosfet records carrying
// Vds+Ron+Qg (mohm*nC = 1e-12 ohm*C):
//   Si  200-399 V  n= 502  min    140  p1    400  p5    638  med   3,240
//   Si  400-700 V  n=1709  min     55  p1  2,400  p5  2,923  med   6,450
//   Si  701-1200V  n= 466  min  1,920  p1  4,500  p5  9,108  med  42,000
//   Si  >1200 V    n=  56  min  1,909  next 215,000 (a 100x gap)
//   SiC 400-700 V  n= 145  min  1,008        SiC 701-1200 n=230 min 880
//   SiC >1200 V    n=  40  min  1,785
//   GaN 200-399 V  n=  49  min    120        GaN 400-700  n=124 min 198
// GaN gets its own (much lower) floor for the same reason it does above -- a
// better FOM is the whole point of the technology -- and SiC its own because the
// 4H-SiC channel reaches ~1 nOhm*C at 650 V where silicon needs ~2.4.
//
// Each floor sits BELOW the lowest datasheet-defensible part of its class, so
// only an incoherent record can reach it. Corpus hit counts at these values
// (2026-09-04): Si 400-700 -> 6 (FDP22N50, STL40NM60N, STL240N6F7 and the three
// Navitas GaNFast parts whose technology field wrongly says "Si"); Si 701-1200
// -> 1 (NV6015C-RA, same mislabel); Si >1200 -> 1 (IXTX3N250L); Si 200-399, SiC
// and GaN -> 0 (forward guards). SUSPICIOUS, never Impossible: the bound is a
// technology argument, not a conservation law, and three of the eight hits are
// records whose ELECTRICALS are right and whose technology LABEL is wrong.
inline constexpr double MOS_FOM_VCLASS_MIN_VDS = 200.0;   // below this the global floor governs
inline constexpr double MOS_FOM_SI_200_SUS = 7.0e-11;     //     70 mohm*nC
inline constexpr double MOS_FOM_SI_400_SUS = 1.2e-9;      //  1,200 mohm*nC
inline constexpr double MOS_FOM_SI_700_SUS = 2.0e-9;      //  2,000 mohm*nC
inline constexpr double MOS_FOM_SI_1200_SUS = 2.0e-8;     // 20,000 mohm*nC
inline constexpr double MOS_FOM_SIC_SUS = 4.0e-10;        //    400 mohm*nC (all classes >=200 V)
inline constexpr double MOS_FOM_GAN_SUS = 1.0e-10;        //    100 mohm*nC (all classes >=200 V)

// ---- Diodes -----------------------------------------------------------------
// Snapback ESD parts guard-band the 1 mA breakdown LIMIT below the working
// voltage (Toshiba DF2B26M4SL: VRWM 24 V, VBR min 21.0 V = 0.875x, with leakage
// separately guaranteed at VRWM). Below 0.75x no spec convention explains it —
// that is a wrong-column value. Calibrated during ABT #500/#507 (2026-08-02).
inline constexpr double DIO_TVS_VBR_VSO_GUARD = 0.75;
// Forward-voltage windows by technology [V].
inline constexpr double DIO_VF_HARD_LO = 0.05, DIO_VF_HARD_HI = 5.0;  // IMP outside
inline constexpr double DIO_VF_SCHOTTKY_LO = 0.2, DIO_VF_SCHOTTKY_HI = 1.3;  // real Schottky Vf 0.26-1.2
inline constexpr double DIO_VF_SI_LO = 0.4, DIO_VF_SI_HI = 2.0;  // fast/ultrafast Si reaches ~1.7-2.5
inline constexpr double DIO_VF_SIC_LO = 0.5, DIO_VF_SIC_HI = 3.5;
// Reverse-recovery charge that should be ~0 for majority-carrier devices [C].
inline constexpr double DIO_QRR_MAJORITY_SUS = 1e-9;  // 1 nC
// Vf*If conduction vs powerDissipation rating ratio.
inline constexpr double DIO_VFIF_RATIO_SUS = 2.0;
// Reverse leakage as a fraction of the forward-current rating. Leakage sits orders
// of magnitude below the rating, so the ratio is a unit-free way to catch a uA/mA
// figure left in the amps field.
//
// CALIBRATED over the whole live catalogue, 2026-08-02 (ABT #524), after the 183
// leakage repairs in this same change: 3,794 diodes carry both fields positive.
// Above 0.02 sits 251 of them (6.62%) — and every single one is Nexperia, whose
// parametric import lost a 1e3 on forwardCurrent, not on leakage (395 of the 491
// PMEG parts whose type name pins both ratings store If 1000x low; ABT #550).
// Excluding that one cohort the fire rate is 0 of 3,665 at every threshold tried
// (0.02 / 0.05 / 0.5 / 1.0). The highest genuine datasheet convention is a hot-Tj
// figure near 1e-2 (Bourns CD2010-B160: 0.5 mA at TJ=25 C on a 1 A part), so 0.02
// clears every real record while still catching a 1e3 prefix slip.
//
// SUSPICIOUS ONLY, deliberately. An Impossible tier is for forward guards that
// invalidate ~nothing existing, and there is no threshold that qualifies today:
// even ratio > 1.0 — leaking more blocking than the part conducts, which is not a
// diode — fires on 20 live records. An earlier draft of this rule shipped 0.05 as
// Impossible on the strength of a "tops out at 0.0167" reading that turned out to
// be wrong about 3.74% of the corpus, which is its own argument for the softer
// grade. Promote only once ABT #550 lands and a real failure is traced here.
inline constexpr double DIO_LEAK_IF_SUS = 0.02;

// 4H-SiC Schottky forward voltage vs the metal-semiconductor BARRIER (adversarial
// physics review, 2026-09-04). The Ni/Ti-on-4H-SiC barrier height is 1.0-1.35 eV,
// so a SiC Schottky cannot conduct its rated current at a drop below the barrier
// itself; every traced SiC Schottky in this corpus lands at 1.2-2.0 V.
//
// THE TRAP, stated so nobody removes the subType gate later: a SILICON Schottky
// legitimately drops 0.3-0.5 V (its barrier is ~0.7 eV, and low-barrier parts go
// lower still). This bound keys on the SiC technology, never on "is a Schottky".
//
// CALIBRATED 2026-09-04 over all 1,099 live SiC diodes carrying forwardVoltage:
//   min 0.60  p1 0.65  p5 0.70  p25 0.84  med 1.30  p95 1.80  max 3.00
//   below 0.70: 42   below 0.80: 198   below 0.90: 325   below 1.00: 397
// 0.80 V is chosen as a STRICT lower bound so the 127-row cluster sitting exactly
// at 0.80 (Wolfspeed C4D17065, ROHM SCS2635KG, ST STPSC9006 ...) is untouched --
// 0.80 is a real published minimum-Vf figure for a low-current SiC part, and the
// bound only condemns values that are BELOW the barrier height. Corpus hit count
// at 0.80: 198 rows (ROHM 88, ST 78, Wolfspeed 16, onsemi 14, Infineon 2).
// SUSPICIOUS: a Vf quoted at microamps really can sit near the barrier, so this
// is a strong smell, not a conservation law. The pre-existing DIO_VF_RANGE SiC
// band (0.5-3.5 V) does not see any of them.
//
// STATE OF THE LIVE CORPUS, stated plainly rather than as a clean-sweep boast:
// a parallel session repaired the entire sub-barrier block a few hours after the
// calibration above, so as of the last measurement on 2026-09-04 this rule fires
// on ZERO live records -- the lowest SiC forwardVoltage in the catalogue is now
// 1.1 V (Wolfspeed SCS220KEC, onsemi FFSH16) and nothing sits below 1.2 V except
// two rows. That makes it a REGRESSION GUARD against the same import reappearing,
// not a detector proven against today's data. The 198-row measurement is what it
// was calibrated on and is preserved above for exactly that reason.
inline constexpr double DIO_VF_SIC_BARRIER_SUS = 0.8;
// Surge-to-average current ratio Ifsm/If. Real rectifiers run 10-40x: the
// single-cycle surge is set by the die's thermal mass over one half-cycle while
// the average rating is set by steady-state cooling, and that ratio is bounded by
// package physics no matter the die size. BAS70VY read 71,000 because its
// forwardCurrent was stored 1000x low (ABT #550's Nexperia block).
//
// CALIBRATED TWICE on 2026-09-04, because the corpus moved underneath the first
// measurement -- another session's ABT #550 repair landed mid-calibration and
// fixed the 489-row Nexperia block. Both readings are recorded because the second
// alone would understate what the rule is for:
//   BEFORE the repair (5,053 rows with both fields):
//     p5 5.0  p25 10  med 22.4  p75 30  p90 120  p95 13,000  max 71,429
//     ratio > 100 -> 513 rows: 489 Nexperia (If 1000x low) + 24 others.
//   AFTER  the repair (4,914 rows):
//     p5 4.5  p25 10  med 20.0  p75 29.6  p90 36  p95 50  p99 80  max 240
//     ratio > 100 ->  24 rows, every one of them a record whose forwardCurrent is
//     the constant 2.0 A of a wrong grid column (Vishay VS-80CPU02-N3 is an 80 A
//     part; onsemi MBRA210LT3G a 2 A part with a real 50 A IFSM, stored as 230).
//
// The margin is thinner than it looks and must be stated honestly: the highest
// DATASHEET-VERIFIED real ratio is 71.4 (Nexperia BAS70VY, 70 mA average / 5 A
// non-repetitive -- the very part the review cited, now repaired), and the highest
// observed non-firing row is 90.9 (BAS16DY). 100 is 2.5x the top of the physical
// 10-40x band but only ~1.1x above that nearest real neighbour, which is exactly
// why this is SUSPICIOUS and not Impossible. The comparison is strictly-greater,
// so the 16-row cluster sitting at exactly 100.0 does not fire either.
//
// TVS/Zener/ESD are excluded -- their surgeCurrent is a peak pulse current
// against a standoff rating, a different pair entirely.
inline constexpr double DIO_SURGE_IF_RATIO_SUS = 100.0;
// powerDissipation against the record's OWN thermal path, for diodes -- the
// analogue of MOS_PTHERMAL_RATIO_SUS. See thermal_power_ceiling() in helpers.hpp
// for the reference-mixing trap this is built around.
//
// CALIBRATED 2026-09-04 over the 2,510 live mosfet+diode records carrying
// powerDissipation, junctionTemperatureMax AND thermalResistanceJunctionCase:
//   ratio p50 0.83  p75 1.09  p90 4.35  p95 10.45  p99 30  max 124
// The highest datasheet-defensible ratio found is 2.0 (Infineon IMW120R050M1H,
// 357 W against 178 W implied) and the next 1.78 (IPP65R070CFD7); everything past
// 3 is a wrong Rth or a wrong Pd (Wolfspeed C3D02060F stores 12 K/W for a 4.4 K/W
// part; onsemi FFSH20 stores 2.0 for 0.55). 4.0 is 2x above the highest defensible
// value.
//
// Diode-only corpus hit count at 4.0, with TVS/Zener/ESD excluded: 2 rows
// (Wolfspeed CSD06060A stores 360 W behind a 75 W path; CSHD060065D 390 W) --
// and those two were themselves repaired by a parallel session later the same
// day, so the last live measurement is 0. Regression guard, not a live detector;
// the mosfet half of the same invariant (MOS_POWER_THERMAL) still fires on 285.
// The exclusion is load-bearing and was found by MEASURING, not by reasoning: an
// un-gated version of this rule fired on 10 rows, and 8 of them -- Vishay
// P6KE200A, Littelfuse SMBJ400A and six SMAJ48 variants -- are real parts whose
// powerDissipation field is the PEAK PULSE power (600 W over 10/1000 us against a
// 5 W steady-state rating). That is a factor of ~100 by design, not a defect.
inline constexpr double DIO_PTHERMAL_RATIO_SUS = 4.0;

// ---- IGBTs ------------------------------------------------------------------
// Collector-emitter saturation voltage [V].
inline constexpr double IGBT_VCESAT_HARD_LO = 0.3, IGBT_VCESAT_HARD_HI = 8.0;  // IMP outside
inline constexpr double IGBT_VCESAT_SUS_LO = 0.8, IGBT_VCESAT_SUS_HI = 4.5;
// Vce(sat) SUS ceiling is Vces-dependent: 1600-1700 V parts legitimately reach ~7 V.
inline constexpr double IGBT_VCESAT_SUS_HI_HV = 7.5, IGBT_VCESAT_HV_VCES = 1200.0;
// Collector current / collector-emitter voltage magnitude sanity. Largest real
// module Ic ~3.6 kA, catalog Vces max ~4500 V; 20 kA / 20 kV are safe IMP ceilings.
inline constexpr double IGBT_IC_SUS = 5.0e3, IGBT_IC_IMP = 2.0e4;
inline constexpr double IGBT_VCES_SUS = 1.0e4, IGBT_VCES_IMP = 2.0e4;
// Vce(sat)/Vces ratio (cross-parameter): real parts p1..p99 = 0.0009..0.0042;
// flag a wide band so only an incoherent (independently-fabricated) pair fires.
inline constexpr double IGBT_VCESAT_RATIO_LO = 3.0e-4, IGBT_VCESAT_RATIO_HI = 2.0e-2;

// ---- BJTs -------------------------------------------------------------------
// Reason about MAGNITUDES (PNP parts carry negative VCEO/IC/VCEsat).
// VCE(sat) [V]: low-VCEsat parts ~50 mV, power BJTs up to ~1-2 V.
inline constexpr double BJT_VCESAT_IMP_LO = 0.01, BJT_VCESAT_IMP_HI = 5.0;  // IMP outside
inline constexpr double BJT_VCESAT_SUS_HI = 2.0;                            // SUS above
// DC current gain hFE: typ 10-500; Darlingtons reach ~30000.
inline constexpr double BJT_HFE_SUS_LO = 5.0, BJT_HFE_SUS_HI = 5.0e4;
// Transition frequency fT [Hz]: audio BJTs ~1 MHz, RF/SiGe up to ~hundreds of GHz.
inline constexpr double BJT_FT_SUS_LO = 1.0e5, BJT_FT_SUS_HI = 1.0e12;

// ---- Varistors (MOV) --------------------------------------------------------
// Ordering: MCOV < varistorVoltage(V_1mA) < clampingVoltage. (MCOV stays below
// the 1 mA conduction knee; the clamp at rated surge current is above it.)
// Clamping ratio V_C / V_1mA (≈VCR): typically 1.5-4.
inline constexpr double VAR_CLAMP_RATIO_SUS_LO = 1.2, VAR_CLAMP_RATIO_SUS_HI = 5.0;
// Non-linearity exponent alpha: MOV typ 15-50; must be >1 to be a varistor.
inline constexpr double VAR_ALPHA_IMP_LO = 1.0;
inline constexpr double VAR_ALPHA_SUS_LO = 10.0, VAR_ALPHA_SUS_HI = 100.0;
// Peak surge current [A]: large station arresters reach ~100 kA.
inline constexpr double VAR_SURGE_SUS = 1.5e5, VAR_SURGE_IMP = 1.0e6;
// Surge-energy rating [J]: largest catalog part ~1080 J; station modules reach low kJ.
inline constexpr double VAR_ENERGY_SUS = 1.0e4, VAR_ENERGY_IMP = 1.0e5;

// ---- Connectors -------------------------------------------------------------
// Rated current per contact [A]: signal mA up to busbar power contacts ~hundreds A.
inline constexpr double CONN_CURRENT_SUS = 250.0, CONN_CURRENT_IMP = 2000.0;
// Rated voltage [V]: HV connectors reach tens of kV.
inline constexpr double CONN_VOLTAGE_SUS = 5.0e4, CONN_VOLTAGE_IMP = 1.0e5;
// Mated-pair contact resistance [Ohm]: power ~0.15 mOhm, signal up to ~0.1 Ohm.
inline constexpr double CONN_RCONTACT_SUS_LO = 1.0e-5, CONN_RCONTACT_SUS_HI = 1.0;
inline constexpr double CONN_RCONTACT_IMP_HI = 100.0;  // not a conducting contact above this
// Insulation resistance [Ohm]: should be >= MOhm (typ GOhm). Below 1 Ohm it is a
// short, not an insulator (catches Ohm-vs-MOhm unit slips).
inline constexpr double CONN_INSULATION_SUS_LO = 1.0e6;
inline constexpr double CONN_INSULATION_IMP_LO = 1.0;
// Air dielectric strength [V/m] = 3 kV/mm. Retained for reference only: the
// clearance checks now use the ideal uniform-field PASCHEN curve (below), which
// is the correct physics and is strictly MORE permissive below ~10 mm — the
// linear rule demands ~5 kV/mm of margin the physics does not require, so it
// would call possible parts IMPOSSIBLE.
inline constexpr double CONN_AIR_DIELECTRIC_VPM = 3.0e6;

// Paschen curve for air at 1 atm, uniform field:
//   V_b(d) = B*p*d / ( ln(A*p*d) - ln(ln(1 + 1/gamma)) )
// Constants converted to SI from the standard air values A = 15 /(cm*Torr),
// B = 365 V/(cm*Torr), secondary-emission coefficient gamma = 0.01.
// Sanity: d = 1 mm -> 5.03 kV; d = 10 mm -> 35.5 kV (3.55 kV/mm) — matches the
// textbook uniform-field values. Left of the Paschen minimum (d < ~10 um at
// 1 atm) the denominator goes non-positive and the check must decline to fire.
inline constexpr double CONN_PASCHEN_A = 11.25;      // 1/(Pa*m)
inline constexpr double CONN_PASCHEN_B = 273.77;     // V/(Pa*m)
inline constexpr double CONN_PASCHEN_LNLN = 1.5292;  // ln(ln(1 + 1/0.01))
inline constexpr double CONN_ATM_PA = 101325.0;      // Pa

// --- Holm contact-voltage relation -------------------------------------------
// The voltage across a closed metallic contact fixes the supertemperature of the
// constriction independently of its geometry (Kohlrausch/Holm voltage-temperature
// relation, Holm, "Electric Contacts", 4th ed.):
//     theta_max = sqrt(theta_bulk^2 + U^2 / (4*L)),  L = 2.44e-8 V^2/K^2
// so each metal has a SOFTENING and a MELTING voltage. If a datasheet's own
// ratedCurrentPerContact * contactResistance exceeds the melting voltage of its
// own stated plating, the two numbers cannot describe the same measurement.
// Measured on the live catalog (46,927 parts carry both): 464 fire (0.99%), and
// the largest ratio to melting voltage is 3.08x — so the plating-based tiers are
// SUSPICIOUS, never IMPOSSIBLE. A ratio of a few x is explained by the two specs
// being written to different conventions (a bulk terminal-to-terminal LLCR that
// includes the clamp and lead, vs the mated-pair constriction of IEC 60512-2-1),
// which is a data-provenance defect, not an impossible part.
// Highest melting voltage of ANY contact metal is tungsten at 1.10 V; five times
// that cannot be reconciled by any measurement convention.
inline constexpr double CONN_HOLM_LORENZ = 2.44e-8;        // V^2/K^2
inline constexpr double CONN_UMELT_MAX_ANY = 1.10;         // W, the ceiling over all metals
inline constexpr double CONN_UCONTACT_IMP_FACTOR = 5.0;    // x CONN_UMELT_MAX_ANY -> IMPOSSIBLE

// Current density [A/m^2] through the contact cross-section implied by the pitch.
// Both standard fine-pitch conventions put the square post at exactly pitch/4
// (2.54 mm -> 0.64 mm sq; 2.00 mm -> 0.50 mm sq), so A_contact ~ (pitch/4)^2.
// 100 A/mm^2 continuous is beyond any solid contact (busbar design runs 1-3
// A/mm^2). SUSPICIOUS only, never IMPOSSIBLE: on a HYBRID connector the rated
// current belongs to a wide power blade while the pitch belongs to the signal
// field, so a legitimate part can exceed this (verified: Hirose BM50U, 15 A
// power contacts in a 0.35 mm signal pitch). Fires on 1,829 / 246,344 = 0.74%.
inline constexpr double CONN_PIN_SIDE_PER_PITCH = 0.25;
inline constexpr double CONN_CURRENT_DENSITY_SUS = 1.0e8;  // A/m^2

// Where a per-contact rating stops being a contact of the stated pitch (ABT #486).
// The density check above cannot separate a wrong record from a legitimate hybrid,
// which is why it is SUSPICIOUS — but a POWER-terminal rating is separable, because
// a terminal carrying tens of amps is a crimp barrel or blade whose BODY does not
// fit on a fine grid. The catalogue states where that boundary is; per-contact
// current above 10 A, by pitch band, over all 392,346 records:
//
//     pitch [mm]   n        median   p99     fraction > 10 A
//     <= 0.8       10,675    0.5      11.5     3.26%
//     0.8 - 1.3    18,326    1.0       4.0     0.04%
//     1.3 - 2.0    60,275    3.9       4.5     0.17%
//     2.0 - 2.54  104,723    3.0       7.0     0.78%
//     2.55 - 3.5   12,746    8.0      17.5    21.07%   <- power terminals start
//     3.6 - 5.0    28,810   12.0      32.5    63.00%
//     5.1 - 7.6     6,575   20.0      57.0    93.11%
//
// The step at 2.54 mm is the vendors' own: >10 A is 0.04-3.26% of parts below it
// and 21-93% above. So a rating above 10 A on a pitch <= 2.54 mm does not describe
// a contact of that pitch. It is IMPOSSIBLE only when the record offers nothing to
// reinterpret it — see CONN_CURRENT_VS_PITCH in connectors.cpp for the
// contactSystem exemption that keeps documented hybrids valid.
inline constexpr double CONN_POWER_CONTACT_A = 10.0;         // A
inline constexpr double CONN_POWER_CONTACT_PITCH_M = 2.54e-3;  // m

// Physically-possible SI ranges, to catch unit slips (mm or um stored as m).
// A pitch of "2.54" is 2.54 METRES; the tightest real connector pitch is ~0.15 mm
// and the widest busbar spacing ~35 mm (verified in-catalog).
inline constexpr double CONN_PITCH_IMP_LO = 5.0e-5, CONN_PITCH_IMP_HI = 1.0e-1;
inline constexpr double CONN_PLATING_IMP_LO = 1.0e-9, CONN_PLATING_IMP_HI = 1.0e-3;
inline constexpr double CONN_LENGTH_IMP_LO = 1.0e-5, CONN_LENGTH_IMP_HI = 1.0;

// Operating temperature [degC]. Polymer-housed parts top out near 260 degC
// (PTFE/PEEK); 1000 degC is not a connector. Real catalog maximum is 260.
inline constexpr double CONN_TEMP_MAX_IMP = 1000.0, CONN_TEMP_MAX_SUS = 300.0;
inline constexpr double CONN_ABSOLUTE_ZERO_C = -273.15;
// Unconverted-Fahrenheit detector: a maximum above 200 degC whose Fahrenheit
// back-conversion lands on a round multiple of 5 degC is almost certainly an
// unconverted degF value. Verified in-catalog: 302 -> 150, 392 -> 200,
// 221 -> 105 (67 parts), while the genuine high-temperature values 205, 250 and
// 260 degC back-convert to 96.1, 121.1 and 126.7 and are untouched.
inline constexpr double CONN_FAHRENHEIT_PROBE_MIN_C = 200.0;
inline constexpr double CONN_FAHRENHEIT_GRID_C = 5.0, CONN_FAHRENHEIT_TOL_C = 0.2;

// Mating cycles. Spring-probe/pogo interfaces legitimately claim 1e6; nothing
// claims 1e7. Tin is soft and fretting-prone (industry durability 25-250 cycles),
// and gold thinner than 0.1 um is a "flash" that wears through in the low hundreds.
inline constexpr double CONN_CYCLES_IMP = 1.0e7, CONN_CYCLES_SUS = 1.0e5;
inline constexpr double CONN_CYCLES_TIN_SUS = 1.0e3;
inline constexpr double CONN_GOLD_FLASH_M = 1.0e-7;

// RF family. Coaxial interfaces are 50 or 75 Ohm; twinax differential pairs 100.
inline constexpr double CONN_Z0_IMP_LO = 1.0, CONN_Z0_IMP_HI = 1.0e3;
inline constexpr double CONN_Z0_SUS_LO = 10.0, CONN_Z0_SUS_HI = 200.0;
inline constexpr double CONN_FREQ_IMP_HI = 1.0e12;  // 1 THz
// A single part quoting mated heights spread wider than this has merged
// conflicting series-level facts into one record.
inline constexpr double CONN_MATED_HEIGHT_SPREAD_SUS = 10.0;

// ---- Analog ICs (AAS) -------------------------------------------------------
// Shared amplifier-family bounds. Sources: TI/ADI op-amp portfolios (GBW 50 MHz–8 GHz,
// OPA855; slew to ~3500 V/µs, EL5102), CMRR/PSRR typ 60–140 dB, Vos chopper µV to ~10 mV.
// |input/output offset|, V: precision µV–mV, but open-loop/high-speed buffers (e.g. TI BUF802)
// spec ~0.8 V; only a rail-scale offset is physically impossible.
inline constexpr double ANA_VOS_IMP = 2.0, ANA_VOS_SUS = 0.1;
inline constexpr double ANA_SLEW_IMP = 1.0e12, ANA_SLEW_SUS = 5.0e10;  // slew rate, V/s
inline constexpr double ANA_DB_IMP = 200.0, ANA_DB_SUS_HI = 180.0, ANA_DB_SUS_LO = 20.0;  // CMRR/PSRR/gain dB
inline constexpr double ANA_VNOISE_SUS_LO = 1.0e-10, ANA_VNOISE_SUS_HI = 1.0e-5;  // V/sqrt(Hz)
inline constexpr double ANA_SUPPLY_IMP = 1000.0, ANA_SUPPLY_SUS = 100.0;  // total supply V
inline constexpr double ANA_CHANNELS_IMP = 256.0, ANA_CHANNELS_SUS = 64.0;
inline constexpr double ANA_GBW_IMP = 1.0e11, ANA_GBW_SUS = 2.0e10;    // GBW, Hz (max ~8 GHz)
// slewRate/GBW ratio [V] (cross-parameter): real op-amps p1..p99 = 0.23..23;
// outside this wide band the two specs were likely invented independently.
inline constexpr double ANA_SR_GBW_LO = 0.05, ANA_SR_GBW_HI = 100.0;
inline constexpr double ANA_IBIAS_SUS = 1.0;                           // |input bias current|, A
// Quiescent supply current per channel [A]: p99 ~40 mA; >2 A is power-driver
// territory (SUS), >10 A is impossible for a single analog channel.
inline constexpr double ANA_IQ_SUS = 2.0, ANA_IQ_IMP = 10.0;
// Comparator propagation delay [s]: fastest ~0.5 ns, slow ~µs.
inline constexpr double CMP_TPD_IMP = 1.0e-3, CMP_TPD_SUS_HI = 1.0e-4, CMP_TPD_SUS_LO = 1.0e-10;
// ADC/DAC: resolution bits (to 32, ENOB ~20), sample/update rate [Sps] (RF to a few GSPS).
inline constexpr double CONV_RES_IMP = 40.0, CONV_RES_SUS = 32.0;
inline constexpr double CONV_RATE_IMP = 1.0e12, CONV_RATE_SUS = 1.0e11;
// Reference voltage [V]: real converter Vref ~0.5-10 V; >20 V is impossible.
inline constexpr double CONV_VREF_IMP = 20.0, CONV_VREF_SUS = 10.0;
// Analog switch / mux on-resistance [Ohm]: ~0.3 Ω to ~kΩ.
inline constexpr double SW_RON_IMP = 1.0e6, SW_RON_SUS = 1.0e4;
// Off-leakage: real analog-switch/mux off-leakage is pA (typ ~10 pA @25C) to a few uA
// hot; > 100 uA is not "off", > 10 mA is a short, not a switch. (TI MUX50x, ADI ADG604,
// NXP NX3L4051, Maxim MAX354, Vishay app note SG2134.)
inline constexpr double SW_LEAK_SUS = 1.0e-4;   // |I_leak(off)| > 100 uA is suspicious
inline constexpr double SW_LEAK_IMP = 1.0e-2;   // |I_leak(off)| > 10 mA is impossible

// ---- Controllers (CTAS) -----------------------------------------------------
// Control ICs span enormous parameter ranges, so magnitude bounds are wide and
// SUSPICIOUS-leaning — they catch unit-error / fabricated values, not exotic-but-
// real parts. Sources: TI UC384x/UCC256xx/UCC2152x/UCC24xxx, ADI ADuM4135/LTC,
// onsemi NCP12xx, Infineon EiceDRIVER, ST L6599, Power Integrations.
// Bounds below are datasheet-calibrated (TI/ADI/onsemi/Infineon/ST/Renesas/Skyworks
// survey) and cross-checked against the live controller catalog's populated fields.
// --- Per-category magnitude bounds (ABT: keyed on function.category) ---------
// A single global bound for "a controller" judges a gate driver and a PWM
// controller identically, and the CTAS controllerCategory vocabulary spans
// devices with genuinely different physics. The corpus proved it: three
// correct, datasheet-read extractions were flagged by the one-size bounds --
//   1EDN7136U   15 MHz  gateDriver   (datasheet: "Operating FSW - - 15 MHz")
//   UC1901-SP    5 MHz  pwmController (isolated-feedback carrier oscillator)
//   IRS25751L   625 V   gateDriver   (HV start-up IC sitting on the bulk rail)
// -- and they were the ONLY rows either bound flagged, i.e. the checks' entire
// live output was false positives. Both bounds are now per-category.
//
// The SUSPICIOUS column is the calibrated one: ~2x the live category maximum,
// or the physical class ceiling where the catalog is thin (only 157 rows carry
// switchingFrequencyMax and 131 carry supplyVoltageAbsoluteMax today, so the
// population alone cannot set a ceiling). A unit error is a factor of 1e3+, so
// a 2x headroom still catches the failure mode these bounds exist for.
//
// The IMPOSSIBLE column is deliberately category-INDEPENDENT and far above every
// class: neither an oscillator frequency nor a pin's absolute-max rating is a
// physics impossibility at any plausible IC value, so the impossible tier is a
// pure unit-error backstop (Hz reported as a raw count of something else; a
// volts field carrying millivolts-as-volts). Everything a real part can reach
// stays SUSPICIOUS -- a false IMPOSSIBLE withholds a real part from design.
//   freq   1 GHz: no silicon control loop or power gate switches at microwave.
//   VabsMax 2 kV: above every junction-isolated/SOI HVIC class (600/1200/1700 V).
struct CtlCategoryLimit {
    const char* category;   // CTAS controllerCategory token; "" = default row
    double freq_sus;        // switchingFrequencyMax SUSPICIOUS above this [Hz]
    double vabsmax_sus;     // supplyVoltageAbsoluteMax SUSPICIOUS above this [V]
};

// Category-independent unit-error backstops (see rationale above).
inline constexpr double CTL_FREQ_IMP = 1.0e9;
inline constexpr double CTL_VABSMAX_IMP = 2.0e3;

// Live maxima at calibration time are quoted per row; a blank means the field is
// unpopulated for that category and the bound comes from the device class.
inline constexpr CtlCategoryLimit CTL_CATEGORY_LIMITS[] = {
    // Power-train controllers: the switching node is the power stage, so f_sw is
    // bounded by the magnetics/FET, and VCC is a logic rail -- except for offline
    // parts whose HV start-up pin (NCP1063 700 V, NCP1399, UCC28880) is what the
    // extractor finds for "absolute maximum supply".
    {"pwmController", 1.0e7, 800.0},              // live max 5 MHz (UC1901-SP), 105 V (LM5039)
    {"dualPwmController", 1.0e7, 800.0},          // same class as pwmController
    {"llcController", 5.0e6, 800.0},              // live max 1.06 MHz (UCC25800-Q1), 25 V
    {"pfcController", 2.0e6, 800.0},              // live max 290 kHz (UCC28070A), 26 V
    {"phaseShiftController", 5.0e6, 250.0},       // no live f_sw/VCC rows
    {"syncRectifierController", 5.0e6, 250.0},    // no live f_sw/VCC rows
    {"multiphaseController", 5.0e6, 250.0},       // VR13/VR14 core rails, low voltage
    {"digitalController", 1.0e7, 250.0},          // DPWM clocks run above analog f_sw
    // Gate drivers do not set a power-stage frequency: their ceiling is the
    // propagation delay / minimum pulse width, which is why 15 MHz is a real
    // datasheet number (1EDN7136U). Level-shift HVICs and HV start-up ICs sit
    // ON the rectified mains bulk rail, so their absolute-max is kV-class.
    {"gateDriver", 3.0e7, 1300.0},                // live max 15 MHz, 625 V (IRS25751L)
    // Feedback / sense / reference parts: a carrier or bandwidth, not a f_sw.
    {"secondaryFeedbackController", 1.0e7, 250.0},
    {"optocouplerFeedback", 1.0e7, 250.0},
    {"currentSenseAmplifier", 1.0e7, 250.0},
    {"isolatedAmplifier", 1.0e7, 250.0},
    // DC parts: any f_sw at all is already unusual, so the bound is tight.
    {"voltageReference", 1.0e6, 250.0},
    {"shuntRegulator", 1.0e6, 250.0},
    {"linearRegulator", 5.0e6, 250.0},
    {"hotSwapController", 1.0e6, 250.0},
    {"eFuse", 1.0e6, 250.0},
    {"loadSwitch", 1.0e6, 250.0},
    {"supervisor", 5.0e6, 250.0},                 // live max 2.5 MHz (TLF35584: SBC w/ buck), 60 V
};

// Default row for an absent or unrecognised category: the WIDEST envelope in the
// table. An unknown category is missing information, not evidence of a defect --
// judging it by a narrow bound is exactly how a correct part gets flagged.
inline constexpr CtlCategoryLimit CTL_CATEGORY_DEFAULT = {"", 3.0e7, 1300.0};

inline constexpr const CtlCategoryLimit& ctl_category_limit(const char* category) {
    if (category != nullptr)
        for (const CtlCategoryLimit& row : CTL_CATEGORY_LIMITS) {
            const char* a = row.category;
            const char* b = category;
            while (*a != '\0' && *a == *b) {
                ++a;
                ++b;
            }
            if (*a == '\0' && *b == '\0') return row;
        }
    return CTL_CATEGORY_DEFAULT;
}
// Gate-drive peak source/sink current [A]: real max 30 A (IXYS-class); UCC5390 17 A.
inline constexpr double CTL_GATE_I_SUS = 30.0, CTL_GATE_I_IMP = 60.0;
// Gate-drive rail voltage [V]: 4.2-35 V rec, 40 V abs (ADuM4120).
inline constexpr double CTL_DRIVE_V_SUS = 45.0, CTL_DRIVE_V_IMP = 60.0;
// Driver propagation delay [s]: 16 ns (UCC27282) to ~4 µs (slow/opto parts in catalog).
inline constexpr double CTL_PROP_DELAY_SUS = 1.0e-5, CTL_PROP_DELAY_IMP = 1.0e-3;
// Internal reference (bandgap) [V]: 1.024 V (REF35) to ~10 V series refs; buried-Zener 7.2 V.
inline constexpr double CTL_VREF_SUS_LO = 0.4, CTL_VREF_SUS_HI = 12.0, CTL_VREF_IMP = 20.0;
// Current-mode CS comparator clamp, MAGNITUDE [V]: 0.2 V (UCC2806x / ICE3PCS01G, which
// sense on the return and so print -0.2 V) to 2.0 V (UCC28950). The stored value is
// SIGNED; these bounds apply to |value| only.
inline constexpr double CTL_CS_THRESH_SUS = 2.5, CTL_CS_THRESH_IMP = 5.0;
// Isolation withstand (VISO, RMS) [V]: 2500 (Si827x) to 7000 (AMC1301).
inline constexpr double CTL_ISO_VISO_SUS = 1.0e4, CTL_ISO_VISO_IMP = 2.5e4;
// Common-mode transient immunity [V/s]: 2e10 to 4e11 (Si827x); normalise kV/µs vs V/ns.
inline constexpr double CTL_CMTI_SUS = 5.0e11, CTL_CMTI_IMP = 2.0e12;
// Max junction temperature [degC]: 150 near-universal abs-max; thermal-SD ~160-165.
inline constexpr double CTL_TJMAX_SUS = 175.0, CTL_TJMAX_IMP = 250.0;
// maxPhaseCount [count]: real max 20 (Renesas RAA228228).
inline constexpr double CTL_PHASE_SUS = 20.0, CTL_PHASE_IMP = 32.0;

// ---- Time bases (TDAS: oscillator / timer / latch) --------------------------
// Fractional-frequency quantities (stability, aging, tolerance, pull range,
// timing accuracy) are DIMENSIONLESS fractions per the TDAS schema (1 ppm =
// 1e-6). Bounds from the July-2026 vendor-catalog research pass; every
// IMPOSSIBLE floor sits >10x beyond published best-in-class so a future
// record-breaking part lands SUSPICIOUS, never IMPOSSIBLE.
//
// Oscillator output frequency windows by technology [Hz]:
// Standard AT-cut quartz fundamental 8-50 MHz (TXC/NDK/Kyocera catalogs);
// inverted-mesa HFF fundamentals reach ~250 MHz (NDK NX2016SF); 3rd/5th/7th
// overtones extend to ~500 MHz lab-grade -> 800 MHz IMP ceiling for any bare
// quartz resonator, 350 MHz IMP for a declared fundamental, 66 MHz SUS for a
// fundamental above the standard-AT range. A declared overtone below 25 MHz
// makes no sense (fundamental territory). Tuning-fork quartz bottoms at the
// kHz watch class -> < 1 kHz IMP.
inline constexpr double TB_F_QUARTZ_IMP = 800.0e6;
inline constexpr double TB_F_QUARTZ_FUND_IMP = 350.0e6;
inline constexpr double TB_F_QUARTZ_FUND_SUS = 66.0e6;
inline constexpr double TB_F_QUARTZ_OT_MIN_SUS = 25.0e6;
inline constexpr double TB_F_QUARTZ_MIN_IMP = 1.0e3;
// Ceramic resonators (Murata CERALOCK, TDK FCR): catalog span ~400 kHz-50 MHz
// -> outside 100 kHz-100 MHz SUS, > 200 MHz IMP.
inline constexpr double TB_F_CERAMIC_SUS_LO = 100.0e3, TB_F_CERAMIC_SUS_HI = 100.0e6;
inline constexpr double TB_F_CERAMIC_IMP = 200.0e6;
// Packaged XO / VCXO / programmable (incl. PLL multiplication): differential
// parts reach 1.5 GHz (SiTime SiT9501; Skyworks Si545 to 1.5 GHz) -> 1.5 GHz
// SUS, 2 GHz IMP.
inline constexpr double TB_F_XO_IMP = 2.0e9, TB_F_XO_SUS = 1.5e9;
// MEMS: SiTime catalog ceiling 725 MHz (SiT9501 family) -> SUS above; 1 GHz IMP.
// A MEMS resonator below 1 Hz does not exist -> IMP.
inline constexpr double TB_F_MEMS_IMP = 1.0e9, TB_F_MEMS_SUS = 725.0e6;
inline constexpr double TB_F_MEMS_MIN_IMP = 1.0;
// Silicon RC: fastest catalog part 170 MHz (ADI/Linear LTC6905) -> SUS above;
// 500 MHz IMP.
inline constexpr double TB_F_SIRC_IMP = 500.0e6, TB_F_SIRC_SUS = 170.0e6;
// OCXO: catalog parts top out ~200 MHz (Rakon/Wenzel high-frequency OCXOs)
// -> 220 MHz SUS, 1 GHz IMP.
inline constexpr double TB_F_OCXO_IMP = 1.0e9, TB_F_OCXO_SUS = 220.0e6;
//
// Frequency stability over temperature (+/- fraction) by technology:
// Plain quartz crystal / XO: tightest catalog cuts +/-5 ppm (Epson TSX-3225
// grades); sub-0.5 ppm without compensation is TCXO physics -> IMP. Loose
// consumer parts +/-100 ppm -> > 200 ppm SUS.
inline constexpr double TB_STAB_XTAL_IMP = 0.5e-6;
inline constexpr double TB_STAB_XTAL_SUS_LO = 5.0e-6, TB_STAB_XTAL_SUS_HI = 200.0e-6;
// TCXO: best published hybrids +/-20 ppb (Rakon HPXO/mercury-class) -> 50 ppb
// SUS floor, 5 ppb IMP; > 10 ppm is not a TCXO.
inline constexpr double TB_STAB_TCXO_IMP = 0.005e-6;
inline constexpr double TB_STAB_TCXO_SUS_LO = 0.05e-6, TB_STAB_TCXO_SUS_HI = 10.0e-6;
// OCXO: best double-oven parts +/-0.2 ppb (Oscilloquartz/Morion DOCXO) ->
// 1 ppb SUS floor, 0.2 ppb IMP; > 0.5 ppm is not oven-stabilised.
inline constexpr double TB_STAB_OCXO_IMP = 0.0002e-6;
inline constexpr double TB_STAB_OCXO_SUS_LO = 0.001e-6, TB_STAB_OCXO_SUS_HI = 0.5e-6;
// MEMS: best TCXO-class MEMS +/-0.5 ppm (SiTime Elite SiT5711) -> SUS floor
// there, 50 ppb IMP; plain MEMS XO tops at +/-50 ppm -> > 100 ppm SUS.
inline constexpr double TB_STAB_MEMS_IMP = 0.05e-6;
inline constexpr double TB_STAB_MEMS_SUS_LO = 0.5e-6, TB_STAB_MEMS_SUS_HI = 100.0e-6;
// Silicon RC oscillators are %-class: best trimmed parts ~0.5% over temp
// (TI LMK6C class reaches ~50 ppm but is MEMS-assisted; pure RC SiT/LTC ~0.5-2%)
// -> < 0.5% SUS, < 500 ppm IMP, > 5% SUS.
inline constexpr double TB_STAB_SIRC_IMP = 500.0e-6;
inline constexpr double TB_STAB_SIRC_SUS_LO = 5000.0e-6, TB_STAB_SIRC_SUS_HI = 0.05;
// Ceramic resonators are %-class (Murata CERALOCK +/-0.2-0.5%): < 100 ppm IMP,
// > 5% SUS.
inline constexpr double TB_STAB_CERAMIC_IMP = 100.0e-6;
inline constexpr double TB_STAB_CERAMIC_SUS_HI = 0.05;
//
// Aging per year (fraction/yr) by technology:
// Quartz crystal / XO / VCXO: typical first-year 1-5 ppm (Abracon/ECS);
// < 0.1 ppm/yr needs an oven, > 30 ppm/yr is broken-seal territory -> IMP both.
inline constexpr double TB_AGE_XTAL_IMP_LO = 0.1e-6, TB_AGE_XTAL_IMP_HI = 30.0e-6;
inline constexpr double TB_AGE_XTAL_SUS_LO = 1.0e-6, TB_AGE_XTAL_SUS_HI = 10.0e-6;
// TCXO: premium parts 0.5-1 ppm/yr (Rakon/NDK) -> < 0.2 SUS, < 0.05 IMP.
inline constexpr double TB_AGE_TCXO_IMP_LO = 0.05e-6;
inline constexpr double TB_AGE_TCXO_SUS_LO = 0.2e-6, TB_AGE_TCXO_SUS_HI = 5.0e-6;
// OCXO: best published ~0.01-0.05 ppm/yr (Oscilloquartz 8607-class) ->
// < 0.01 SUS, < 0.0005 IMP; > 1 ppm/yr is not an OCXO.
inline constexpr double TB_AGE_OCXO_IMP_LO = 0.0005e-6;
inline constexpr double TB_AGE_OCXO_SUS_LO = 0.01e-6, TB_AGE_OCXO_SUS_HI = 1.0e-6;
// MEMS: SiTime specs +/-0.5-1 ppm first year -> < 0.05 SUS, < 0.01 IMP.
inline constexpr double TB_AGE_MEMS_IMP_LO = 0.01e-6;
inline constexpr double TB_AGE_MEMS_SUS_LO = 0.05e-6;
//
// RMS phase jitter [s] (12 kHz-20 MHz-class integration band): best published
// 40-70 fs (SiTime SiT9501 70 fs; Microchip VC-714 40 fs) -> 25 fs SUS floor,
// 5 fs IMP (thermal floor). Silicon RC is orders worse: best ~1-10 ps ->
// < 5 ps SUS, < 500 fs IMP. A single-ended CMOS output below 100 fs is
// marketing, not measurement -> SUS.
inline constexpr double TB_JIT_IMP = 5.0e-15, TB_JIT_SUS = 25.0e-15;
inline constexpr double TB_JIT_SIRC_IMP = 500.0e-15, TB_JIT_SIRC_SUS = 5.0e-12;
inline constexpr double TB_JIT_CMOS_SUS = 100.0e-15;
//
// Startup time [s] by class:
// Quartz MHz XO/VCXO/TCXO: typical 2-10 ms, fastest ~1 ms -> < 200 us SUS,
// < 50 us IMP (a quartz resonator cannot ring up that fast), > 100 ms SUS.
inline constexpr double TB_START_XO_IMP = 50.0e-6;
inline constexpr double TB_START_XO_SUS_LO = 200.0e-6, TB_START_XO_SUS_HI = 0.1;
// 32.768 kHz tuning-fork class (f < 100 kHz): Q ~ 50k-90k needs 0.1-1 s
// (Micro Crystal/Epson app notes) -> < 0.1 s SUS, < 10 ms IMP, > 5 s SUS.
inline constexpr double TB_START_KHZ_F = 100.0e3;
inline constexpr double TB_START_KHZ_IMP = 0.01;
inline constexpr double TB_START_KHZ_SUS_LO = 0.1, TB_START_KHZ_SUS_HI = 5.0;
// OCXO warm-up: minutes (IQD/Rakon 1-5 min typical) -> < 30 s SUS, < 1 s IMP
// (the oven cannot thermally settle), > 30 min SUS.
inline constexpr double TB_START_OCXO_IMP = 1.0;
inline constexpr double TB_START_OCXO_SUS_LO = 30.0, TB_START_OCXO_SUS_HI = 1800.0;
// MEMS: SiTime specs 1-10 ms typical, fastest ~300 us -> < 100 us SUS,
// < 1 us IMP, > 50 ms SUS.
inline constexpr double TB_START_MEMS_IMP = 1.0e-6;
inline constexpr double TB_START_MEMS_SUS_LO = 100.0e-6, TB_START_MEMS_SUS_HI = 0.05;
// Silicon RC: us-class (LTC6905 ~100 us; SiT8021-class RC starts ~1 us) ->
// < 100 ns IMP, > 10 ms SUS.
inline constexpr double TB_START_SIRC_IMP = 100.0e-9, TB_START_SIRC_SUS_HI = 0.01;
//
// Supply / power cross-checks:
// OCXO oven power: steady-state 0.3-1.5 W (IQD OCXOP, Rakon ROX) -> a steady
// currentConsumption under 30 mA cannot keep an oven hot: IMP. warmupPower
// (peak, W): typical 1.5-5 W -> < 0.5 W or > 10 W SUS, < 100 mW IMP.
inline constexpr double TB_OCXO_I_IMP = 0.030;
inline constexpr double TB_OCXO_WARMUP_IMP = 0.1;
inline constexpr double TB_OCXO_WARMUP_SUS_LO = 0.5, TB_OCXO_WARMUP_SUS_HI = 10.0;
// Any packaged oscillator: lowest published draw ~1 uA (SiTime SiT1569
// 32 kHz TCXO-class, Micro Crystal RV modules) -> < 0.1 uA IMP.
inline constexpr double TB_OSC_I_MIN_IMP = 1.0e-7;
//
// Pull range (+/- fraction): quartz VCXO catalogs span +/-25..200 ppm
// (Abracon/Renesas); > 200 ppm SUS, > 1000 ppm IMP (varactor pull cannot bend
// quartz that far), < 10 ppm SUS (not usefully pullable). MEMS DCXOs reach
// +/-1600 ppm (SiTime SiT3907) -> SUS above, 3200 ppm IMP.
inline constexpr double TB_PULL_VCXO_IMP = 1000.0e-6;
inline constexpr double TB_PULL_VCXO_SUS_HI = 200.0e-6, TB_PULL_VCXO_SUS_LO = 10.0e-6;
inline constexpr double TB_PULL_MEMS_IMP = 3200.0e-6, TB_PULL_MEMS_SUS = 1600.0e-6;
//
// Output type vs frequency: single-ended CMOS tops out ~250 MHz (SiTime/
// Abracon catalog filters) -> SUS above, 500 MHz IMP. Differential formats
// (LVDS/LVPECL/HCSL) below 1 MHz make no catalog sense -> SUS.
inline constexpr double TB_CMOS_F_IMP = 500.0e6, TB_CMOS_F_SUS = 250.0e6;
inline constexpr double TB_DIFF_F_MIN_SUS = 1.0e6;
//
// 32.768 kHz watch-crystal class: tolerance is +/-10/+/-20 ppm (Epson FC-135,
// Micro Crystal CC7V) -> < 5 ppm at exactly 32768 Hz SUS.
inline constexpr double TB_WATCH_F = 32768.0;
inline constexpr double TB_WATCH_TOL_SUS = 5.0e-6;
// Initial frequency tolerance upper sanity, any class: > 10% is not a
// frequency-control product.
inline constexpr double TB_TOL_SUS = 0.1;
//
// Timers (555 class): bipolar NE555 (TI/onsemi) 4.5-16 V, <= 500 kHz astable,
// 1-3% initial accuracy; CMOS TLC555/LMC555 (TI) 1.5-15 V (2.1-3 MHz),
// 0.5-2% accuracy. IMP bounds sit beyond any published successor.
inline constexpr double TB_TMR_BIP_F_IMP = 5.0e6, TB_TMR_BIP_F_SUS = 500.0e3;
inline constexpr double TB_TMR_BIP_V_IMP_LO = 3.0, TB_TMR_BIP_V_IMP_HI = 20.0;
inline constexpr double TB_TMR_BIP_V_SUS_LO = 4.5, TB_TMR_BIP_V_SUS_HI = 16.0;
inline constexpr double TB_TMR_CMOS_F_IMP = 10.0e6, TB_TMR_CMOS_F_SUS = 3.0e6;
inline constexpr double TB_TMR_CMOS_V_IMP_LO = 1.0, TB_TMR_CMOS_V_IMP_HI = 20.0;
inline constexpr double TB_TMR_CMOS_V_SUS_LO = 1.5, TB_TMR_CMOS_V_SUS_HI = 15.0;
inline constexpr double TB_TMR_ACC_IMP_LO = 0.001;  // < 0.1% initial: RC timers can't
inline constexpr double TB_TMR_BIP_ACC_SUS_LO = 0.01, TB_TMR_CMOS_ACC_SUS_LO = 0.005;
inline constexpr double TB_TMR_ACC_SUS_HI = 0.1;
inline constexpr double TB_TMR_CH_SUS = 4.0;  // 556 is 2; quad timers exist, > 4 SUS
//
// Latches (discrete SR logic, 74HC279 class): tPD ~13-25 ns HC, ~2-5 ns
// AUC/LVC; sub-100 ps discrete logic does not exist -> IMP; < 1 ns or > 1 us
// SUS. Logic supply windows 0.8-18 V (AUP to 4000B) -> outside 0.5-20 V SUS.
inline constexpr double TB_LATCH_TPD_IMP = 100.0e-12;
inline constexpr double TB_LATCH_TPD_SUS_LO = 1.0e-9, TB_LATCH_TPD_SUS_HI = 1.0e-6;
inline constexpr double TB_LATCH_V_SUS_LO = 0.5, TB_LATCH_V_SUS_HI = 20.0;
//
// Behavioral atoms (design intent, not physics claims — light bounds only):
// an ideal oscillator above 10 GHz or a monostable one-shot longer than an
// hour is almost certainly a unit slip.
inline constexpr double TB_BEH_OSC_F_SUS = 10.0e9;
inline constexpr double TB_BEH_TMR_ONTIME_SUS = 3600.0;

// ---- Thermistors (THERM_*) -------------------------------------------------
// Bounds from NTC/PTC datasheets + app notes (Vishay NTCLE/NTCLG, TDK/EPCOS B57,
// Murata NCP/NCU, Amphenol/Thermometrics, Littelfuse, Ametherm inrush limiters).
// R25 (Ohm): inrush limiters reach ~0.05 Ohm (Ametherm SL32 0R230=0.25); high-R
// sensing NTCs reach ~10 MOhm (1 MOhm common ceiling).
inline constexpr double THERM_R25_IMP_LO = 1e-3;    // < 1 mOhm: a short, not a thermistor
inline constexpr double THERM_R25_IMP_HI = 1e8;     // > 100 MOhm impossible
inline constexpr double THERM_R25_SUS_LO = 0.05;    // below inrush-limiter floor
inline constexpr double THERM_R25_SUS_HI = 1e7;     // above typical sensing max (10 MOhm)
// B constant / beta (K): real NTC-oxide parts ~2400..5000 K (TDK/EPCOS, Niccomp NCT=2410).
inline constexpr double THERM_B_IMP_LO = 1000.0;
inline constexpr double THERM_B_IMP_HI = 7000.0;
inline constexpr double THERM_B_SUS_LO = 2000.0;
inline constexpr double THERM_B_SUS_HI = 5500.0;
// Resistance tolerance (fraction): tightest ~0.001, loosest ~0.2..0.25.
inline constexpr double THERM_TOL_IMP_HI = 0.5;     // >= 50% is not a tolerance
inline constexpr double THERM_TOL_SUS_HI = 0.30;    // above the loosest real grade (25%)
// Dissipation constant (W/K, still air): micro-bead ~0.3 mW/K; large potted disc ~0.2 W/K.
// Oil-referenced figures run several x higher, so hard-gate only at 1 W/K (impossible).
inline constexpr double THERM_DISS_IMP_LO = 1e-5;
inline constexpr double THERM_DISS_IMP_HI = 10.0;   // generous vs the 1 W/K air ceiling (oil-safe)
inline constexpr double THERM_DISS_SUS_LO = 2e-4;
inline constexpr double THERM_DISS_SUS_HI = 0.25;   // above the ~0.2 W/K potted max
// Thermal time constant (s, still air): micro-bead ~0.1 s; potted probe ~232..400 s.
inline constexpr double THERM_TAU_IMP_HI = 1000.0;  // > 1000 s is not a thermistor
inline constexpr double THERM_TAU_SUS_HI = 400.0;
// Implied heat capacity C_th = tau * dissipationConstant (J/K): ~0.5 mJ/K (bead) to ~2 J/K (disc).
inline constexpr double THERM_CTH_SUS_LO = 1e-6;
inline constexpr double THERM_CTH_SUS_HI = 10.0;
// Operating temperature (deg C).
inline constexpr double THERM_ABS_ZERO_C = -273.15;
inline constexpr double THERM_TEMP_MAX_IMP = 1200.0;  // rare-earth high-temp NTC reaches ~1100
inline constexpr double THERM_TEMP_MAX_SUS = 330.0;   // above glass-NTC max (~300)
// PTC switch (reference) temperature (deg C): sensors to ~165, heaters (same physics) to ~320.
inline constexpr double THERM_PTC_TSW_IMP_HI = 400.0;
inline constexpr double THERM_PTC_TSW_SUS_HI = 330.0;
// Max steady-state current for inrush-limiter NTCs (A): MM35-DIN reaches 80 A.
inline constexpr double THERM_ISS_IMP_HI = 100.0;
inline constexpr double THERM_ISS_SUS_HI = 50.0;

}  // namespace tas::thr
