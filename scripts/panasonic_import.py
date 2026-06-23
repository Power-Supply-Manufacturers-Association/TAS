#!/usr/bin/env python3
"""Map -> validate -> dedupe -> write Panasonic records to TAS staging.

For each raw category file in data/staging/panasonic_raw/*.jsonl:
  1. map_record (panasonic_map)
  2. jsonschema validate inner object against the family schema (authoritative,
     mirrors tests/test_data.py registry)
  3. C++ tas_validator physics check for magnetic/capacitor/resistor (not varistor)
     -> IMPOSSIBLE finding => quarantine
  4. dedupe vs existing TAS data on lowercased reference/partNumber
  5. write OK records to data/staging/panasonic_<type>.ndjson,
     rejects to data/staging/panasonic_<type>.quarantine.ndjson

Does NOT touch the main data/*.ndjson files. Prints a per-category report.
"""
import json, sys, glob, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panasonic_map as M

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))           # TAS/
PROTEUS = os.path.dirname(REPO)                                               # PSMA/
RAW = os.path.join(REPO, "data", "staging", "panasonic_raw")
STAGE = os.path.join(REPO, "data", "staging")

TYPE_FILE = {"capacitor": "capacitors", "resistor": "resistors",
             "varistor": "varistors", "magnetic": "magnetics"}
DISC = {"capacitor": ["capacitor"], "resistor": ["resistor"],
        "varistor": ["varistor"], "magnetic": ["magnetic"]}
SCHEMA = {"capacitor": ("CAS", "capacitor.json"), "resistor": ("RAS", "resistor.json"),
          "varistor": ("RAS", "varistor.json"), "magnetic": ("MAS", "magnetic.json")}

# ---- jsonschema registry (mirrors tests/test_data.py _build_full_registry) ----
def build_registry():
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    def walk(d):
        for p in sorted(glob.glob(os.path.join(d, "**", "*.json"), recursive=True)):
            try: yield p, json.load(open(p))
            except Exception: pass
    by_id, by_path = {}, {}
    for repo in ("PEAS", "MAS", "CAS", "RAS", "SAS", "TAS", "CIAS", "AAS"):
        d = os.path.join(PROTEUS, repo, "schemas")
        if not os.path.isdir(d): continue
        for path, schema in walk(d):
            by_path[path] = schema
            sid = schema.get("$id")
            if sid: by_id[sid] = schema
    META = {"$schema", "$id", "title", "description", "$comment"}
    for sid, schema in list(by_id.items()):
        body = set(schema.keys()) - META
        if body == {"$ref"} and schema["$ref"].startswith((".", "/")):
            path = next((p for p, s in by_path.items() if s is schema), None)
            if not path: continue
            target = os.path.normpath(os.path.join(os.path.dirname(path), schema["$ref"]))
            ts = by_path.get(target)
            if ts:
                inl = {k: v for k, v in ts.items() if k not in ("$id", "$schema")}
                inl["$schema"] = schema.get("$schema", "https://json-schema.org/draft/2020-12/schema")
                inl["$id"] = sid
                by_id[sid] = inl
    return Registry().with_resources(
        [(sid, Resource(contents=s, specification=DRAFT202012)) for sid, s in by_id.items()])

def load_validators():
    from jsonschema import Draft202012Validator
    reg = build_registry()
    out = {}
    for t, (repo, f) in SCHEMA.items():
        schema = json.load(open(os.path.join(PROTEUS, repo, "schemas", f)))
        out[t] = Draft202012Validator(schema, registry=reg)
    return out

def load_cpp_validator():
    sys.path.insert(0, os.path.join(REPO, "validator", "build"))
    try:
        import tas_validator
        return tas_validator
    except Exception as e:
        print(f"  [warn] C++ validator unavailable ({e}); jsonschema-only", flush=True)
        return None

# ---- existing-reference index for dedupe ----
def existing_refs(type_key):
    fpath = os.path.join(REPO, "data", TYPE_FILE[type_key] + ".ndjson")
    refs = set()
    if not os.path.exists(fpath): return refs
    disc = type_key
    for line in open(fpath):
        line = line.strip()
        if not line: continue
        try: r = json.loads(line)
        except Exception: continue
        comp = r.get(disc, {})
        mi = comp.get("manufacturerInfo", {})
        for v in (mi.get("reference"),
                  mi.get("datasheetInfo", {}).get("part", {}).get("partNumber")):
            if v: refs.add(str(v).lower())
    return refs

def inner(rec, type_key):
    node = rec
    for k in DISC[type_key]:
        node = node[k]
    return node

def main(only=None):
    validators = load_validators()
    cpp = load_cpp_validator()
    files = sorted(glob.glob(os.path.join(RAW, "*.jsonl")))
    if only:
        only = set(only); files = [f for f in files if os.path.basename(f)[:-6] in only]
    # group by type for staging output + dedupe
    out_recs = collections.defaultdict(list)
    quar_recs = collections.defaultdict(list)
    existing = {}
    report = []
    seen_in_run = collections.defaultdict(set)
    for f in files:
        cat = os.path.basename(f)[:-6]
        raws = [json.loads(l) for l in open(f) if l.strip()]
        if not raws: continue
        tkey = raws[0]["type"]
        if tkey not in existing:
            existing[tkey] = existing_refs(tkey)
        v = validators[tkey]
        c = collections.Counter()
        for raw in raws:
            rec, status, reason = M.map_record(raw)
            if status == "skip":
                c["skip"] += 1; continue
            pn = M._part_number(raw).lower()
            if status == "quarantine":
                stub = rec or {"_partNumber": M._part_number(raw), "_type": tkey, "_category": cat}
                quar_recs[tkey].append((stub, f"{cat}: {reason}")); c["quar_map"] += 1; continue
            # dedupe
            if pn in existing[tkey] or pn in seen_in_run[tkey]:
                c["dup"] += 1; continue
            obj = inner(rec, tkey)
            errs = sorted(v.iter_errors(obj), key=lambda e: e.path)
            if errs:
                msg = errs[0].message[:120]
                quar_recs[tkey].append((rec, f"{cat}: schema: {msg}")); c["quar_schema"] += 1; continue
            if cpp is not None and tkey in ("capacitor", "resistor", "magnetic"):
                try:
                    verdict = cpp.validate(rec)
                    if not verdict.valid:
                        bad = ";".join(fd.code for fd in verdict.findings
                                       if str(fd.severity).endswith("Impossible"))
                        quar_recs[tkey].append((rec, f"{cat}: physics: {bad}")); c["quar_phys"] += 1; continue
                except Exception:
                    pass
            seen_in_run[tkey].add(pn)
            out_recs[tkey].append(rec); c["ok"] += 1
        report.append((cat, tkey, len(raws), dict(c)))

    # write staging
    for tkey, recs in out_recs.items():
        p = os.path.join(STAGE, f"panasonic_{TYPE_FILE[tkey]}.ndjson")
        with open(p, "w") as fh:
            for r in recs: fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"WROTE {p}: {len(recs)}")
    for tkey, recs in quar_recs.items():
        p = os.path.join(STAGE, f"panasonic_{TYPE_FILE[tkey]}.quarantine.ndjson")
        with open(p, "w") as fh:
            for r, why in recs:
                r2 = dict(r); r2["_quarantineReason"] = why
                fh.write(json.dumps(r2, ensure_ascii=False) + "\n")
        print(f"WROTE {p}: {len(recs)}")

    print("\n=== PER-CATEGORY REPORT ===")
    print(f"{'category':36} {'type':10} {'raw':>6} {'ok':>6} {'dup':>5} {'qmap':>5} {'qsch':>5} {'qphy':>5} {'skip':>4}")
    tot = collections.Counter()
    for cat, tkey, n, c in report:
        print(f"{cat:36} {tkey:10} {n:6} {c.get('ok',0):6} {c.get('dup',0):5} "
              f"{c.get('quar_map',0):5} {c.get('quar_schema',0):5} {c.get('quar_phys',0):5} {c.get('skip',0):4}")
        for k, val in c.items(): tot[k] += val
    print(f"\nTOTAL ok={tot['ok']} dup={tot['dup']} quar_map={tot['quar_map']} "
          f"quar_schema={tot['quar_schema']} quar_phys={tot['quar_phys']} skip={tot['skip']}")

if __name__ == "__main__":
    main(sys.argv[1:] or None)
