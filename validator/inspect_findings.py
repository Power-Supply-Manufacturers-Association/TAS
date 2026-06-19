#!/usr/bin/env python3
"""Show sample records that trip a given check code, with the relevant fields,
so we can tell validator false-positives from genuine bad data."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "build"))
import tas_validator  # noqa: E402

DATA = HERE.parent / "data"
FILEMAP = {
    "magnetics": "magnetic", "capacitors": "capacitor", "resistors": "resistor",
    "diodes": "semiconductor", "mosfets": "semiconductor", "igbts": "semiconductor",
}


def ds(rec):
    disc = next(iter(rec))
    obj = rec[disc]
    if disc == "semiconductor":
        obj = obj[next(iter(obj))]
    return obj.get("manufacturerInfo", {}).get("reference", "?"), \
        obj.get("manufacturerInfo", {}).get("datasheetInfo", {})


def main():
    want_code = sys.argv[1]
    sev = sys.argv[2] if len(sys.argv) > 2 else "IMPOSSIBLE"
    n_show = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    shown = 0
    for fname in FILEMAP:
        path = DATA / f"{fname}.ndjson"
        if not path.exists():
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                try:
                    v = tas_validator.validate(rec)
                except Exception:
                    continue
                hit = [fd for fd in v.findings if fd.code == want_code and fd.severity == sev]
                if not hit:
                    continue
                ref, dsi = ds(rec)
                elec = dsi.get("electrical", {})
                part = dsi.get("part", {})
                print(f"[{fname}] ref={ref}  tech={part.get('technology') or part.get('material')}")
                for fd in hit[:1]:
                    print(f"    {fd.code} value={fd.value:.4g} thr={fd.threshold:.4g}  {fd.message}")
                print(f"    electrical={json.dumps(elec)[:300]}")
                shown += 1
                if shown >= n_show:
                    return
    print(f"(shown {shown})")


if __name__ == "__main__":
    main()
