#!/usr/bin/env python3
"""Map scraped Yageo Group parts (/tmp/yageo_<cat>.json) to PEAS docs across
CAS/MAS/RAS/SAS, dedupe vs existing TAS, validate, write per-target candidate
files + a quarantine file for parts missing schema-required fields.
Datasheet URL = https://yageogroup.com/content/datasheet/asset/file/<dataSheet>.
Uses the already-SI 'Compare *' parameter values.
"""
import json, re, sys
from pathlib import Path
from collections import Counter, defaultdict
REPO = Path('/home/alf/PSMA/TAS'); sys.path.insert(0, str(REPO/'scripts'))
from tdk_meister_import import build_validators

def pvmap(part):
    m={}
    for pv in part.get('parameterValues',[]):
        n=pv.get('parameterName'); v=pv.get('value')
        if n is not None and n not in m: m[n]=v
    return m
def num(v):
    if v is None: return None
    s=str(v).strip()
    m=re.match(r'-?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?', s.replace(',',''))
    return float(m.group(0)) if m else None
def dsurl(part):
    d=part.get('dataSheet')
    return f"https://yageogroup.com/content/datasheet/asset/file/{d}" if d else None
def mi_base(part):
    mi={'name':part.get('manufacturer') or 'Yageo','reference':part['basePn'],'status':'production'}
    u=dsurl(part)
    if u: mi['datasheetUrl']=u
    return mi
def mech_thermal(P):
    out={}
    mech={}
    for k,key in (('Compare Length','length'),('Compare Width','width'),('Compare Height','height'),('Compare Diameter','diameter')):
        if num(P.get(k)) is not None: mech[key]={'nominal':num(P[k])}
    lo,hi=num(P.get('Compare Temperature Minimum')),num(P.get('Compare Temperature Maximum'))
    return mech, (lo,hi)

# ---------- technology maps ----------
RES_TECH={'thin film':'thinFilm','thick film':'thickFilm','metal film':'metalFilm','metal oxide':'metalOxide',
 'wirewound':'wirewound','wire wound':'wirewound','carbon film':'carbonFilm','carbon composition':'carbonComposition',
 'metal foil':'metalFoil','current sense':'currentSenseShunt','melf':'melf','metal strip':'currentSenseShunt'}
def res_tech(P):
    for src in (P.get('Type'),P.get('Technology'),P.get('Style')):
        if not src: continue
        s=str(src).lower()
        for k,v in RES_TECH.items():
            if k in s: return v
    return 'thickFilm'  # dominant Yageo chip resistor tech
def cap_tech(P):
    t=(P.get('Type') or '').lower(); tc=(P.get('Temperature Coefficient') or P.get('Technology') or '').upper()
    if 'tantalum' in t:
        if 'poly' in t or 'poly' in (P.get('Technology') or '').lower(): return 'tantalum-polymer',None
        return 'tantalum-mno2',None
    if 'niobium' in t: return 'niobium-oxide',None
    if 'aluminum' in t or 'alum' in t:
        if 'poly' in t: return 'aluminum-electrolytic-polymer',None
        return 'aluminum-electrolytic-wet',None
    if 'film' in t:
        if 'polyester' in t or 'pet' in t: return 'film-polyester',None
        return 'film-polypropylene',None
    if 'mica' in t: return 'mica',None
    if 'super' in t or 'edlc' in t: return 'supercapacitor-edlc',None
    if 'ceramic' in t or 'mlcc' in t or 'ceramic' in (P.get('Technology') or '').lower():
        code=''
        m=re.search(r'(C0G|NP0|X7R|X5R|X6S|X7S|X7T|X8R|Y5V|Z5U|U2J|C0H)', tc)
        if m: code=m.group(1)
        if code in ('C0G','NP0','U2J','C0H'): return 'ceramic-class-1',code
        if code in ('Y5V','Z5U'): return 'ceramic-class-3',code
        if code: return 'ceramic-class-2',code
        return 'ceramic-class-2',None
    return None,None
def assembly(P):
    m=(P.get('Mounting') or P.get('Style') or '').lower()
    if 'smd' in m or 'surface' in m or 'smt' in m or 'chip' in m: return 'SMT'
    if 'through' in m or 'tht' in m or 'radial' in m or 'axial' in m: return 'THT'
    return 'SMT'

def map_magnetic(part, cat):
    P=pvmap(part); mech,(lo,hi)=mech_thermal(P)
    ind=num(P.get('Compare Inductance')); dcr=num(P.get('Compare DC Resistance')); cur=num(P.get('Compare Current'))
    typ=(P.get('Type') or '').lower()
    el={}
    if cat=='emc' and ('common mode' in typ or 'cmc' in typ or 'choke' in typ):
        el['subtype']='commonModeChoke'
        if dcr is not None: el['dcResistances']=[{'nominal':dcr}]
        if ind is not None: el['inductance']={'nominal':ind}
        if cur is not None: el['ratedCurrents']=[cur]
    elif cat=='emc' and ('bead' in typ or 'ferrite' in typ):
        el['subtype']='chipBead'
        if dcr is not None: el['dcResistance']={'nominal':dcr}
        if cur is not None: el['ratedCurrents']=[cur]
    elif cat=='transformers':
        el['subtype']='transformer'
        if ind is not None: el['inductance']={'nominal':ind}
        if cur is not None: el['ratedCurrents']=[cur]
    else:
        el['subtype']='inductor'
        if ind is not None: el['inductance']={'nominal':ind}
        if dcr is not None: el['dcResistance']={'nominal':dcr}
        if cur is not None: el['ratedCurrents']=[cur]
    di={'part':{'partNumber':part['basePn']},'electrical':[el]}
    if mech: di['mechanical']=mech
    if lo is not None or hi is not None:
        ot={}
        if lo is not None: ot['minimum']=lo
        if hi is not None: ot['maximum']=hi
        di['thermal']={'operatingTemperature':ot}
    mi=mi_base(part);
    if P.get('Series'): mi['family']=str(P['Series'])[:60]
    mi['datasheetInfo']=di
    return {'magnetic':{'manufacturerInfo':mi}}

def map_capacitor(part):
    P=pvmap(part)
    cap=num(P.get('Capacitance')); v=num(P.get('Compare Voltage DC')) or num(P.get('Voltage DC'))
    tech,diel=cap_tech(P)
    if cap is None or v is None or tech is None: return None
    part_d={'partNumber':part['basePn'],'technology':tech}
    if diel: part_d['dielectricCode']=diel
    el={'capacitance':{'nominal':cap},'ratedVoltage':v}
    shape={'assembly':assembly(P),'shapeType':str(P.get('Style') or 'Chip')[:40]}
    mech={'shape':shape}
    dims={}
    for k,key in (('Compare Length','length'),('Compare Width','width'),('Compare Height','height'),('Compare Thickness','thickness'),('Compare Diameter','diameter')):
        if num(P.get(k)) is not None: dims[key]={'nominal':num(P[k])}
    if dims: mech['dimensions']=dims
    di={'part':part_d,'electrical':el,'mechanical':mech}
    lo,hi=num(P.get('Compare Temperature Minimum')),num(P.get('Compare Temperature Maximum'))
    if lo is not None or hi is not None:
        ot={}
        if lo is not None: ot['minimum']=lo
        if hi is not None: ot['maximum']=hi
        di['thermal']={'temperature':ot}
    mi=mi_base(part)
    if P.get('Series'): mi['family']=str(P['Series'])[:60]
    mi['datasheetInfo']=di
    return {'capacitor':{'manufacturerInfo':mi}}

def parse_res(s):
    if not s: return None
    m=re.match(r'([0-9]*\.?[0-9]+)\s*([kKmMrRgG]?)', str(s).strip())
    if not m: return num(s)
    val=float(m.group(1)); mult={'k':1e3,'m':1e6,'g':1e9,'r':1,'':1}.get(m.group(2).lower(),1)
    # 'm' in resistor display = Mega (MFR uses K/M); milli rare -> treat M as mega
    return val*mult
def map_resistor(part):
    P=pvmap(part)
    r=num(P.get('Compare Resistance'))
    if r is None: r=parse_res(P.get('Resistance (Resistors)') or P.get('Resistance'))
    tolp=num(P.get('Resistance Tolerance')); tol=tolp/100.0 if tolp is not None else None
    pw=num(P.get('Compare Power')) or num(P.get('Power'))
    if r is None or tol is None or pw is None: return None
    el={'resistance':{'nominal':r},'tolerance':tol,'powerRating':pw}
    tcr=num(P.get('Temperature Coefficient (Resistors)'))
    if tcr is not None: el['temperatureCoefficient']=tcr
    part_d={'partNumber':part['basePn'],'technology':res_tech(P)}
    di={'part':part_d,'electrical':el}
    mech={}
    for k,key in (('Compare Length','length'),('Compare Width','width'),('Compare Thickness','height')):
        if num(P.get(k)) is not None: mech[key]={'nominal':num(P[k])}
    if mech: di['mechanical']=mech
    lo,hi=num(P.get('Compare Temperature Minimum')),num(P.get('Compare Temperature Maximum'))
    if lo is not None or hi is not None:
        ot={}
        if lo is not None: ot['minimum']=lo
        if hi is not None: ot['maximum']=hi
        di['thermal']={'operatingTemperature':ot}
    mi=mi_base(part)
    if P.get('Series'): mi['family']=str(P['Series'])[:60]
    mi['datasheetInfo']=di
    return {'resistor':{'manufacturerInfo':mi}}

def map_varistor(part):
    P=pvmap(part)
    vv=num(P.get('Compare Varistor Voltage')); clamp=num(P.get('Compare Clamping Voltage')); surge=num(P.get('Surge Current'))
    if vv is None or clamp is None or surge is None: return None
    el={'varistorVoltage':{'nominal':vv},'clampingVoltage':clamp,'peakSurgeCurrent':surge}
    if num(P.get('Compare Voltage DC')) is not None: el['maxContinuousDcVoltage']=num(P['Compare Voltage DC'])
    if num(P.get('Compare Voltage AC')) is not None: el['maxContinuousAcVoltage']=num(P['Compare Voltage AC'])
    if num(P.get('Capacitance')) is not None: el['capacitance']=num(P['Capacitance'])
    if num(P.get('Energy')) is not None: el['energyAbsorption']=num(P['Energy'])
    di={'part':{'partNumber':part['basePn'],'technology':'metalOxide'},'electrical':el}
    mi=mi_base(part); mi['datasheetInfo']=di
    return {'varistor':{'manufacturerInfo':mi}}

def map_semi(part):
    P=pvmap(part); tech=(P.get('Technology') or '').lower(); typ=(P.get('Type') or '').lower()
    # MOSFET
    if 'channel' in tech or 'mosfet' in typ:
        el={}
        mp={'drainSourceVoltage':'Drain-Source Voltage','gateThresholdVoltage':'Gate Threshold Voltage',
            'totalGateCharge':'Total_Gate_Charge','continuousDrainCurrent':'Continuous Drain Current',
            'onResistance':'On Resistance'}
        for f,p in mp.items():
            if num(P.get(p)) is not None: el[f]=num(P[p])
        req=['drainSourceVoltage','onResistance','continuousDrainCurrent','gateThresholdVoltage','totalGateCharge']
        if not all(k in el for k in req): return None
        sub='nChannel' if 'n-channel' in tech or 'n channel' in tech else ('pChannel' if 'p-channel' in tech else None)
        partd={'partNumber':part['basePn'],'technology':'Si'}
        if sub: partd['subType']=sub
        di={'part':partd,'electrical':el}
        mi=mi_base(part); mi['datasheetInfo']=di
        return {'semiconductor':{'mosfet':{'manufacturerInfo':mi}}}
    # else DIODE (no required electrical)
    el={}
    for f,p in (('reverseVoltage','Reverse Voltage'),('forwardCurrent','Forward Current'),
                ('forwardVoltage','Forward Voltage'),('reverseRecoveryTime','Recovery Time')):
        if num(P.get(p)) is not None: el[f]=num(P[p])
    di={'part':{'partNumber':part['basePn'],'technology':'Si'},'electrical':el}
    mi=mi_base(part); mi['datasheetInfo']=di
    return {'semiconductor':{'diode':{'manufacturerInfo':mi}}}

ROUTE={'capacitors':('capacitor',map_capacitor),'resistors':('resistor',map_resistor),
       'circuit_protection':('varistor',map_varistor),'semiconductors':('semi',map_semi),
       'inductors':('magnetic',lambda p:map_magnetic(p,'inductors')),
       'emc':('magnetic',lambda p:map_magnetic(p,'emc')),
       'transformers':('magnetic',lambda p:map_magnetic(p,'transformers'))}

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
                    pn=(o.get('part') or {}).get('partNumber') if isinstance(o.get('part'),dict) else None
                    if pn: refs.add(pn)
                    st.extend(o.values())
                elif isinstance(o,list): st.extend(o)
    return refs

def main():
    mas_v,cas_v,var_v=build_validators()
    from jsonschema import Draft202012Validator
    reg=var_v._registry
    def V(path): return Draft202012Validator(json.load(open(REPO.parent/path)), registry=reg)
    res_v=V('RAS/schemas/resistor.json'); mos_v=V('SAS/schemas/mosfet.json'); dio_v=V('SAS/schemas/diode.json')
    VAL={'capacitor':(cas_v,['capacitor']),'magnetic':(mas_v,['magnetic']),'varistor':(var_v,['varistor']),
         'resistor':(res_v,['resistor']),'mosfet':(mos_v,['semiconductor','mosfet']),'diode':(dio_v,['semiconductor','diode'])}
    TARGETFILE={'capacitor':'capacitors','magnetic':'magnetics','varistor':'varistors','resistor':'resistors',
                'mosfet':'mosfets','diode':'diodes'}
    existing=existing_refs(['capacitors','magnetics','varistors','resistors','mosfets','diodes'])
    out=defaultdict(list); quar=[]; st=Counter(); emitted=set()
    for cat,(_,mapper) in ROUTE.items():
        d=json.load(open(f'/tmp/yageo_{cat}.json'))
        for part in d:
            pn=part['basePn']
            if pn in existing or pn in emitted: st[f'skip::{cat}']+=1; continue
            try: doc=mapper(part)
            except Exception as e: doc=None
            if doc is None:
                quar.append({'_yageoRaw':{'basePn':pn,'manufacturer':part.get('manufacturer'),'category':cat,'params':pvmap(part)},
                             '_triage':{'disposition':'unmapped','reason':f'{cat}: missing schema-required fields','date':'2026-06-22'}})
                st[f'quar::{cat}']+=1; continue
            # determine target+validator
            top=next(iter(doc))
            if top=='semiconductor':
                sub=next(iter(doc['semiconductor'])); key=sub
            else: key=top
            v,disc=VAL[key]
            body=doc
            for dkey in disc: body=body[dkey]
            errs=list(v.iter_errors(body))
            if errs:
                quar.append({'_yageoRaw':{'basePn':pn,'category':cat},'_triage':{'disposition':'invalid','reason':errs[0].message[:120],'date':'2026-06-22'}})
                st[f'invalid::{cat}']+=1
                if st[f'invalid::{cat}']<=3: print('INVALID',cat,pn,errs[0].message[:90],file=sys.stderr)
                continue
            out[TARGETFILE[key]].append(json.dumps(doc,ensure_ascii=False))
            emitted.add(pn); st[f'ok::{key}']+=1
    for tf,lines in out.items():
        open(f'/tmp/yageo_new_{tf}.ndjson','w').write('\n'.join(lines)+'\n')
    open('/tmp/yageo_quarantine.ndjson','w').write('\n'.join(json.dumps(q,ensure_ascii=False) for q in quar)+'\n')
    print('STATS:',dict(sorted(st.items())),file=sys.stderr)
    print('OUT FILES:',{tf:len(l) for tf,l in out.items()},'quar:',len(quar),file=sys.stderr)

if __name__=='__main__': main()
