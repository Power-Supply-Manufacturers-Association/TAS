#!/usr/bin/env python3
"""
Convert three Wuerth Elektronik film capacitor Excel databases into CAS-format NDJSON.

Source files:
  1. Caps_Safety.xlsx  (safety film caps: X1, X2, Y1, Y2)
  2. Caps_DC-Film.xlsx (DC film caps)
  3. Caps_DCLink.xlsx  (DC-Link film caps)

Target: append to /home/alfonso/OpenConverters/TAS/data/capacitors.ndjson
"""

import json
import math
import openpyxl
import re
import sys
from pathlib import Path

NDJSON_PATH = Path("/home/alfonso/OpenConverters/TAS/data/capacitors.ndjson")
SRC_DIR = Path("/mnt/c/Users/alfon/Downloads/temp")


def safe_float(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v in ("", "-", "N/A", "#N/A", "#REF!", "TBD", "n.a.", "n/a"):
            return None
        v = re.sub(r'[a-zA-Zµ°/]+$', '', v).strip()
        if not v:
            return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def safe_int(v):
    f = safe_float(v)
    return int(f) if f is not None else None


def safe_str(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def dim_nom(val):
    if val is None:
        return None
    return {"nominal": val}


def determine_assembly(asm_str):
    if asm_str is None:
        asm_str = ""
    s = str(asm_str).upper().strip()
    if "SMT" in s or "SMD" in s or "SURFACE" in s:
        return "SMT"
    if "SNAP" in s:
        return "Snap-In"
    if "SCREW" in s:
        return "Screw Type"
    return "THT"


def determine_shape(type_str):
    if type_str is None:
        type_str = ""
    s = str(type_str).upper().strip()
    if "4-PIN" in s or "4 PIN" in s:
        return "Box Type 4-pin"
    if "BOX" in s:
        return "Box type"
    if "CYLINDRICAL" in s or "RADIAL" in s:
        return "Radial Cylindrical"
    if "H-CHIP" in s:
        return "H-Chip"
    if "V-CHIP" in s:
        return "V-Chip"
    if "SMD" in s or "CHIP" in s:
        return "SMD Chip"
    return "Box type"


def load_existing_part_numbers():
    existing = set()
    if NDJSON_PATH.exists():
        with open(NDJSON_PATH, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        d = json.loads(line)
                        pn = d['manufacturerInfo']['datasheetInfo']['part']['partNumber']
                        existing.add(pn)
                    except (json.JSONDecodeError, KeyError):
                        pass
    return existing


def read_sheet(filepath, sheet_name):
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb[sheet_name]
    all_rows = list(ws.iter_rows(min_row=1, values_only=False))
    headers_r2 = {c.column: c.value for c in all_rows[1] if c.value}
    internal_r4 = {c.column: c.value for c in all_rows[3] if c.value}
    conv_r10 = {}
    for c in all_rows[9]:
        if c.value is not None and c.column > 1 and isinstance(c.value, (int, float)):
            conv_r10[c.column] = c.value
    data_rows = all_rows[13:]
    records = []
    for row in data_rows:
        vals = {}
        has_data = False
        for c in row:
            if c.value is not None and c.column > 1:
                vals[c.column] = c.value
                if c.column in (2, 3):
                    has_data = True
        if has_data and vals.get(2) is not None:
            records.append(vals)
    wb.close()
    return records, headers_r2, internal_r4, conv_r10


def build_entry(
    part_number, series, technology, description, case_str,
    cap_nom, cap_min, cap_max, cap_min_longterm,
    rated_voltage, df, df_freq,
    insulation_resistance, esr, esr_freq,
    ripple_current, ripple_freq, ripple_temp,
    temp_min, temp_max,
    width, width_max, length, length_max,
    height, height_max, pitch, pin_diameter, pin_length,
    footprint_m2, volume_m3,
    assembly, shape_type,
    packaging, vpe, wgu,
    rs, cs, ls, riso
):
    cap_dim = {}
    if cap_nom is not None:
        cap_dim["nominal"] = cap_nom
    if cap_min is not None:
        cap_dim["minimum"] = cap_min
    if cap_max is not None:
        cap_dim["maximum"] = cap_max
    if not cap_dim:
        cap_dim = {"nominal": 0}

    def dim_with_max(nom, mx):
        if nom is None:
            return None
        d = {"nominal": nom}
        if mx is not None:
            d["maximum"] = mx
        return d

    temp_dim = None
    if temp_min is not None or temp_max is not None:
        temp_dim = {}
        if temp_min is not None:
            temp_dim["minimum"] = temp_min
        if temp_max is not None:
            temp_dim["maximum"] = temp_max

    return {
        "manufacturerInfo": {
            "datasheetInfo": {
                "part": {
                    "partNumber": part_number,
                    "series": series or "",
                    "technology": technology,
                    "matchcodeDescription": description or "",
                    "case": case_str or "",
                    "useInDcTool": True,
                    "internalViewOnly": None
                },
                "electrical": {
                    "capacitance": cap_dim,
                    "capacitanceDriftLongTermPercent": 0,
                    "capacitanceMinimumLongTerm": cap_min_longterm if cap_min_longterm is not None else 0,
                    "ratedVoltage": rated_voltage if rated_voltage is not None else 0,
                    "dissipationFactor": df if df is not None else 0,
                    "dissipationFactorFrequency": df_freq if df_freq is not None else 0,
                    "leakageCurrent": 0,
                    "insulationResistance": insulation_resistance if insulation_resistance is not None else 0,
                    "esr": esr,
                    "esrFrequency": esr_freq,
                    "esrForLosses": esr if esr is not None else 0,
                    "rippleCurrent": ripple_current if ripple_current is not None else 0,
                    "rippleCurrentFrequency": ripple_freq if ripple_freq is not None else 0,
                    "rippleCurrentTemperature": ripple_temp,
                    "rippleCurrentFrequencyPoints": {"xData": [], "yData": []},
                    "rippleCurrentTemperaturePoints": {"xData": [], "yData": []},
                    "thermalResistance": None,
                    "capacitanceSaturationMLCC": None,
                    "vthMLCC": None
                },
                "thermal": {
                    "temperature": temp_dim,
                    "tcc": None
                },
                "mechanical": {
                    "dimensions": {
                        "diameter": None,
                        "width": dim_with_max(width, width_max),
                        "length": dim_with_max(length, length_max),
                        "height": dim_with_max(height, height_max),
                        "pitch": dim_nom(pitch),
                        "pinDiameter": dim_nom(pin_diameter),
                        "pinLength": dim_nom(pin_length)
                    },
                    "shape": {
                        "assembly": assembly or "THT",
                        "shapeType": shape_type or "Box type",
                        "volume": dim_nom(volume_m3),
                        "footprint": dim_nom(footprint_m2)
                    }
                },
                "business": {
                    "packaging": packaging or "",
                    "vpe": vpe if vpe is not None else 0,
                    "moq": vpe if vpe is not None else 0,
                    "leadTime": None,
                    "stock": None,
                    "distribution": None,
                    "wgu": wgu or "0090",
                    "alphaPlanDescription": description or "",
                    "priceCost": 0.0,
                    "weCustomWeight": 0.0
                },
                "lifetime": {
                    "lifetimeEndurance": None,
                    "maxLifetime": None,
                    "aexp": None,
                    "bexp": None,
                    "deltaT0": None,
                    "kfactor": None,
                    "vxfactor": None,
                    "endDefinitionC": None,
                    "endDefinitionEsr": None,
                    "usefulLife": None,
                    "eoUsefulLifeC": None,
                    "eoUsefulLifeR": None,
                    "usefulLifeComment": None
                },
                "modelParams": {
                    "rs": rs,
                    "cs": cs,
                    "ls": ls,
                    "riso": riso
                },
                "factors": {
                    "rippleCurrentFrequencyFactorFrequency": [],
                    "rippleCurrentFrequencyFactorAmplitude": [],
                    "rippleCurrentTemperatureFactorTemperature": [],
                    "rippleCurrentTemperatureFactorAmplitude": []
                }
            }
        }
    }


def fmt_cap(c_raw_pF):
    if c_raw_pF is None:
        return "??"
    if c_raw_pF >= 1e6:
        return f"{c_raw_pF/1e6:.1f}uF"
    elif c_raw_pF >= 1e3:
        return f"{c_raw_pF/1e3:.0f}nF"
    else:
        return f"{c_raw_pF:.0f}pF"


def parse_freq_str(s):
    if s is None:
        return None
    m = re.search(r'(\d+(?:\.\d+)?)\s*(k|M|G)?', str(s))
    if m:
        val = float(m.group(1))
        mult = {'k': 1e3, 'M': 1e6, 'G': 1e9}.get(m.group(2), 1)
        return val * mult
    return None


# ---------------------------------------------------------------------------
# Safety caps
# ---------------------------------------------------------------------------
def convert_safety(existing_pns):
    records, headers, internal, conv = read_sheet(
        SRC_DIR / "Caps_Safety.xlsx", "Caps_Safety"
    )
    entries = []
    skipped_internal = 0
    skipped_dup = 0

    for rec in records:
        ivo = safe_str(rec.get(4))
        if ivo and ivo.upper() not in ("", "N", "NO", "FALSE"):
            skipped_internal += 1
            continue

        order_code = safe_str(rec.get(3))
        if not order_code:
            continue
        if order_code in existing_pns:
            skipped_dup += 1
            continue

        series = safe_str(rec.get(2))
        c_raw = safe_float(rec.get(10))
        c_F = c_raw * 1e-12 if c_raw is not None else None

        tol_neg = safe_float(rec.get(12))
        tol_pos = safe_float(rec.get(13))
        cap_min = c_F * (1 - abs(tol_neg) / 100.0) if c_F and tol_neg else None
        cap_max = c_F * (1 + abs(tol_pos) / 100.0) if c_F and tol_pos else None

        ur_ac = safe_float(rec.get(14))
        ur_dc = safe_float(rec.get(17))
        rated_voltage = ur_ac if ur_ac else ur_dc

        safety_class = safe_str(rec.get(18))
        impulse_voltage = safe_float(rec.get(19))

        df_1k = safe_float(rec.get(22))
        df_10k = safe_float(rec.get(23))
        df_100k = safe_float(rec.get(24))
        df_1M = safe_float(rec.get(25))

        df = df_1k if df_1k is not None else (df_10k if df_10k is not None else df_100k)
        df_freq = 1000 if df_1k is not None else (10000 if df_10k is not None else (100000 if df_100k is not None else 0))

        riso_raw = safe_float(rec.get(27))
        riso_ohm = riso_raw * 1e9 if riso_raw is not None else None

        temp_min = safe_float(rec.get(29))
        temp_max = safe_float(rec.get(30))

        # Dimensions: raw in mm -> m
        length_m = safe_float(rec.get(34))
        length_m = length_m * 0.001 if length_m else None
        length_max_m = safe_float(rec.get(35))
        length_max_m = length_max_m * 0.001 if length_max_m else None
        width_m = safe_float(rec.get(36))
        width_m = width_m * 0.001 if width_m else None
        width_max_m = safe_float(rec.get(37))
        width_max_m = width_max_m * 0.001 if width_max_m else None
        height_m = safe_float(rec.get(38))
        height_m = height_m * 0.001 if height_m else None
        height_max_m = safe_float(rec.get(39))
        height_max_m = height_max_m * 0.001 if height_max_m else None
        footprint_m2 = safe_float(rec.get(40))
        footprint_m2 = footprint_m2 * 1e-6 if footprint_m2 else None
        pitch_m = safe_float(rec.get(41))
        pitch_m = pitch_m * 0.001 if pitch_m else None
        pin_length_m = safe_float(rec.get(42))
        pin_length_m = pin_length_m * 0.001 if pin_length_m else None
        pin_diam_m = safe_float(rec.get(43))
        pin_diam_m = pin_diam_m * 0.001 if pin_diam_m else None

        volume_m3 = None
        if length_m and width_m and height_m:
            volume_m3 = length_m * width_m * height_m

        rs = safe_float(rec.get(44))
        cs = safe_float(rec.get(45))
        ls = safe_float(rec.get(46))
        rp = safe_float(rec.get(47))

        asm_str = safe_str(rec.get(54))
        assembly = determine_assembly(asm_str)
        type_str = safe_str(rec.get(6))
        cap_case = safe_str(rec.get(5))

        tech_str = safe_str(rec.get(49))
        if tech_str and "CERAMIC" in tech_str.upper():
            technology = "MLCC Class I"
        else:
            technology = "Film Capacitor"

        if assembly == "SMT":
            shape_type = "SMD Chip"
        else:
            shape_type = determine_shape(type_str) if type_str else "Box type"

        packaging = safe_str(rec.get(63)) or ""
        vpe = safe_int(rec.get(64)) or 0
        wgu = safe_str(rec.get(66)) or "0090"

        v_str = f"{int(rated_voltage)}V" if rated_voltage else ""
        sc_str = f" {safety_class}" if safety_class else ""
        description = f"WE {series} {fmt_cap(c_raw)} {v_str}{sc_str} Safety Cap"

        entry = build_entry(
            part_number=order_code, series=series, technology=technology,
            description=description, case_str=cap_case or "",
            cap_nom=c_F, cap_min=cap_min, cap_max=cap_max,
            cap_min_longterm=cap_min if cap_min else (c_F or 0),
            rated_voltage=rated_voltage, df=df, df_freq=df_freq,
            insulation_resistance=riso_ohm or 0,
            esr=rs, esr_freq=None,
            ripple_current=0, ripple_freq=0, ripple_temp=None,
            temp_min=temp_min, temp_max=temp_max,
            width=width_m, width_max=width_max_m,
            length=length_m, length_max=length_max_m,
            height=height_m, height_max=height_max_m,
            pitch=pitch_m, pin_diameter=pin_diam_m, pin_length=pin_length_m,
            footprint_m2=footprint_m2, volume_m3=volume_m3,
            assembly=assembly, shape_type=shape_type,
            packaging=packaging, vpe=vpe, wgu=wgu,
            rs=rs, cs=cs, ls=ls, riso=rp
        )

        # Enrich description
        desc = entry["manufacturerInfo"]["datasheetInfo"]["part"]["matchcodeDescription"]
        if safety_class:
            desc += f" [{safety_class}]"
        if impulse_voltage:
            desc += f" Uimp={int(impulse_voltage)}V"
        df_parts = []
        if df_1k is not None:
            df_parts.append(f"DF@1kHz={df_1k}%")
        if df_10k is not None:
            df_parts.append(f"DF@10kHz={df_10k}%")
        if df_100k is not None:
            df_parts.append(f"DF@100kHz={df_100k}%")
        if df_1M is not None:
            df_parts.append(f"DF@1MHz={df_1M}%")
        if df_parts:
            desc += " " + " ".join(df_parts)
        if ur_dc and ur_ac and ur_dc != ur_ac:
            desc += f" UR(DC)={int(ur_dc)}V"
        entry["manufacturerInfo"]["datasheetInfo"]["part"]["matchcodeDescription"] = desc

        entries.append(entry)

    print(f"Safety: {len(records)} rows, {skipped_internal} internalViewOnly, {skipped_dup} duplicates, {len(entries)} new entries")
    return entries


# ---------------------------------------------------------------------------
# DC-Film caps
# ---------------------------------------------------------------------------
def convert_dc_film(existing_pns):
    records, headers, internal, conv = read_sheet(
        SRC_DIR / "Caps_DC-Film.xlsx", "Caps_DC-Film"
    )
    entries = []
    skipped_internal = 0
    skipped_dup = 0

    for rec in records:
        ivo = safe_str(rec.get(4))
        if ivo and ivo.upper() not in ("", "N", "NO", "FALSE"):
            skipped_internal += 1
            continue

        order_code = safe_str(rec.get(3))
        if not order_code:
            continue
        if order_code in existing_pns:
            skipped_dup += 1
            continue

        series = safe_str(rec.get(2))
        c_raw = safe_float(rec.get(9))
        c_F = c_raw * 1e-12 if c_raw is not None else None

        tol_raw = safe_float(rec.get(10))
        cap_min = c_F * (1 - abs(tol_raw) / 100.0) if c_F and tol_raw else None
        cap_max = c_F * (1 + abs(tol_raw) / 100.0) if c_F and tol_raw else None

        ur = safe_float(rec.get(11))

        df_1k = safe_float(rec.get(13))
        df_10k = safe_float(rec.get(14))
        df_100k = safe_float(rec.get(15))
        df = df_1k if df_1k is not None else (df_10k if df_10k is not None else df_100k)
        df_freq = 1000 if df_1k is not None else (10000 if df_10k is not None else (100000 if df_100k is not None else 0))

        riso_raw = safe_float(rec.get(16))
        riso_ohm = riso_raw * 1e9 if riso_raw is not None else None

        esr_raw = safe_float(rec.get(17))
        esr_ohm = esr_raw * 0.001 if esr_raw is not None else None
        esr_freq = parse_freq_str(rec.get(18))

        ripple_current = safe_float(rec.get(19))
        temp_min = safe_float(rec.get(23))
        temp_max = safe_float(rec.get(25))

        pitch_m = safe_float(rec.get(27))
        pitch_m = pitch_m * 0.001 if pitch_m else None
        pin_length_m = safe_float(rec.get(29))
        pin_length_m = pin_length_m * 0.001 if pin_length_m else None
        pin_diam_m = safe_float(rec.get(30))
        pin_diam_m = pin_diam_m * 0.001 if pin_diam_m else None
        length_m = safe_float(rec.get(31))
        length_m = length_m * 0.001 if length_m else None
        length_max_m = safe_float(rec.get(32))
        length_max_m = length_max_m * 0.001 if length_max_m else None
        width_m = safe_float(rec.get(33))
        width_m = width_m * 0.001 if width_m else None
        width_max_m = safe_float(rec.get(34))
        width_max_m = width_max_m * 0.001 if width_max_m else None
        height_m = safe_float(rec.get(35))
        height_m = height_m * 0.001 if height_m else None
        height_max_m = safe_float(rec.get(36))
        height_max_m = height_max_m * 0.001 if height_max_m else None
        footprint_m2 = safe_float(rec.get(37))
        footprint_m2 = footprint_m2 * 1e-6 if footprint_m2 else None

        volume_m3 = None
        if length_m and width_m and height_m:
            volume_m3 = length_m * width_m * height_m

        rs = safe_float(rec.get(38))
        cs = safe_float(rec.get(39))
        ls = safe_float(rec.get(40))
        rp = safe_float(rec.get(41))

        asm_str = safe_str(rec.get(49))
        assembly = determine_assembly(asm_str)
        type_str = safe_str(rec.get(6))
        shape_type = determine_shape(type_str)
        cap_case = safe_str(rec.get(5))

        packaging = safe_str(rec.get(54)) or ""
        vpe = safe_int(rec.get(55)) or 0
        wgu = safe_str(rec.get(57)) or "0090"

        v_str = f"{int(ur)}V" if ur else ""
        description = f"WE {series} {fmt_cap(c_raw)} {v_str} DC Film Cap"

        entry = build_entry(
            part_number=order_code, series=series, technology="Film Capacitor",
            description=description, case_str=cap_case or "",
            cap_nom=c_F, cap_min=cap_min, cap_max=cap_max,
            cap_min_longterm=cap_min if cap_min else (c_F or 0),
            rated_voltage=ur, df=df, df_freq=df_freq,
            insulation_resistance=riso_ohm or 0,
            esr=esr_ohm, esr_freq=esr_freq,
            ripple_current=ripple_current or 0,
            ripple_freq=10000 if ripple_current else 0,
            ripple_temp=70.0 if ripple_current else None,
            temp_min=temp_min, temp_max=temp_max,
            width=width_m, width_max=width_max_m,
            length=length_m, length_max=length_max_m,
            height=height_m, height_max=height_max_m,
            pitch=pitch_m, pin_diameter=pin_diam_m, pin_length=pin_length_m,
            footprint_m2=footprint_m2, volume_m3=volume_m3,
            assembly=assembly, shape_type=shape_type,
            packaging=packaging, vpe=vpe, wgu=wgu,
            rs=rs, cs=cs, ls=ls, riso=rp
        )

        df_parts = []
        if df_1k is not None:
            df_parts.append(f"DF@1kHz={df_1k}%")
        if df_10k is not None:
            df_parts.append(f"DF@10kHz={df_10k}%")
        if df_100k is not None:
            df_parts.append(f"DF@100kHz={df_100k}%")
        if df_parts:
            entry["manufacturerInfo"]["datasheetInfo"]["part"]["matchcodeDescription"] += " " + " ".join(df_parts)

        entries.append(entry)

    print(f"DC-Film: {len(records)} rows, {skipped_internal} internalViewOnly, {skipped_dup} duplicates, {len(entries)} new entries")
    return entries


# ---------------------------------------------------------------------------
# DCLink caps
# ---------------------------------------------------------------------------
def convert_dclink(existing_pns):
    records, headers, internal, conv = read_sheet(
        SRC_DIR / "Caps_DCLink.xlsx", "Caps_DCLink"
    )
    entries = []
    skipped_internal = 0
    skipped_dup = 0

    for rec in records:
        ivo = safe_str(rec.get(4))
        if ivo and ivo.upper() not in ("", "N", "NO", "FALSE"):
            skipped_internal += 1
            continue

        order_code = safe_str(rec.get(3))
        if not order_code:
            continue
        if order_code in existing_pns:
            skipped_dup += 1
            continue

        series = safe_str(rec.get(2))
        c_raw = safe_float(rec.get(9))
        c_F = c_raw * 1e-12 if c_raw is not None else None

        tol_raw = safe_float(rec.get(10))
        cap_min = c_F * (1 - abs(tol_raw) / 100.0) if c_F and tol_raw else None
        cap_max = c_F * (1 + abs(tol_raw) / 100.0) if c_F and tol_raw else None

        ur_85 = safe_float(rec.get(11))
        ur_105 = safe_float(rec.get(12))

        df_1k = safe_float(rec.get(14))
        df_10k = safe_float(rec.get(15))
        df_100k = safe_float(rec.get(16))
        df = df_1k if df_1k is not None else (df_10k if df_10k is not None else df_100k)
        df_freq = 1000 if df_1k is not None else (10000 if df_10k is not None else (100000 if df_100k is not None else 0))

        riso_raw = safe_float(rec.get(17))
        riso_ohm = riso_raw * 1e9 if riso_raw is not None else None

        esr_raw = safe_float(rec.get(18))
        esr_ohm = esr_raw * 0.001 if esr_raw is not None else None
        esr_freq = parse_freq_str(rec.get(19))

        ripple_current = safe_float(rec.get(20))
        ipeak = safe_float(rec.get(21))

        temp_min = safe_float(rec.get(25))
        temp_max = safe_float(rec.get(27))

        pitch_m = safe_float(rec.get(30))
        pitch_m = pitch_m * 0.001 if pitch_m else None
        pin_length_m = safe_float(rec.get(32))
        pin_length_m = pin_length_m * 0.001 if pin_length_m else None
        pin_diam_m = safe_float(rec.get(33))
        pin_diam_m = pin_diam_m * 0.001 if pin_diam_m else None
        length_m = safe_float(rec.get(34))
        length_m = length_m * 0.001 if length_m else None
        length_max_m = safe_float(rec.get(35))
        length_max_m = length_max_m * 0.001 if length_max_m else None
        width_m = safe_float(rec.get(36))
        width_m = width_m * 0.001 if width_m else None
        width_max_m = safe_float(rec.get(37))
        width_max_m = width_max_m * 0.001 if width_max_m else None
        height_m = safe_float(rec.get(38))
        height_m = height_m * 0.001 if height_m else None
        height_max_m = safe_float(rec.get(39))
        height_max_m = height_max_m * 0.001 if height_max_m else None
        footprint_m2 = safe_float(rec.get(40))
        footprint_m2 = footprint_m2 * 1e-6 if footprint_m2 else None

        volume_m3 = None
        if length_m and width_m and height_m:
            volume_m3 = length_m * width_m * height_m

        rs = safe_float(rec.get(41))
        cs = safe_float(rec.get(42))
        ls = safe_float(rec.get(43))
        rp = safe_float(rec.get(44))

        asm_str = safe_str(rec.get(52))
        assembly = determine_assembly(asm_str)
        type_str = safe_str(rec.get(6))
        shape_type = determine_shape(type_str)
        cap_case = safe_str(rec.get(5))

        packaging = safe_str(rec.get(57)) or ""
        vpe = safe_int(rec.get(58)) or 0
        wgu = safe_str(rec.get(60)) or "0090"

        v_str = f"{int(ur_85)}V" if ur_85 else ""
        description = f"WE {series} {fmt_cap(c_raw)} {v_str} DC-Link Cap"

        entry = build_entry(
            part_number=order_code, series=series, technology="Film Capacitor",
            description=description, case_str=cap_case or "",
            cap_nom=c_F, cap_min=cap_min, cap_max=cap_max,
            cap_min_longterm=cap_min if cap_min else (c_F or 0),
            rated_voltage=ur_85, df=df, df_freq=df_freq,
            insulation_resistance=riso_ohm or 0,
            esr=esr_ohm, esr_freq=esr_freq,
            ripple_current=ripple_current or 0,
            ripple_freq=10000 if ripple_current else 0,
            ripple_temp=70.0 if ripple_current else None,
            temp_min=temp_min, temp_max=temp_max,
            width=width_m, width_max=width_max_m,
            length=length_m, length_max=length_max_m,
            height=height_m, height_max=height_max_m,
            pitch=pitch_m, pin_diameter=pin_diam_m, pin_length=pin_length_m,
            footprint_m2=footprint_m2, volume_m3=volume_m3,
            assembly=assembly, shape_type=shape_type,
            packaging=packaging, vpe=vpe, wgu=wgu,
            rs=rs, cs=cs, ls=ls, riso=rp
        )

        df_parts = []
        if df_1k is not None:
            df_parts.append(f"DF@1kHz={df_1k}%")
        if df_10k is not None:
            df_parts.append(f"DF@10kHz={df_10k}%")
        if df_100k is not None:
            df_parts.append(f"DF@100kHz={df_100k}%")
        if df_parts:
            entry["manufacturerInfo"]["datasheetInfo"]["part"]["matchcodeDescription"] += " " + " ".join(df_parts)
        if ur_105:
            entry["manufacturerInfo"]["datasheetInfo"]["part"]["matchcodeDescription"] += f" UR@105C={int(ur_105)}V"
        if ipeak:
            entry["manufacturerInfo"]["datasheetInfo"]["part"]["matchcodeDescription"] += f" Ipeak={ipeak}A"

        entries.append(entry)

    print(f"DCLink: {len(records)} rows, {skipped_internal} internalViewOnly, {skipped_dup} duplicates, {len(entries)} new entries")
    return entries


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    existing_pns = load_existing_part_numbers()
    print(f"Existing parts in NDJSON: {len(existing_pns)}")

    all_entries = []

    safety_entries = convert_safety(existing_pns)
    for e in safety_entries:
        existing_pns.add(e["manufacturerInfo"]["datasheetInfo"]["part"]["partNumber"])
    all_entries.extend(safety_entries)

    dc_film_entries = convert_dc_film(existing_pns)
    for e in dc_film_entries:
        existing_pns.add(e["manufacturerInfo"]["datasheetInfo"]["part"]["partNumber"])
    all_entries.extend(dc_film_entries)

    dclink_entries = convert_dclink(existing_pns)
    all_entries.extend(dclink_entries)

    if all_entries:
        with open(NDJSON_PATH, 'a') as f:
            for entry in all_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        print(f"\nAppended {len(all_entries)} entries to {NDJSON_PATH}")
    else:
        print("\nNo new entries to append.")

    total_lines = 0
    with open(NDJSON_PATH, 'r') as f:
        for line in f:
            if line.strip():
                total_lines += 1
    print(f"Total lines in NDJSON: {total_lines}")


if __name__ == "__main__":
    main()
