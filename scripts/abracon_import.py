#!/usr/bin/env python3
"""Map scraped Abracon parts (/tmp/abracon_<cat>.json) to PEAS docs:
 inductors->MAS inductor, ferritebeads->MAS chipBead, commonmodechokes->MAS
 commonModeChoke, supercapacitors->CAS. Dedupe vs existing TAS, validate, write
 candidates. Datasheet URL is the row's Datasheet field. Units: inductance µH,
 DCR mΩ, SRF MHz, dims mm, impedance Ohm@MHz."""
import json, re, sys
from pathlib import Path
from collections import Counter
REPO=Path('/home/alf/PSMA/TAS'); sys.path.insert(0,str(REPO/'scripts'))
from tdk_meister_import import build_validators

def f(s):
    if s is None: return None
    m=re.search(r'-?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?', str(s).replace(',',''))
    return float(m.group(0)) if m else None
def temp(s):
    if not s: return None,None
    n=re.findall(r'-?[0-9]+', s)
    return (float(n[0]),float(n[1])) if len(n)>=2 else (None,None)
def imp_at(s):
    """'120 Ohms @ 100MHz' -> (120.0, 1e8)"""
    if not s: return None,None
    mag=f(s.split('@')[0]) if '@' in s else f(s)
    fr=None
    m=re.search(r'@\s*([0-9.]+)\s*([kKmMgG]?)Hz', s)
    if m:
        mult={'k':1e3,'m':1e6,'g':1e9,'':1}[m.group(2).lower()]
        fr=float(m.group(1))*mult
    return mag,fr

def loadcat(cat):
    d=json.load(open(f'/tmp/abracon_{cat}.json'))
    idx={c.get('DisplayName'):i for i,c in enumerate(d['columns'])}
    return d['rows'], idx
def cell(row, idx, name):
    i=idx.get(name)
    if i is None or i>=len(row.get('Cells',[])): return None
    return row['Cells'][i].get('Content')

def mi_base(row):
    mi={'name':'Abracon','reference':row['SkuName'],'status':'production'}
    if row.get('Datasheet'): mi['datasheetUrl']=row['Datasheet']
    if row.get('Series'): mi['family']=row['Series']
    return mi
def dims(row, idx, L='Length / Diameter', W='Width', H='Height'):
    m={}
    for col,key in ((L,'length'),(W,'width'),(H,'height')):
        v=f(cell(row,idx,col))
        if v is not None: m[key]={'nominal':v/1000.0}
    return m
def thermal(row, idx):
    lo,hi=temp(cell(row,idx,'Operating Temperature'))
    if lo is None and hi is None: return None
    ot={}
    if lo is not None: ot['minimum']=lo
    if hi is not None: ot['maximum']=hi
    return {'operatingTemperature':ot}

def map_inductor(row, idx):
    el={'subtype':'inductor'}
    ind=f(cell(row,idx,'Inductance'))
    if ind is not None: el['inductance']={'nominal':ind*1e-6}
    dcr=f(cell(row,idx,'DC Resistance Max'))
    if dcr is not None: el['dcResistance']={'maximum':dcr/1000.0}
    cur=f(cell(row,idx,'Temperature Rise Current'))
    if cur is not None: el['ratedCurrents']=[cur]
    isat=f(cell(row,idx,'Saturation Current'))
    if isat is not None: el['saturationCurrentPeak']=isat
    srf=f(cell(row,idx,'SRF'))
    if srf is not None: el['selfResonantFrequency']=srf*1e6
    part={'partNumber':row['SkuName']}
    if (cell(row,idx,'Automotive Rating') or '').strip(): part['automotive']=True
    di={'part':part,'electrical':[el],'mechanical':dims(row,idx)}
    t=thermal(row,idx)
    if t: di['thermal']=t
    mi=mi_base(row); mi['datasheetInfo']=di
    return {'magnetic':{'manufacturerInfo':mi}}

def map_bead(row, idx):
    el={'subtype':'chipBead'}
    dcr=f(cell(row,idx,'DC Resistance Max'))
    if dcr is not None: el['dcResistance']={'maximum':dcr/1000.0}
    cur=f(cell(row,idx,'Current Rating Max'))
    if cur is not None: el['ratedCurrents']=[cur]
    mag,fr=imp_at(cell(row,idx,'Impedance @ Frequency'))
    if mag is not None and fr is not None:
        el['impedancePoints']=[{'frequency':fr,'impedance':{'magnitude':mag}}]
    di={'part':{'partNumber':row['SkuName']},'electrical':[el],'mechanical':dims(row,idx)}
    t=thermal(row,idx)
    if t: di['thermal']=t
    mi=mi_base(row); mi['datasheetInfo']=di
    return {'magnetic':{'manufacturerInfo':mi}}

def map_cmc(row, idx):
    el={'subtype':'commonModeChoke'}
    dcr=f(cell(row,idx,'DC Resistance Max'))
    if dcr is not None: el['dcResistances']=[{'maximum':dcr/1000.0}]
    cur=f(cell(row,idx,'Current Rating Max'))
    if cur is not None: el['ratedCurrents']=[cur]
    v=f(cell(row,idx,'DC Voltage Max'))
    if v is not None: el['ratedVoltageDC']=v
    indmag,indfr=imp_at(cell(row,idx,'Inductance @ Frequency'))
    if indmag is not None: el['inductance']={'nominal':indmag*1e-6}
    mag,fr=imp_at(cell(row,idx,'Impedance @ Frequency'))
    if mag is not None and fr is not None:
        el['impedancePoints']=[{'frequency':fr,'impedance':{'magnitude':mag}}]
    di={'part':{'partNumber':row['SkuName']},'electrical':[el],'mechanical':dims(row,idx)}
    t=thermal(row,idx)
    if t: di['thermal']=t
    mi=mi_base(row); mi['datasheetInfo']=di
    return {'magnetic':{'manufacturerInfo':mi}}

def map_supercap(row, idx):
    cap=f(cell(row,idx,'Capacitance')); v=f(cell(row,idx,'Voltage Rating'))
    if cap is None or v is None: return None
    el={'capacitance':{'nominal':cap},'ratedVoltage':v,'polarized':True}
    esr=f(cell(row,idx,'ESR Max'))
    if esr is not None: el['esr']=esr/1000.0
    leak=f(cell(row,idx,'Leakage Current'))
    if leak is not None: el['leakageCurrent']=leak
    mt=(cell(row,idx,'Mounting Type') or '').lower()
    asm='SMT' if ('surface' in mt or 'smd' in mt) else 'THT'
    shape={'assembly':asm,'shapeType':str(cell(row,idx,'Package Format') or 'EDLC')[:40]}
    mech={'shape':shape}
    dd={}
    for col,key in (('Length / Diameter','diameter'),('Width','width'),('Height','height')):
        vv=f(cell(row,idx,col))
        if vv is not None: dd[key]={'nominal':vv/1000.0}
    if dd: mech['dimensions']=dd
    part={'partNumber':row['SkuName'],'technology':'supercapacitor-edlc'}
    di={'part':part,'electrical':el,'mechanical':mech}
    lo,hi=temp(cell(row,idx,'Operating Temperature'))
    if lo is not None or hi is not None:
        ot={}
        if lo is not None: ot['minimum']=lo
        if hi is not None: ot['maximum']=hi
        di['thermal']={'temperature':ot}
    mi=mi_base(row); mi['datasheetInfo']=di
    return {'capacitor':{'manufacturerInfo':mi}}

ROUTE={'inductors':('magnetic','magnetics',map_inductor),
       'ferritebeads':('magnetic','magnetics',map_bead),
       'commonmodechokes':('magnetic','magnetics',map_cmc),
       'supercapacitors':('capacitor','capacitors',map_supercap)}

def existing_refs(fnames):
    refs=set()
    for fn in fnames:
        p=REPO/'data'/f'{fn}.ndjson'
        if not p.exists(): continue
        for l in p.open():
            l=l.strip()
            if not l: continue
            try: r=json.loads(l)
            except: continue
            st=[r]
            while st:
                o=st.pop()
                if isinstance(o,dict):
                    mi=o.get('manufacturerInfo')
                    if isinstance(mi,dict) and mi.get('reference'): refs.add(mi['reference'])
                    if isinstance(o.get('part'),dict) and o['part'].get('partNumber'): refs.add(o['part']['partNumber'])
                    st.extend(o.values())
                elif isinstance(o,list): st.extend(o)
    return refs

def main():
    mas_v,cas_v,_=build_validators()
    VAL={'magnetic':(mas_v,'magnetic'),'capacitor':(cas_v,'capacitor')}
    existing=existing_refs(['magnetics','capacitors'])
    out={}; st=Counter(); emitted=set()
    for cat,(kind,tf,mapper) in ROUTE.items():
        rows,idx=loadcat(cat)
        for row in rows:
            pn=row.get('SkuName')
            if not pn or pn in existing or pn in emitted: st[f'skip::{cat}']+=1; continue
            try: doc=mapper(row,idx)
            except Exception as e: doc=None
            if doc is None: st[f'nodata::{cat}']+=1; continue
            v,disc=VAL[kind]
            errs=list(v.iter_errors(doc[disc]))
            if errs:
                st[f'invalid::{cat}']+=1
                if st[f'invalid::{cat}']<=3: print('INVALID',cat,pn,errs[0].message[:80],file=sys.stderr)
                continue
            out.setdefault(tf,[]).append(json.dumps(doc,ensure_ascii=False))
            emitted.add(pn); st[f'ok::{cat}']+=1
    for tf,lines in out.items():
        open(f'/tmp/abracon_new_{tf}.ndjson','w').write('\n'.join(lines)+'\n')
    print('STATS:',dict(sorted(st.items())),file=sys.stderr)
    print('OUT:',{tf:len(l) for tf,l in out.items()},file=sys.stderr)

if __name__=='__main__': main()
