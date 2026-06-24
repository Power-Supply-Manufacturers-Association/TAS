#!/usr/bin/env python3
"""Convert Toshiba parametric CSVs -> SAS/PEAS NDJSON records (staged).

Reads the Toshiba downloads param_*.csv, builds one record per part in the SAS
discriminator shape (semiconductor.{mosfet,diode,igbt}), all values SI base
units. Routes each record to one of:
  - staging/toshiba/<type>.main.ndjson    -> complete; promote to TAS catalog
  - staging/toshiba/<type>.quarantine.ndjson -> missing schema-required field(s)

No value is fabricated. Missing required fields are recorded in quarantineReason
and the record goes to the librarian backlog files on promotion.
"""
import csv, json, os, re

DL = "/mnt/c/Users/Alfonso/Downloads"
OUT = "/home/alf/PSMA/TAS/staging/toshiba"
os.makedirs(OUT, exist_ok=True)

# ---- parsing helpers --------------------------------------------------------
def nums(s):
    if s is None:
        return []
    return [float(x) for x in re.findall(r"[-+]?\d*\.?\d+", str(s).replace(",", " "))]

def one(s):
    n = nums(s)
    return n[0] if n else None

def maxabs(s):
    n = nums(s)
    return max((abs(x) for x in n), default=None)

def status_of(lifecycle):
    lc = (lifecycle or "").strip().lower()
    if "under development" in lc or "preview" in lc or "planned" in lc:
        return "preview"
    if "end of life" in lc or "eol" in lc or "discontinu" in lc or "obsolete" in lc:
        return "obsolete"
    if "not recommended" in lc or "nrnd" in lc:
        return "nrnd"
    return "production"

def mech(row, dims_key="Width x Length x Height(mm)"):
    """W x L x H in mm -> mechanical dict (SI metres)."""
    m = {}
    smt = (row.get("Surface mount package") or "").strip().upper()
    if smt == "Y":
        m["assemblyType"] = "smt"
    elif smt == "N":
        m["assemblyType"] = "tht"
    case = (row.get("Toshiba Package Name") or "").strip()
    if case:
        m["case"] = case
    d = nums(row.get(dims_key, ""))
    if len(d) >= 3:
        m["width"] = {"nominal": round(d[0] * 1e-3, 9)}
        m["length"] = {"nominal": round(d[1] * 1e-3, 9)}
        m["height"] = {"nominal": round(d[2] * 1e-3, 9)}
    return m

def part_block(row, technology, subType):
    p = {"partNumber": row["Part Number"].strip(), "technology": technology}
    if subType:
        p["subType"] = subType
    case = (row.get("Toshiba Package Name") or "").strip()
    if case:
        p["case"] = case
    desc = (row.get("Description") or "").strip()
    if desc:
        p["description"] = desc
    aec = (row.get("AEC-Q101(*) For more information please contact the sales representative.") or "").strip()
    if aec and aec.lower().startswith("qualif"):
        p["qualification"] = "Automotive (AEC-Q101)"
    return p

def base_record(disc, row, technology, subType):
    """Build the manufacturerInfo + datasheetInfo skeleton (no electrical yet)."""
    mi = {
        "name": "Toshiba",
        "reference": row["Part Number"].strip(),
        "status": status_of(row.get("Life-cycle")),
    }
    desc = (row.get("Description") or "").strip()
    if desc:
        mi["description"] = desc
    di = {"part": part_block(row, technology, subType)}
    m = mech(row)
    rec = {"semiconductor": {disc: {"manufacturerInfo": mi}}}
    mi["datasheetInfo"] = di
    rec["_mech"] = m  # attach for later (popped before write)
    return rec, di

def finalize(rec, di, electrical):
    di["electrical"] = electrical
    m = rec.pop("_mech")
    if m:
        di["mechanical"] = m
    return rec

# ---- on-resistance picker (highest populated |VGS| = full enhancement) ------
def pick_ron(row, rds_cols):
    best = None  # (vgs, value)
    for c in rds_cols:
        v = one(row.get(c, ""))
        if v is None:
            continue
        vgs = maxabs(c.split("=")[-1])  # ...|VGS|=10V -> 10
        if best is None or (vgs is not None and vgs > best[0]):
            best = (vgs or 0, v)
    return best  # (vgs, ron) or None

# ============================================================================
buckets = {}  # (type, 'main'|'quar') -> list of (record, reason)
def put(typ, kind, rec, reason=None):
    buckets.setdefault((typ, kind), []).append((rec, reason))

MOS_REQ = ["drainSourceVoltage", "onResistance", "continuousDrainCurrent",
           "gateThresholdVoltage", "totalGateCharge"]

def mosfet_electrical(row, rds_cols, qg_key="Qg(nC)", ciss_key="Ciss(pF)",
                      vdss_key="VDSS(V)", vgss_key="VGSS(V)", id_key="ID(A)",
                      pd_key="PD(W)", vth_min=None, vth_max=None):
    e = {}
    if (v := maxabs(row.get(vdss_key, ""))) is not None:
        e["drainSourceVoltage"] = v
    if (v := maxabs(row.get(vgss_key, ""))) is not None:
        e["gateSourceVoltageMax"] = v
    if (v := one(row.get(id_key, ""))) is not None:
        e["continuousDrainCurrent"] = abs(v)
    if pd_key and (v := one(row.get(pd_key, ""))) is not None:
        e["powerDissipation"] = v
    if ciss_key and (v := one(row.get(ciss_key, ""))) is not None:
        e["inputCapacitance"] = round(v * 1e-12, 18)
    if qg_key and (v := one(row.get(qg_key, ""))) is not None:
        e["totalGateCharge"] = round(v * 1e-9, 15)
    ron = pick_ron(row, rds_cols)
    if ron:
        e["onResistance"] = ron[1]
        if ron[0]:
            e["onResistanceVgs"] = ron[0]
    if vth_min is not None or vth_max is not None:
        gv = {}
        if vth_min is not None:
            gv["minimum"] = vth_min
        if vth_max is not None:
            gv["maximum"] = vth_max
        if gv:
            e["gateThresholdVoltage"] = gv
    return e

def route_mosfet(rec, di, e, typ):
    missing = [f for f in MOS_REQ if f not in e]
    di["electrical"] = e
    m = rec.pop("_mech")
    if m:
        di["mechanical"] = m
    if missing:
        rec["quarantineReason"] = ("incomplete Toshiba parametric data; missing required "
                                   "MOSFET field(s): " + ", ".join(missing) + " (2026-06-24)")
        put(typ, "incomplete", rec)
    else:
        put(typ, "main", rec)

def diode_required(e, subType):
    """Return the list of schema-required electrical fields still missing."""
    if subType == "zener":
        req = ["breakdownVoltage", "powerDissipation"]
    elif subType == "esd":
        miss = [] if "standoffVoltage" in e else ["standoffVoltage"]
        if not any(k in e for k in ("peakPulseCurrent", "peakPulsePower", "esdVoltageContact")):
            miss.append("peakPulseCurrent|peakPulsePower|esdVoltageContact")
        return miss
    elif subType == "tvs":
        miss = [f for f in ("standoffVoltage", "clampingVoltage") if f not in e]
        if not any(k in e for k in ("peakPulseCurrent", "peakPulsePower")):
            miss.append("peakPulseCurrent|peakPulsePower")
        return miss
    else:  # rectifier family
        req = ["reverseVoltage", "forwardVoltage", "forwardCurrent"]
    return [f for f in req if f not in e]

def route_diode(rec, di, e, subType, vf_note=None):
    finalize(rec, di, e)
    missing = diode_required(e, subType)
    if not missing:
        put("diodes", "main", rec)
    elif missing == ["forwardVoltage"]:
        rec["quarantineReason"] = (vf_note or
            "missing required forwardVoltage (V_F not in Toshiba parametric CSV) (2026-06-24)")
        put("diodes", "missing_vf", rec)
    else:
        rec["quarantineReason"] = (f"{subType} diode missing required field(s): "
                                   + ", ".join(missing) + " (2026-06-24)")
        put("diodes", "incomplete", rec)

# ---- 304 / 313 single Si MOSFETs (no Vth column) ---------------------------
for fid in ["304", "313"]:
    rows = list(csv.DictReader(open(f"{DL}/param_{fid}_en_us.csv", encoding="utf-8-sig")))
    rds_cols = [c for c in rows[0].keys() if c.startswith("RDS(ON)")]
    for row in rows:
        if not row.get("Part Number", "").strip():
            continue
        sub = "pChannel" if "p-ch" in (row.get("Polarity") or "").lower() else "nChannel"
        rec, di = base_record("mosfet", row, "Si", sub)
        e = mosfet_electrical(row, rds_cols)
        route_mosfet(rec, di, e, "mosfets")

# ---- 317 single SiC MOSFETs (complete: has Vth) ----------------------------
rows = list(csv.DictReader(open(f"{DL}/param_317_en_us.csv", encoding="utf-8-sig")))
rds_cols = [c for c in rows[0].keys() if c.startswith("RDS(ON)")]
for row in rows:
    if not row.get("Part Number", "").strip():
        continue
    sub = "pChannel" if "p-ch" in (row.get("Polarity") or "").lower() else "nChannel"
    rec, di = base_record("mosfet", row, "SiC", sub)
    e = mosfet_electrical(row, rds_cols, vth_min=one(row.get("Vth(Min)(V)")),
                          vth_max=one(row.get("Vth(Max)(V)")))
    route_mosfet(rec, di, e, "mosfets")

# ---- 314 dual complementary MOSFETs -> two records (#N / #P) ---------------
rows = list(csv.DictReader(open(f"{DL}/param_314_en_us.csv", encoding="utf-8-sig")))
for row in rows:
    mpn = row.get("Part Number", "").strip()
    if not mpn:
        continue
    vdss = nums(row.get("VDSS(V)", ""))  # [30, -30]
    for idx, (suf, sub, idk, cissk, qgk, rcols) in enumerate([
        ("#N", "nChannel", "ID:Q1(A)", "Ciss:Q1(pF)Typ.", "Qg:Q1(nC)Typ.",
         [c for c in row if c.startswith("RDS(ON):Q1")]),
        ("#P", "pChannel", "ID:Q2(A)", "Ciss:Q2(pF)Typ.", "Qg:Q2(nC)Typ.",
         [c for c in row if c.startswith("RDS(ON):Q2")]),
    ]):
        r2 = dict(row)
        r2["Part Number"] = mpn + suf
        r2["VDSS(V)"] = str(abs(vdss[idx])) if idx < len(vdss) else ""
        rec, di = base_record("mosfet", r2, "Si", sub)
        rec["semiconductor"]["mosfet"]["manufacturerInfo"]["description"] = (
            (row.get("Description") or "").strip() +
            f" [orderable MPN {mpn}; this record = {sub} device of the dual]")
        di["part"]["description"] = rec["semiconductor"]["mosfet"]["manufacturerInfo"]["description"]
        e = mosfet_electrical(r2, rcols, qg_key=qgk, ciss_key=cissk, id_key=idk,
                              vgss_key="VGSS(V)", pd_key="PD(W)")
        route_mosfet(rec, di, e, "mosfets")

# ============================================================================
# IGBTs (308) — no Vce(sat) column -> all incomplete
rows = list(csv.DictReader(open(f"{DL}/param_308_en_us.csv", encoding="utf-8-sig")))
for row in rows:
    if not row.get("Part Number", "").strip():
        continue
    rec, di = base_record("igbt", row, "Si", "nChannel")
    e = {}
    if (v := one(row.get("VCES (Max)(V)"))) is not None:
        e["collectorEmitterVoltage"] = v
    if (v := one(row.get("IC (Max)(A)"))) is not None:
        e["continuousCollectorCurrent"] = v
    missing = [f for f in ["collectorEmitterVoltage", "continuousCollectorCurrent",
                           "collectorEmitterSaturation"] if f not in e]
    di["electrical"] = e
    m = rec.pop("_mech")
    if m:
        di["mechanical"] = m
    rec["quarantineReason"] = ("incomplete Toshiba parametric data; missing required IGBT "
                               "field(s): " + ", ".join(missing) +
                               " (no Vce(sat) in parametric CSV) (2026-06-24)")
    put("igbts", "incomplete", rec)

# ============================================================================
# DIODES
def diode_mech(row):
    return mech(row)

def col(row, frag):
    for k in row:
        if frag.lower() in k.lower():
            return row[k]
    return None

# 205 Zener (VZ + PD) -> main
for row in csv.DictReader(open(f"{DL}/param_205_en_us.csv", encoding="utf-8-sig")):
    if not row.get("Part Number", "").strip():
        continue
    rec, di = base_record("diode", row, "Si", "zener")
    e = {}
    if (v := one(row.get("VZ (Typ.) (V)"))) is not None:
        e["breakdownVoltage"] = {"nominal": v}
    if (v := one(row.get("PD (Max) (W)"))) is not None:
        e["powerDissipation"] = v
    route_diode(rec, di, e, "zener")

# 206 ESD protection diodes -> esd
for row in csv.DictReader(open(f"{DL}/param_206_en_us.csv", encoding="utf-8-sig")):
    if not row.get("Part Number", "").strip():
        continue
    rec, di = base_record("diode", row, "Si", "esd")
    e = {}
    if (v := maxabs(col(row, "VRWM"))) is not None:
        e["standoffVoltage"] = v
    if (v := one(col(row, "VBR"))) is not None:
        e["breakdownVoltage"] = {"minimum": abs(v)}
    if (v := one(col(row, "Clamp voltage"))) is not None:
        e["clampingVoltage"] = abs(v)
    if (v := one(col(row, "Peak pulsecurrent"))) is not None:
        e["peakPulseCurrent"] = v
    if (v := maxabs(col(row, "Electrostaticdischarge voltage"))) is not None:
        e["esdVoltageContact"] = v * 1000.0  # kV -> V
    if (v := one(col(row, "ReversecurrentIR"))) is not None:
        e["reverseLeakageCurrent"] = v * 1e-6  # uA -> A
    if (v := one(col(row, "CT (Typ.)"))) is not None:
        e["junctionCapacitance"] = round(v * 1e-12, 18)
    route_diode(rec, di, e, "esd")

# 207 switching diodes (Si)
for row in csv.DictReader(open(f"{DL}/param_207_en_us.csv", encoding="utf-8-sig")):
    if not row.get("Part Number", "").strip():
        continue
    cat = (row.get("Product Category") or "").lower()
    sub = "schottky" if "schottky" in cat else "fast"
    rec, di = base_record("diode", row, "Si", sub)
    e = {}
    if (v := one(row.get("VR (Max) (V)"))) is not None:
        e["reverseVoltage"] = v
    if (v := one(row.get("IO (Max) (A)"))) is not None:
        e["forwardCurrent"] = v
    vf = one(row.get("VF (Max) (V)")) or one(row.get("VF (Typ.) (V)"))
    if vf is not None:
        e["forwardVoltage"] = vf
    if (v := one(row.get("CT (Typ.) (pF)"))) is not None:
        e["junctionCapacitance"] = round(v * 1e-12, 18)
    route_diode(rec, di, e, sub)

# 208 small-signal Schottky
for row in csv.DictReader(open(f"{DL}/param_208_en_us.csv", encoding="utf-8-sig")):
    if not row.get("Part Number", "").strip():
        continue
    rec, di = base_record("diode", row, "Si", "schottky")
    e = {}
    if (v := one(row.get("VR/VRRM (Max)(V)"))) is not None:
        e["reverseVoltage"] = v
    if (v := one(row.get("IF/IF(AV)/IO (Max) (A)"))) is not None:
        e["forwardCurrent"] = v
    if (v := one(row.get("VFM (Max) (V)"))) is not None:
        e["forwardVoltage"] = v
    if (v := one(row.get("IR/IRRM (Max)(mA)"))) is not None:
        e["reverseLeakageCurrent"] = v * 1e-3  # mA -> A
    if (v := one(row.get("CT/Cj (Typ.)(pF)"))) is not None:
        e["junctionCapacitance"] = round(v * 1e-12, 18)
    route_diode(rec, di, e, "schottky")

# 210 SiC Schottky
for row in csv.DictReader(open(f"{DL}/param_210_en_us.csv", encoding="utf-8-sig")):
    if not row.get("Part Number", "").strip():
        continue
    rec, di = base_record("diode", row, "SiC", "sicSchottky")
    e = {}
    if (v := one(row.get("VRRM (Max)(V)"))) is not None:
        e["reverseVoltage"] = v
    if (v := one(row.get("IF(DC) (Max)(A)"))) is not None:
        e["forwardCurrent"] = v
    vf = one(row.get("VF (Max)(V)")) or one(row.get("VF (Typ.)(V)"))
    if vf is not None:
        e["forwardVoltage"] = vf
    if (v := one(row.get("IR (Max)(μA)"))) is not None:
        e["reverseLeakageCurrent"] = v * 1e-6
    route_diode(rec, di, e, "sicSchottky")

# 204 rectifier/fast-recovery (NO VF) -> librarian VF backlog
for row in csv.DictReader(open(f"{DL}/param_204_en_us.csv", encoding="utf-8-sig")):
    if not row.get("Part Number", "").strip():
        continue
    cat = (row.get("Category") or "").lower()
    sub = "fastRecovery" if "super fast" in cat else ("fast" if ("fast" in cat or "efficiency" in cat) else "standard")
    rec, di = base_record("diode", row, "Si", sub)
    e = {}
    if (v := one(row.get("VRRM (Max)(V)"))) is not None:
        e["reverseVoltage"] = v
    if (v := one(row.get("IF(AV) (Max)(A)"))) is not None:
        e["forwardCurrent"] = v
    if (v := one(row.get("trr (Max)(ns)"))) is not None:
        e["reverseRecoveryTime"] = round(v * 1e-9, 15)
    route_diode(rec, di, e, sub)

# ============================================================================
# write staging files
counts = {}
for (typ, kind), items in sorted(buckets.items()):
    path = f"{OUT}/{typ}.{kind}.ndjson"
    with open(path, "w") as f:
        for rec, _ in items:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    counts[f"{typ}.{kind}"] = len(items)
print(json.dumps(counts, indent=2))
