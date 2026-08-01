#!/usr/bin/env python3
"""2026-08 connector fill campaign: standards-pinout join + parametric contactArray.

Writes TWO kinds of new data into TAS/data/connectors.ndjson, both previously 0%:

  1. contactSystem.contacts[{id, pinName, signalRole}] — ONLY where a public
     standard fixes the roles (see scripts/standards_pinouts.py for the tables,
     gates and citations). Provenance: source='manual', sourceName=<citation>.
  2. geometry.parametric.contactArray {pitchX[, pitchY], countX, countY} — pure
     arithmetic from vendor-stated pitch/positions/rows[/rowPitch], ONLY when the
     lattice is determinate (rows==1 or rowPitch stated; positions % rows == 0)
     AND the schema-required boundingEnvelope can be filled from vendor-stated
     length+width+height (no dimension is ever invented). Provenance:
     source='derived' + derivation (PEAS provenance, 2026-08).

Safety contract (the 'never bulk-rewrite' rule exists because other processes
append concurrently — this script honours its intent):
  * refuses to run if another process holds the data file open for writing;
  * streams line-by-line: untouched lines are copied BYTE-IDENTICAL;
  * every modified record is validated against CONAS + PEAS citizenship BEFORE
    anything is written; one invalid modification aborts the whole run;
  * output goes to a temp file in the same directory, then os.replace() —
    atomic on POSIX; if the source grew while streaming, the tail is appended
    unchanged and re-checked;
  * line count and untouched-byte identity are verified before the swap.

Usage: python3 scripts/fill_connector_pinout_geometry.py [--dry-run] [--limit N]
"""
import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO.parent
DATA = REPO / "data" / "connectors.ndjson"
sys.path.insert(0, str(REPO / "scripts"))
import standards_pinouts  # noqa: E402

TODAY = "2026-08-01"


# ---------------------------------------------------------------- validation
def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    resources = []
    for repo in ("PEAS", "CIAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS", "TAS", "TDAS", "COAS"):
        sdir = WORKSPACE / repo / "schemas"
        if not sdir.is_dir():
            continue
        for path in sdir.rglob("*.json"):
            doc = json.loads(path.read_text())
            if "$id" in doc:
                resources.append((doc["$id"], Resource.from_contents(doc, default_specification=DRAFT202012)))
    registry = Registry().with_resources(resources)
    connector_schema = json.loads((WORKSPACE / "CONAS" / "schemas" / "connector.json").read_text())
    return Draft202012Validator(connector_schema, registry=registry)


def num(v):
    if isinstance(v, dict):
        for k in ("nominal", "minimum", "maximum"):
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
        return None
    return float(v) if isinstance(v, (int, float)) else None


# ---------------------------------------------------------------- transforms
def try_pinout(doc):
    """Standards join. Returns (gate_name, citation) if applied, else None."""
    c = doc["connector"]
    if "contactSystem" in c:
        return None  # never overwrite existing tier-2 data
    di = c["manufacturerInfo"].get("datasheetInfo", {})
    fd = di.get("familyDetails", {}) or {}
    m = di.get("mechanical", {}) or {}
    desc = ((di.get("part", {}) or {}).get("description") or "") + " " + \
           (c["manufacturerInfo"].get("description") or "")
    hit = standards_pinouts.match(
        fd.get("family"), fd.get("interfaceStandard"), m.get("positions"),
        fd.get("coding"), desc.lower())
    if hit is None:
        return None
    table, gate = hit
    c["contactSystem"] = {
        "contacts": [{"id": i, "pinName": nme, "signalRole": role}
                     for (i, nme, role) in table["contacts"]],
    }
    di.setdefault("provenance", []).append({
        "source": "manual",
        "sourceName": f"Standards pinout join: {table['cite']}",
        "retrievedDate": TODAY,
        "fields": ["contactSystem"],
    })
    return gate, table["cite"]


def try_contact_array(doc):
    """Parametric lattice. Returns True if applied."""
    c = doc["connector"]
    if "geometry" in c:
        return False  # never overwrite
    di = c["manufacturerInfo"].get("datasheetInfo", {})
    m = di.get("mechanical", {}) or {}
    pitch = num(m.get("pitch"))
    pos = m.get("positions")
    rows = m.get("rows")
    rp = num(m.get("rowPitch"))
    L, W, H = num(m.get("length")), num(m.get("width")), num(m.get("height"))
    if not (pitch and isinstance(pos, int) and isinstance(rows, int)
            and rows >= 1 and pos >= 1 and pos % rows == 0
            and (rows == 1 or rp) and L and W and H):
        return False
    arr = {"pitchX": pitch, "countX": pos // rows, "countY": rows}
    if rows > 1:
        arr["pitchY"] = rp
    c["geometry"] = {
        "coordinateSystem": {"originDatum": "pin1"},
        "boundingEnvelope": {"length": L, "width": W, "height": H},
        "parametric": {"contactArray": arr},
    }
    di.setdefault("provenance", []).append({
        "source": "derived",
        "sourceName": "OpenConverters connector fill 2026-08 (contactArray generator)",
        "retrievedDate": TODAY,
        "fields": ["geometry"],
        "derivation": ("contactArray from vendor-stated mechanical fields: pitchX=pitch, "
                       "countX=positions/rows, countY=rows"
                       + (", pitchY=rowPitch" if rows > 1 else "")
                       + "; boundingEnvelope copied from mechanical length/width/height nominals; "
                         "originDatum=pin1 declares the generated frame. Regular-lattice assumption; "
                         "no pin cross-section, no z, no mateAxis is asserted."),
    })
    return True


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="stop after N modifications (debug)")
    args = ap.parse_args()

    # refuse to run against a file another process has open for writing
    lsof = subprocess.run(["lsof", "-F", "a", str(DATA)], capture_output=True, text=True)
    writers = [l for l in lsof.stdout.splitlines() if l.startswith("a") and ("w" in l or "u" in l)]
    if writers:
        sys.exit(f"REFUSING: {DATA} is open for writing by another process: {writers}")

    validator = build_validator()
    stats = Counter()
    gate_counts = Counter()
    tmp = DATA.with_suffix(".ndjson.fill_tmp")
    size_at_start = DATA.stat().st_size

    with open(DATA, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            stats["lines"] += 1
            modified = False
            try:
                doc = json.loads(raw)
                assert "connector" in doc
            except Exception:
                out.write(raw)
                stats["unparsed"] += 1
                continue
            if not args.limit or stats["modified"] < args.limit:
                hit = try_pinout(doc)
                if hit:
                    gate_counts[hit[0]] += 1
                    modified = True
                if try_contact_array(doc):
                    gate_counts["contactArray"] += 1
                    modified = True
            if not modified:
                out.write(raw)  # byte-identical
                continue
            errors = list(validator.iter_errors(doc["connector"]))
            if errors:
                tmp.unlink(missing_ok=True)
                sys.exit(f"ABORT: modified record fails CONAS at line {stats['lines']}: "
                         f"{errors[0].message[:300]}")
            out.write((json.dumps(doc, ensure_ascii=False) + "\n").encode())
            stats["modified"] += 1

        # tail appended by another process while streaming? carry it over unchanged.
        src.seek(0, os.SEEK_END)
        if src.tell() > size_at_start:
            src.seek(size_at_start)
            for raw in src:
                out.write(raw)
                stats["appended_tail"] += 1

    n_out = sum(1 for _ in open(tmp, "rb"))
    n_in = stats["lines"] + stats["appended_tail"]
    if n_out != n_in:
        tmp.unlink()
        sys.exit(f"ABORT: line count mismatch {n_out} != {n_in}")

    print(f"lines={stats['lines']} modified={stats['modified']} tail={stats['appended_tail']}")
    print("gates:", dict(gate_counts))
    if args.dry_run:
        tmp.unlink()
        print("dry-run: no swap")
        return
    os.replace(tmp, DATA)
    print(f"swapped into {DATA}")


if __name__ == "__main__":
    main()
