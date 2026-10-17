# Database metrics (2026-10-17)

**Quality: 51.7 %**  ·  **Coverage: 28.0 %**

Quality = weighted checklist per part, quarantined rows count 0; coverage = distinct live parts / estimated in-scope Datasheet Archive parts. Method: `scripts/db_metrics.py` docstring and `validator/metrics/`; field and curve lists: `scripts/metrics_spec.json`.

| Catalogue | Live | Quarantine | Quality | Existence | Traced | Fields | Curves | URL | Valid | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| analog_ics | 4,932 | 15 | 69.7 % | 94.9 % | 26.8 % | 73.9 % | — | 100.0 % | 99.8 % | 5.9 % |
| bjts | 4,711 | 166 | 58.2 % | 93.6 % | 20.4 % | 56.9 % | — | 79.2 % | 98.9 % | 9.1 % |
| capacitors | 422,432 | 7,922 | 60.1 % | 79.7 % | 50.5 % | 74.8 % | 9.8 % | 68.2 % | 96.8 % | 45.5 % |
| connector_accessories | 18,837 | 0 | 51.1 % | 44.5 % | 2.9 % | 67.1 % | — | 93.1 % | 99.8 % | 10.7 % |
| connectors | 413,739 | 46,720 | 44.6 % | 69.6 % | 41.6 % | 57.3 % | 0.6 % | 60.5 % | 83.3 % | 27.5 % |
| controllers | 5,245 | 355 | 62.7 % | 73.0 % | 63.8 % | 46.8 % | — | 93.7 % | 89.2 % | 2.8 % |
| diodes | 34,626 | 2,789 | 55.5 % | 73.4 % | 41.3 % | 59.6 % | 0.0 % | 71.2 % | 99.5 % | 5.6 % |
| igbts | 1,978 | 779 | 24.9 % | 56.2 % | 1.8 % | 36.7 % | 0.3 % | 66.6 % | 100.0 % | 50.5 % |
| magnetics | 90,205 | 843 | 47.1 % | 67.0 % | 6.2 % | 71.2 % | 10.3 % | 68.0 % | 95.1 % | 35.5 % |
| mosfets | 11,395 | 3,644 | 39.3 % | 93.2 % | 24.2 % | 52.0 % | 0.0 % | 92.9 % | 95.6 % | 8.5 % |
| potentiometers | 3,610 | 0 | 52.5 % | 61.2 % | 58.2 % | 50.9 % | 0.0 % | 60.5 % | 100.0 % | 10.4 % |
| relays | 7,584 | 21 | 69.7 % | 98.3 % | 38.2 % | 60.7 % | — | 99.3 % | 100.0 % | 10.2 % |
| resistors | 397,491 | 2,029 | 51.4 % | 66.2 % | 39.2 % | 62.5 % | 0.0 % | 64.9 % | 98.3 % | 58.4 % |
| switches | 3,264 | 3 | 55.4 % | 98.9 % | 10.8 % | 38.5 % | 57.4 % | 99.7 % | 100.0 % | 1.5 % |
| thermistors | 626 | 0 | 83.0 % | 98.5 % | 98.5 % | 55.5 % | — | 72.6 % | 100.0 % | 11.5 % |
| timing_devices | 22,336 | 25 | 56.6 % | 51.5 % | 33.0 % | 70.2 % | — | 46.6 % | 99.6 % | 10.5 % |
| varistors | 4,086 | 3,364 | 26.8 % | 73.2 % | 23.2 % | 61.3 % | 0.0 % | 66.7 % | 99.9 % | 49.5 % |
| **total** | 1,447,097 | 68,675 | **51.7 %** | | | | | | | **28.0 %** |

## Defects behind the valid column

| Catalogue | Schema-invalid | Not a part body | Blade Runner could not evaluate | Most frequent Blade Runner findings |
|---|---:|---:|---:|---|
| analog_ics | 0 | 0 | 0 | ANA_SUPPLY 5, CMP_TPD 4, GEN_PROVENANCE_MISSING 4, SW_LEAK 2 |
| bjts | 0 | 0 | 0 | BJT_VCESAT_RANGE 81, BJT_HFE_RANGE 24 |
| capacitors | 0 | 0 | 0 | CAP_DF_BOUNDS 27,049, CAP_E_SERIES 340, CAP_ENERGY_DENSITY 12, CAP_LEAKAGE_CV 6 |
| connector_accessories | 0 | 0 | 0 | ACC_AWG_AREA 52, GEN_FAMILY_MISMATCH 5 |
| connectors | 1,580 | 0 | 0 | GEN_SPARSE 116,320, CONN_CONTACT_VOLTAGE 28,893, CONN_CURRENT_DENSITY 883, CONN_CURRENT_RANGE 293 |
| controllers | 0 | 0 | 0 | GEN_SPARSE 1,136, GEN_PROVENANCE_MISSING 9, CTL_REF_RANGE 2 |
| diodes | 0 | 0 | 0 | DIO_VF_RANGE 317, DIO_SURGE_RATIO 19, DIO_VF_POWER 18, DIO_TVS_ORDERING 1 |
| igbts | 0 | 0 | 0 |  |
| magnetics | 0 | 0 | 0 | GEN_SPARSE 4,314, MAG_E_SERIES 2,740, MAG_DCR_GEOM 728, MAG_WINDING_DATA_INCOMPLETE 461 |
| mosfets | 0 | 0 | 0 | MOS_VTH_WINDOW 761, GEN_SPARSE 213, MOS_POWER_THERMAL 13, MOS_FOM_VCLASS 9 |
| potentiometers | 0 | 0 | 0 |  |
| relays | 0 | 0 | 0 | CONTACT_FORM_THROW 3, GEN_FAMILY_MISMATCH 3 |
| resistors | 0 | 0 | 0 | RES_E_SERIES 13,201, GEN_OVERPRECISION 29, RES_R_RANGE 8, GEN_SERIES_IS_MANUFACTURER 3 |
| switches | 0 | 0 | 0 | GEN_FAMILY_MISMATCH 2 |
| thermistors | 0 | 0 | 0 |  |
| timing_devices | 0 | 0 | 0 | TB_OSC_PULL_RANGE 49, TB_OSC_MODE_FREQ 38, TB_OSC_FREQ_TECH 30, TB_OSC_STABILITY 17 |
| varistors | 0 | 0 | 0 | VAR_CLAMP_RATIO 5 |

148 quarantined rows carry no `_quarantineSource` and are not charged to any catalogue.
