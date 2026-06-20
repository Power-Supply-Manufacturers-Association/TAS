// SPDX-License-Identifier: Apache-2.0
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
// SRF * sqrt(L) — parasitic resonance (ported).
inline constexpr double MAG_SRF_L_IMP = 1e6;
inline constexpr double MAG_SRF_L_SUS = 5e5;  // only when L > 1 nH
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

// ---- Capacitors -------------------------------------------------------------
// Stored-energy density 1/2 C V^2 over volume [J/m^3]. Industry figures:
// alum-elec ~0.01-0.05, film ~0.02-0.2, conventional ceramic <0.2, tantalum up
// to ~9, advanced MLCC up to ~22 J/cm^3. Hard ceiling 50 J/cm^3.
inline constexpr double CAP_ENERGY_DENSITY_IMP = 50e6;       // 50 J/cm^3
inline constexpr double CAP_ENERGY_DENSITY_SUS_ALUM = 0.1e6;  // 0.1 J/cm^3
inline constexpr double CAP_ENERGY_DENSITY_SUS_TANT = 9e6;    // 9 J/cm^3
inline constexpr double CAP_ENERGY_DENSITY_SUS_FILM = 0.3e6;  // 0.3 J/cm^3
inline constexpr double CAP_ENERGY_DENSITY_SUS_CERAMIC = 0.5e6;  // 0.5 J/cm^3 (non-advanced)
// Inductance magnitude [H] — dimension-free sanity catching uH/mH/H unit slips.
// The largest practical wound inductors are ~10 H; nothing in a parts catalog
// reaches 100 H (most power magnetics are uH-mH).
inline constexpr double MAG_L_MAGNITUDE_IMP = 100.0;     // > 100 H impossible
inline constexpr double MAG_L_MAGNITUDE_SUS = 1.0;       // > 1 H suspicious
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
inline constexpr double CAP_DF_TANTALUM = 0.10;
inline constexpr double CAP_DF_ELECTROLYTIC = 0.30;
inline constexpr double CAP_DF_POLYMER = 0.05;
inline constexpr double CAP_DF_DEFAULT = 0.5;  // generic upper sanity
// Leakage: I_leak / (C*V)  [1/s] — fraction of charge bled per second.
// Electrolytics ~1e-2..1e-1; film/ceramic far lower. Physically impossible above.
inline constexpr double CAP_LEAKAGE_PER_CV_IMP = 10.0;
inline constexpr double CAP_LEAKAGE_PER_CV_SUS = 1.0;
// Insulation time constant Riso*C [s]: electrolytics short (~50-1000s), film/
// ceramic long (>1e4 s). Suspicious outside a very wide band.
inline constexpr double CAP_RC_SECONDS_SUS_LOW = 1.0;
inline constexpr double CAP_RC_SECONDS_SUS_HIGH = 1e9;

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
// P = V^2/R consistency: implied power vs powerRating ratio.
inline constexpr double RES_PVR_RATIO_SUS = 5.0;
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
// Specific-Ron floor proxy: Ron*Vds^2 [ohm*V^2] minimum for a single die by
// technology. Silicon obeys Ron,sp ~ k*BV^2.5; with die area unknown this is an
// advisory (SUS) lower bound only. Calibrated so a 600 V Si part with Ron < a
// few mohm or a 1200 V SiC part with Ron < ~0.1 ohm gets flagged.
inline constexpr double MOS_RON_VDS2_SI_SUS = 50.0;    // Si / superjunction
inline constexpr double MOS_RON_VDS2_SIC_SUS = 5.0;    // SiC
inline constexpr double MOS_RON_VDS2_GAN_SUS = 1.0;    // GaN

// ---- Diodes -----------------------------------------------------------------
// Forward-voltage windows by technology [V].
inline constexpr double DIO_VF_HARD_LO = 0.05, DIO_VF_HARD_HI = 5.0;  // IMP outside
inline constexpr double DIO_VF_SCHOTTKY_HI = 0.9;
inline constexpr double DIO_VF_SI_LO = 0.4, DIO_VF_SI_HI = 1.3;
inline constexpr double DIO_VF_SIC_LO = 0.7, DIO_VF_SIC_HI = 2.0;
// Reverse-recovery charge that should be ~0 for majority-carrier devices [C].
inline constexpr double DIO_QRR_MAJORITY_SUS = 1e-9;  // 1 nC
// Vf*If conduction vs powerDissipation rating ratio.
inline constexpr double DIO_VFIF_RATIO_SUS = 2.0;

// ---- IGBTs ------------------------------------------------------------------
// Collector-emitter saturation voltage [V].
inline constexpr double IGBT_VCESAT_HARD_LO = 0.3, IGBT_VCESAT_HARD_HI = 8.0;  // IMP outside
inline constexpr double IGBT_VCESAT_SUS_LO = 0.8, IGBT_VCESAT_SUS_HI = 4.5;

}  // namespace tas::thr
