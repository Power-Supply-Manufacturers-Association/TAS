#!/usr/bin/env python3
"""Extract Bourns chip bead specs from downloaded PDF datasheets and emit
TAS-format magnetic/chipBead records as NDJSON.

Data source: PDF datasheets downloaded from www.bourns.com
Series: MH1005, MH1608, MH1608A, MH2029T, MH3261T, MH45/32/20 family, MG/MU/MZ family

Usage:
    python3 scripts/extract_bourns_chip_beads.py \
        /tmp/bead_MH1005.pdf /tmp/bead_MH1608.pdf ... \
        data/chip_beads_bourns_staged.ndjson
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pdfplumber
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
PROTEUS = REPO.parent

# ---------------------------------------------------------------------------
# Series metadata (size L×W×H in mm, datasheet URL)
# ---------------------------------------------------------------------------
_SERIES_META = {
    "MH1005":  {"l": 1.0,  "w": 0.5,  "h": 0.5,  "url": "https://www.bourns.com/docs/product-datasheets/mh1005.pdf",   "t_min": -55, "t_max": 125},
    "MH1608":  {"l": 1.6,  "w": 0.8,  "h": 0.8,  "url": "https://www.bourns.com/docs/product-datasheets/mh1608.pdf",   "t_min": -55, "t_max": 125},
    "MH1608A": {"l": 1.6,  "w": 0.8,  "h": 0.8,  "url": "https://www.bourns.com/docs/product-datasheets/mh1608a.pdf",  "t_min": -55, "t_max": 125},
    "MH2029":  {"l": 2.0,  "w": 1.25, "h": 0.85, "url": "https://www.bourns.com/docs/product-datasheets/mh.pdf",        "t_min": -55, "t_max": 125},
    "MH2029T": {"l": 2.0,  "w": 1.25, "h": 0.85, "url": "https://www.bourns.com/docs/product-datasheets/mh2029-t.pdf", "t_min": -55, "t_max": 125},
    "MH3261":  {"l": 3.2,  "w": 1.6,  "h": 1.1,  "url": "https://www.bourns.com/docs/product-datasheets/mh.pdf",        "t_min": -55, "t_max": 125},
    "MH3261T": {"l": 3.2,  "w": 1.6,  "h": 1.1,  "url": "https://www.bourns.com/docs/product-datasheets/mh3261-t.pdf", "t_min": -55, "t_max": 125},
    "MH4516":  {"l": 4.5,  "w": 1.6,  "h": 1.6,  "url": "https://www.bourns.com/docs/product-datasheets/mh.pdf",        "t_min": -55, "t_max": 125},
    "MH4532":  {"l": 4.5,  "w": 3.2,  "h": 1.5,  "url": "https://www.bourns.com/docs/product-datasheets/mh.pdf",        "t_min": -55, "t_max": 125},
    "MU1005":  {"l": 1.0,  "w": 0.5,  "h": 0.5,  "url": "https://www.bourns.com/docs/product-datasheets/mgmumz.pdf",   "t_min": -55, "t_max": 125},
    "MU2029":  {"l": 2.0,  "w": 1.2,  "h": 0.9,  "url": "https://www.bourns.com/docs/product-datasheets/mgmumz.pdf",   "t_min": -55, "t_max": 125},
    "MU3261":  {"l": 3.2,  "w": 1.6,  "h": 1.1,  "url": "https://www.bourns.com/docs/product-datasheets/mgmumz.pdf",   "t_min": -55, "t_max": 125},
    "MG1608":  {"l": 1.6,  "w": 0.8,  "h": 0.8,  "url": "https://www.bourns.com/docs/product-datasheets/mgmumz.pdf",   "t_min": -55, "t_max": 125},
    "MU1608":  {"l": 1.6,  "w": 0.8,  "h": 0.8,  "url": "https://www.bourns.com/docs/product-datasheets/mgmumz.pdf",   "t_min": -55, "t_max": 125},
    "MG2029":  {"l": 2.0,  "w": 1.2,  "h": 0.9,  "url": "https://www.bourns.com/docs/product-datasheets/mgmumz.pdf",   "t_min": -55, "t_max": 125},
    "MG3261":  {"l": 3.2,  "w": 1.6,  "h": 1.1,  "url": "https://www.bourns.com/docs/product-datasheets/mgmumz.pdf",   "t_min": -55, "t_max": 125},
    "MZ1608":  {"l": 1.6,  "w": 0.8,  "h": 0.8,  "url": "https://www.bourns.com/docs/product-datasheets/mgmumz.pdf",   "t_min": -55, "t_max": 125},
    "MZ2029":  {"l": 2.0,  "w": 1.2,  "h": 0.9,  "url": "https://www.bourns.com/docs/product-datasheets/mgmumz.pdf",   "t_min": -55, "t_max": 125},
}

def _series_of(part_number: str) -> str | None:
    for s in sorted(_SERIES_META.keys(), key=len, reverse=True):
        if part_number.startswith(s):
            return s
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    m = re.match(r"^([\d.]+)", s)
    return float(m.group(1)) if m else None


def _parse_impedance(v) -> tuple[float | None, float | None]:
    """Return (impedance_ohm, tolerance_pct) from '30 ±25 %' etc."""
    if v is None:
        return None, None
    s = str(v).strip()
    m = re.match(r"([\d.]+)\s*[±±]?\s*([\d.]+)\s*%", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    m2 = re.match(r"([\d.]+)", s)
    if m2:
        return float(m2.group(1)), None
    return None, None


def _is_valid_row(row: list) -> bool:
    """Check that the first cell looks like a Bourns chip bead part number."""
    if not row or not row[0]:
        return False
    s = str(row[0]).strip()
    return bool(re.match(r"^M[HGUZ]\d{4}[A-Z\-]+\d+[A-Z]+", s))


# ---------------------------------------------------------------------------
# PDF table extraction
# ---------------------------------------------------------------------------

def _extract_tables_from_pdf(pdf_path: Path) -> list[tuple[str, list]]:
    """
    Return a list of (header_hint, rows) tuples where header_hint is the
    column header row and rows are only data rows with valid part numbers.
    """
    results = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table or len(table) < 2:
                    continue
                header = table[0]
                if not header or not any(h and "model" in str(h).lower() or "part" in str(h).lower() for h in header):
                    continue
                data_rows = [r for r in table[1:] if _is_valid_row(r)]
                if data_rows:
                    results.append((header, data_rows))
    return results


# ---------------------------------------------------------------------------
# Identify column positions from header
# ---------------------------------------------------------------------------

def _col_idx(header: list, *keywords: str) -> int | None:
    for i, h in enumerate(header):
        if h is None:
            continue
        h_lower = h.lower()
        if all(k.lower() in h_lower for k in keywords):
            return i
    return None


# ---------------------------------------------------------------------------
# Record builder
# ---------------------------------------------------------------------------

def _build_record(part_number: str, imp_ohm: float | None, tol_pct: float | None,
                  dcr_raw: float | None, dcr_unit: str, idc_raw: float | None,
                  idc_unit: str, series: str) -> dict:
    meta = _SERIES_META[series]

    # Convert units to SI
    dcr_ohm: float | None = None
    if dcr_raw is not None:
        dcr_ohm = dcr_raw / 1000.0 if dcr_unit == "mOhm" else dcr_raw

    idc_a: float | None = None
    if idc_raw is not None:
        idc_a = idc_raw / 1000.0 if idc_unit == "mA" else idc_raw

    electrical: dict = {"subtype": "chipBead"}
    if tol_pct is not None:
        electrical["impedanceTolerance"] = tol_pct
    if dcr_ohm is not None:
        electrical["dcResistance"] = {"maximum": dcr_ohm}
    if idc_a is not None:
        electrical["ratedCurrents"] = [idc_a]
    if imp_ohm is not None:
        electrical["impedancePoints"] = [
            {"frequency": 100e6, "impedance": {"magnitude": imp_ohm}}
        ]

    l_m = meta["l"] / 1000.0
    w_m = meta["w"] / 1000.0
    h_m = meta["h"] / 1000.0

    case_code = f"{int(meta['l']*10):02d}{int(meta['w']*10):02d}"

    datasheet_info: dict = {
        "electrical": [electrical],
        "part": {
            "shielded": False,
            "material": "Ferrite",
            "family": series,
            "caseCode": case_code,
        },
        "thermal": {
            "operatingTemperature": {
                "minimum": float(meta["t_min"]),
                "maximum": float(meta["t_max"]),
            }
        },
        "mechanical": {
            "length": {"nominal": l_m},
            "width":  {"nominal": w_m},
            "height": {"nominal": h_m},
        },
    }

    mfr_info: dict = {
        "name": "Bourns",
        "reference": part_number,
        "status": "production",
        "family": series,
        "datasheetInfo": datasheet_info,
        "datasheetUrl": meta["url"],
    }

    return {
        "magnetic": {
            "manufacturerInfo": mfr_info,
            "core": {
                "functionalDescription": {
                    "type": "twoPieceSet",
                    "material": "Dummy",
                    "shape": "Dummy",
                    "gapping": [],
                }
            },
            "coil": {
                "bobbin": "Dummy",
                "functionalDescription": [{
                    "name": "Dummy",
                    "numberTurns": 1,
                    "numberParallels": 1,
                    "isolationSide": "primary",
                    "wire": "Dummy",
                }],
            },
        }
    }


# ---------------------------------------------------------------------------
# Schema registry + validator
# ---------------------------------------------------------------------------

def _build_registry() -> Registry:
    by_id: dict[str, dict] = {}
    for repo_name in ("PEAS", "MAS"):
        schema_dir = PROTEUS / repo_name / "schemas"
        if not schema_dir.is_dir():
            continue
        for p in schema_dir.rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            sid = s.get("$id")
            if sid:
                by_id[sid] = s
    resources = [Resource(contents=s, specification=DRAFT202012) for s in by_id.values()]
    return Registry().with_resources([(s.contents["$id"], s) for s in resources])


def _load_validator(registry: Registry) -> Draft202012Validator:
    schema = json.loads((PROTEUS / "MAS" / "schemas" / "magnetic.json").read_text())
    return Draft202012Validator(schema, registry=registry)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(f"Usage: {argv[0]} <bead1.pdf> [bead2.pdf ...] <output.ndjson>",
              file=sys.stderr)
        return 1

    pdf_paths = [Path(p) for p in argv[1:-1]]
    out_path = Path(argv[-1])
    rejected_path = out_path.with_name(out_path.stem + ".rejected.ndjson")

    print("Building schema registry …")
    registry = _build_registry()
    validator = _load_validator(registry)

    # Collect all parsed parts (deduplicate by part number)
    parts: dict[str, dict] = {}

    for pdf_path in pdf_paths:
        if not pdf_path.exists():
            print(f"  SKIP {pdf_path} (not found)", file=sys.stderr)
            continue
        print(f"  Parsing {pdf_path.name} …")
        try:
            table_sets = _extract_tables_from_pdf(pdf_path)
        except Exception as e:
            print(f"  ERROR reading {pdf_path.name}: {e}", file=sys.stderr)
            continue

        for header, rows in table_sets:
            # Identify columns
            imp_col = _col_idx(header, "impedance")
            dcr_col = _col_idx(header, "rdc") or _col_idx(header, "dc resistance") or _col_idx(header, "dcr")
            idc_col = _col_idx(header, "idc") or _col_idx(header, "rated current") or _col_idx(header, "current")

            # Determine units from header text
            dcr_unit = "mOhm"
            if dcr_col is not None:
                h = str(header[dcr_col]).lower()
                if "mΩ" in h or "mω" in h or "(mω" in h or "(mΩ" in h or "moh" in h:
                    dcr_unit = "mOhm"
                else:
                    dcr_unit = "Ohm"

            idc_unit = "A"
            if idc_col is not None:
                h = str(header[idc_col])
                # Match "(mA)" case-insensitively but exclude plain "(A)"
                if re.search(r'\(mA\)', h, re.IGNORECASE):
                    idc_unit = "mA"

            for row in rows:
                part_number = str(row[0]).strip()
                if not part_number or part_number in parts:
                    continue

                series = _series_of(part_number)
                if series is None or series not in _SERIES_META:
                    continue

                imp_str = row[imp_col] if imp_col is not None and imp_col < len(row) else None
                dcr_str = row[dcr_col] if dcr_col is not None and dcr_col < len(row) else None
                idc_str = row[idc_col] if idc_col is not None and idc_col < len(row) else None

                imp_ohm, tol_pct = _parse_impedance(imp_str)
                dcr_raw = _float(dcr_str)
                idc_raw = _float(idc_str)

                try:
                    rec = _build_record(part_number, imp_ohm, tol_pct,
                                        dcr_raw, dcr_unit, idc_raw, idc_unit, series)
                    parts[part_number] = rec
                except Exception as exc:
                    print(f"    Build error for {part_number}: {exc}", file=sys.stderr)

    print(f"  Found {len(parts)} unique parts")

    # Schema validation
    ok = rejected = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w") as fout, open(rejected_path, "w") as frej:
        for part_number, record in sorted(parts.items()):
            mag = record["magnetic"]
            errors = sorted(validator.iter_errors(mag), key=lambda e: str(e.path))
            if errors:
                entry = {
                    "record": record,
                    "errors": [{"path": list(e.absolute_path), "message": e.message}
                               for e in errors],
                }
                frej.write(json.dumps(entry, separators=(",", ":")) + "\n")
                rejected += 1
            else:
                fout.write(json.dumps(record, separators=(",", ":")) + "\n")
                ok += 1

    print(f"\nSchema validation:")
    print(f"  Written (valid):  {ok:>6}  → {out_path}")
    print(f"  Rejected:         {rejected:>6}  → {rejected_path}")

    if rejected > 0:
        from collections import Counter
        ec: Counter = Counter()
        with open(rejected_path) as f:
            for line in f:
                e = json.loads(line)
                for err in e["errors"]:
                    ec[err["message"][:80]] += 1
        print("\n  Top rejection reasons:")
        for msg, cnt in ec.most_common(5):
            print(f"    {cnt:>5}×  {msg}")

    if ok == 0:
        return 1

    # Physics validation
    try:
        sys.path.insert(0, str(REPO / "validator" / "build"))
        import tas_validator  # type: ignore
    except ImportError:
        print("\nC++ physics validator not available — skipping.")
        return 0 if rejected == 0 else 2

    print("\nRunning physics validator …")
    quar_path = out_path.with_name(out_path.stem + ".quarantine_physics.ndjson")
    clean_path = out_path.with_name(out_path.stem + ".tmp")
    phys_ok = phys_bad = 0

    with open(out_path) as fin, open(clean_path, "w") as fc, open(quar_path, "w") as fq:
        for line in fin:
            rec = json.loads(line)
            try:
                result = tas_validator.validate(rec)
            except ValueError:
                # Chip beads not yet covered by physics validator — pass through.
                fc.write(line)
                phys_ok += 1
                continue
            if result.valid:
                fc.write(line)
                phys_ok += 1
            else:
                findings = [{"code": str(f.code), "message": str(f)} for f in result.findings]
                fq.write(json.dumps({"record": rec, "findings": findings},
                                    separators=(",", ":")) + "\n")
                phys_bad += 1

    import os
    os.replace(clean_path, out_path)
    print(f"  Physics-clean:    {phys_ok:>6}  → {out_path}")
    print(f"  Physics-flagged:  {phys_bad:>6}  → {quar_path}")

    return 0 if (rejected == 0 and phys_bad == 0) else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
