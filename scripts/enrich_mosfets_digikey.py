#!/usr/bin/env python3
"""Backfill the validation-failing MOSFETs in TAS/data/mosfets.ndjson with the
schema-required electrical fields (drainSourceVoltage, onResistance,
continuousDrainCurrent, gateThresholdVoltage, totalGateCharge) from the DigiKey
v4 keyword API, plus datasheet URL, package, and Digi-Key distributorsInfo.
Run with --apply to write."""
import json, re, sys, time, urllib.request, urllib.parse, urllib.error
from pathlib import Path
REPO=Path('/home/alf/PSMA/TAS'); sys.path.insert(0,str(REPO/'scripts'))
from tdk_meister_import import build_validators
from jsonschema import Draft202012Validator
CID='cN8i6L6KnNGJB2h3zsQgC7KvWf8AccsC'; CS='8QpIINW6VK9loIeF'
APPLY='--apply' in sys.argv

_tok={'v':None}
def token(force=False):
    if _tok['v'] and not force: return _tok['v']
    data=urllib.parse.urlencode({'client_id':CID,'client_secret':CS,'grant_type':'client_credentials'}).encode()
    _tok['v']=json.loads(urllib.request.urlopen(urllib.request.Request('https://api.digikey.com/v1/oauth2/token',data=data),timeout=30).read())['access_token']
    return _tok['v']

def search(mpn):
    body=json.dumps({"Keywords":mpn,"Limit":5}).encode()
    for attempt in range(3):
        at=token(force=attempt>0)
        req=urllib.request.Request('https://api.digikey.com/products/v4/search/keyword',data=body,method='POST',
          headers={'Authorization':'Bearer '+at,'X-DIGIKEY-Client-Id':CID,'accept':'application/json','content-type':'application/json','X-DIGIKEY-Locale-Site':'US','X-DIGIKEY-Locale-Currency':'USD'})
        try:
            return json.loads(urllib.request.urlopen(req,timeout=30).read())
        except urllib.error.HTTPError as e:
            if e.code in (401,429): time.sleep(1.5); continue
            return None
        except Exception: time.sleep(1); continue
    return None

def val(s):
    if not s: return None
    s=str(s).replace('±','').replace(',','').strip()
    m=re.match(r'([-+]?[0-9]*\.?[0-9]+)\s*([pnµumkMG]?)', s)
    if not m: return None
    mult={'p':1e-12,'n':1e-9,'µ':1e-6,'u':1e-6,'m':1e-3,'k':1e3,'M':1e6,'G':1e9,'':1.0}
    return float(m.group(1))*mult[m.group(2)]

def pick(prods, mpn):
    mt=mpn.upper().replace('-','').replace(' ','')
    for p in prods:
        pn=(p.get('ManufacturerProductNumber') or '').upper().replace('-','').replace(' ','')
        if pn==mt or pn.startswith(mt) or mt.startswith(pn): return p
    return prods[0] if prods else None

def enrich(p):
    P={pr.get('ParameterText'):pr.get('ValueText') for pr in p.get('Parameters',[])}
    el={
      'drainSourceVoltage':val(P.get('Drain to Source Voltage (Vdss)')),
      'continuousDrainCurrent':val(P.get('Current - Continuous Drain (Id) @ 25°C')),
      'onResistance':val(P.get('Rds On (Max) @ Id, Vgs')),
      'gateThresholdVoltage':val(P.get('Vgs(th) (Max) @ Id')),
      'totalGateCharge':val(P.get('Gate Charge (Qg) (Max) @ Vgs')),
    }
    el={k:v for k,v in el.items() if v is not None}
    extra={'gateSourceVoltageMax':val(P.get('Vgs (Max)')),'inputCapacitance':val(P.get('Input Capacitance (Ciss) (Max) @ Vds')),
           'powerDissipation':val(P.get('Power Dissipation (Max)'))}
    for k,v in extra.items():
        if v is not None: el[k]=v
    case=P.get('Supplier Device Package') or P.get('Package / Case')
    ds=p.get('DatasheetUrl')
    di=None
    pv=(p.get('ProductVariations') or [])
    up=p.get('UnitPrice'); qa=p.get('QuantityAvailable')
    if up:
        d={'name':'Digi-Key','cost':{'value':round(float(up),5),'currency':'USD'}}
        if pv:
            v0=pv[0]
            if v0.get('DigiKeyProductNumber'): d['reference']=v0['DigiKeyProductNumber']
            pkg=(v0.get('PackageType') or {}).get('Value')
            if pkg: d['packaging']=pkg
            if v0.get('MinimumOrderQuantity'): d['moq']=v0['MinimumOrderQuantity']
        if p.get('ProductUrl'): d['link']=p['ProductUrl']
        if isinstance(qa,int): d['stock']=qa
        di=d
    return el, (case if case and case!='-' else None), (ds if ds else None), di

def main():
    mas_v,cas_v,var_v=build_validators(); reg=var_v._registry
    mos_v=Draft202012Validator(json.load(open(REPO.parent/'SAS/schemas/mosfet.json')),registry=reg)
    src=REPO/'data'/'mosfets.ndjson'
    lines=src.read_text().splitlines()
    # identify failing
    failing=[]
    for i,l in enumerate(lines):
        if not l.strip(): continue
        try: r=json.loads(l)
        except: continue
        b=r.get('semiconductor',{}).get('mosfet')
        if b and list(mos_v.iter_errors(b)):
            ref=b.get('manufacturerInfo',{}).get('reference')
            if ref: failing.append((i,ref))
    print(f"failing mosfets: {len(failing)}", file=sys.stderr)
    cache={}; from collections import Counter; st=Counter()
    for n,(i,ref) in enumerate(failing,1):
        if ref not in cache:
            d=search(ref); time.sleep(0.25)
            cache[ref]=d
        d=cache[ref]
        prods=(d or {}).get('Products') or []
        if not prods: st['not_on_digikey']+=1; continue
        p=pick(prods,ref)
        el,case,ds,di=enrich(p)
        r=json.loads(lines[i]); b=r['semiconductor']['mosfet']; mi=b['manufacturerInfo']
        di_info=mi.setdefault('datasheetInfo',{})
        cur=di_info.get('electrical',{}) or {}
        cur.update(el); di_info['electrical']=cur
        if case: di_info.setdefault('part',{})['case']=case
        if ds and not mi.get('datasheetUrl'): mi['datasheetUrl']=ds
        if di and 'distributorsInfo' not in b: b['distributorsInfo']=[di]
        errs=list(mos_v.iter_errors(b))
        if errs:
            st['still_invalid']+=1
            if st['still_invalid']<=5: print('STILL',ref,errs[0].message[:60],file=sys.stderr)
            continue
        lines[i]=json.dumps(r,ensure_ascii=False)
        st['enriched']+=1
        if n%40==0: print(f"  {n}/{len(failing)} enriched={st['enriched']}",file=sys.stderr,flush=True)
    print('STATS:',dict(st),file=sys.stderr)
    if APPLY:
        src.write_text('\n'.join(lines)+'\n'); print('APPLIED',file=sys.stderr)
    else: print('(dry-run; --apply to write)',file=sys.stderr)

if __name__=='__main__': main()
