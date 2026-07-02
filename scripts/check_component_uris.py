#!/usr/bin/env python3
"""Component-URI integrity pass: resolve every catalog URI a TAS/CIAS document uses.

Why
---
`stage.circuit` (TAS topology.json `$defs/circuitRef`) and CIAS `component.data`
each allow *either* an inline document *or* a bare URI string into a data file.
The schemas constrain the string only to `minLength: 1`, so a dangling reference
(typo'd part number, renamed catalog, missing record) is invisible to JSON Schema
validation. This script defines the URI grammar actually in use and resolves every
such URI against the live catalogs, reporting anything that dangles.
(July-2026 review item: "component.data / circuitRef URIs are completely
unconstrained strings — dangling references are invisible".)

The grammar (derived from the surveyed population, 2026-07)
-----------------------------------------------------------
    uri     = "TAS/data/" catalog ".ndjson" "?" key "=" value
    catalog = 1*( ALPHA / DIGIT / "_" / "-" / "." )   ; a file that EXISTS in TAS/data/
    key     = "partNumber" / "name" / "placeholder"
    value   = 1*( any char except "&" "=" "?" )       ; exactly one key=value pair,
                                                      ; no URL-encoding, non-empty

Key semantics:

* ``partNumber=<pn>``   -> resolves to exactly one record in <catalog> whose
  ``manufacturerInfo.reference`` or ``manufacturerInfo.datasheetInfo.part.partNumber``
  (reached through the discriminator wrap(s)) equals <pn> exactly.
* ``name=<brick-name>`` -> resolves to exactly one record whose top-level ``name``
  equals <brick-name> (CIAS bricks, i.e. circuits.ndjson).
* ``placeholder=<refdes>`` -> a deliberate pre-sourcing slot: the component has NOT
  been bound to a real part yet. Emitted by scripts/topology_templates.py and
  loudly rejected by scripts/tas_to_spice.py. It resolves to nothing by design;
  only the grammar and the existence of <catalog> are checked.

Anything else — a different key, a missing/empty value, extra ``&`` params, a path
not under ``TAS/data/``, a non-``.ndjson`` target — is MALFORMED.

Where URIs are looked for
-------------------------
* ``data/circuits.ndjson``   — every CIAS brick's ``components[].data`` strings.
* ``data/converters.ndjson`` — every TAS doc's ``topology.stages[].circuit`` strings,
  plus ``components[].data`` strings inside *inline* stage circuits.
* ``examples/*.json``        — same as converters.
* Safety net: any OTHER string starting with ``TAS/data/`` anywhere in those
  documents is flagged as ``unexpected-field`` (a URI living where no URI belongs).

Violation classes (each is a failure; exit status 1 if any are found):
    malformed        - string does not parse under the grammar above
    missing-catalog  - <catalog> is not a file in TAS/data/
    unresolved       - key=value matches no record in <catalog>
    multi-match      - key=value matches more than one record (ambiguous)
    unexpected-field - a TAS/data/ URI string outside component.data / stage.circuit

Usage
-----
    python3 scripts/check_component_uris.py                 # examples + converters + circuits
    python3 scripts/check_component_uris.py --no-circuits   # skip the 95 MB brick library

Performance: source files are streamed line by line; only catalogs that are
actually referenced are scanned, once each, with a substring pre-filter so the
multi-hundred-MB catalogs are read but hardly ever JSON-parsed. Whole pass runs
in well under two minutes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
EXAMPLES = REPO / "examples"

RESOLVING_KEYS = ("partNumber", "name")
PLACEHOLDER_KEY = "placeholder"
URI_RE = re.compile(
    r"^TAS/data/(?P<catalog>[A-Za-z0-9_.\-]+\.ndjson)"
    r"\?(?P<key>partNumber|name|placeholder)=(?P<value>[^&=?]+)$"
)
URI_LIKE_PREFIX = "TAS/data/"


# ---------------------------------------------------------------------------
# URI extraction
# ---------------------------------------------------------------------------


class FoundUri:
    __slots__ = ("uri", "source", "field")

    def __init__(self, uri: str, source: str, field: str):
        self.uri = uri          # the raw string
        self.source = source    # e.g. "converters.ndjson:12" or "01_flyback....json"
        self.field = field      # JSON-path-ish location within the document

    def __repr__(self):
        return f"{self.source} @ {self.field}: {self.uri!r}"


def _extract_from_brick(brick: dict, source: str, prefix: str, out: list[FoundUri]):
    for i, comp in enumerate(brick.get("components") or []):
        if isinstance(comp, dict) and isinstance(comp.get("data"), str):
            name = comp.get("name", i)
            out.append(FoundUri(comp["data"], source,
                                f"{prefix}components[{name}].data"))


def _extract_from_tas(doc: dict, source: str, out: list[FoundUri]):
    stages = (doc.get("topology") or {}).get("stages") or []
    for i, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        circ = stage.get("circuit")
        sname = stage.get("name", i)
        if isinstance(circ, str):
            out.append(FoundUri(circ, source, f"stages[{sname}].circuit"))
        elif isinstance(circ, dict):
            _extract_from_brick(circ, source, f"stages[{sname}].circuit.", out)


def _extract_unexpected(obj, source: str, known: set[int],
                        out: list[FoundUri], path: str = "$"):
    """Safety net: URI-like strings anywhere OTHER than the fields above."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _extract_unexpected(v, source, known, out, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _extract_unexpected(v, source, known, out, f"{path}[{i}]")
    elif isinstance(obj, str) and obj.startswith(URI_LIKE_PREFIX):
        if id(obj) not in known:
            out.append(FoundUri(obj, source, f"{path} [unexpected-field]"))


def _extract_document(doc: dict, source: str, is_brick: bool) -> list[FoundUri]:
    out: list[FoundUri] = []
    if is_brick:
        _extract_from_brick(doc, source, "", out)
    else:
        _extract_from_tas(doc, source, out)
    known = {id(f.uri) for f in out}
    unexpected: list[FoundUri] = []
    _extract_unexpected(doc, source, known, unexpected)
    return out + unexpected


def collect_uris(include_circuits: bool = True) -> list[FoundUri]:
    found: list[FoundUri] = []
    for path in sorted(EXAMPLES.glob("*.json")):
        found.extend(_extract_document(json.loads(path.read_text()),
                                       path.name, is_brick=False))
    conv = DATA / "converters.ndjson"
    if conv.exists():
        with conv.open() as fh:
            for ln, line in enumerate(fh, 1):
                line = line.strip()
                if line:
                    found.extend(_extract_document(
                        json.loads(line), f"converters.ndjson:{ln}", is_brick=False))
    if include_circuits:
        circ = DATA / "circuits.ndjson"
        if circ.exists():
            with circ.open() as fh:
                for ln, line in enumerate(fh, 1):
                    line = line.strip()
                    if line:
                        found.extend(_extract_document(
                            json.loads(line), f"circuits.ndjson:{ln}", is_brick=True))
    return found


# ---------------------------------------------------------------------------
# Catalog resolution
# ---------------------------------------------------------------------------


def _part_number_candidates(rec, out: set[str]):
    """All strings a partNumber= URI may legally match in one record:
    every manufacturerInfo.reference and every
    manufacturerInfo.datasheetInfo.part.partNumber, wherever the
    discriminator wrap put them."""
    if isinstance(rec, dict):
        for k, v in rec.items():
            if k == "manufacturerInfo" and isinstance(v, dict):
                ref = v.get("reference")
                if isinstance(ref, str):
                    out.add(ref)
                dsi = v.get("datasheetInfo")
                if isinstance(dsi, dict):
                    pn = (dsi.get("part") or {}).get("partNumber") \
                        if isinstance(dsi.get("part"), dict) else None
                    if isinstance(pn, str):
                        out.add(pn)
            else:
                _part_number_candidates(v, out)
    elif isinstance(rec, list):
        for v in rec:
            _part_number_candidates(v, out)


def resolve_catalog(catalog_path: Path,
                    wanted: dict[str, set[str]]) -> dict[tuple[str, str], list[int]]:
    """One streaming pass over a catalog. ``wanted`` is {key: {values}} for the
    resolving keys. Returns {(key, value): [matching line numbers]}.

    A cheap substring pre-filter keeps json.loads off the overwhelming majority
    of lines in the multi-hundred-MB catalogs (substring presence is a necessary
    condition for an exact-string match)."""
    hits: dict[tuple[str, str], list[int]] = defaultdict(list)
    all_values = sorted({v for vals in wanted.values() for v in vals})
    if not all_values:
        return hits
    with catalog_path.open() as fh:
        for ln, line in enumerate(fh, 1):
            if not any(v in line for v in all_values):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # unparseable catalog lines are test_data.py's problem
            if "partNumber" in wanted:
                cands: set[str] = set()
                _part_number_candidates(rec, cands)
                for v in wanted["partNumber"] & cands:
                    hits[("partNumber", v)].append(ln)
            if "name" in wanted:
                nm = rec.get("name") if isinstance(rec, dict) else None
                if isinstance(nm, str) and nm in wanted["name"]:
                    hits[("name", nm)].append(ln)
    return hits


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def run_check(include_circuits: bool = True,
              out=sys.stdout) -> tuple[list[str], dict]:
    """Returns (violations, stats). Empty violations == clean."""
    found = collect_uris(include_circuits=include_circuits)

    violations: list[str] = []
    parsed: list[tuple[FoundUri, str, str, str]] = []  # (f, catalog, key, value)
    per_source_total: dict[str, int] = defaultdict(int)
    shape_counts: dict[str, int] = defaultdict(int)

    for f in found:
        per_source_total[f.source.split(":")[0]] += 1
        if f.field.endswith("[unexpected-field]"):
            violations.append(f"unexpected-field: {f}")
            continue
        m = URI_RE.match(f.uri)
        if not m:
            violations.append(f"malformed: {f}")
            continue
        catalog, key, value = m["catalog"], m["key"], m["value"]
        shape_counts[f"TAS/data/{catalog}?{key}=<v>"] += 1
        cat_path = DATA / catalog
        if not cat_path.is_file():
            violations.append(f"missing-catalog: {f}")
            continue
        if key != PLACEHOLDER_KEY:
            parsed.append((f, catalog, key, value))

    # group the resolving lookups per catalog, scan each referenced catalog once
    wanted_by_catalog: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set))
    for _f, catalog, key, value in parsed:
        wanted_by_catalog[catalog][key].add(value)

    hits_by_catalog: dict[str, dict[tuple[str, str], list[int]]] = {}
    for catalog, wanted in sorted(wanted_by_catalog.items()):
        hits_by_catalog[catalog] = resolve_catalog(DATA / catalog, wanted)

    for f, catalog, key, value in parsed:
        lines = hits_by_catalog[catalog].get((key, value), [])
        if not lines:
            violations.append(
                f"unresolved: {f} — no record in {catalog} with {key}={value!r}")
        elif len(lines) > 1:
            violations.append(
                f"multi-match: {f} — {key}={value!r} matches {catalog} lines "
                f"{lines[:10]}{' ...' if len(lines) > 10 else ''}")

    stats = {
        "total_uris": len(found),
        "per_source": dict(per_source_total),
        "shapes": dict(shape_counts),
        "violations": len(violations),
    }

    print(f"component-URI integrity: {stats['total_uris']} URI strings found", file=out)
    for src, n in sorted(per_source_total.items()):
        print(f"  {src}: {n}", file=out)
    print("shapes:", file=out)
    for shape, n in sorted(shape_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:6d}  {shape}", file=out)
    if violations:
        print(f"\n{len(violations)} VIOLATIONS:", file=out)
        for v in violations:
            print(f"  {v}", file=out)
    else:
        print("\nzero dangling references — all URIs resolve", file=out)
    return violations, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Resolve every component/circuit URI in TAS documents "
                    "against the live catalogs (see module docstring).")
    ap.add_argument("--no-circuits", action="store_true",
                    help="skip data/circuits.ndjson (the 95 MB brick library); "
                         "examples/ and data/converters.ndjson are always scanned")
    args = ap.parse_args(argv)
    violations, _stats = run_check(include_circuits=not args.no_circuits)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
