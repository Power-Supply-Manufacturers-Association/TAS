#!/usr/bin/env python3
"""JAE (Japan Aviation Electronics) connector harvest -> CONAS staging NDJSON.

SOURCE (captured live from the real UI, not guessed):
  products.jae.com is an AEM site whose search is backed by **Sitecore Discover**.
  Its config is published in the page <meta> tags of every products.jae.com page:
      <meta name="data-search-config-api-url"
            content="https://discover-apse2.sitecorecloud.io/discover/v2/100108037">
      <meta name="data-search-config-api-key" content="01-78d6e015-...">
      <meta name="data-search-config-sources"
            content='{"en":{"series":"1219241","products":"1219242","articles":"1219243"}}'>

  Exact request (captured with playwright on https://products.jae.com/gl/en/connectors/search/):
      POST https://discover-apse2.sitecorecloud.io/discover/v2/100108037
      authorization: <api key>
      content-type: application/json
      referer: https://products.jae.com/
      {"widget":{"items":[{"rfk_id":"rfkid_7","entity":"content",
                           "sources":["1219242"],
                           "search":{"content":{},"offset":N,"limit":L,
                                     "query":{"operator":"and"},"facet":{"all":false}}}]},
       "context":{"locale":{"country":"us","language":"en"}}}
  Response: {"widgets":[{"content":[...], "total_item":7322, ...}]}

  source 1219242 = orderable products (part numbers); 1219241 = series.

Subcommands:
  pull   -> raw JSON pages to <raw>/jae_products_*.json (+ series)
  build  -> staging/jae/records.ndjson (+ rejected.ndjson), schema + Blade Runner gated
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

TAS = Path(__file__).resolve().parent.parent
API = "https://discover-apse2.sitecorecloud.io/discover/v2/100108037"
KEY = "01-78d6e015-46bd36f702edb690e0618dfda796ba2649d6e9f2"
SRC_PRODUCTS = "1219242"
SRC_SERIES = "1219241"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/151.0.0.0 Safari/537.36")
RAW = Path("/tmp/jae_raw")
TODAY = "2026-07-31"


def query(source, offset, limit):
    body = {"widget": {"items": [{"rfk_id": "rfkid_7", "entity": "content",
                                  "sources": [source],
                                  "search": {"content": {}, "offset": offset, "limit": limit,
                                             "query": {"operator": "and"},
                                             "facet": {"all": False}}}]},
            "context": {"locale": {"country": "us", "language": "en"}}}
    r = requests.post(API, json=body, timeout=60, headers={
        "authorization": KEY, "content-type": "application/json",
        "referer": "https://products.jae.com/", "user-agent": UA})
    r.raise_for_status()
    return r.json()["widgets"][0]


def cmd_pull(a):
    RAW.mkdir(parents=True, exist_ok=True)
    for tag, src in (("products", SRC_PRODUCTS), ("series", SRC_SERIES)):
        off, total, got = 0, None, 0
        while total is None or off < total:
            w = query(src, off, a.limit)
            total = w["total_item"]
            items = w["content"]
            if not items:
                break
            json.dump(items, open(RAW / f"jae_{tag}_{off:06d}.json", "w"))
            got += len(items)
            off += len(items)
            print(f"{tag} {got}/{total}", flush=True)
            time.sleep(0.25)
        print(f"{tag}: {got} items")


# --------------------------------------------------------------------------
# conversion
# --------------------------------------------------------------------------

# JAE category_key -> CONAS familyDetails.family.  Only mappings that are
# unambiguous from JAE's own taxonomy; anything else is rejected, never guessed.
FAMILY = {
    "board-to-board": "boardToBoard",
    "board-to-fpc": "fpcFfc",
    "board-to-cable": "wireToBoard",
    "cable-to-cable": "wireToWire",
    "circular": "circular",
    "coaxial": "rf",
    "high-current": "power",
    "memory-card": "cardEdge",
    "memory-module": "cardEdge",
    "io": "dataInterface",
    "rectangular": "wireToWire",
    "charging-plug": "power",
    "fiber-optic": None,      # optical, not an electrical connector family in CONAS
    "tools": None,            # crimp tools, not connectors
}

CONNECTOR_TYPE_POLARITY = {
    "plug": "male", "header": "male", "pin": "male", "male": "male", "tab": "male",
    "socket": "female", "receptacle": "female", "jack": "female", "female": "female",
}

# connector_type values that are NOT a connector: loose contacts, tools, spares.
NON_CONNECTOR_TYPE = ("accessory", "contact", "tool", "terminal for", "cover",
                      "cap", "spacer", "jig", "crimping", "extraction")

# Data-interface standards.  A record only gets one when the vendor's OWN text
# (description / features / compliance standard) literally names it.
INTERFACE_TOKENS = ["USB4", "USB Type-C", "USB-C", "Type-C", "USB 3.2", "USB 3.1",
                    "USB 3.0", "USB 2.0", "Micro USB", "Mini USB", "USB",
                    "HDMI", "DisplayPort", "Thunderbolt", "RJ45", "RJ-45",
                    "Ethernet", "SATA", "PCI Express", "PCIe", "microSD", "SD",
                    "SIM", "CompactFlash", "D-Sub", "DVI", "FAKRA"]


def first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def values(v):
    """A JAE scalar field -> the list of numbers it publishes.

    '3' -> [3.0]; '23, 13' -> [23.0, 13.0] (a hybrid connector printing one
    rating per contact type); '10,000' -> [10000.0] (thousands separator).
    """
    v = first(v)
    if v is None:
        return []
    if isinstance(v, (int, float)):
        return [float(v)]
    s = str(v).strip()
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):
        return [float(s.replace(",", ""))]
    out = []
    for tok in re.split(r"[,;/]", s):
        m = re.match(r"\s*([-+]?\d*\.?\d+)", tok)
        if m:
            out.append(float(m.group(1)))
    return out


def num(v):
    """Single unambiguous published number, else None.

    A field that publishes SEVERAL values (e.g. rated_current '23, 13' on a
    hybrid power+signal connector) is ambiguous for a single-valued CONAS field,
    so nothing is emitted rather than one of them being picked.
    """
    vs = values(v)
    return vs[0] if len(vs) == 1 else None


def temp_range(v):
    """'-40 to +85' / '-55~+125' (degC) -> {minimum, maximum}."""
    v = first(v)
    if not v:
        return None
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", str(v))
    if len(nums) < 2:
        return None
    lo, hi = float(nums[0]), float(nums[1])
    if lo >= hi:
        return None
    return {"minimum": lo, "maximum": hi}


def polarity(d):
    for ct in (d.get("connector_type") or []):
        t = str(ct).strip().lower()
        for k, v in CONNECTOR_TYPE_POLARITY.items():
            if k in t:
                return v
    return None


STATUS = {"active": "production", "obsolete": "obsolete",
          "active(nrnd)": "nrnd", "pending obsolescence": "nrnd",
          "under development": "preview"}


def interface_standard(d, series_doc):
    """Explicitly published data-interface standard, or None.

    Only a literal occurrence in JAE's own description / features / compliance
    standard counts - the standard is never inferred from a series name.
    """
    blobs = []
    for src in (d, series_doc or {}):
        for k in ("description", "name"):
            if src.get(k):
                blobs.append(str(src[k]))
        for k in ("features", "compliance_standard", "certification_standards"):
            v = src.get(k)
            if isinstance(v, list):
                blobs.extend(str(x) for x in v)
            elif v:
                blobs.append(str(v))
    text = " | ".join(blobs)
    low = text.lower()
    for tok in INTERFACE_TOKENS:
        if tok.lower() in low:
            return tok
    return None


def convert(d, series_specs, series_docs=None):
    """product doc -> ({"connector": ...}, missing[]).  No invented values."""
    pn = (d.get("sku_lable") or d.get("name") or "").strip()
    if not pn:
        return None, ["partNumber"]
    cat = (d.get("category_key") or "").strip().lower()
    fam = FAMILY.get(cat, "MISSING")
    missing = []
    if fam == "MISSING":
        missing.append(f"unmapped JAE category '{cat}'")
        fam = None
    elif fam is None:
        missing.append(f"JAE category '{cat}' has no CONAS connector family")
    ctypes = " ".join(str(x).lower() for x in (d.get("connector_type") or []))
    if any(t in ctypes for t in NON_CONNECTOR_TYPE):
        missing.append(f"not a connector (connector_type '{ctypes[:60]}')")

    part = {"partNumber": pn}
    pol = polarity(d)
    if pol:
        part["matingPolarity"] = pol
    series = d.get("series_label") or d.get("series_id")
    if series:
        part["series"] = str(series)
    desc = d.get("description")
    if desc:
        part["description"] = str(desc)[:1000]

    ss = series_specs.get(str(series).lower()) if series else None

    electrical = {}
    cur = num(d.get("rated_current"))
    if cur is None and ss:
        cur = ss.get("rated_current")
    if cur is not None and cur > 0:
        electrical["ratedCurrentPerContact"] = cur
    volt = num(d.get("rated_voltage"))
    if volt is None and ss:
        volt = ss.get("rated_voltage")
    if volt is not None and volt > 0:
        electrical["ratedVoltage"] = volt
    wv = num(d.get("withstand_voltage"))
    if wv is not None and wv > 0:
        electrical["dielectricWithstandingVoltage"] = wv

    mechanical = {}
    poles = num(d.get("pole_of_product")) or num(d.get("number_of_poles"))
    if poles is not None and poles >= 1:
        mechanical["positions"] = int(poles)
    pitch_mm = num(d.get("mounting_contact_pitch"))
    if pitch_mm is not None and pitch_mm > 0:
        mechanical["pitch"] = pitch_mm / 1000.0          # mm -> m
    cycles = num(d.get("repeated_insertion_removal_times"))
    if cycles is not None and cycles >= 0:
        mechanical["matingCycles"] = int(cycles)

    if fam == "rf":
        # CONAS familyRf REQUIRES characteristicImpedance; JAE Discover does not
        # publish it -> cannot emit an rf record without inventing it.
        missing.append("rf connector without published characteristicImpedance")

    di = {"part": part, "electrical": electrical, "mechanical": mechanical}
    if fam:
        fd = {"family": fam}
        if fam == "dataInterface":
            # interfaceStandard is REQUIRED on familyDataInterface.
            std = interface_standard(d, (series_docs or {}).get(str(series).lower()))
            if std:
                fd["interfaceStandard"] = std
            else:
                missing.append("dataInterface connector without published interfaceStandard")
        di["familyDetails"] = fd
    tr = temp_range(d.get("operating_temp_range")) or (ss or {}).get("operating_temp_range")
    if tr:
        di["environmental"] = {"operatingTemperature": tr}
    di["provenance"] = [{"source": "manufacturerParametric",
                         "sourceName": "JAE products.jae.com Sitecore Discover API "
                                       "(discover-apse2.sitecorecloud.io/discover/v2/100108037, "
                                       "source 1219242)",
                         "sourceUrl": d.get("url") or "https://products.jae.com/gl/en/connectors/",
                         "retrievedDate": TODAY}]

    if "ratedCurrentPerContact" not in electrical and fam != "rf":
        missing.append("ratedCurrentPerContact")
    if "familyDetails" not in di:
        missing.append("familyDetails")

    st = STATUS.get(str(first(d.get("sale_status")) or "active").strip().lower(), "production")
    mi = {"name": "JAE", "reference": pn, "status": st, "datasheetInfo": di}
    if series:
        mi["family"] = str(series)
    if desc:
        mi["description"] = str(desc)[:1000]
    if d.get("url"):
        mi["datasheetUrl"] = d["url"]
    return {"connector": {"manufacturerInfo": mi}}, missing


def load_series_specs():
    """series_label(lower) -> series-level rated_current / rated_voltage / temp.

    Only used to fill a part-level field the PART page leaves null; the series
    page is the same manufacturer's published spec for that series.
    """
    out = {}
    for f in sorted(RAW.glob("jae_series_*.json")):
        for d in json.load(open(f)):
            key = str(d.get("name") or d.get("series_id") or "").strip().lower()
            if not key:
                continue
            e = {}
            c = num(d.get("rated_current"))
            if c is not None and c > 0:
                e["rated_current"] = c
            v = num(d.get("rated_voltage"))
            if v is not None and v > 0:
                e["rated_voltage"] = v
            t = temp_range(d.get("operating_temp_range"))
            if t:
                e["operating_temp_range"] = t
            if e:
                out[key] = e
    return out


def load_series_docs():
    """series name(lower) -> the raw series document (for its features text)."""
    out = {}
    for f in sorted(RAW.glob("jae_series_*.json")):
        for d in json.load(open(f)):
            key = str(d.get("name") or d.get("series_id") or "").strip().lower()
            if key:
                out[key] = d
    return out


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    psma = TAS.parent
    by = {}
    for repo in ("PEAS", "CONAS"):
        for p in (psma / repo / "schemas").rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if isinstance(s, dict) and s.get("$id"):
                by[s["$id"]] = s
    reg = Registry().with_resources(
        [(k, Resource(contents=v, specification=DRAFT202012)) for k, v in by.items()])
    return Draft202012Validator(
        json.loads((psma / "CONAS" / "schemas" / "connector.json").read_text()), registry=reg)


def cmd_build(a):
    out_dir = TAS / "staging" / "jae"
    out_dir.mkdir(parents=True, exist_ok=True)
    series_specs = load_series_specs()
    series_docs = load_series_docs()
    v = build_validator()
    sys.path.insert(0, str(TAS / "validator" / "build-ninja"))
    import tas_validator

    good, bad = [], []
    stats = {"rows": 0, "dup": 0, "schema_fail": 0, "impossible": 0, "suspicious": 0}
    fam_count = {}
    seen = set()
    for f in sorted(RAW.glob("jae_products_*.json")):
        for d in json.load(open(f)):
            stats["rows"] += 1
            pn = (d.get("sku_lable") or d.get("name") or "").strip()
            if pn in seen:
                stats["dup"] += 1
                continue
            seen.add(pn)
            rec, missing = convert(d, series_specs, series_docs)
            if rec is None:
                bad.append({"partNumber": None, "reason": "no part number", "raw": d})
                continue
            if missing:
                rec["quarantineReason"] = "incomplete JAE data: " + "; ".join(missing)
                bad.append(rec)
                continue
            errs = sorted(v.iter_errors(rec["connector"]), key=lambda e: e.path)
            if errs:
                stats["schema_fail"] += 1
                rec["quarantineReason"] = "schema: " + errs[0].message[:300]
                bad.append(rec)
                continue
            verdict = tas_validator.validate(rec)
            imp = [x for x in verdict.findings if str(x.severity) == "IMPOSSIBLE"]
            sus = [x for x in verdict.findings if str(x.severity) == "SUSPICIOUS"]
            if imp:
                stats["impossible"] += 1
                rec["quarantineReason"] = "blade-runner IMPOSSIBLE: " + "; ".join(
                    f"{x.code}: {x.message}" for x in imp)[:300]
                bad.append(rec)
                continue
            if sus:
                stats["suspicious"] += 1
                for x in sus:
                    stats.setdefault("suspicious_codes", {})
                    stats["suspicious_codes"][x.code] = \
                        stats["suspicious_codes"].get(x.code, 0) + 1
            fam = rec["connector"]["manufacturerInfo"]["datasheetInfo"]["familyDetails"]["family"]
            fam_count[fam] = fam_count.get(fam, 0) + 1
            good.append(rec)

    with open(out_dir / "records.ndjson", "w") as fo:
        for r in good:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out_dir / "rejected.ndjson", "w") as fo:
        for r in bad:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats["good"] = len(good)
    stats["rejected"] = len(bad)
    stats["families"] = fam_count
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(fn=cmd_pull)
    p = sub.add_parser("build")
    p.set_defaults(fn=cmd_build)
    args = ap.parse_args()
    args.fn(args)
