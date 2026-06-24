#!/usr/bin/env python3
"""TI ADC/DAC + analog switch/mux -> AAS NDJSON.
Reads .playwright-mcp/ti_{adc,dac,switchmux}.json. ADC/DAC require resolution+architecture
(records with unmappable architecture are skipped). switch/mux classified from Configuration.
"""
import json, re, os
MCP="/home/alf/PSMA/.playwright-mcp"; OUT="/home/alf/PSMA/TAS/staging/ti"; os.makedirs(OUT,exist_ok=True)
def f(v):
    if v is None or isinstance(v,list): return None
    m=re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?",str(v)); return float(m.group()) if m else None
def s1(v): return (v[0] if isinstance(v,list) and v else v) if not isinstance(v,(int,float)) else v
STATUS={"ACTIVE":"production","PREVIEW":"preview","NRND":"nrnd","OBSOLETE":"obsolete","PRE_RELEASE":"preview"}
ADC_ARCH={"sar":"SAR","pipeline":"pipeline","delta-sigma":"deltaSigma","delta-sigma modulator":"deltaSigma","folding interpolating":"folding","flash":"flash","dual slope":"dualSlope"}
DAC_ARCH={"string":"stringDac","r-2r":"r2r","multiplying dac":"multiplying","multiplying":"multiplying","current source":"currentSteering","current sink":"currentSteering","current steering":"currentSteering","segmented":"segmented","sigma-delta":"sigmaDelta","delta-sigma":"sigmaDelta"}
def mi_of(gpn,status,e):
    return {"manufacturerInfo":{"name":"Texas Instruments","reference":gpn,"status":STATUS.get(status,"production"),
            "datasheetUrl":f"https://www.ti.com/lit/gpn/{gpn}","datasheetInfo":{"part":{"partNumber":gpn},"electrical":e}}}

def conv_adc(r):
    P=r["params"]; gpn=r["gpn"]
    arch=ADC_ARCH.get((str(s1(P.get("Architecture")) or "")).lower())
    res=f(P.get("Resolution"))
    if not gpn or arch is None or res is None: return None
    e={"resolution":int(res),"architecture":arch}
    if (v:=f(P.get("Number of input channels"))) is not None: e["numberOfChannels"]=int(v)
    if (v:=f(P.get("Sample ratemax"))) is not None: e["sampleRate"]=v
    dyn={}
    if (v:=f(P.get("SNR")) or f(P.get("SNRtyp"))) is not None: dyn["signalToNoiseRatio"]=v
    if (v:=f(P.get("SFDR")) or f(P.get("SFDRtyp"))) is not None: dyn["spuriousFreeDynamicRange"]=v
    if dyn: e["dynamics"]=dyn
    return {"adc":mi_of(gpn,r.get("status"),e)}

def conv_dac(r):
    P=r["params"]; gpn=r["gpn"]
    arch=DAC_ARCH.get((str(s1(P.get("Architecture")) or "")).lower())
    res=f(P.get("Resolution"))
    if not gpn or arch is None or res is None: return None
    e={"resolution":int(res),"architecture":arch}
    ot=(str(s1(P.get("Output type")) or "")).lower()
    if "current" in ot: e["outputType"]="current"
    elif "buffer" in ot: e["outputType"]="voltageBuffered"
    elif "voltage" in ot: e["outputType"]="voltageUnbuffered"
    if (v:=f(P.get("Sample/update rate"))) is not None: e["updateRate"]=v
    if (v:=f(P.get("Settling time"))) is not None: e["settlingTime"]=v
    if (v:=f(P.get("Number of channels"))) is not None: e["numberOfChannels"]=int(v)
    return {"dac":mi_of(gpn,r.get("status"),e)}

SW_CFG={"spdt":"SPDT","dpdt":"DPDT","dpst":"DPST","sp3t":"SP3T","sp4t":"SP4T","dp3t":"DP3T","dp4t":"DP4T","4pst":"4PST"}
def conv_switch(r):
    P=r["params"]; gpn=r["gpn"]
    if not gpn: return None
    cfg=str(s1(P.get("Configuration")) or "")
    cl=cfg.lower()
    m=re.match(r"(\d+):1",cfg)
    is_mux = ("mux" in cl or "crosspoint" in cl or "exchange" in cl or (m and int(m.group(1))>=3))
    e={}  # switchCore fields (valid for both analogSwitch and multiplexer)
    if (v:=f(P.get("Ronmax")) or f(P.get("Rontyp"))) is not None: e["onResistance"]=v
    if (v:=f(P.get("ON-state leakage currentmax"))) is not None: e["offLeakageCurrent"]=v
    if (v:=f(P.get("Bandwidth")) or f(P.get("Bandwidthmax"))) is not None: e["bandwidth"]=v
    nch=f(P.get("Number of channels"))
    supply={}
    if (v:=f(P.get("Supply voltagemax")) or f(P.get("Drain supply voltagemax")) or f(P.get("Power supply voltage - single"))) is not None: supply["maximumSupplyVoltage"]=v
    if (v:=f(P.get("Supply voltagemin")) or f(P.get("Drain supply voltagemin"))) is not None: supply["minimumSupplyVoltage"]=v
    if supply: e["supply"]=supply
    if is_mux:
        disc="multiplexer"
        n = int(m.group(1)) if m else (int(nch) if nch and nch>=2 else None)  # input count >=2
        if n and n>=2: e["numberOfChannels"]=n
        if cfg: e["multiplexerConfiguration"]=cfg
    else:
        disc="analogSwitch"
        if nch is not None: e["numberOfSwitches"]=int(nch)
        sc=next((vv for k,vv in SW_CFG.items() if k in cl), None)
        e["switchConfiguration"]=sc or "SPST-NO"  # required; SPST defaults to normally-open
    return {disc:mi_of(gpn,r.get("status"),e)}

def main():
    for fn,conv,label in [("adc",conv_adc,"adc"),("dac",conv_dac,"dac"),("switchmux",conv_switch,"switchmux")]:
        recs=json.load(open(f"{MCP}/ti_{fn}.json"))["records"]
        out=[c for c in (conv(r) for r in recs) if c]
        with open(f"{OUT}/{label}.ndjson","w") as fo:
            for r in out: fo.write(json.dumps(r,ensure_ascii=False)+"\n")
        print(f"{label}: {len(out)} / {len(recs)}")

if __name__=="__main__": main()
