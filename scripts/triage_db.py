#!/usr/bin/env python3
"""Triage TAS DB validation failures: full error taxonomy with the actual
offending keys/fields, counts, an example part ref per category, and which
libraries it hits. Read-only — proposes nothing, changes nothing."""
from __future__ import annotations
import json, re, sys
from collections import defaultdict
from pathlib import Path
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]; PSMA = REPO.parent; DATA = REPO / "data"
sys.path.insert(0, str(REPO / "scripts"))
from validate_db import build_registry, LIBS  # reuse

def ref_of(rec):
    def find(o):
        if isinstance(o, dict):
            if 'reference' in o.get('manufacturerInfo', {}): return o['manufacturerInfo']['reference']
            for v in o.values():
                r = find(v)
                if r: return r
        elif isinstance(o, list):
            for v in o:
                r = find(v)
                if r: return r
    return find(rec) or "<no ref>"

def sig(e):
    path = "/".join(str(x) for x in e.absolute_path if not isinstance(x, int))
    if e.validator == "additionalProperties":
        allowed = set(e.schema.get("properties", {}))
        extra = sorted(k for k in (e.instance or {}) if k not in allowed)
        return f"extra keys {extra} @ {path or '<root>'}"
    if e.validator == "required":
        m = re.search(r"'([^']+)' is a required", e.message)
        return f"missing required '{m.group(1) if m else '?'}' @ {path}"
    if e.validator == "type":
        return f"wrong type (got {type(e.instance).__name__}) @ {path}"
    if e.validator == "enum":
        return f"bad enum value {e.instance!r} @ {path}"
    return f"{re.sub(chr(39)+'[^'+chr(39)+']*'+chr(39),'X',e.message)[:60]} @ {path}"

reg = build_registry()
cat = defaultdict(lambda: {"count": 0, "libs": set(), "sample_ref": None, "sample_val": None})

for fname, disc, repo, sch in LIBS:
    p = DATA / fname
    if not p.exists() or p.stat().st_size < 1000:
        continue
    v = Draft202012Validator(json.loads((PSMA / repo / "schemas" / sch).read_text()), registry=reg)
    for line in p.open():
        line = line.strip()
        if not line or line.startswith("version https"): continue
        try: rec = json.loads(line)
        except: continue
        body, ok = rec, True
        for k in disc:
            if isinstance(body, dict) and list(body.keys()) == [k]: body = body[k]
            else: ok = False; break
        if not ok:
            s = f"<wrong top-level wrapper> in {fname}"
            c = cat[s]; c["count"] += 1; c["libs"].add(fname); continue
        seen = set()
        for e in v.iter_errors(body):
            s = sig(e)
            if s in seen: continue   # count each signature once per record
            seen.add(s)
            c = cat[s]; c["count"] += 1; c["libs"].add(fname)
            if c["sample_ref"] is None:
                c["sample_ref"] = ref_of(rec)
                val = e.instance
                c["sample_val"] = (json.dumps(val)[:120] if not isinstance(val, dict)
                                   else "keys=" + str(sorted(val))[:120])

print(f"{'COUNT':>9}  CATEGORY  (libs)  e.g. ref | sample")
print("-" * 100)
for s, c in sorted(cat.items(), key=lambda kv: -kv[1]["count"]):
    libs = ",".join(sorted(x.replace('.ndjson','') for x in c["libs"]))
    print(f"{c['count']:>9}  {s}\n            libs=[{libs}]  e.g. {c['sample_ref']} | {c['sample_val']}")
