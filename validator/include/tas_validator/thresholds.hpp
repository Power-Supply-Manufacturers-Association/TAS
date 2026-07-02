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

// ---- Diodes -----------------------------------------------------------------
// Forward-voltage windows by technology [V].
inline constexpr double DIO_VF_HARD_LO = 0.05, DIO_VF_HARD_HI = 5.0;  // IMP outside
inline constexpr double DIO_VF_SCHOTTKY_LO = 0.2, DIO_VF_SCHOTTKY_HI = 1.3;  // real Schottky Vf 0.26-1.2
inline constexpr double DIO_VF_SI_LO = 0.4, DIO_VF_SI_HI = 2.0;  // fast/ultrafast Si reaches ~1.7-2.5
inline constexpr double DIO_VF_SIC_LO = 0.5, DIO_VF_SIC_HI = 3.5;
// Reverse-recovery charge that should be ~0 for majority-carrier devices [C].
inline constexpr double DIO_QRR_MAJORITY_SUS = 1e-9;  // 1 nC
// Vf*If conduction vs powerDissipation rating ratio.
inline constexpr double DIO_VFIF_RATIO_SUS = 2.0;

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
// Air dielectric strength [V/m] = 3 kV/mm: minimum clearance for ratedVoltage is
// V / this; a smaller clearance would arc over (IMPOSSIBLE).
inline constexpr double CONN_AIR_DIELECTRIC_VPM = 3.0e6;

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

// ---- Controllers (CTAS) -----------------------------------------------------
// Control ICs span enormous parameter ranges, so magnitude bounds are wide and
// SUSPICIOUS-leaning — they catch unit-error / fabricated values, not exotic-but-
// real parts. Sources: TI UC384x/UCC256xx/UCC2152x/UCC24xxx, ADI ADuM4135/LTC,
// onsemi NCP12xx, Infineon EiceDRIVER, ST L6599, Power Integrations.
// Bounds below are datasheet-calibrated (TI/ADI/onsemi/Infineon/ST/Renesas/Skyworks
// survey) and cross-checked against the live controller catalog's populated fields.
// VCC absolute-max [V]: logic parts ≤38 V; hot-swap/eFuse bus parts to ~120 V.
inline constexpr double CTL_VABSMAX_SUS = 120.0, CTL_VABSMAX_IMP = 400.0;
// Switching frequency [Hz]: real max ~2 MHz (IR35201/UCD3138 DPWM).
inline constexpr double CTL_FREQ_SUS = 3.0e6, CTL_FREQ_IMP = 1.0e7;
// Gate-drive peak source/sink current [A]: real max 30 A (IXYS-class); UCC5390 17 A.
inline constexpr double CTL_GATE_I_SUS = 30.0, CTL_GATE_I_IMP = 60.0;
// Gate-drive rail voltage [V]: 4.2-35 V rec, 40 V abs (ADuM4120).
inline constexpr double CTL_DRIVE_V_SUS = 45.0, CTL_DRIVE_V_IMP = 60.0;
// Driver propagation delay [s]: 16 ns (UCC27282) to ~4 µs (slow/opto parts in catalog).
inline constexpr double CTL_PROP_DELAY_SUS = 1.0e-5, CTL_PROP_DELAY_IMP = 1.0e-3;
// Internal reference (bandgap) [V]: 1.024 V (REF35) to ~10 V series refs; buried-Zener 7.2 V.
inline constexpr double CTL_VREF_SUS_LO = 0.4, CTL_VREF_SUS_HI = 12.0, CTL_VREF_IMP = 20.0;
// Current-mode CS comparator clamp [V]: 0.25 V (NCP12700) to 2.0 V (UCC28950).
inline constexpr double CTL_CS_THRESH_SUS = 2.5, CTL_CS_THRESH_IMP = 5.0;
// Isolation withstand (VISO, RMS) [V]: 2500 (Si827x) to 7000 (AMC1301).
inline constexpr double CTL_ISO_VISO_SUS = 1.0e4, CTL_ISO_VISO_IMP = 2.5e4;
// Common-mode transient immunity [V/s]: 2e10 to 4e11 (Si827x); normalise kV/µs vs V/ns.
inline constexpr double CTL_CMTI_SUS = 5.0e11, CTL_CMTI_IMP = 2.0e12;
// Max junction temperature [degC]: 150 near-universal abs-max; thermal-SD ~160-165.
inline constexpr double CTL_TJMAX_SUS = 175.0, CTL_TJMAX_IMP = 250.0;
// maxPhaseCount [count]: real max 20 (Renesas RAA228228).
inline constexpr double CTL_PHASE_SUS = 20.0, CTL_PHASE_IMP = 32.0;

}  // namespace tas::thr
