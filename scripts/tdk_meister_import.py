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
import json, os, re, sys
from pathlib import Path
from collections import Counter, defaultdict

REPO = Path('/home/alf/PSMA/TAS')
PROTEUS = REPO.parent
# TDK_RAW lets a sample be run through the same code path as the full extract.
RAW = Path(os.environ.get('TDK_RAW', '/tmp/tdk_raw.jsonl'))
OUT_DIR = Path(os.environ.get('TDK_OUT', '/tmp'))

sys.path.insert(0, str(REPO / 'scripts'))
# THE GATE (2026-09-06). This importer used to validate each candidate against
# its schema and SKIP the ones that failed, counting them in a stats line. A
# skipped row is invisible the moment nobody reads stderr, and schema validity
# was never the property that failed here anyway: 549 of this importer's chip
# beads validated perfectly while all carrying an identical minted 1e-09 H.
# ingest_gate carries that check and five more, and a refusal ABORTS.
from ingest_gate import IngestGate, IngestRefused   # noqa: E402

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

# TDK's `series` table carries two sentinel rows on its negative ids - -1 "dummy" and
# -2 "TBD" - which a class points at when it has no series. They are not device families,
# and there is nothing behind them to recover: `class` is the only part -> series link.
# So the field is left ABSENT rather than filled from the part number, which would invent
# a series TDK never published.
SENTINEL_SERIES = {'dummy', 'tbd'}

def family_of(rec):
    s = (rec.get('series') or '').strip()
    if s and s.lower() not in SENTINEL_SERIES:
        return s
    return None

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
IMPEDANCE_AT_FREQ_IDS = ('303000000',)  # TDK's "impedance @ 100 MHz" column

# ABT #1090: a vendor's impedance column does NOT carry one fixed frequency
# across families. WE's does not -- REDEXPERT's Impedance is |Z| @ 100 MHz for
# WE-CBF/WAFB/MLS but a Zmax at a part-specific frequency for WE-PF, and taking
# the 100 MHz convention across the boundary wrote |Z|(100 MHz) = 1180 next to a
# published Zmax = 2900 @ 90 MHz. So where TDK's own cell states a frequency,
# that frequency is used; 100 MHz is assumed only when the cell states none, and
# is the documented meaning of this column rather than a guess about the part.
_FREQ = re.compile(r"(\d+(?:[.,]\d+)?)\s*([kKmMgG])?[hH]z")
_FREQ_MULT = {"": 1.0, "k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6, "g": 1e9, "G": 1e9}

def impedance_frequency(specs):
    """The frequency TDK's impedance cell states, else the column's 100 MHz."""
    text = disp(specs, IMPEDANCE_AT_FREQ_IDS[0]) or ""
    m = _FREQ.search(text)
    if m:
        return float(m.group(1).replace(",", ".")) * _FREQ_MULT[m.group(2) or ""]
    return 1.0e8

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
                {'frequency': impedance_frequency(specs),
                 'impedance': {'magnitude': imp}}
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

# CAS `electrical.ratedVoltage` is a DC rating, so ONLY TDK's DC voltage specs may
# feed it. The AC specs are named here as an explicit DENYLIST rather than left out,
# because they used to sit INSIDE this tuple: VOLT_ID was
# ('301000030', '301000910', '301000050') under a comment reading "Rated Voltage
# (DC)", ordering the AC spec AHEAD of the DC one. It was inert only because
# 301000910 is text-only today and num() skips it - the moment TDK populates it
# numerically, every disc capacitor's ratedVoltage silently becomes an AC rating.
#
#   301000030  numeric, 16,384 parts, "Rated Voltage(DC)"        -> DC
#   301000050  numeric,    318 parts, disc caps, e.g. 400        -> DC
#   301000910  text,     1,159 parts, "X1/440VAC, Y1/400VAC"     -> AC safety class
#   301000350  text,       261 parts, "X1/440VAC, Y1/400VAC"     -> AC safety class
#   301000900  text,       522 parts, "X1/440" / "Y1/400"        -> AC safety class
VOLT_DC_ID = ('301000030', '301000050')                    # 050 = disc caps
VOLT_AC_ID = ('301000910', '301000350', '301000900')       # never a DC source
TC_ID = '301000070'                         # Temperature characteristic code


class AcRatingAsDc(Exception):
    """A DC field was about to be filled from an AC rating spec."""


def dc_rated_voltage(specs, _dc_ids=None):
    """TDK's DC rated voltage, or None. Refuses to return an AC rating.

    The selection and the denylist are enforced in the SAME function, so a future
    edit that widens the id tuple cannot re-create the ordering bug silently: an
    AC id reaching this point raises rather than returning a number.
    """
    ids = VOLT_DC_ID if _dc_ids is None else _dc_ids
    for sid in ids:
        v = num(specs, sid)
        if v is None:
            continue
        if sid in VOLT_AC_ID:
            raise AcRatingAsDc(
                "spec %s is an AC rating and cannot supply electrical.ratedVoltage" % sid)
        return v
    return None

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

def is_eol_stub(rec):
    """True when TDK keeps this part number only as a catalogue redirect.

    map_magnetic's stub guard keys on "no electrical values and no mechanical
    data". That test CANNOT work for capacitors: TDK's disc-capacitor stubs carry
    a full 32-spec record - capacitance, tolerance, IR, dimensions - so they sail
    through it and become live parts. 57 such rows did, and one of them acquired a
    440 V rating that belongs to its REPLACEMENT part (10 mm stub vs 8 mm successor).

    THE SIGNAL IS THE SENTINEL CLASS. TDK's `class` table carries negative class_ids
    whose series_id points at the sentinel series -1 "dummy" / -2 "TBD"; the two
    coincide exactly (0 mismatches over all 862 classes), so `rec['series']` - which
    the extractor already carries - is an exact proxy for the class_id and no new
    extractor field is needed. Over TDK's five capacitor categories:

        sentinel class:      610 Obsolete, 1 In Development, 0 Production
        non-sentinel class:  7,006 Production, 5,366 NRND, 3,719 Obsolete

    So a sentinel-class capacitor is never a part TDK is selling.

    THE REPLACEMENT SPEC (100000080) IS NOT THE SIGNAL, and this is the part worth
    recording: 5,845 parts on REAL classes also name a replacement - 2,428 NRND and
    1,591 Obsolete capacitors among them. Gating on it would refuse ~4,000 genuine,
    published parts. Requiring BOTH would be worse still: 128 of the sentinel
    capacitors name no replacement at all and would pass straight through, which is
    exactly the hole being closed. Hence: sentinel class alone.
    """
    return (rec.get('series') or '').strip().lower() in SENTINEL_SERIES


def map_capacitor(rec):
    specs = rec['specs']
    cat = rec['category2']
    if is_eol_stub(rec):
        return None, 'EOL redirect stub (sentinel class)'
    capf, _ = first_num(specs, CAP_ID)
    volt = dc_rated_voltage(specs)
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
# main
# ---------------------------------------------------------------------------

def main():
    existing = existing_tdk_refs()
    print(f"existing TDK refs in TAS: {len(existing)}", file=sys.stderr)
    gates = {'mas': IngestGate('magnetics.ndjson'),
             'cap': IngestGate('capacitors.ndjson'),
             'var': IngestGate('varistors.ndjson')}
    # Rows are BUFFERED, never streamed: the cohort rules (minted constant,
    # arithmetic ladder, duplicate identity, one document cited for many parts)
    # are properties of the batch, so nothing may be written until the whole
    # batch has been judged.
    buffered = {'mas': [], 'cap': [], 'var': []}
    stats = Counter()
    quarantine = defaultdict(list)
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
            gates['mas'].admit(doc)      # raises IngestRefused; import stops
            buffered['mas'].append(doc)
            stats['mas_ok'] += 1
            stats[f'mas_ok::{cat}'] += 1
        elif cat in CAS_CATS:
            doc, why = map_capacitor(rec)
            if doc is None:
                repl = disp(rec['specs'], '100000080')
                if why.startswith('EOL redirect stub') or (
                        why.startswith('missing') and (repl or len(rec['specs']) <= 4)):
                    stats['cap_eol_stub'] += 1
                    eol_stubs.append((rec['part_no'], cat, repl))
                else:
                    stats['cap_quarantine'] += 1
                    quarantine[why].append(rec['part_no'])
                continue
            gates['cap'].admit(doc)      # raises IngestRefused; import stops
            buffered['cap'].append(doc)
            stats['cap_ok'] += 1
            stats[f'cap_ok::{cat}'] += 1
        elif cat in VARISTOR_CATS:
            doc, why = map_varistor(rec)
            if doc is None:
                stats['var_quarantine'] += 1
                quarantine[why].append(rec['part_no'])
                continue
            gates['var'].admit(doc)      # raises IngestRefused; import stops
            buffered['var'].append(doc)
            stats['var_ok'] += 1
        else:
            stats[f'unrouted::{cat}'] += 1

    for kind in ('mas', 'cap', 'var'):
        gates[kind].close()      # cohort rules; raises before anything is written
    for kind, fname in (('mas', 'tdk_new_magnetics.ndjson'),
                        ('cap', 'tdk_new_capacitors.ndjson'),
                        ('var', 'tdk_new_varistors.ndjson')):
        with (OUT_DIR / fname).open('w') as fo:
            for doc in buffered[kind]:
                fo.write(json.dumps(doc, ensure_ascii=False) + '\n')
    # write the EOL-stub report so nothing is silently dropped
    with (OUT_DIR / 'tdk_eol_stubs.csv').open('w') as f:
        f.write('part_no,category,replacement\n')
        for pn, cat, repl in eol_stubs:
            f.write(f"{pn},{cat},{repl or ''}\n")
    print("\n=== STATS ===", file=sys.stderr)
    for k in sorted(stats):
        print(f"  {k}: {stats[k]}", file=sys.stderr)
    print(f"\nEOL stubs (no datasheet data, redirect only): {len(eol_stubs)} "
          f"-> {OUT_DIR / 'tdk_eol_stubs.csv'}", file=sys.stderr)
    with_repl = sum(1 for _, _, r in eol_stubs if r)
    print(f"  of which {with_repl} name a replacement part", file=sys.stderr)
    if quarantine:
        print("\n=== CAP QUARANTINE ===", file=sys.stderr)
        for why, items in quarantine.items():
            print(f"  {why}: {len(items)} (e.g. {items[:3]})", file=sys.stderr)
# ---------------------------------------------------------------------------
# --selftest: paired fixtures, each one a defect that actually shipped
# ---------------------------------------------------------------------------
# Every fixture below is a REAL TDK Meister record, transcribed from
# TstDB.tmdb. Each guard is asserted in BOTH directions: a must-fire case and a
# must-stay-quiet case. A guard proved only by its must-fire case is
# indistinguishable from one that refuses everything, and the must-stay-quiet
# cases here are the ones that would have caught a lazy fix:
#   A3  a LIVE part that names a replacement  -> the replacement spec is not the signal
#   A4  a STUB that names no replacement      -> nor is its absence
#   B3  an MLCC with only the ordinary DC id  -> the DC path still works

def _spec(pairs):
    """{spec_id: [{'num'|'display': v}]} in the shape the raw extract produces."""
    out = {}
    for sid, val in pairs:
        out.setdefault(sid, []).append(
            {'num': val} if isinstance(val, (int, float)) else {'display': val})
    return out


# TDK CD10-E2GA152MYGS, part_id 100028881, class_id -123 (sentinel, series "TBD").
# 32 specs - a complete record - plus a replacement pointer to CD45-E2GA152M-GKA.
_STUB_SPECS = _spec([
    ('100000080', 'CD45-E2GA152M-GKA'),
    ('200000130', 7), ('200000170', 10), ('201000220', 10),
    ('301000000', 1.5e-09), ('301000010', '\u00b120%'), ('301000050', 400),
    ('301000070', 'E'), ('400000060', -25), ('400000070', 125),
])
# TDK CD45-E2GA152M-GKA, part_id 100028911, class_id 10707 (real class/series).
# Same capacitance, 8 mm not 10 mm, and it is THIS part that carries X1/440VAC.
_LIVE_SPECS = _spec([
    ('200000130', 6), ('200000170', 8), ('201000220', 10),
    ('301000000', 1.5e-09), ('301000010', '\u00b120%'), ('301000050', 400),
    ('301000070', 'E'), ('301000350', 'X1/440VAC, Y1/400VAC'),
    ('301000900', 'X1/440'), ('301000910', 'X1/440VAC, Y1/400VAC'),
    ('400000060', -25), ('400000070', 125),
])


def _cap(part_no, series, specs, cat='lead-disc'):
    return {'part_no': part_no, 'category2': cat, 'series': series,
            'specs': specs, 'disabled': 0, 'url_substring': None}


def _check(name, expect, fn):
    try:
        got = fn()
    except Exception as exc:                      # noqa: BLE001 - a raise is a result
        got = '%s: %s' % (type(exc).__name__, exc)
    ok = (got == expect)
    print('%-4s %-24s expected %-24s %s'
          % ('PASS' if ok else 'FAIL', repr(got)[:24], repr(expect)[:24], name))
    return ok


def _mapped_voltage(rec):
    doc, why = map_capacitor(rec)
    if doc is None:
        return 'REJECTED: %s' % why
    return doc['capacitor']['manufacturerInfo']['datasheetInfo']['electrical']['ratedVoltage']


def selftest():
    r = []

    # -- A: the EOL redirect-stub guard -------------------------------------
    r.append(_check('A1  sentinel-class disc stub CD10-E2GA152MYGS (must fire)',
                    'REJECTED: EOL redirect stub (sentinel class)',
                    lambda: _mapped_voltage(_cap('CD10-E2GA152MYGS', 'TBD', _STUB_SPECS))))

    r.append(_check('A2  its live replacement CD45-E2GA152M-GKA (must stay quiet)',
                    400,
                    lambda: _mapped_voltage(_cap('CD45-E2GA152M-GKA', 'CD', _LIVE_SPECS))))

    # A live, published part that ALSO names a replacement. 5,845 TDK parts on real
    # classes do - 2,428 NRND and 1,591 Obsolete capacitors among them. A guard keyed
    # on the replacement spec would refuse every one of them.
    _live_with_repl = dict(_LIVE_SPECS)
    _live_with_repl['100000080'] = [{'display': 'C1210X7R2A105K085AC'}]
    r.append(_check('A3  live part that names a replacement (must stay quiet)',
                    400,
                    lambda: _mapped_voltage(_cap('CD45-E2GA152M-GKA', 'CD', _live_with_repl))))

    # A stub that names NO replacement. 128 of the 610 sentinel-class capacitors
    # are like this; requiring a replacement pointer would let them all through.
    _stub_no_repl = {k: v for k, v in _STUB_SPECS.items() if k != '100000080'}
    r.append(_check('A4  sentinel-class stub with no replacement spec (must fire)',
                    'REJECTED: EOL redirect stub (sentinel class)',
                    lambda: _mapped_voltage(_cap('CD10-E2GA152MYNS', 'dummy', _stub_no_repl))))

    # -- B: an AC rating may never fill the DC field ------------------------
    # TDK publishes 301000910 as TEXT today, which is the only reason the old
    # ordering never fired. This fixture is that field POPULATED NUMERICALLY -
    # the one change upstream would need to make for the bug to become live.
    _ac_numeric = dict(_LIVE_SPECS)
    _ac_numeric['301000910'] = [{'num': 440}]
    r.append(_check('B1  AC spec populated numerically, DC spec present (must stay quiet)',
                    400,
                    lambda: _mapped_voltage(_cap('CD45-E2GA152M-GKA', 'CD', _ac_numeric))))

    r.append(_check('B2  an AC id handed to the DC reader (must fire)',
                    "AcRatingAsDc: spec 301000910 is an AC rating and cannot "
                    "supply electrical.ratedVoltage",
                    lambda: dc_rated_voltage(_ac_numeric, _dc_ids=('301000910',))))

    _mlcc = _spec([('301000000', 1e-07), ('301000030', 16), ('301000070', 'X7R')])
    r.append(_check('B3  ordinary MLCC, DC id 301000030 (must stay quiet)',
                    16,
                    lambda: _mapped_voltage(_cap('C1608X7R1C104K080AC', 'C', _mlcc, 'mlcc'))))

    print('\n%d/%d passed' % (sum(r), len(r)))
    return 0 if all(r) else 1


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(selftest())
    main()
