#!/usr/bin/env python3
"""Append 247 confirmed power inductor entries to TAS/data/magnetics.ndjson."""

import json
import os

TARGET = "/home/alfonso/OpenConverters/TAS/data/magnetics.ndjson"


def make_entry(manufacturer, family, reference, description, package, topology_type,
               inductance_H, dcr_ohm, isat_A, irated_A, datasheet_url="",
               tol_pct=20):
    nom = inductance_H
    lo = nom * 0.80
    hi = nom * 1.20
    return {
        "inputs": {
            "designRequirements": {
                "magnetizingInductance": {
                    "nominal": nom,
                    "minimum": lo,
                    "maximum": hi,
                },
                "turnsRatios": [],
                "topology": "Buck",
            }
        },
        "magnetic": {
            "manufacturerInfo": {
                "name": manufacturer,
                "reference": reference,
                "status": "production",
                "family": family,
                "description": description,
                "datasheetUrl": datasheet_url,
                "dataCompleteness": "complete",
            },
            "commercialSpecs": {
                "inductance": {
                    "nominal": nom,
                    "minimum": lo,
                    "maximum": hi,
                },
                "tolerancePercent": tol_pct,
                "dcResistanceMax": dcr_ohm,
                "saturationCurrent": isat_A,
                "ratedCurrent": irated_A,
                "package": package,
            },
            "functionalDescription": {
                "type": "inductor",
                "topology": topology_type,
            },
        },
        "outputs": [],
    }


# ---------------------------------------------------------------------------
# Build all 247 entries
# ---------------------------------------------------------------------------
entries = []

# ---- TDK SPM6530T (10 entries) ----
SPM6530T_URL = "https://product.tdk.com/en/search/inductor/inductor/smd/info?part_no=SPM6530T-R25M"
pkg = "6.5x6.3x3.0mm"
family = "SPM6530T"
mfr = "TDK"
desc = "TDK SPM6530T shielded ferrite SMD power inductor"
topo = "shielded"
for code, L, dcr, isat, irated in [
    ("R25M",  250e-9,   4.0e-3,  19.0, 15.5),
    ("R56M",  560e-9,   9.5e-3,  12.0, 10.0),
    ("1R0M",  1.0e-6,  13.0e-3,  10.0,  8.3),
    ("1R5M",  1.5e-6,  19.0e-3,   8.3,  7.0),
    ("2R2M",  2.2e-6,  28.0e-3,   6.8,  5.8),
    ("3R3M",  3.3e-6,  41.0e-3,   5.6,  4.8),
    ("4R7M",  4.7e-6,  60.0e-3,   4.6,  3.9),
    ("6R8M",  6.8e-6,  88.0e-3,   3.8,  3.2),
    ("100M", 10.0e-6, 130.0e-3,   3.1,  2.6),
    ("150M", 15.0e-6, 195.0e-3,   2.5,  2.1),
]:
    ref = f"SPM6530T-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              SPM6530T_URL))

# ---- TDK VLS6045EX (15 entries) ----
VLS6045_URL = "https://product.tdk.com/en/search/inductor/inductor/smd/info?part_no=VLS6045EX-1R0M"
pkg = "6.0x6.0x4.5mm"
family = "VLS6045EX"
desc = "TDK VLS6045EX shielded ferrite SMD power inductor"
for code, L, dcr, isat, irated in [
    ("1R0M",    1e-6,   7.9e-3,  13.7, 11.1),
    ("1R5M",  1.5e-6,  11.0e-3,  10.8,  9.1),
    ("2R2M",  2.2e-6,  16.0e-3,   8.9,  7.7),
    ("3R3M",  3.3e-6,  22.0e-3,   7.3,  6.3),
    ("4R7M",  4.7e-6,  32.0e-3,   5.9,  5.2),
    ("6R8M",  6.8e-6,  47.0e-3,   5.1,  4.5),
    ("100M",  10e-6,   72.0e-3,   4.1,  3.7),
    ("150M",  15e-6,  108.0e-3,   3.5,  3.0),
    ("220M",  22e-6,  165.0e-3,   2.7,  2.4),
    ("330M",  33e-6,  230.0e-3,   2.3,  2.1),
    ("470M",  47e-6,  335.0e-3,   1.8,  1.7),
    ("680M",  68e-6,  490.0e-3,   1.5,  1.4),
    ("101M", 100e-6,  740.0e-3,   1.2,  1.2),
    ("151M", 150e-6,    1.08,    1.0,  1.0),
    ("221M", 220e-6,    1.56,    0.83, 0.83),
]:
    ref = f"VLS6045EX-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              VLS6045_URL))

# ---- TDK VLS4012ET (15 entries) ----
VLS4012_URL = "https://product.tdk.com/en/search/inductor/inductor/smd/info?part_no=VLS4012ET-1R0M"
pkg = "4.0x4.0x1.2mm"
family = "VLS4012ET"
desc = "TDK VLS4012ET shielded ferrite SMD power inductor"
for code, L, dcr, isat, irated in [
    ("1R0M",    1e-6,   53e-3,  3.0,  2.4),
    ("1R5M",  1.5e-6,   72e-3,  2.5,  2.0),
    ("2R2M",  2.2e-6,  110e-3,  2.0,  1.7),
    ("3R3M",  3.3e-6,  160e-3,  1.6,  1.4),
    ("4R7M",  4.7e-6,  240e-3,  1.4,  1.2),
    ("6R8M",  6.8e-6,  370e-3,  1.1,  1.0),
    ("100M",  10e-6,   560e-3,  0.90, 0.82),
    ("150M",  15e-6,   810e-3,  0.76, 0.70),
    ("220M",  22e-6,    1.21,   0.61, 0.57),
    ("330M",  33e-6,    1.72,   0.51, 0.49),
    ("470M",  47e-6,    2.56,   0.42, 0.40),
    ("680M",  68e-6,    3.74,   0.34, 0.33),
    ("101M", 100e-6,    5.50,   0.28, 0.27),
    ("151M", 150e-6,    8.00,   0.23, 0.22),
    ("221M", 220e-6,   11.70,   0.19, 0.18),
]:
    ref = f"VLS4012ET-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              VLS4012_URL))

# ---- TDK SLF10145T (11 entries) ----
SLF10145_URL = "https://product.tdk.com/en/search/inductor/inductor/smd/info?part_no=SLF10145T-100M4R7-PF"
pkg = "10.5x10.5x4.5mm"
family = "SLF10145T"
desc = "TDK SLF10145T shielded ferrite SMD power inductor"
for code, L, dcr, isat, irated in [
    ("100M",  10e-6,   27e-3,  8.8,  7.4),
    ("150M",  15e-6,   42e-3,  7.5,  6.1),
    ("220M",  22e-6,   62e-3,  6.1,  5.0),
    ("330M",  33e-6,   85e-3,  5.0,  4.2),
    ("470M",  47e-6,  125e-3,  4.1,  3.5),
    ("680M",  68e-6,  180e-3,  3.5,  3.0),
    ("101M", 100e-6,  265e-3,  2.9,  2.5),
    ("151M", 150e-6,  390e-3,  2.3,  2.0),
    ("221M", 220e-6,  560e-3,  1.9,  1.7),
    ("331M", 330e-6,  840e-3,  1.6,  1.4),
    ("471M", 470e-6,   1.22,   1.3,  1.2),
]:
    ref = f"SLF10145T-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              SLF10145_URL))

# ---- TDK SLF12565T (11 entries) ----
SLF12565_URL = "https://product.tdk.com/en/search/inductor/inductor/smd/info?part_no=SLF12565T-100M5R5-PF"
pkg = "12.5x12.5x6.5mm"
family = "SLF12565T"
desc = "TDK SLF12565T shielded ferrite SMD power inductor"
for code, L, dcr, isat, irated in [
    ("100M",  10e-6,   16e-3, 12.5, 10.5),
    ("150M",  15e-6,   24e-3, 10.2,  8.8),
    ("220M",  22e-6,   36e-3,  8.5,  7.4),
    ("330M",  33e-6,   50e-3,  6.8,  5.9),
    ("470M",  47e-6,   73e-3,  5.7,  5.1),
    ("680M",  68e-6,  105e-3,  4.8,  4.3),
    ("101M", 100e-6,  155e-3,  3.8,  3.5),
    ("151M", 150e-6,  230e-3,  3.2,  2.9),
    ("221M", 220e-6,  335e-3,  2.6,  2.4),
    ("331M", 330e-6,  490e-3,  2.2,  2.0),
    ("471M", 470e-6,  720e-3,  1.7,  1.6),
]:
    ref = f"SLF12565T-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              SLF12565_URL))

# ---- Bourns SRR1280 (18 entries) ----
SRR1280_URL = "https://www.bourns.com/docs/product-datasheets/srr1280.pdf"
pkg = "12.5x12.0x8.0mm"
family = "SRR1280"
mfr = "Bourns"
desc = "Bourns SRR1280 shielded ferrite SMD power inductor"
topo = "shielded"
for code, L, dcr, isat, irated in [
    ("1R0M",    1e-6,   7.4e-3,  9.0,  8.2),
    ("1R5M",  1.5e-6,  11.0e-3,  7.8,  7.0),
    ("2R2M",  2.2e-6,  16.0e-3,  6.4,  5.6),
    ("3R3M",  3.3e-6,  24.0e-3,  5.0,  4.4),
    ("4R7M",  4.7e-6,  33.0e-3,  4.3,  3.8),
    ("6R8M",  6.8e-6,  48.0e-3,  3.6,  3.2),
    ("100M",  10e-6,   70.0e-3,  2.9,  2.6),
    ("150M",  15e-6,  105.0e-3,  2.4,  2.2),
    ("220M",  22e-6,  155.0e-3,  2.0,  1.8),
    ("330M",  33e-6,  230.0e-3,  1.6,  1.5),
    ("470M",  47e-6,  340.0e-3,  1.4,  1.3),
    ("680M",  68e-6,  500.0e-3,  1.2,  1.1),
    ("101M", 100e-6,  730.0e-3,  0.97, 0.92),
    ("151M", 150e-6,    1.08,   0.80, 0.76),
    ("221M", 220e-6,    1.60,   0.65, 0.62),
    ("331M", 330e-6,    2.40,   0.53, 0.50),
    ("471M", 470e-6,    3.40,   0.44, 0.42),
    ("681M", 680e-6,    4.90,   0.37, 0.35),
]:
    ref = f"SRR1280-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              SRR1280_URL))

# ---- Bourns SRR6028 (18 entries) ----
SRR6028_URL = "https://www.bourns.com/docs/product-datasheets/srr6028.pdf"
pkg = "6.0x6.0x2.8mm"
family = "SRR6028"
desc = "Bourns SRR6028 shielded ferrite SMD power inductor"
for code, L, dcr, isat, irated in [
    ("1R5M",  1.5e-6,   40e-3,  2.5,  1.9),
    ("2R2M",  2.2e-6,   58e-3,  2.0,  1.6),
    ("3R3M",  3.3e-6,   88e-3,  1.6,  1.3),
    ("4R7M",  4.7e-6,  126e-3,  1.4,  1.1),
    ("6R8M",  6.8e-6,  183e-3,  1.1,  0.93),
    ("100M",  10e-6,   268e-3,  0.94, 0.79),
    ("150M",  15e-6,   400e-3,  0.77, 0.65),
    ("220M",  22e-6,   590e-3,  0.63, 0.54),
    ("330M",  33e-6,   880e-3,  0.52, 0.44),
    ("470M",  47e-6,    1.25,   0.43, 0.37),
    ("680M",  68e-6,    1.82,   0.36, 0.31),
    ("101M", 100e-6,    2.67,   0.30, 0.26),
    ("151M", 150e-6,    3.97,   0.24, 0.21),
    ("221M", 220e-6,    5.88,   0.20, 0.17),
    ("331M", 330e-6,    8.66,   0.17, 0.14),
    ("471M", 470e-6,   12.50,   0.14, 0.12),
    ("681M", 680e-6,   18.00,   0.11, 0.10),
    ("102M",   1e-3,   26.50,   0.09, 0.085),
]:
    ref = f"SRR6028-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              SRR6028_URL))

# ---- Bourns SRR4018 (13 entries) ----
SRR4018_URL = "https://www.bourns.com/docs/product-datasheets/srr4018.pdf"
pkg = "4.0x4.0x1.8mm"
family = "SRR4018"
desc = "Bourns SRR4018 shielded ferrite SMD power inductor"
for code, L, dcr, isat, irated in [
    ("1R0M",    1e-6,   70e-3,  1.4,  1.2),
    ("1R5M",  1.5e-6,  100e-3,  1.2,  0.97),
    ("2R2M",  2.2e-6,  148e-3,  0.97, 0.80),
    ("3R3M",  3.3e-6,  218e-3,  0.80, 0.67),
    ("4R7M",  4.7e-6,  312e-3,  0.67, 0.56),
    ("6R8M",  6.8e-6,  455e-3,  0.56, 0.47),
    ("100M",  10e-6,   672e-3,  0.46, 0.38),
    ("150M",  15e-6,   988e-3,  0.38, 0.32),
    ("220M",  22e-6,    1.46,   0.31, 0.26),
    ("330M",  33e-6,    2.20,   0.26, 0.22),
    ("470M",  47e-6,    3.14,   0.21, 0.18),
    ("680M",  68e-6,    4.55,   0.18, 0.15),
    ("101M", 100e-6,    6.70,   0.14, 0.12),
]:
    ref = f"SRR4018-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              SRR4018_URL))

# ---- Sumida CDRH6D28 (17 entries) ----
CDRH6D28_URL = "https://www.sumida.com/products/datasheet/CDRH6D28.pdf"
pkg = "6.5x6.5x2.8mm"
family = "CDRH6D28"
mfr = "Sumida"
desc = "Sumida CDRH6D28 unshielded drum-core SMD power inductor"
topo = "unshielded"
for code, L, dcr, isat, irated in [
    ("1R0M",    1e-6,   32e-3,  2.0,  1.7),
    ("1R5M",  1.5e-6,   47e-3,  1.7,  1.4),
    ("2R2M",  2.2e-6,   70e-3,  1.4,  1.1),
    ("3R3M",  3.3e-6,  103e-3,  1.1,  0.95),
    ("4R7M",  4.7e-6,  147e-3,  0.95, 0.79),
    ("6R8M",  6.8e-6,  213e-3,  0.79, 0.66),
    ("100M",  10e-6,   313e-3,  0.65, 0.54),
    ("150M",  15e-6,   469e-3,  0.53, 0.44),
    ("220M",  22e-6,   688e-3,  0.44, 0.37),
    ("330M",  33e-6,    1.03,   0.36, 0.30),
    ("470M",  47e-6,    1.47,   0.30, 0.25),
    ("680M",  68e-6,    2.13,   0.25, 0.21),
    ("101M", 100e-6,    3.13,   0.21, 0.17),
    ("151M", 150e-6,    4.69,   0.17, 0.14),
    ("221M", 220e-6,    6.88,   0.14, 0.12),
    ("331M", 330e-6,   10.30,   0.11, 0.09),
    ("471M", 470e-6,   14.70,   0.09, 0.08),
]:
    ref = f"CDRH6D28-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              CDRH6D28_URL))

# ---- Sumida CDRH8D28 (16 entries) ----
CDRH8D28_URL = "https://www.sumida.com/products/datasheet/CDRH8D28.pdf"
pkg = "8.3x8.3x2.8mm"
family = "CDRH8D28"
desc = "Sumida CDRH8D28 unshielded drum-core SMD power inductor"
for code, L, dcr, isat, irated in [
    ("1R0M",    1e-6,   18e-3,  3.5,  3.0),
    ("1R5M",  1.5e-6,   27e-3,  2.8,  2.4),
    ("2R2M",  2.2e-6,   40e-3,  2.3,  1.9),
    ("3R3M",  3.3e-6,   60e-3,  1.9,  1.6),
    ("4R7M",  4.7e-6,   85e-3,  1.6,  1.3),
    ("6R8M",  6.8e-6,  122e-3,  1.3,  1.1),
    ("100M",  10e-6,   180e-3,  1.1,  0.92),
    ("150M",  15e-6,   270e-3,  0.90, 0.75),
    ("220M",  22e-6,   395e-3,  0.74, 0.62),
    ("330M",  33e-6,   595e-3,  0.60, 0.50),
    ("470M",  47e-6,   845e-3,  0.50, 0.42),
    ("680M",  68e-6,    1.22,   0.42, 0.35),
    ("101M", 100e-6,    1.80,   0.34, 0.29),
    ("151M", 150e-6,    2.70,   0.28, 0.23),
    ("221M", 220e-6,    3.95,   0.23, 0.19),
    ("331M", 330e-6,    5.95,   0.19, 0.16),
]:
    ref = f"CDRH8D28-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              CDRH8D28_URL))

# ---- Sumida CDRH10D48 (23 entries) ----
CDRH10D48_URL = "https://www.sumida.com/products/datasheet/CDRH10D48.pdf"
pkg = "10.5x10.5x4.8mm"
family = "CDRH10D48"
desc = "Sumida CDRH10D48 unshielded drum-core SMD power inductor"
for code, L, dcr, isat, irated in [
    ("4R7M",    4.7e-6,   30e-3,   3.8,  3.3),
    ("6R8M",    6.8e-6,   44e-3,   3.2,  2.7),
    ("100M",    10e-6,    65e-3,   2.6,  2.2),
    ("150M",    15e-6,    97e-3,   2.1,  1.8),
    ("220M",    22e-6,   142e-3,   1.7,  1.5),
    ("330M",    33e-6,   214e-3,   1.4,  1.2),
    ("470M",    47e-6,   305e-3,   1.2,  1.0),
    ("680M",    68e-6,   438e-3,   0.97, 0.85),
    ("101M",   100e-6,   645e-3,   0.80, 0.70),
    ("151M",   150e-6,   965e-3,   0.65, 0.57),
    ("221M",   220e-6,    1.41,    0.54, 0.47),
    ("331M",   330e-6,    2.13,    0.44, 0.38),
    ("471M",   470e-6,    3.02,    0.37, 0.32),
    ("681M",   680e-6,    4.38,    0.31, 0.27),
    ("102M",     1e-3,    6.43,    0.25, 0.22),
    ("152M",   1.5e-3,    9.65,    0.21, 0.18),
    ("222M",   2.2e-3,   14.20,    0.17, 0.15),
    ("332M",   3.3e-3,   21.30,    0.14, 0.12),
    ("472M",   4.7e-3,   30.50,    0.11, 0.10),
    ("682M",   6.8e-3,   44.00,    0.09, 0.08),
    ("103M",    10e-3,   64.50,    0.08, 0.07),
    ("153M",    15e-3,   96.50,    0.06, 0.055),
    ("223M",    22e-3,  142.00,    0.05, 0.045),
]:
    ref = f"CDRH10D48-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              CDRH10D48_URL))

# ---- Sumida CDMC6D28NP (9 entries) — metal composite shielded ----
# Note: prompt says 9 entries but lists 7 data rows; using 7 actual rows given.
CDMC6D28NP_URL = "https://www.sumida.com/products/datasheet/CDMC6D28NP.pdf"
pkg = "6.5x7.25x2.8mm"
family = "CDMC6D28NP"
desc = "Sumida CDMC6D28NP metal-composite shielded SMD power inductor"
topo = "shielded"
for code, L, dcr, isat, irated in [
    ("R20MC",  200e-9,   3.5e-3,  27.2, 22.0),
    ("R47MC",  470e-9,   8.3e-3,  13.5, 11.0),
    ("1R0MC",    1e-6,  16.0e-3,   9.0,  7.5),
    ("1R5MC",  1.5e-6,  24.0e-3,   7.5,  6.0),
    ("2R2MC",  2.2e-6,  19.3e-3,   6.8,  5.5),
    ("3R3MC",  3.3e-6,  55.0e-3,   5.5,  4.5),
    ("4R7MC",  4.7e-6,  78.0e-3,   4.5,  3.7),
]:
    ref = f"CDMC6D28NP-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              CDMC6D28NP_URL))

# ---- Vishay IHLP-2020CZ-01 (14 entries) ----
IHLP2020_URL = "https://www.vishay.com/docs/34349/ihlp2020cz01.pdf"
pkg = "5.18x5.49x3.0mm"
family = "IHLP-2020CZ-01"
mfr = "Vishay"
desc = "Vishay IHLP-2020CZ-01 metal-alloy composite SMD power inductor"
topo = "shielded"
for code, L, dcr, isat, irated in [
    ("1R0M01",    1e-6,  13.7e-3,  9.2,  6.8),
    ("1R5M01",  1.5e-6,  20.7e-3,  7.2,  5.5),
    ("2R2M01",  2.2e-6,  30.0e-3,  6.1,  4.6),
    ("3R3M01",  3.3e-6,  43.0e-3,  5.0,  3.8),
    ("4R7M01",  4.7e-6,  61.0e-3,  4.1,  3.2),
    ("6R8M01",  6.8e-6,  95.0e-3,  3.5,  2.7),
    ("100M01",  10e-6,  140.0e-3,  2.8,  2.2),
    ("150M01",  15e-6,  214.0e-3,  2.3,  1.8),
    ("220M01",  22e-6,  312.0e-3,  1.9,  1.5),
    ("330M01",  33e-6,  470.0e-3,  1.5,  1.2),
    ("470M01",  47e-6,  670.0e-3,  1.3,  1.0),
    ("680M01",  68e-6,  970.0e-3,  1.1,  0.85),
    ("101M01", 100e-6,    1.43,   0.89,  0.69),
    ("151M01", 150e-6,    2.14,   0.73,  0.57),
]:
    ref = f"IHLP-2020CZ-01-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              IHLP2020_URL))

# ---- Vishay IHLP-2525CZ-01 (9 entries) ----
IHLP2525_URL = "https://www.vishay.com/docs/34350/ihlp2525cz01.pdf"
pkg = "6.6x6.6x3.0mm"
family = "IHLP-2525CZ-01"
desc = "Vishay IHLP-2525CZ-01 metal-alloy composite SMD power inductor"
for code, L, dcr, isat, irated in [
    ("1R0M01",   1e-6,   7.5e-3, 12.5,  9.5),
    ("2R2M01", 2.2e-6,  18.0e-3,  8.0,  6.2),
    ("4R7M01", 4.7e-6,  37.0e-3,  5.5,  4.3),
    ("100M01",  10e-6,  80.0e-3,  3.7,  2.9),
    ("220M01",  22e-6, 175.0e-3,  2.5,  2.0),
    ("330M01",  33e-6, 260.0e-3,  2.0,  1.6),
    ("470M01",  47e-6, 375.0e-3,  1.7,  1.4),
    ("680M01",  68e-6, 545.0e-3,  1.4,  1.1),
    ("101M01", 100e-6, 800.0e-3,  1.1,  0.9),
]:
    ref = f"IHLP-2525CZ-01-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              IHLP2525_URL))

# ---- Vishay IHLP-4040DZ-01 (14 entries) ----
IHLP4040_01_URL = "https://www.vishay.com/docs/34353/ihlp4040dz01.pdf"
pkg = "10.7x10.7x4.1mm"
family = "IHLP-4040DZ-01"
desc = "Vishay IHLP-4040DZ-01 high-saturation metal-alloy composite SMD power inductor"
for code, L, dcr, isat, irated in [
    ("1R0M01",    1e-6,   2.8e-3,  30.0, 24.0),
    ("1R5M01",  1.5e-6,   4.2e-3,  24.0, 19.0),
    ("2R2M01",  2.2e-6,   6.1e-3,  20.0, 16.0),
    ("3R3M01",  3.3e-6,   9.2e-3,  16.0, 13.0),
    ("4R7M01",  4.7e-6,  13.1e-3,  13.0, 11.0),
    ("6R8M01",  6.8e-6,  19.0e-3,  11.0,  9.0),
    ("100M01",  10e-6,   28.0e-3,   9.0,  7.3),
    ("150M01",  15e-6,   42.0e-3,   7.3,  6.0),
    ("220M01",  22e-6,   62.0e-3,   6.0,  4.9),
    ("330M01",  33e-6,   92.0e-3,   5.0,  4.0),
    ("470M01",  47e-6,  132.0e-3,   4.1,  3.3),
    ("680M01",  68e-6,  192.0e-3,   3.4,  2.8),
    ("101M01", 100e-6,  282.0e-3,   2.8,  2.3),
    ("221M01", 220e-6,  620.0e-3,   1.9,  1.6),
]:
    ref = f"IHLP-4040DZ-01-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              IHLP4040_01_URL))

# ---- Vishay IHLP-4040DZ-11 (18 entries) ----
IHLP4040_11_URL = "https://www.vishay.com/docs/34354/ihlp4040dz11.pdf"
pkg = "10.7x10.7x4.1mm"
family = "IHLP-4040DZ-11"
desc = "Vishay IHLP-4040DZ-11 low-DCR metal-alloy composite SMD power inductor"
for code, L, dcr, isat, irated in [
    ("1R0M11",    1e-6,   1.8e-3,  40.0, 31.0),
    ("1R5M11",  1.5e-6,   2.7e-3,  32.0, 25.0),
    ("2R2M11",  2.2e-6,   3.9e-3,  27.0, 21.0),
    ("3R3M11",  3.3e-6,   5.8e-3,  22.0, 17.0),
    ("4R7M11",  4.7e-6,   8.3e-3,  18.0, 14.0),
    ("6R8M11",  6.8e-6,  12.0e-3,  15.0, 12.0),
    ("100M11",  10e-6,   17.7e-3,  12.0,  9.5),
    ("150M11",  15e-6,   26.0e-3,   9.8,  7.8),
    ("220M11",  22e-6,   38.0e-3,   8.0,  6.4),
    ("330M11",  33e-6,   57.0e-3,   6.6,  5.3),
    ("470M11",  47e-6,   81.0e-3,   5.4,  4.3),
    ("680M11",  68e-6,  117.0e-3,   4.5,  3.6),
    ("101M11", 100e-6,  172.0e-3,   3.7,  2.9),
    ("151M11", 150e-6,  258.0e-3,   3.0,  2.4),
    ("221M11", 220e-6,  378.0e-3,   2.5,  2.0),
    ("331M11", 330e-6,  566.0e-3,   2.0,  1.6),
    ("471M11", 470e-6,  806.0e-3,   1.6,  1.3),
    ("681M11", 680e-6,    1.17,    1.4,  1.1),
]:
    ref = f"IHLP-4040DZ-11-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              IHLP4040_11_URL))

# ---- Vishay IHLP-5050CE-01 (16 entries) ----
IHLP5050_URL = "https://www.vishay.com/docs/34356/ihlp5050ce01.pdf"
pkg = "13.2x12.9x3.5mm"
family = "IHLP-5050CE-01"
desc = "Vishay IHLP-5050CE-01 very-high-current metal-alloy composite SMD power inductor"
for code, L, dcr, isat, irated in [
    ("1R0M01",    1e-6,   1.0e-3,  80.0, 60.0),
    ("1R5M01",  1.5e-6,   1.5e-3,  65.0, 49.0),
    ("2R2M01",  2.2e-6,   2.2e-3,  53.0, 41.0),
    ("3R3M01",  3.3e-6,   3.3e-3,  43.0, 34.0),
    ("4R7M01",  4.7e-6,   4.7e-3,  36.0, 28.0),
    ("6R8M01",  6.8e-6,   6.8e-3,  30.0, 24.0),
    ("100M01",  10e-6,   10.0e-3,  24.0, 19.0),
    ("150M01",  15e-6,   15.0e-3,  20.0, 16.0),
    ("220M01",  22e-6,   22.0e-3,  16.0, 13.0),
    ("330M01",  33e-6,   33.0e-3,  13.0, 11.0),
    ("470M01",  47e-6,   47.0e-3,  11.0,  9.0),
    ("680M01",  68e-6,   68.0e-3,   9.0,  7.5),
    ("101M01", 100e-6,  100.0e-3,   7.5,  6.2),
    ("151M01", 150e-6,  150.0e-3,   6.2,  5.1),
    ("221M01", 220e-6,  220.0e-3,   5.1,  4.2),
    ("331M01", 330e-6,  330.0e-3,   4.2,  3.5),
]:
    ref = f"IHLP-5050CE-01-{code}"
    entries.append(make_entry(mfr, family, ref, desc, pkg, topo, L, dcr, isat, irated,
                              IHLP5050_URL))

# ---------------------------------------------------------------------------
# Verify count before writing
# ---------------------------------------------------------------------------
# CDMC6D28NP: prompt says 9 entries but only 7 data rows were supplied;
# all other series match. Confirmed total from supplied data = 245.
assert len(entries) == 245, f"Expected 245 entries, got {len(entries)}"

# ---------------------------------------------------------------------------
# Check existing references to avoid duplicates
# ---------------------------------------------------------------------------
existing_refs = set()
with open(TARGET, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            ref = (obj.get("magnetic") or {}).get("manufacturerInfo", {}).get("reference", "")
            if ref:
                existing_refs.add(ref)
        except json.JSONDecodeError:
            pass

# ---------------------------------------------------------------------------
# Append new entries
# ---------------------------------------------------------------------------
added = 0
skipped = 0
with open(TARGET, "a", encoding="utf-8") as f:
    for entry in entries:
        ref = entry["magnetic"]["manufacturerInfo"]["reference"]
        if ref in existing_refs:
            skipped += 1
            continue
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
        added += 1

# ---------------------------------------------------------------------------
# Final count
# ---------------------------------------------------------------------------
with open(TARGET, "r", encoding="utf-8") as f:
    total_lines = sum(1 for line in f if line.strip())

print(f"Entries prepared : {len(entries)} (245 confirmed data rows; CDMC6D28NP had 7 rows not 9)")
print(f"Already existed  : {skipped}")
print(f"Newly appended   : {added}")
print(f"Total non-blank  : {total_lines}")
expected = 4826 + added
print(f"Expected total   : {expected}")
assert total_lines == expected, f"Line count mismatch: {total_lines} vs {expected}"
print("OK - count verified.")
