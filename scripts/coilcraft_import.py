#!/usr/bin/env python3
"""Map scraped Coilcraft power inductors (/tmp/coilcraft_power.json) to MAS
magnetic docs, dedupe against existing TAS Coilcraft parts (by reference AND
part.partNumber, plus suffix-variant prefix match), validate, write candidates.

No fabricated values: a field is emitted only when the source cell has data.
"""
import json, re, sys
from pathlib import Path

REPO = Path('/home/alf/PSMA/TAS')
sys.path.insert(0, str(REPO / 'scripts'))
from tdk_meister_import import build_validators

SRC = json.load(open('/tmp/coilcraft_power.json'))
COLS = [c['id'] for c in SRC['columns']]          # inductance,dcrmax,isat,irms,parttempmax,voltagerating,length,width,height,mounting,aecgrade,skuprice
IDX = {c: i for i, c in enumerate(COLS)}

def cellnum(row, col):
    i = IDX.get(col)
    if i is None or i >= len(row.get('cells', [])):
        return None
    v = (row['cells'][i].get('content') or '').strip()
    if v in ('', '-', 'N/A', '–'):
        return None
    m = re.match(r'-?[0-9]*\.?[0-9]+', v.replace(',', ''))
    return float(m.group(0)) if m else None

def celltext(row, col):
    i = IDX.get(col)
    if i is None or i >= len(row.get('cells', [])):
        return None
    return (row['cells'][i].get('content') or '').strip() or None

def existing_ids():
    ids = set()
    for l in (REPO / 'data' / 'magnetics.ndjson').open():
        l = l.strip()
        if not l:
            continue
        try:
            mi = json.loads(l)['magnetic']['manufacturerInfo']
        except Exception:
            continue
        if (mi.get('name') or '') != 'Coilcraft':
            continue
        if mi.get('reference'):
            ids.add(mi['reference'])
        pn = mi.get('datasheetInfo', {}).get('part', {}).get('partNumber')
        if pn:
            ids.add(pn)
    return ids

def map_row(row):
    pn = row.get('skuName')
    if not pn:
        return None
    el = {'subtype': 'inductor'}
    L = cellnum(row, 'inductance')      # µH
    if L is not None: el['inductance'] = {'nominal': L * 1e-6}
    dcr = cellnum(row, 'dcrmax')        # mΩ
    if dcr is not None: el['dcResistance'] = {'maximum': dcr / 1000.0}
    isat = cellnum(row, 'isat')
    if isat is not None: el['saturationCurrentPeak'] = isat
    irms = cellnum(row, 'irms')
    if irms is not None: el['ratedCurrents'] = [irms]
    if len(el) == 1:
        return None                     # no electrical data -> skip

    part = {'partNumber': pn}
    if (celltext(row, 'aecgrade') or '0') not in ('0', '', None):
        part['automotive'] = True

    dsinfo = {'part': part, 'electrical': [el]}
    mech = {}
    for col, key in (('length', 'length'), ('width', 'width'), ('height', 'height')):
        v = cellnum(row, col)           # mm
        if v is not None: mech[key] = {'nominal': v / 1000.0}
    if (celltext(row, 'mounting') or '').upper() == 'SM':
        mech['assemblyType'] = 'SMD'
    if mech: dsinfo['mechanical'] = mech
    tmax = cellnum(row, 'parttempmax')
    if tmax is not None:
        dsinfo['thermal'] = {'operatingTemperature': {'maximum': tmax}}

    url = (row.get('productUrl') or '').split('?')[0]
    mi = {'name': 'Coilcraft', 'reference': pn, 'status': 'production',
          'datasheetInfo': dsinfo}
    if url:
        mi['datasheetUrl'] = 'https://www.coilcraft.com' + url
    fam = re.match(r'[A-Za-z]+', pn)
    if fam: mi['family'] = fam.group(0)
    return {'magnetic': {'manufacturerInfo': mi}}

def main():
    ids = existing_ids()
    mas_v, _, _ = build_validators()
    out = open('/tmp/coilcraft_new_magnetics.ndjson', 'w')
    from collections import Counter
    st = Counter()
    emitted = set()
    for row in SRC['rows']:
        pn = row.get('skuName')
        if not pn:
            st['no_pn'] += 1; continue
        if pn in emitted:
            st['dup_row'] += 1; continue
        if pn in ids or any(e.startswith(pn) for e in ids):   # exact or suffix-variant
            st['skip_existing'] += 1; continue
        doc = map_row(row)
        if doc is None:
            st['no_data'] += 1; continue
        errs = list(mas_v.iter_errors(doc['magnetic']))
        if errs:
            st['invalid'] += 1
            if st['invalid'] <= 5:
                print('INVALID', pn, errs[0].message[:80], file=sys.stderr)
            continue
        out.write(json.dumps(doc, ensure_ascii=False) + '\n')
        emitted.add(pn)
        st['ok'] += 1
    out.close()
    print('STATS:', dict(st), file=sys.stderr)

if __name__ == '__main__':
    main()
