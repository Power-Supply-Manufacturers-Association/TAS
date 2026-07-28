#!/usr/bin/env python3
"""Convert a vendor-direct TDK common-mode choke/filter pull into TAS magnetic
records (NDJSON), superseding the synthetic-MPN rows quarantined under ABT #281.

    python3 scripts/extract_tdk_cmc.py \
        tdk-cmf_cmc.json data/magnetics_tdk_cmc_staged.ndjson

Input is the parametric grid captured from
https://product.tdk.com/en/search/emc/emc/cmf_cmc/list — a JSON envelope of
{source, total, columns, rows} where rows are the rendered table cells. That pull
is validated at capture time against TDK's OWN CSV export (identical values across
every shared part and field), so the columns here are the vendor's, not a scrape's
interpretation of them.

Every record is validated against the MAS JSON Schema (Draft 2020-12) before it is
written; failures go to a sibling .rejected.ndjson rather than being coerced.

WHAT IS DELIBERATELY DROPPED (and counted — nothing disappears quietly):

  * WILDCARD SERIES STUBS. TDK lists retired families as a pattern, e.g. `B82796*`
    and `B82799*`: status Obsolete, no inductance, no Rdc, no rated current. A
    wildcard is not an orderable part number, and inventing members to fit the
    pattern is precisely the fabrication ABT #281 exists to prevent.
  * ROWS WITH NEITHER AN INDUCTANCE NOR AN IMPEDANCE POINT. That pair is what a
    common-mode choke is selected on; a rated current and an Rdc alone describe
    what the part survives, not what it does.

A "New" badge is rendered INSIDE the part-number cell for recently-released parts
('New ACT1210D-131-2P-TL01'). It is stripped — an MPN never contains a space, and
letting UI chrome into `reference` is how a catalog ends up with unorderable part
numbers in the first place (this ticket's whole subject).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]        # TAS/
PROTEUS = REPO.parent                             # PSMA/

SOURCE_URL = "https://product.tdk.com/en/search/emc/emc/cmf_cmc/list"

# TDK lifecycle wording -> the MAS status enum
# (production | prototype | nrnd | obsolete | preview).
# "EOL announced" is NOT obsolete: the part is still orderable, it just must not
# go into a new design — which is what nrnd means. Calling it obsolete would drop
# it from pickers that can legitimately still use it for a repair/second source.
STATUS = {
    "production": "production",
    "production (nrnd)": "nrnd",
    "eol announced": "nrnd",
    "obsolete": "obsolete",
    "in development": "preview",        # announced, not yet in volume production
}

MPN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./_-]*$")


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
            if s.get("$id"):
                by_id[s["$id"]] = s
    resources = [Resource(contents=s, specification=DRAFT202012) for s in by_id.values()]
    return Registry().with_resources([(s.contents["$id"], s) for s in resources])


def _load_magnetic_schema(registry: Registry):
    schema = json.loads((PROTEUS / "MAS" / "schemas" / "magnetic.json").read_text())
    return Draft202012Validator(schema, registry=registry)


def clean_mpn(cell: str) -> str:
    """The part number with any rendered badge removed. An MPN carries no space,
    so a multi-token cell is chrome + MPN, never a part number with a space."""
    return cell.split()[-1] if " " in cell.strip() else cell.strip()


_SI = {"": 1.0, "k": 1e3, "M": 1e6, "G": 1e9, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "n": 1e-9, "p": 1e-12}


def parse_value_at(cell: str, unit_scale: float) -> tuple[float | None, float | None]:
    """'4.7 at 10kHz' -> (4.7 * unit_scale, 10000.0). Returns (None, None) when the
    cell is blank or unparseable — never a guessed value."""
    if not cell or not cell.strip():
        return None, None
    m = re.match(r"^\s*([\d.]+)\s*(?:at\s*([\d.]+)\s*([kMGmunpµ]?)Hz)?\s*$", cell.strip())
    if not m:
        return None, None
    try:
        value = float(m.group(1)) * unit_scale
    except ValueError:
        return None, None
    freq = None
    if m.group(2):
        try:
            freq = float(m.group(2)) * _SI.get(m.group(3) or "", 1.0)
        except ValueError:
            freq = None
    return value, freq


def parse_max(cell: str) -> float | None:
    """'1.5 Max.' -> 1.5 ; '0.3' -> 0.3 ; blank -> None."""
    if not cell or not cell.strip():
        return None
    m = re.match(r"^\s*([\d.]+)\s*(?:Max\.?)?\s*$", cell.strip(), re.I)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def parse_temp_range(cell: str) -> tuple[float | None, float | None]:
    m = re.match(r"^\s*(-?\d+)\s*to\s*(-?\d+)\s*$", (cell or "").strip())
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def build_record(row: dict) -> dict | None:
    """A TAS magnetic for one catalog row, or None when the row carries no
    electrical content worth selecting on."""
    mpn = clean_mpn(row.get("Part No. ?", ""))
    inductance_h, l_freq = parse_value_at(row.get("Common-mode Inductance / mH", ""), 1e-3)
    z_ohm, z_freq = parse_value_at(row.get("Common-mode Impedance / Ω", ""), 1.0)
    rated_a = parse_max(row.get("Rated Current (Max.) / A", ""))
    rdc_ohm = parse_max(row.get("Rdc / Ω", ""))
    t_min, t_max = parse_temp_range(row.get("Operating Temp. Range / °C", ""))

    # A common-mode choke's FUNCTION is its common-mode impedance, published
    # either as an inductance or as |Z| at a stated frequency (signal-line parts
    # are conventionally specified the second way — which is why ~half of these
    # carry no inductance and Blade Runner scores them GEN_SPARSE; that is the
    # vendor's spec convention, not missing data). A row with neither cannot be
    # selected for filtering at all: a rated current and an Rdc describe what it
    # survives, not what it does.
    if inductance_h is None and z_ohm is None:
        return None

    electrical: dict = {"subtype": "commonModeChoke"}
    if inductance_h is not None:
        electrical["inductance"] = {"nominal": inductance_h}
    if rated_a is not None:
        electrical["ratedCurrents"] = [rated_a]
    if rdc_ohm is not None:
        # The vendor publishes Rdc as a maximum; carry it as one rather than
        # promoting it to a nominal it never claimed.
        electrical["dcResistances"] = [{"maximum": rdc_ohm}]
    if z_ohm is not None and z_freq is not None:
        electrical["impedancePoints"] = [{"frequency": z_freq, "impedance": {"magnitude": z_ohm}}]

    part: dict = {"description": " ".join(
        x for x in [row.get("Processing and Feature ?", ""), row.get("Mounting Method", ""),
                    row.get("L x W Size", "")] if x).strip() or "TDK common mode choke"}

    datasheet_info: dict = {
        "part": part,
        "electrical": [electrical],
        # manufacturerParametric: the vendor's own parametric catalog, which is
        # exactly what this is — not a datasheet read and not a distributor row
        # (distributor rows are what ABT #281/#286 are cleaning up).
        "provenance": [{
            "source": "manufacturerParametric",
            "sourceName": "TDK Product Center parametric catalog (EMC / signal-line common mode chokes)",
            "sourceUrl": SOURCE_URL,
        }],
    }
    if t_min is not None and t_max is not None:
        datasheet_info["thermal"] = {"operatingTemperature": {"minimum": t_min, "maximum": t_max}}

    return {"magnetic": {
        "manufacturerInfo": {
            "name": "TDK",
            "reference": mpn,
            "status": STATUS.get(row.get("Status", "").strip().lower(), "production"),
            "datasheetInfo": datasheet_info,
        },
        # Catalog rows describe terminal behaviour, not geometry; the corpus uses
        # these placeholders for every parametric-sourced magnetic.
        "core": {"functionalDescription": {
            "type": "twoPieceSet", "material": "Dummy", "shape": "Dummy", "gapping": []}},
        "coil": {"bobbin": "Dummy", "functionalDescription": [{
            "name": "Dummy", "numberTurns": 1, "numberParallels": 1,
            "isolationSide": "primary", "wire": "Dummy"}]},
    }}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.exit(__doc__)
    src, dest = Path(argv[0]), Path(argv[1])
    pull = json.loads(src.read_text())
    columns, rows = pull["columns"], pull["rows"]

    validator = _load_magnetic_schema(_build_registry())
    kept, rejected = [], []
    dropped = {"wildcardSeriesStub": 0, "noSelectableImpedance": 0, "malformedMpn": 0}

    for cells in rows:
        row = dict(zip(columns, cells))
        mpn = clean_mpn(row.get("Part No. ?", ""))
        if "*" in mpn:
            dropped["wildcardSeriesStub"] += 1
            continue
        if not MPN_RE.match(mpn):
            dropped["malformedMpn"] += 1
            continue
        record = build_record(row)
        if record is None:
            dropped["noSelectableImpedance"] += 1
            continue
        errors = sorted(validator.iter_errors(record["magnetic"]), key=lambda e: e.path)
        if errors:
            rejected.append({"reference": mpn, "errors": [e.message for e in errors[:4]], "record": record})
        else:
            kept.append(record)

    dest.write_text("".join(json.dumps(r) + "\n" for r in kept))
    print(f"wrote {len(kept)} TDK common-mode-choke records to {dest}")
    print(f"  source: {pull.get('source', SOURCE_URL)} ({pull.get('total')} parts listed)")
    for reason, n in dropped.items():
        if n:
            print(f"  dropped {n} rows: {reason}")
    if rejected:
        rej = dest.with_suffix(".rejected.ndjson")
        rej.write_text("".join(json.dumps(r) + "\n" for r in rejected))
        print(f"  {len(rejected)} rows FAILED MAS schema validation -> {rej}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
