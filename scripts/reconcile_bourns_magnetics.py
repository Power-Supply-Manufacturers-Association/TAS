#!/usr/bin/env python3
"""Reconcile Bourns inductor records in data/magnetics.ndjson against the
authoritative Bourns parametric export (bourns-parametric-...-0925.xlsx).

Only records whose part number is in the sheet are touched, and only fields that
DISAGREE with the sheet (beyond a 2% tolerance) or are MISSING are changed —
correct values are left alone. Fixes the primary spec inductance plus
dcResistance / ratedCurrents / saturationCurrentPeak / selfResonantFrequency.

Run with --apply to write; default is a dry-run report.
Parts not in the sheet (Bourns transformers / modules) are not handled here.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / 'data'
SHEET = Path("/mnt/c/Users/Alfonso/Downloads/bourns-parametric-062026-0925.xlsx")
APPLY = '--apply' in sys.argv
TOL = 0.02


def num(x):
    if x is None:
        return None
    s = str(x).replace(',', '').strip()
    if s.upper() in ('N/A', 'NA', '', '-'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_sheet():
    import openpyxl
    wb = openpyxl.load_workbook(SHEET, read_only=True, data_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    hdr = [str(h).strip() if h is not None else '' for h in next(it)]
    H = {h: i for i, h in enumerate(hdr)}
    out = {}
    for r in it:
        if not r or r[0] is None:
            continue
        pn = str(r[0]).strip()
        out[pn] = {
            'L': num(r[H['Inductance (μH)']]),                # µH
            'tol': num(r[H['Tolerance']]),                    # %
            'Irated': num(r[H['Current Rating (A)']]),
            'Isat': num(r[H['Current Saturation (A)']]),
            'DCR': num(r[H['DCR (Ohms)']]),
            'SRF': num(r[H['SRF (MHz)']]),                    # MHz
        }
    wb.close()
    return out


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= TOL * abs(b)


def main():
    sheet = load_sheet()
    src = DATA / 'magnetics.ndjson'
    out_lines = []
    fixes = []           # (pn, field, old, new)
    touched = 0
    for raw in src.open():
        raw = raw.rstrip('\n')
        if not raw.strip():
            continue
        rec = json.loads(raw)
        mi = rec.get('magnetic', {}).get('manufacturerInfo', {})
        pn = mi.get('reference')
        s = sheet.get(pn) if mi.get('name') == 'Bourns' else None
        if not s:
            out_lines.append(raw)
            continue
        el_list = mi.setdefault('datasheetInfo', {}).setdefault('electrical', [])
        if not el_list:
            el_list.append({'subtype': 'inductor'})
        el = el_list[0]
        # Only inductor op-points map to the inductor parametric sheet. Skip
        # commonModeChoke / chipBead subtypes (different field schema; stamping
        # inductor fields like dcResistance on them is invalid).
        if el.get('subtype', 'inductor') != 'inductor':
            out_lines.append(raw)
            continue
        changed = False

        # inductance (µH -> H)
        if s['L'] is not None:
            newL = s['L'] * 1e-6
            cur = el.get('inductance')
            curnom = cur.get('nominal') if isinstance(cur, dict) else (cur if isinstance(cur, (int, float)) else None)
            if not close(curnom, newL):
                d = {'nominal': newL}
                if s['tol']:
                    d['minimum'] = newL * (1 - s['tol'] / 100.0)
                    d['maximum'] = newL * (1 + s['tol'] / 100.0)
                el['inductance'] = d
                fixes.append((pn, 'inductance', curnom, newL)); changed = True

        # dcResistance (Ohms) -> dcResistance.maximum
        if s['DCR'] is not None:
            cur = el.get('dcResistance', {})
            curv = cur.get('maximum') if isinstance(cur, dict) else None
            if not close(curv, s['DCR']):
                el.setdefault('dcResistance', {})['maximum'] = s['DCR']
                fixes.append((pn, 'dcResistance.max', curv, s['DCR'])); changed = True

        # ratedCurrents
        if s['Irated'] is not None:
            cur = el.get('ratedCurrents')
            curv = cur[0] if isinstance(cur, list) and cur else None
            if not close(curv, s['Irated']):
                el['ratedCurrents'] = [s['Irated']]
                fixes.append((pn, 'ratedCurrents', curv, s['Irated'])); changed = True

        # saturationCurrentPeak
        if s['Isat'] is not None:
            curv = el.get('saturationCurrentPeak')
            if not close(curv, s['Isat']):
                el['saturationCurrentPeak'] = s['Isat']
                fixes.append((pn, 'saturationCurrentPeak', curv, s['Isat'])); changed = True

        # selfResonantFrequency (MHz -> Hz)
        if s['SRF'] is not None:
            newF = s['SRF'] * 1e6
            curv = el.get('selfResonantFrequency')
            if not close(curv, newF):
                el['selfResonantFrequency'] = newF
                fixes.append((pn, 'SRF', curv, newF)); changed = True

        if changed:
            touched += 1
        out_lines.append(json.dumps(rec, ensure_ascii=False))

    by_field = {}
    for _, f, _, _ in fixes:
        by_field[f] = by_field.get(f, 0) + 1
    print(f"records touched: {touched}; total field fixes: {len(fixes)} {by_field}")
    for pn, f, old, new in fixes[:25]:
        print(f"  {pn} {f}: {old} -> {new}")
    if APPLY:
        src.write_text('\n'.join(out_lines) + '\n')
        print("APPLIED to magnetics.ndjson")
    else:
        print("(dry-run; pass --apply to write)")


if __name__ == '__main__':
    main()
