#!/usr/bin/env python3
"""ABT #400: build CONAS records for Würth connectors from table + datasheet.

Two sources, merged per order code:
  staging/we_conn/rows.jsonl   product-line table columns (I_R, pins, pitch, mount, ...)
  staging/we_conn/specs.jsonl  datasheet PDF properties (insulation resistance, DWV,
                               mating cycles, contact resistance, materials, VSWR, ...)

The datasheet wins wherever both publish a value: it is the controlled document, the
table is a catalogue rendering of it.

NOTHING IS INVENTED. A field absent from both sources is absent from the record. Parts
that cannot satisfy CONAS's required fields (rf without a published impedance, non-rf
without a published per-contact current, dataInterface without an identifiable standard)
are written to a quarantine file with a reason instead of being fabricated into validity.

  we_connectors_build.py            # dry run: counts + rejection reasons
  we_connectors_build.py --apply    # write staging/we/records.ndjson (+ quarantine)
"""
import argparse
import gzip
import json
import re
import sys
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
STAGE = TAS / "staging" / "we_conn"
OUTDIR = TAS / "staging" / "we"
OUT = OUTDIR / "records.ndjson"
QUAR = OUTDIR / "quarantine.ndjson"
RETRIEVED = "2026-07-31"

MFR = "Würth Elektronik"
DS_URL = "https://www.we-online.com/components/products/datasheet/{}.pdf"
PL_URL = "https://www.we-online.com/en/components/products/{}#{}"

# Category path -> (family, extra familyDetails). Order matters: first match wins.
FAMILY_RULES = [
    ("/coax", "rf", {}),
    ("/circular-connectors", "circular", {}),
    ("/terminal_blocks", "terminalBlock", {}),
    ("/fpc_connector_and_ffc_cab", "fpcFfc", {}),
    ("/board-to-board_connectors/wr-phd", "pinHeaderSocket", {}),
    ("/board-to-board_connectors", "boardToBoard", {}),
    ("/wire-to-board", "wireToBoard", {}),
    ("/input_output_connectors/wr-crd", "cardEdge", {}),
    ("/input_output_connectors/wr-dsub", "dataInterface", {"interfaceStandard": "D-Sub"}),
    ("/input_output_connectors/wr-mj", "dataInterface", {"interfaceStandard": "Modular Jack"}),
    ("/led_connectors", "wireToBoard", {}),
]
# Product lines whose category is ambiguous but whose NAME pins the family.
NAME_RULES = [
    (r"WR[-_]?PHD", "pinHeaderSocket", {}),
    (r"WR[-_]?FPC|FFC", "fpcFfc", {}),
    (r"CONTBL|TERMINAL_BLOCK", "terminalBlock", {}),
    (r"COAX|SMA|SMP|SMB|MCX|MMCX|BNC|TNC|UMRF|RPSMA|N_TYPE|WR_ADPT", "rf", {}),
    (r"CIRCM|CIRCULAR", "circular", {}),
    (r"DSUB|D-SUB", "dataInterface", {"interfaceStandard": "D-Sub"}),
    (r"USB", "dataInterface", {"interfaceStandard": "USB"}),
    (r"MODULAR_JACK|WR_MJ", "dataInterface", {"interfaceStandard": "Modular Jack"}),
    (r"HDMI", "dataInterface", {"interfaceStandard": "HDMI"}),
    (r"WTB|WR_WTB|MPC|RAST|BHD|NPC|REDFIT|MINI_MODULE|WR_MM", "wireToBoard", {}),
    (r"BTB|BOARD_TO_BOARD", "boardToBoard", {}),
]
# Categories that are not connectors at all.
SKIP_CATEGORIES = ("/connector_tools", "/design_kits_connector", "/battery_fuseholder",
                   "/battery_holders", "/reference_guides")
SKIP_NAMES = (r"^DESIGNKIT", r"TOOL", r"CRIMPING", r"EXTRACTION", r"LABEL", r"STICKER",
              r"FILTER_BAG", r"ACCESSOR")

# conas-materials ids that actually exist in CONAS/data/conas-materials.ndjson. A material
# WE names but the registry does not define is left out — a dangling ref is worse than a
# missing field, and inventing an id is not ours to do.
MATERIALS = {
    "beryllium copper": "cube-berylliumCopper",
    "brass": "cuznSn-brass",
    "copper": "cu-etp-copper",
    "pa66": "pa66-nylon",
    "pa66 gf30": "pa66-gf30",
    "lcp gf30": "lcp-gf30",
}
PLATINGS = {"gold": "au-gold", "tin": "sn-tin", "nickel": "ni-nickel"}

MOUNT = {"smt": "smt", "smd": "smt", "tht": "tht", "thr": "tht", "press-fit": "pressFit",
         "pressfit": "pressFit", "skedd": "skedd", "panel": "panel", "cable": "cable",
         "solder": "tht"}
ORIENT = {"straight": "vertical", "vertical": "vertical", "right angle": "rightAngle",
          "angled": "rightAngle", "horizontal": "horizontal"}


def clean(v):
    """Table cells arrive as 'Pins8' / 'Pitch2.54 mm' — the label is glued to the value."""
    if v is None:
        return None
    v = re.sub(r"\s+", " ", v).strip()
    return None if v in ("", "-", "–") else v


SCALE = {"mm": 1e-3, "cm": 1e-2, "m": 1.0, "mA": 1e-3, "A": 1.0, "kV": 1e3, "V": 1.0,
         "mΩ": 1e-3, "Ω": 1.0, "kΩ": 1e3, "MΩ": 1e6, "GHz": 1e9, "MHz": 1e6, "kHz": 1e3}
# WE writes thousands with a space ('5 000 V'). Matching only \d+ read that as 5, and
# because a missing unit was treated as "unitless, scale 1", it sailed through as a 5 V
# withstanding rating on a 125 V part — caught by Blade Runner's CONN_DWV_VS_RATED.
CELL_NUM = re.compile(r"(-?\d+(?:[  ]\d{3})*(?:[.,]\d+)?)\s*"
                      r"(mm|cm|mA|kV|mΩ|kΩ|MΩ|GHz|MHz|kHz|[mVAΩ])?")


def cell_num(cells, key, units=None):
    v = clean(cells.get(key))
    if not v:
        return None
    m = CELL_NUM.search(v)
    if not m:
        return None
    u = m.group(2)
    if units and u not in units:      # a required unit must actually be present
        return None
    x = float(m.group(1).replace(" ", "").replace(" ", "").replace(",", "."))
    return x * SCALE.get(u, 1.0)


def wire_gauge(raw):
    """'Wire Section 28 to 12 (AWG) 0.2 to 2.5 (mm²)' -> AWG span + area span in m^2.

    WE writes the pair in either direction ('12 to 30' as well as '28 to 12'), so both
    ends are sorted rather than taken positionally.
    """
    v = clean(raw)
    if not v:
        return None
    out = {}
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*to\s*(\d+(?:[.,]\d+)?)\s*\(AWG\)", v)
    if m:
        a, b = sorted(float(x.replace(",", ".")) for x in m.groups())
        out["minimumAwg"], out["maximumAwg"] = a, b
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*to\s*(\d+(?:[.,]\d+)?)\s*\(mm²\)", v)
    if m:
        a, b = sorted(float(x.replace(",", ".")) for x in m.groups())
        out["minimumArea"], out["maximumArea"] = a * 1e-6, b * 1e-6
    return out or None


def family_of(pl, category, title):
    hay = f"{pl} {title or ''}".upper()
    if any(re.search(p, hay) for p in SKIP_NAMES):
        return None, None, "tool/kit/accessory, not a connector"
    if category and any(s in category for s in SKIP_CATEGORIES):
        return None, None, f"non-connector category {category}"
    if category:
        for frag, fam, extra in FAMILY_RULES:
            if frag in category:
                return fam, dict(extra), None
    for pat, fam, extra in NAME_RULES:
        if re.search(pat, hay):
            return fam, dict(extra), None
    return None, None, "family not determinable from category or product-line name"


def category_map():
    """product line -> deepest em/connectors category path on its own cached page."""
    out = {}
    for p in (STAGE / "html").glob("*.html.gz"):
        with gzip.open(p, "rt", encoding="utf-8", errors="replace") as fh:
            h = fh.read()
        cs = set(re.findall(r'/en/components/products/(em/connectors[a-zA-Z0-9_/-]*)"', h))
        if cs:
            out[p.name[:-8]] = max(cs, key=lambda c: (c.count("/"), len(c)))
    return out


def build(code, pl, cells, spec, category):
    title = cells.get("_title")
    fam, extra, why = family_of(pl, category, title)
    if not fam:
        return None, why

    el, mech, env, fd = {}, {}, {}, dict(extra or {})
    fd.pop("family", None)

    # --- electrical: datasheet first, table as fallback -----------------------------
    ir = spec.get("ratedCurrentPerContact")
    if ir is None:
        ir = cell_num(cells, "I R", ["A", "mA"])
    if ir is not None:
        el["ratedCurrentPerContact"] = ir
    rv = spec.get("ratedVoltage")
    if rv is None:
        rv = cell_num(cells, "Working Voltage", ["V", "kV"])
    if rv is not None:
        el["ratedVoltage"] = rv
    if spec.get("dielectricWithstandingVoltage") is not None:
        el["dielectricWithstandingVoltage"] = spec["dielectricWithstandingVoltage"]
    else:
        dwv = cell_num(cells, "Withstanding Voltage", ["V", "kV"])
        if dwv is not None:
            el["dielectricWithstandingVoltage"] = dwv
    if spec.get("insulationResistance") is not None:
        el["insulationResistance"] = spec["insulationResistance"]
    cr = spec.get("contactResistance")
    if cr is None:
        cr = cell_num(cells, "Contact Resistance", ["mΩ", "Ω", "kΩ"])
        crmax = True
    else:
        crmax = spec.get("contactResistanceIsMax", True)
    if cr is not None:
        el["contactResistance"] = {"maximum": cr} if crmax else {"nominal": cr}

    # --- mechanical -----------------------------------------------------------------
    pins = cell_num(cells, "Pins")
    if pins and pins > 0:
        mech["positions"] = int(pins)
    pitch = cell_num(cells, "Pitch", ["mm", "cm", "m"])
    if pitch:
        mech["pitch"] = pitch
    if spec.get("matingCycles") is not None:
        mech["matingCycles"] = spec["matingCycles"]
    mnt = (clean(cells.get("Mount")) or "").replace("Mount", "").strip().lower()
    if mnt in MOUNT:
        mech["mountingStyle"] = MOUNT[mnt]
    typ = (clean(cells.get("Type")) or "").replace("Type", "").strip().lower()
    if typ in ORIENT:
        mech["orientation"] = ORIENT[typ]

    # --- environmental --------------------------------------------------------------
    ot = spec.get("operatingTemperature")
    if ot is None:
        raw = clean(cells.get("Operating Temperature")) or ""
        m = re.search(r"(-?\d+)\s*°?C?\s*up to\s*\+?(-?\d+)\s*°C", raw)
        if m:
            ot = {"minimum": float(m.group(1)), "maximum": float(m.group(2))}
    if ot:
        env["operatingTemperature"] = ot
    ip = clean(cells.get("Ingress Protection Code"))
    if ip:
        ip = ip.replace("Ingress Protection Code", "").strip()
        if re.fullmatch(r"IP\d{2}[A-Z]?", ip):
            env["ipRating"] = ip

    # --- family details -------------------------------------------------------------
    if fam == "rf":
        z = spec.get("characteristicImpedance") or cell_num(cells, "Z", ["Ω"])
        if z is None:
            return None, "rf connector with no published characteristic impedance"
        fd["characteristicImpedance"] = z
        if spec.get("maxVswr") is not None:
            fd["maxVswr"] = spec["maxVswr"]
        if spec.get("frequencyRange"):
            fd["frequencyRange"] = spec["frequencyRange"]
        iface = re.search(r"\b(SMP|SMA|SMB|MCX|MMCX|BNC|TNC|U\.?FL|UMRF|RP-?SMA|N)\b",
                          (title or pl).upper())
        if iface:
            fd["interface"] = iface.group(1)
    elif fam == "dataInterface" and "interfaceStandard" not in fd:
        std = re.search(r"\b(USB|HDMI|D-?SUB|RJ45|DISPLAYPORT)\b", (title or pl).upper())
        if not std:
            return None, "dataInterface with no identifiable interface standard"
        fd["interfaceStandard"] = {"DSUB": "D-Sub", "D-SUB": "D-Sub"}.get(
            std.group(1), std.group(1).title() if std.group(1) != "USB" else "USB")

    # --- family-specific data WE actually publishes in the table --------------------
    hay = f"{pl} {title or ''}".upper()
    wgr = wire_gauge(cells.get("Wire Section") or cells.get("Stranded Wire Section (AWG)"))
    if fam in ("wireToBoard", "wireToWire", "terminalBlock") and wgr:
        fd["wireGaugeRange"] = wgr
    if fam in ("wireToBoard", "wireToWire"):
        if "CRIMP" in (mnt or "").upper() or "CRIMP" in hay:
            fd["termination"] = "crimp"
        elif "IDC" in hay or "IDC" in (clean(cells.get("Description")) or "").upper():
            fd["termination"] = "idc"
    if fam == "terminalBlock":
        if category and "spring_clamp" in category:
            fd["clampType"] = "springCage"
        elif category and "rising_cage" in category:
            fd["clampType"] = "screw"
        if category and "pluggable" in category:
            fd["pluggable"] = True
    if fam == "pinHeaderSocket":
        ctype = (clean(cells.get("Contact Type")) or "").replace("Contact Type", "").strip()
        if ctype.lower() in ("stamped", "machined"):
            fd["pinStyle"] = ctype.lower()
    if fam == "fpcFfc":
        if re.search(r"NON[-_]?ZIF", hay):
            fd["actuatorType"] = "nonZif"
        elif "ZIF" in hay:
            fd["actuatorType"] = "zif"
    if fam == "circular":
        cod = (clean(cells.get("Coding")) or "").replace("Coding", "").strip()
        if re.fullmatch(r"[A-Z0-9]{1,3}", cod):
            fd["coding"] = cod
    if fam in ("circular", "dataInterface"):
        sh = (clean(cells.get("Shielding")) or "").replace("Shielding", "").strip().lower()
        if sh in ("shielded", "unshielded"):
            fd["shielded"] = sh == "shielded"
    fd["family"] = fam

    if fam != "rf" and "ratedCurrentPerContact" not in el:
        return None, "no published rated current per contact (CONAS requires it)"

    # --- materials: only ids the registry actually defines ---------------------------
    mat = {}
    cm = (spec.get("contactMaterial") or "").lower().strip()
    im = (spec.get("insulatorMaterial") or "").lower().strip()
    for key, ref in MATERIALS.items():
        if cm == key:
            mat["contactBaseMaterialRef"] = ref
        if im == key:
            mat["housingMaterialRef"] = ref
    plating = {}
    cp = (spec.get("contactPlating") or "").lower()
    for key, ref in PLATINGS.items():
        if re.search(rf"\b{key}\b", cp) and "over " + key not in cp:
            plating["matingAreaMaterialRef"] = ref
            break
    m = re.search(r"over\s+(gold|tin|nickel)", cp)
    if m:
        plating["underplatingMaterialRef"] = PLATINGS[m.group(1)]
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*[µμ]m", cp)
    if m and plating.get("matingAreaMaterialRef"):
        plating["matingAreaThickness"] = float(m.group(1).replace(",", ".")) * 1e-6
    # `material` requires BOTH contact base and housing refs, so it is all-or-nothing.
    if "contactBaseMaterialRef" in mat and "housingMaterialRef" in mat:
        if plating.get("matingAreaMaterialRef"):
            mat["contactPlating"] = plating
    else:
        mat = {}

    status = "production"
    st = (clean(cells.get("Status")) or "").lower()
    if "obsolete" in st or "discontinued" in st:
        status = "obsolete"
    elif "nrnd" in st or "not recommended" in st:
        status = "nrnd"

    ds = {
        "part": {k: v for k, v in {
            "partNumber": code,
            "description": title,
            "series": (title or "").split()[0] if title else None,
        }.items() if v},
        "electrical": el,
        "mechanical": mech,
        "familyDetails": fd,
        "provenance": [
            {"source": "manufacturerParametric",
             "sourceName": f"we-online.com product-line table {pl}",
             "sourceUrl": PL_URL.format(pl, code),
             "retrievedDate": RETRIEVED},
        ],
    }
    if env:
        ds["environmental"] = env
    if mat:
        ds["material"] = mat
    if spec:
        ds["provenance"].append(
            {"source": "manufacturerDatasheet",
             "sourceName": f"Würth Elektronik datasheet {code}",
             "sourceUrl": DS_URL.format(code),
             "retrievedDate": RETRIEVED})

    return {"connector": {"manufacturerInfo": {
        "name": MFR, "reference": code, "status": status,
        "datasheetUrl": DS_URL.format(code), "datasheetInfo": ds}}}, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    from merge_staged_connectors import build_validator
    gate = BladeGate("connector")
    v = build_validator()

    cats = category_map()
    specs = {}
    sp = STAGE / "specs.jsonl"
    if sp.exists():
        for ln in sp.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                r = json.loads(ln)
                specs[r["orderCode"]] = r["spec"]

    rows = {}
    for ln in (STAGE / "rows.jsonl").read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        pl = r["productLine"]
        for row in r["rows"]:
            e = rows.setdefault(row["orderCode"], {"pls": [], "cells": {}})
            e["pls"].append(pl)
            e["cells"].update(row["cells"])

    # Existing references, so a re-run cannot double-insert.
    have = set()
    with (TAS / "data" / "connectors.ndjson").open(encoding="utf-8") as fh:
        for line in fh:
            if '"reference"' not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            ref = ((o.get("connector") or {}).get("manufacturerInfo") or {}).get("reference")
            if ref:
                have.add(ref)

    good, quar, reasons, schema_bad, blade_bad = [], [], {}, [], []
    dupes = 0
    for code, e in sorted(rows.items()):
        if code in have:
            dupes += 1
            continue
        # Prefer the most specific product line (one with a known category).
        pl = next((p for p in e["pls"] if p in cats), e["pls"][0])
        rec, why = build(code, pl, e["cells"], specs.get(code, {}), cats.get(pl))
        if rec is None:
            reasons[why] = reasons.get(why, 0) + 1
            quar.append({"orderCode": code, "productLine": pl, "quarantineReason": why,
                         "cells": e["cells"], "spec": specs.get(code, {})})
            continue
        c = rec["connector"]
        errs = sorted(v.iter_errors(c), key=lambda x: x.path)
        if errs:
            reasons["schema-invalid"] = reasons.get("schema-invalid", 0) + 1
            if len(schema_bad) < 6:
                schema_bad.append(f"{code}: {errs[0].message[:130]}")
            quar.append({"orderCode": code, "productLine": pl,
                         "quarantineReason": f"schema: {errs[0].message[:150]}"})
            continue
        ok, bwhy = gate.check(c)
        if not ok:
            reasons["blade-impossible"] = reasons.get("blade-impossible", 0) + 1
            if len(blade_bad) < 6:
                blade_bad.append(f"{code}: {bwhy}")
            quar.append({"orderCode": code, "productLine": pl,
                         "quarantineReason": f"blade: {bwhy}"})
            continue
        good.append(rec)

    print(f"order codes staged      : {len(rows)}")
    print(f"already in TAS          : {dupes}")
    print(f"BUILT + VALIDATED       : {len(good)}")
    print(f"not built               : {len(quar)}")
    for w, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>6}  {w}")
    for b in schema_bad:
        print("   schema:", b)
    for b in blade_bad:
        print("   BLADE :", b)
    print(" ", gate.summary())

    # Field coverage of what we are about to write.
    cov = {}
    for r in good:
        ds = r["connector"]["manufacturerInfo"]["datasheetInfo"]
        for sec in ("electrical", "mechanical", "environmental", "material",
                    "familyDetails", "mating"):
            for k in (ds.get(sec) or {}):
                cov[f"{sec}.{k}"] = cov.get(f"{sec}.{k}", 0) + 1
    print("\nfield coverage of built records:")
    for k, n in sorted(cov.items(), key=lambda kv: -kv[1]):
        print(f"   {n:>6} ({100*n/max(len(good),1):>4.0f}%)  {k}")

    if not a.apply:
        print("\nDRY RUN — pass --apply to stage")
        return 0
    OUTDIR.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for r in good:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with QUAR.open("w", encoding="utf-8") as fh:
        for q in quar:
            fh.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"\nstaged {len(good)} -> {OUT}\n       {len(quar)} -> {QUAR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
