#!/usr/bin/env python3
"""Reshape TAS magnetic records to MAS magnetic.json's model:
  - datasheetInfo.electrical: flat inductor dict -> [{subtype:"inductor", ...}]
    (ratedCurrent -> ratedCurrents:[x]; other fields 1:1; only present fields kept)
  - drop core.physicalDescription ({dimensions} — redundant with datasheetInfo.mechanical)
  - drop root extras with no schema home: reverseEngineering, substitutesInfo, temperatureRange
All mappings are lossless field-preserving except the explicitly-dropped redundant/unmodelled keys.
"""
import json, os, sys

INDUCTOR_FIELDS = ("inductance", "dcResistance", "saturationCurrentPeak", "selfResonantFrequency")
ROOT_DROP = ("reverseEngineering", "substitutesInfo", "temperatureRange")

def migrate_electrical(el):
    if not isinstance(el, dict):
        return el  # already array / absent
    e = {"subtype": "inductor"}
    for f in INDUCTOR_FIELDS:
        if el.get(f) is not None:
            e[f] = el[f]
    rc = el.get("ratedCurrent")
    if isinstance(rc, (int, float)):
        e["ratedCurrents"] = [rc]
    elif isinstance(el.get("ratedCurrents"), list):
        e["ratedCurrents"] = el["ratedCurrents"]
    return [e]

def migrate(body, stats):
    di = body.get("manufacturerInfo", {}).get("datasheetInfo")
    if isinstance(di, dict) and isinstance(di.get("electrical"), dict):
        di["electrical"] = migrate_electrical(di["electrical"]); stats["electrical"] += 1
    core = body.get("core")
    if isinstance(core, dict) and "physicalDescription" in core:
        core.pop("physicalDescription"); stats["core_phys"] += 1
    for k in ROOT_DROP:
        if k in body:
            body.pop(k); stats[k] += 1

def main():
    path = os.path.join(os.path.dirname(__file__), "..", "data", "magnetics.ndjson")
    stats = {"electrical": 0, "core_phys": 0, **{k: 0 for k in ROOT_DROP}}
    out = []
    for line in open(path):
        raw = line.rstrip("\n")
        if not raw or raw.startswith("version https"):
            out.append(raw); continue
        r = json.loads(raw)
        body = r.get("magnetic", r)
        migrate(body, stats)
        out.append(json.dumps(r, ensure_ascii=False))
    open(path, "w").write("\n".join(out) + "\n")
    print("migrated magnetics.ndjson:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

if __name__ == "__main__":
    sys.exit(main())
