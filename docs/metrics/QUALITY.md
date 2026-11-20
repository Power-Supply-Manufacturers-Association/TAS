# Database metrics (2026-11-20)

**Quality: 71.3 %**  ·  **Coverage: 18.5 %**

Quality = weighted checklist per part, quarantined rows count 0; coverage = distinct live parts / estimated in-scope Datasheet Archive parts. Method: `scripts/db_metrics.py` docstring and `validator/metrics/`; field and curve lists: `scripts/metrics_spec.json`.

| Catalogue | Live | Quarantine | Quality | Existence | Traced | Fields | Curves | URL | Valid | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| analog_ics | 8,163 | 15 | 76.0 % | 93.3 % | 52.0 % | 72.0 % | — | 97.7 % | 99.8 % | 9.5 % |
| bjts | 6,419 | 166 | 63.9 % | 97.2 % | 38.7 % | 58.8 % | — | 72.4 % | 95.8 % | 11.4 % |
| capacitors | 723,347 | 8,063 | 72.5 % | 98.2 % | 81.9 % | 76.1 % | 9.9 % | 75.8 % | 99.9 % | 13.3 % |
| connector_accessories | 23,656 | 0 | 61.8 % | 63.0 % | 28.8 % | 69.2 % | — | 86.0 % | 99.9 % | 11.6 % |
| connectors | 418,154 | 36,253 | 67.6 % | 98.4 % | 71.9 % | 57.4 % | 0.0 % | 66.3 % | 94.6 % | 20.4 % |
| controllers | 12,260 | 355 | 72.6 % | 86.0 % | 77.5 % | 50.5 % | — | 94.2 % | 92.0 % | 6.2 % |
| diodes | 50,987 | 4,861 | 63.4 % | 85.1 % | 60.5 % | 64.3 % | 1.3 % | 77.8 % | 99.6 % | 6.3 % |
| igbts | 2,317 | 674 | 35.0 % | 71.8 % | 30.9 % | 38.4 % | 3.0 % | 66.1 % | 100.0 % | 59.1 % |
| magnetics | 120,105 | 1,641 | 63.6 % | 87.8 % | 53.6 % | 70.9 % | 19.1 % | 73.0 % | 97.1 % | 16.6 % |
| mosfets | 14,254 | 1,980 | 50.8 % | 96.7 % | 42.8 % | 55.1 % | 2.5 % | 89.3 % | 95.7 % | 8.6 % |
| potentiometers | 4,222 | 0 | 59.4 % | 61.1 % | 58.6 % | 50.9 % | 45.5 % | 60.5 % | 100.0 % | 10.5 % |
| relays | 10,371 | 21 | 74.4 % | 98.8 % | 54.1 % | 60.7 % | — | 99.2 % | 99.0 % | 7.6 % |
| resistors | 1,324,715 | 2,060 | 73.9 % | 98.4 % | 78.3 % | 76.0 % | 23.3 % | 72.6 % | 99.3 % | 37.8 % |
| switches | 14,150 | 3 | 76.6 % | 99.5 % | 79.0 % | 54.3 % | 57.4 % | 69.4 % | 99.0 % | 6.0 % |
| thermistors | 3,110 | 0 | 82.4 % | 99.4 % | 99.4 % | 52.8 % | — | 70.5 % | 99.9 % | 43.9 % |
| timing_devices | 16,678 | 5,765 | 46.9 % | 58.3 % | 36.8 % | 81.8 % | — | 52.8 % | 99.5 % | 1.3 % |
| varistors | 5,890 | 2,325 | 39.7 % | 82.3 % | 44.2 % | 59.6 % | 0.0 % | 71.4 % | 99.9 % | 63.5 % |
| **total** | 2,758,798 | 64,182 | **71.3 %** | | | | | | | **18.5 %** |

## Defects behind the valid column

| Catalogue | Schema-invalid | Not a part body | Blade Runner could not evaluate | Most frequent Blade Runner findings |
|---|---:|---:|---:|---|
| analog_ics | 0 | 0 | 0 | ANA_SUPPLY 8, ANA_SLEW_GBW 7, CMP_TPD 6, GEN_PROVENANCE_MISSING 4 |
| bjts | 0 | 0 | 0 | GEN_SPARSE 375, BJT_VCESAT_RANGE 146, BJT_HFE_RANGE 19, BJT_VCBO_VS_VCEO 1 |
| capacitors | 0 | 0 | 0 | CAP_E_SERIES 717, CAP_DF_BOUNDS 124, CAP_MAGNITUDE 28, CAP_ENERGY_DENSITY 24 |
| connector_accessories | 0 | 0 | 0 | ACC_AWG_AREA 52, GEN_CITATION_SEARCH_QUERY 7, GEN_FAMILY_MISMATCH 5 |
| connectors | 0 | 0 | 0 | GEN_SPARSE 44,446, CONN_CONTACT_VOLTAGE 295, GEN_FAMILY_MISMATCH 113, CONN_ROWS_POSITIONS 45 |
| controllers | 0 | 0 | 0 | GEN_SPARSE 1,961, GEN_PROVENANCE_MISSING 9, CTL_REF_RANGE 2, GEN_FAMILY_MISMATCH 2 |
| diodes | 0 | 0 | 0 | DIO_VF_RANGE 339, DIO_TVS_PULSE_PRODUCT 23, DIO_VF_POWER 23, DIO_TVS_ORDERING 15 |
| igbts | 0 | 0 | 0 |  |
| magnetics | 0 | 0 | 0 | GEN_SPARSE 4,370, MAG_DCR_GEOM 785, MAG_E_SERIES 571, MAG_WINDING_DATA_INCOMPLETE 461 |
| mosfets | 0 | 0 | 0 | MOS_VTH_WINDOW 915, GEN_SPARSE 264, MOS_AMB_PD_THERMAL 38, MOS_POWER_THERMAL 15 |
| potentiometers | 0 | 0 | 0 |  |
| relays | 0 | 0 | 0 | GEN_FAMILY_MISMATCH 175, GEN_SERIES_IS_MANUFACTURER 24, GEN_SPARSE 10, CONTACT_FORM_THROW 3 |
| resistors | 0 | 0 | 0 | RES_E_SERIES 17,537, GEN_OVERPRECISION 32, RES_R_RANGE 30, GEN_SERIES_IS_MANUFACTURER 3 |
| switches | 0 | 0 | 0 | GEN_SERIES_IS_MANUFACTURER 152, GEN_SPARSE 115, GEN_FAMILY_MISMATCH 2, CONTACT_LIFE_ORDER 1 |
| thermistors | 0 | 0 | 0 | THERM_TOLERANCE 8 |
| timing_devices | 0 | 0 | 0 | TB_OSC_PULL_RANGE 49, TB_OSC_MODE_FREQ 37, TB_OSC_FREQ_TECH 30, TB_OSC_STABILITY 17 |
| varistors | 0 | 0 | 0 | VAR_CLAMP_RATIO 6 |

148 quarantined rows carry no `_quarantineSource` and are not charged to any catalogue.
