#!/usr/bin/env python3
"""Migrate datasheetInfo.business -> a distributorInfo entry (sibling of
manufacturerInfo), then delete the business block.

Mapping (per decision): distribution -> name (else the manufacturer's own name);
packaging/moq/leadTime -> same; pu -> vpe (when vpe absent); cost / non-zero
priceCost -> cost {value, currency:"USD"}. priceCost==0 and nulls are dropped (junk).
"""
import json, glob, os, sys

def to_int(x):
    if isinstance(x, bool): return None
    if isinstance(x, int): return x
    if isinstance(x, float) and x.is_integer(): return int(x)
    return None

def build_distributor(biz, mfr_name):
    name = biz.get("distribution") or mfr_name
    if not name:
        return None  # cannot form a valid distributorInfo without a name
    d = {"name": name}
    if biz.get("packaging") is not None: d["packaging"] = biz["packaging"]
    moq = to_int(biz.get("moq"))
    if moq is not None: d["moq"] = moq
    vpe = to_int(biz.get("vpe") if biz.get("vpe") is not None else biz.get("pu"))
    if vpe is not None: d["vpe"] = vpe
    if isinstance(biz.get("leadTime"), (int, float)): d["leadTime"] = biz["leadTime"]
    cost = biz.get("cost")
    if not isinstance(cost, (int, float)) or cost == 0:
        pc = biz.get("priceCost")
        cost = pc if isinstance(pc, (int, float)) and pc > 0 else None
    if isinstance(cost, (int, float)) and cost > 0:
        d["cost"] = {"value": cost, "currency": "USD"}
    return d

def process(obj, stats):
    """Find dicts holding manufacturerInfo.datasheetInfo.business; migrate them."""
    if isinstance(obj, dict):
        mi = obj.get("manufacturerInfo")
        if isinstance(mi, dict):
            di = mi.get("datasheetInfo")
            if isinstance(di, dict) and isinstance(di.get("business"), dict):
                biz = di.pop("business")
                stats["dropped"] += 1
                entry = build_distributor(biz, mi.get("name"))
                if entry is not None:
                    obj.setdefault("distributorsInfo", []).append(entry)
                    stats["distributors_added"] += 1
                else:
                    stats["no_name"] += 1
        for v in obj.values():
            process(v, stats)
    elif isinstance(obj, list):
        for v in obj:
            process(v, stats)

def main():
    stats = {"dropped": 0, "distributors_added": 0, "no_name": 0}
    for f in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "data", "*.ndjson"))):
        if os.path.getsize(f) < 1000:
            continue
        changed = False
        out = []
        for line in open(f):
            raw = line.rstrip("\n")
            if not raw or raw.startswith("version https"):
                out.append(raw); continue
            try: r = json.loads(raw)
            except: out.append(raw); continue
            before = stats["dropped"]
            process(r, stats)
            if stats["dropped"] != before: changed = True
            out.append(json.dumps(r, ensure_ascii=False))
        if changed:
            open(f, "w").write("\n".join(out) + "\n")
            print(f"  {os.path.basename(f)}: migrated")
    print(f"\nbusiness blocks removed: {stats['dropped']}")
    print(f"distributorInfo entries added: {stats['distributors_added']}")
    print(f"could not name (skipped entry, business still removed): {stats['no_name']}")

if __name__ == "__main__":
    sys.exit(main())
