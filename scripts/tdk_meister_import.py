#!/usr/bin/env python3
"""Import new TDK Meister parts into TAS as MAS (magnetics) and CAS (capacitors).

Source: /tmp/tdk_raw.jsonl  (produced by /tmp/tdk_extract_raw.py from the
TDK Meister Access DB C:\\ProgramData\\TDK\\TDKMeister\\tdkData\\TstDB.tmdb).

Pipeline:
  1. load raw TDK parts + their spec dicts
  2. dedupe against TDK references already present in TAS/data/*.ndjson
  3. map each NEW part -> {"magnetic": ...} or {"capacitor": ...}
  4. validate every candidate against MAS/CAS schemas (same registry as tests)
  5. write candidates to /tmp/tdk_new_magnetics.ndjson / _capacitors.ndjson

No fabricated values: a field is emitted only when the source provides it.
Parts that cannot supply a schema-required field are quarantined and reported.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
from collections import Counter, defaultdict

REPO = Path('/home/alf/PSMA/TAS')
PROTEUS = REPO.parent
RAW = Path('/tmp/tdk_raw.jsonl')

# ---------------------------------------------------------------------------
# spec helpers
# ---------------------------------------------------------------------------

def _entries(specs, sid):
    e = specs.get(sid)
    if e is None:
        return []
    return e if isinstance(e, list) else [e]

def num(specs, sid):
    """First numeric value for a spec id, else None."""
    for x in _entries(specs, sid):
        if 'num' in x:
            return x['num']
    return None

def disp(specs, sid):
    for x in _entries(specs, sid):
        if 'display' in x: return x['display']
        if 'text' in x: return x['text']
    return None

def mm(v):
    return None if v is None else v / 1000.0

def dim(specs, nom_id, min_id=None, max_id=None):
    """dimensionWithTolerance in metres from mm spec ids; None if empty."""
    d = {}
    n = num(specs, nom_id)
    if n is not None: d['nominal'] = mm(n)
    if min_id and num(specs, min_id) is not None: d['minimum'] = mm(num(specs, min_id))
    if max_id and num(specs, max_id) is not None: d['maximum'] = mm(num(specs, max_id))
    return d or None

PCT = re.compile(r'±?\s*([0-9.]+)\s*%')

def tol_minmax(nominal, tol_text):
    """Return (min,max) from a '±X%' tolerance string, else (None,None)."""
    if nominal is None or not tol_text:
        return None, None
    m = PCT.search(tol_text)
    if not m:
        return None, None
    p = float(m.group(1)) / 100.0
    return nominal * (1 - p), nominal * (1 + p)

EIA = re.compile(r'\[EIA\s+([^\]]+)\]')

def case_code(specs):
    for sid in ('200000000', '203000030'):
        s = disp(specs, sid)
        if s:
            m = EIA.search(s)
            if m: return m.group(1).strip()
    return None

def family_of(rec):
    s = rec.get('series') or ''
    if s and s.lower() != 'dummy':
        return s
    m = re.match(r'[A-Za-z]+', rec['part_no'])
    return m.group(0) if m else None

def datasheet_url(rec):
    sub = rec.get('url_substring')
    if sub:
        return f"https://product.tdk.com/en/search/{sub}/info?part_no={rec['part_no']}"
    return f"https://www.tdk.com/en/search/compass/part_no/{rec['part_no']}"

def status_of(rec):
    return 'obsolete' if rec.get('disabled') else 'production'

def temp_block(specs):
    lo, hi = num(specs, '400000060'), num(specs, '400000070')
    if lo is None and hi is None:
        return None
    ot = {}
    if lo is not None: ot['minimum'] = lo
    if hi is not None: ot['maximum'] = hi
    return {'operatingTemperature': ot}

def weight_dim(specs):
    g = num(specs, '400000050')          # grams
    return {'nominal': g / 1000.0} if g is not None else None

def mech_mas(specs):
    """MAS mechanical: length/width/height/diameter/weight (metres/kg)."""
    m = {}
    L = dim(specs, '200000040', '200000030', '200000050')
    W = dim(specs, '200000080', '200000070', '200000090')
    # height: prefer dedicated height nominal (120), else thickness (130)
    H = dim(specs, '200000120', '200000110')
    if H is None:
        t = num(specs, '200000130')
        if t is not None: H = {'nominal': mm(t)}
    D = dim(specs, '200000170')
    wt = weight_dim(specs)
    if L: m['length'] = L
    if W: m['width'] = W
    if H: m['height'] = H
    if D: m['diameter'] = D
    if wt: m['weight'] = wt
    return m or None

# ---------------------------------------------------------------------------
# MAS magnetic mapping
# ---------------------------------------------------------------------------

CHIPBEAD_CATS = {'beads', 'suppression-filter'}
INDUCTANCE_IDS = ('302000000', '303000430', '303000280')  # plain / line-filter / common-mode
DCR_IDS = ('302000050', '303000140')
RATED_I_IDS = ('302000060', '303000180')
SRF_IDS = ('302000130',)
IMPEDANCE_AT_FREQ_IDS = ('303000000',)  # impedance@100MHz

def first_num(specs, ids):
    for sid in ids:
        v = num(specs, sid)
        if v is not None:
            return v, sid
    return None, None

def map_magnetic(rec):
    specs = rec['specs']
    cat = rec['category2']
    electrical = {}
    dcr = first_num(specs, DCR_IDS)[0]
    rated = first_num(specs, RATED_I_IDS)[0]
    srf = first_num(specs, SRF_IDS)[0]
    if cat in CHIPBEAD_CATS:
        electrical['subtype'] = 'chipBead'
        imp = first_num(specs, IMPEDANCE_AT_FREQ_IDS)[0]
        if imp is not None:
            electrical['impedancePoints'] = [
                {'frequency': 1.0e8, 'impedance': {'magnitude': imp}}
            ]
        tol = disp(specs, '303000010')
        if tol:
            m = PCT.search(tol)
            if m: electrical['impedanceTolerance'] = float(m.group(1)) / 100.0
    else:
        electrical['subtype'] = 'inductor'
        ind, ind_id = first_num(specs, INDUCTANCE_IDS)
        if ind is not None:
            indd = {'nominal': ind}
            # inductance tolerance (302000010 for plain inductors)
            lo, hi = tol_minmax(ind, disp(specs, '302000010'))
            if lo is not None: indd['minimum'], indd['maximum'] = lo, hi
            electrical['inductance'] = indd
        if srf is not None:
            electrical['selfResonantFrequency'] = srf
    if dcr is not None:
        electrical['dcResistance'] = {'maximum': dcr}
    if rated is not None:
        electrical['ratedCurrents'] = [rated]

    part = {'partNumber': rec['part_no']}
    cc = case_code(specs)
    if cc: part['caseCode'] = cc

    mech = mech_mas(specs)
    # EOL redirect stub: no real electrical values and no mechanical data — TDK
    # keeps it only as a pointer to its replacement part. Not importable.
    if len(electrical) == 1 and not mech:
        return None
    dsinfo = {'part': part, 'electrical': [electrical]}
    if mech: dsinfo['mechanical'] = mech
    th = temp_block(specs)
    if th: dsinfo['thermal'] = th

    mi = {
        'name': 'TDK',
        'reference': rec['part_no'],
        'status': status_of(rec),
        'datasheetUrl': datasheet_url(rec),
        'datasheetInfo': dsinfo,
    }
    fam = family_of(rec)
    if fam: mi['family'] = fam
    return {'magnetic': {'manufacturerInfo': mi}}

# ---------------------------------------------------------------------------
# CAS capacitor mapping (ceramic only — all TDK Meister cap categories)
# ---------------------------------------------------------------------------

# Maps an EIA dielectric code or a TDK/JIS characteristic code to
# (technology enum, EIA dielectricCode-or-None). TDK/JIS single-letter codes
# (B, R high-K class-II; E, F very-high-K class-III) have no EIA equivalent, so
# dielectricCode is left None for them rather than inventing one.
TDK_CLASS = {
    # class I — temperature compensating
    'C0G': ('ceramic-class-1', 'C0G'), 'NP0': ('ceramic-class-1', 'NP0'),
    'NPO': ('ceramic-class-1', 'NP0'), 'CH': ('ceramic-class-1', 'CH'),
    'C0H': ('ceramic-class-1', 'C0H'), 'SL': ('ceramic-class-1', 'SL'),
    'U2J': ('ceramic-class-1', 'U2J'), 'C': ('ceramic-class-1', 'C0G'),
    # class II — stable high-K
    'X7R': ('ceramic-class-2', 'X7R'), 'X5R': ('ceramic-class-2', 'X5R'),
    'X6S': ('ceramic-class-2', 'X6S'), 'X7S': ('ceramic-class-2', 'X7S'),
    'X7T': ('ceramic-class-2', 'X7T'), 'X8R': ('ceramic-class-2', 'X8R'),
    'X8L': ('ceramic-class-2', 'X8L'), 'X6T': ('ceramic-class-2', 'X6T'),
    'X6S': ('ceramic-class-2', 'X6S'),
    'B': ('ceramic-class-2', None), 'R': ('ceramic-class-2', None),
    'JB': ('ceramic-class-2', None),    # TDK JIS B characteristic (X7R-like)
    # class III — very-high-K
    'Z5U': ('ceramic-class-3', 'Z5U'), 'Y5V': ('ceramic-class-3', 'Y5V'),
    'Y5U': ('ceramic-class-3', 'Y5U'),
    'E': ('ceramic-class-3', None), 'F': ('ceramic-class-3', None),
}
DIEL_IN_PN = re.compile(r'(C0G|NP0|NPO|X7R|X5R|X6S|X6T|X7S|X7T|X8R|X8L|Y5V|Y5U|Z5U|U2J|C0H|CH|SL)')
# TDK feedthrough (CKD…) encodes the characteristic letter right after the
# series/dimension 'J', before the 2-char voltage code, e.g. CKD710J*B*0G105.
FEEDTHRU_CHAR = re.compile(r'J([BCREF])[0-9][A-Z]')

CAP_ID = ('301000000', '301000491')        # Capacitance / Nominal Capacitance
VOLT_ID = ('301000030', '301000910', '301000050')  # Rated Voltage (DC); 050 = disc caps
TC_ID = '301000070'                         # Temperature characteristic code

CAP_ASSEMBLY = {
    'mlcc': 'SMT', 'feedthrough': 'SMT', 'ceralink': 'SMT',
    'lead-mlcc': 'THT', 'lead-disc': 'THT',
}
CAP_SHAPETYPE = {
    'mlcc': 'SMD Chip', 'feedthrough': 'SMD Chip', 'ceralink': 'SMD',
    'lead-mlcc': 'Radial', 'lead-disc': 'Radial Disc',
}

def diel_class(code, part_no, cat):
    if cat == 'ceralink':
        return 'ceramic-class-2', None     # antiferroelectric PLZT, class-II-like
    c = (code or '').upper().replace(' ', '')
    if c in TDK_CLASS:
        return TDK_CLASS[c]
    m = DIEL_IN_PN.search(c)              # EIA token embedded in a verbose code string
    if m:
        return TDK_CLASS.get(m.group(1), (None, None))
    if not c:                              # no temp-char spec: read it from the part number
        m = DIEL_IN_PN.search(part_no.upper())
        if m:
            return TDK_CLASS.get(m.group(1), (None, None))
        if cat == 'feedthrough':
            fm = FEEDTHRU_CHAR.search(part_no.upper())
            if fm:
                return TDK_CLASS.get(fm.group(1), (None, None))
    return (None, None)

def cap_dimensions(specs):
    d = {}
    L = dim(specs, '200000040', '200000030', '200000050')
    W = dim(specs, '200000080', '200000070', '200000090')
    H = dim(specs, '200000120', '200000110')
    T = num(specs, '200000130')
    D = dim(specs, '200000170')
    F = num(specs, '201000220')           # lead spacing -> pitch
    if L: d['length'] = L
    if W: d['width'] = W
    if H: d['height'] = H
    if T is not None: d['thickness'] = {'nominal': mm(T)}
    if D: d['diameter'] = D
    if F is not None: d['pitch'] = {'nominal': mm(F)}
    return d or None

def map_capacitor(rec):
    specs = rec['specs']
    cat = rec['category2']
    capf, _ = first_num(specs, CAP_ID)
    volt, _ = first_num(specs, VOLT_ID)
    if capf is None or volt is None:
        return None, 'missing capacitance or ratedVoltage'
    tech, diel = diel_class(disp(specs, TC_ID), rec['part_no'], cat)
    if tech is None:
        return None, 'undeterminable ceramic class'

    part = {'partNumber': rec['part_no'], 'technology': tech}
    if diel: part['dielectricCode'] = diel
    cc = case_code(specs)
    if cc: part['case'] = cc

    capd = {'nominal': capf}
    lo, hi = tol_minmax(capf, disp(specs, '301000010'))
    if lo is not None: capd['minimum'], capd['maximum'] = lo, hi
    electrical = {'capacitance': capd, 'ratedVoltage': volt}

    shape = {'assembly': CAP_ASSEMBLY[cat], 'shapeType': CAP_SHAPETYPE[cat]}
    mech = {'shape': shape}
    dims = cap_dimensions(specs)
    if dims: mech['dimensions'] = dims

    dsinfo = {'part': part, 'electrical': electrical, 'mechanical': mech}
    th = temp_block(specs)
    if th: dsinfo['thermal'] = {'temperature': th['operatingTemperature']}

    mi = {'name': 'TDK', 'reference': rec['part_no'], 'status': status_of(rec),
          'datasheetUrl': datasheet_url(rec), 'datasheetInfo': dsinfo}
    fam = family_of(rec)
    if fam: mi['family'] = fam
    return {'capacitor': {'manufacturerInfo': mi}}, None

# ---------------------------------------------------------------------------
# RAS varistor mapping (multilayer chip varistors)
# ---------------------------------------------------------------------------

def map_varistor(rec):
    s = rec['specs']
    vv = num(s, '305000110')      # varistor voltage V_1mA (nominal)
    clamp = num(s, '305000120')   # max clamping voltage
    surge = num(s, '305000100')   # max surge current 8/20us
    missing = [n for n, v in (('varistorVoltage', vv), ('clampingVoltage', clamp),
                              ('peakSurgeCurrent', surge)) if v is None]
    if missing:
        return None, 'missing ' + ','.join(missing)
    elec = {'varistorVoltage': {'nominal': vv}, 'clampingVoltage': clamp,
            'peakSurgeCurrent': surge, 'surgeWaveform': '8/20'}
    for key, sid in (('maxContinuousAcVoltage', '305000090'),
                     ('maxContinuousDcVoltage', '305000080'),
                     ('capacitance', '305000000')):
        v = num(s, sid)
        if v is not None:
            elec[key] = v

    part = {'partNumber': rec['part_no'], 'technology': 'multiLayer'}
    cc = case_code(s)
    if cc: part['case'] = cc

    dsinfo = {'part': part, 'electrical': elec}
    mech = {}
    L = dim(s, '200000040', '200000030', '200000050')
    W = dim(s, '200000080', '200000070', '200000090')
    H = dim(s, '200000120', '200000110')
    if H is None:
        t = num(s, '200000130')
        if t is not None: H = {'nominal': mm(t)}
    if L: mech['length'] = L
    if W: mech['width'] = W
    if H: mech['height'] = H
    if cc: mech['case'] = cc
    wg = num(s, '400000050')          # grams; RAS weight is a plain number (kg)
    if wg is not None: mech['weight'] = wg / 1000.0
    if mech: dsinfo['mechanical'] = mech
    lo, hi = num(s, '400000060'), num(s, '400000070')
    if lo is not None or hi is not None:
        ot = {}
        if lo is not None: ot['minimum'] = lo
        if hi is not None: ot['maximum'] = hi
        dsinfo['thermal'] = {'operatingTemperature': ot}

    mi = {'name': 'TDK', 'reference': rec['part_no'], 'status': status_of(rec),
          'datasheetUrl': datasheet_url(rec), 'datasheetInfo': dsinfo}
    fam = family_of(rec)
    if fam: mi['family'] = fam
    return {'varistor': {'manufacturerInfo': mi}}, None

# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------

MAS_CATS = {'smd', 'beads', 'cmf_cmc', 'line-filter', '3tf',
            'suppression-filter', 'lan', 'plc'}
CAS_CATS = {'mlcc', 'lead-mlcc', 'lead-disc', 'ceralink', 'feedthrough'}
VARISTOR_CATS = {'varistor_ctvs'}
# excluded / quarantined separately: esd-notch, chip_protector (no PEAS home)

# ---------------------------------------------------------------------------
# existing-reference dedupe
# ---------------------------------------------------------------------------

def existing_tdk_refs():
    refs = set()
    for fn in ('magnetics', 'capacitors', 'resistors', 'varistors'):
        p = REPO / 'data' / f'{fn}.ndjson'
        if not p.exists():
            continue
        for line in p.open():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            stack = [r]
            while stack:
                o = stack.pop()
                if isinstance(o, dict):
                    mi = o.get('manufacturerInfo')
                    if isinstance(mi, dict) and str(mi.get('name', '')).upper() == 'TDK':
                        if mi.get('reference'):
                            refs.add(mi['reference'])
                    stack.extend(o.values())
                elif isinstance(o, list):
                    stack.extend(o)
    return refs

# ---------------------------------------------------------------------------
# validation registry (mirrors tests/test_data.py)
# ---------------------------------------------------------------------------

def build_validators():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    by_id, by_path = {}, {}
    for repo_name in ('PEAS', 'SAS', 'CAS', 'RAS', 'MAS'):
        d = PROTEUS / repo_name / 'schemas'
        if not d.is_dir():
            continue
        for p in d.rglob('*.json'):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            p = p.resolve()
            by_path[p] = s
            if s.get('$id'):
                by_id[s['$id']] = s
    META = {'$schema', '$id', 'title', 'description', '$comment'}
    for sid, s in list(by_id.items()):
        if set(s) - META != {'$ref'}:
            continue
        path = next((p for p, v in by_path.items() if v is s), None)
        if path is None:
            continue
        tgt = by_path.get((path.parent / s['$ref']).resolve())
        if tgt is None:
            continue
        inl = {k: v for k, v in tgt.items() if k not in ('$id', '$schema')}
        inl['$id'] = sid
        inl['$schema'] = s.get('$schema', 'https://json-schema.org/draft/2020-12/schema')
        by_id[sid] = inl
    reg = Registry().with_resources(
        [(sid, Resource(contents=s, specification=DRAFT202012)) for sid, s in by_id.items()]
    )
    mas = json.loads((PROTEUS / 'MAS' / 'schemas' / 'magnetic.json').read_text())
    cas = json.loads((PROTEUS / 'CAS' / 'schemas' / 'capacitor.json').read_text())
    ras = json.loads((PROTEUS / 'RAS' / 'schemas' / 'varistor.json').read_text())
    return (Draft202012Validator(mas, registry=reg),
            Draft202012Validator(cas, registry=reg),
            Draft202012Validator(ras, registry=reg))

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    existing = existing_tdk_refs()
    print(f"existing TDK refs in TAS: {len(existing)}", file=sys.stderr)
    mas_v, cas_v, var_v = build_validators()

    mag_out = open('/tmp/tdk_new_magnetics.ndjson', 'w')
    cap_out = open('/tmp/tdk_new_capacitors.ndjson', 'w')
    var_out = open('/tmp/tdk_new_varistors.ndjson', 'w')
    stats = Counter()
    quarantine = defaultdict(list)
    fail_samples = defaultdict(list)
    eol_stubs = []   # (part_no, category, replacement)

    # dedupe by part_no, keeping the entry with the most specs (TDK lists a few
    # part_nos under multiple part_ids — one populated, one empty redirect).
    best = {}
    for line in RAW.open():
        rec = json.loads(line)
        if rec['part_no'] not in best or len(rec['specs']) > len(best[rec['part_no']]['specs']):
            best[rec['part_no']] = rec

    for rec in best.values():
        cat = rec['category2']
        if rec['part_no'] in existing:
            stats['skip_existing'] += 1
            continue
        if cat in MAS_CATS:
            doc = map_magnetic(rec)
            if doc is None:
                stats['mas_eol_stub'] += 1
                eol_stubs.append((rec['part_no'], cat, disp(rec['specs'], '100000080')))
                continue
            errs = list(mas_v.iter_errors(doc['magnetic']))
            if errs:
                stats['mas_invalid'] += 1
                if len(fail_samples['mas']) < 8:
                    fail_samples['mas'].append(
                        f"{rec['part_no']} ({cat}): {errs[0].message} @ {list(errs[0].absolute_path)}")
                continue
            mag_out.write(json.dumps(doc, ensure_ascii=False) + '\n')
            stats['mas_ok'] += 1
            stats[f'mas_ok::{cat}'] += 1
        elif cat in CAS_CATS:
            doc, why = map_capacitor(rec)
            if doc is None:
                repl = disp(rec['specs'], '100000080')
                if why.startswith('missing') and (repl or len(rec['specs']) <= 4):
                    stats['cap_eol_stub'] += 1
                    eol_stubs.append((rec['part_no'], cat, repl))
                else:
                    stats['cap_quarantine'] += 1
                    quarantine[why].append(rec['part_no'])
                continue
            errs = list(cas_v.iter_errors(doc['capacitor']))
            if errs:
                stats['cap_invalid'] += 1
                if len(fail_samples['cap']) < 8:
                    fail_samples['cap'].append(
                        f"{rec['part_no']} ({cat}): {errs[0].message} @ {list(errs[0].absolute_path)}")
                continue
            cap_out.write(json.dumps(doc, ensure_ascii=False) + '\n')
            stats['cap_ok'] += 1
            stats[f'cap_ok::{cat}'] += 1
        elif cat in VARISTOR_CATS:
            doc, why = map_varistor(rec)
            if doc is None:
                stats['var_quarantine'] += 1
                quarantine[why].append(rec['part_no'])
                continue
            errs = list(var_v.iter_errors(doc['varistor']))
            if errs:
                stats['var_invalid'] += 1
                if len(fail_samples['var']) < 8:
                    fail_samples['var'].append(
                        f"{rec['part_no']}: {errs[0].message} @ {list(errs[0].absolute_path)}")
                continue
            var_out.write(json.dumps(doc, ensure_ascii=False) + '\n')
            stats['var_ok'] += 1
        else:
            stats[f'unrouted::{cat}'] += 1

    mag_out.close(); cap_out.close(); var_out.close()
    # write the EOL-stub report so nothing is silently dropped
    with open('/tmp/tdk_eol_stubs.csv', 'w') as f:
        f.write('part_no,category,replacement\n')
        for pn, cat, repl in eol_stubs:
            f.write(f"{pn},{cat},{repl or ''}\n")
    print("\n=== STATS ===", file=sys.stderr)
    for k in sorted(stats):
        print(f"  {k}: {stats[k]}", file=sys.stderr)
    print(f"\nEOL stubs (no datasheet data, redirect only): {len(eol_stubs)} "
          f"-> /tmp/tdk_eol_stubs.csv", file=sys.stderr)
    with_repl = sum(1 for _, _, r in eol_stubs if r)
    print(f"  of which {with_repl} name a replacement part", file=sys.stderr)
    if quarantine:
        print("\n=== CAP QUARANTINE ===", file=sys.stderr)
        for why, items in quarantine.items():
            print(f"  {why}: {len(items)} (e.g. {items[:3]})", file=sys.stderr)
    for kind, samples in fail_samples.items():
        if samples:
            print(f"\n=== {kind} VALIDATION FAILURES (sample) ===", file=sys.stderr)
            for s in samples:
                print(f"  {s}", file=sys.stderr)

if __name__ == '__main__':
    main()
