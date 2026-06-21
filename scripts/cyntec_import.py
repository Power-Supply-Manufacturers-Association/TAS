#!/usr/bin/env python3
"""Import Cyntec inductor parametric exports (Downloads/SearchResult*.xlsx) into
MAS magnetic docs. Two files: CMLB family (897) and VAMV family (344).
Dedupe against existing TAS magnetics (reference + part.partNumber). Validate.
No fabricated values; datasheet URL not present in the exports (omitted)."""
import json, re, sys
from pathlib import Path
import openpyxl

REPO = Path('/home/alf/PSMA/TAS')
sys.path.insert(0, str(REPO / 'scripts'))
from tdk_meister_import import build_validators

FILES = ["/mnt/c/Users/Alfonso/Downloads/SearchResult.xlsx",
         "/mnt/c/Users/Alfonso/Downloads/SearchResult (1).xlsx"]

def norm(h):
    return re.sub(r'[^a-z0-9@]', '', str(h or '').lower())

def num(v):
    if v is None: return None
    s = str(v).strip()
    if s in ('', '-', 'None', 'N/A'): return None
    m = re.match(r'-?[0-9]*\.?[0-9]+', s.replace(',', ''))
    return float(m.group(0)) if m else None

def col(headers, *keys):
    """index of first header whose normalized form contains all tokens of any key."""
    for i, h in enumerate(headers):
        n = norm(h)
        for key in keys:
            toks = [norm(t) for t in key]
            if all(t in n for t in toks):
                return i
    return None

def existing_ids():
    ids = set()
    for l in (REPO / 'data' / 'magnetics.ndjson').open():
        l = l.strip()
        if not l: continue
        try: mi = json.loads(l)['magnetic']['manufacturerInfo']
        except: continue
        if mi.get('reference'): ids.add(mi['reference'])
        pn = mi.get('datasheetInfo', {}).get('part', {}).get('partNumber')
        if pn: ids.add(pn)
    return ids

def main():
    ids = existing_ids()
    mas_v, _, _ = build_validators()
    out = open('/tmp/cyntec_new_magnetics.ndjson', 'w')
    from collections import Counter
    st = Counter(); emitted = set()
    for fn in FILES:
        wb = openpyxl.load_workbook(fn, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        H = list(next(it))
        ci = {
            'pn':   col(H, ['part', 'number']),
            'L':    col(H, ['length']),
            'W':    col(H, ['width']),
            'Hgt':  col(H, ['height']),
            'ind':  col(H, ['inductance']),
            'dcr':  col(H, ['dcr']),
            'idc':  col(H, ['idc']),
            'isat': col(H, ['isat@25'], ['isat']),
            'tmin': col(H, ['templowest']),
            'tmax': col(H, ['temphighest']),
        }
        for r in it:
            if ci['pn'] is None or ci['pn'] >= len(r): continue
            pn = r[ci['pn']]
            if not pn: continue
            pn = str(pn).strip()
            if pn in emitted: st['dup_row'] += 1; continue
            if pn in ids: st['skip_existing'] += 1; continue
            def g(k):
                i = ci[k]; return num(r[i]) if (i is not None and i < len(r)) else None
            el = {'subtype': 'inductor'}
            if g('ind') is not None: el['inductance'] = {'nominal': g('ind') * 1e-6}
            if g('dcr') is not None: el['dcResistance'] = {'nominal': g('dcr') / 1000.0}
            if g('idc') is not None: el['ratedCurrents'] = [g('idc')]
            if g('isat') is not None: el['saturationCurrentPeak'] = g('isat')
            if len(el) == 1: st['no_data'] += 1; continue
            dsinfo = {'part': {'partNumber': pn}, 'electrical': [el]}
            mech = {}
            for k, key in (('L', 'length'), ('W', 'width'), ('Hgt', 'height')):
                if g(k) is not None: mech[key] = {'nominal': g(k) / 1000.0}
            if mech: dsinfo['mechanical'] = mech
            if g('tmin') is not None or g('tmax') is not None:
                ot = {}
                if g('tmin') is not None: ot['minimum'] = g('tmin')
                if g('tmax') is not None: ot['maximum'] = g('tmax')
                dsinfo['thermal'] = {'operatingTemperature': ot}
            fam = re.match(r'[A-Za-z]+', pn)
            mi = {'name': 'Cyntec', 'reference': pn, 'status': 'production',
                  'datasheetInfo': dsinfo}
            if fam: mi['family'] = fam.group(0)
            doc = {'magnetic': {'manufacturerInfo': mi}}
            errs = list(mas_v.iter_errors(doc['magnetic']))
            if errs:
                st['invalid'] += 1
                if st['invalid'] <= 5: print('INVALID', pn, errs[0].message[:70], file=sys.stderr)
                continue
            out.write(json.dumps(doc, ensure_ascii=False) + '\n')
            emitted.add(pn); st['ok'] += 1
        wb.close()
    out.close()
    print('STATS:', dict(st), file=sys.stderr)

if __name__ == '__main__':
    main()
