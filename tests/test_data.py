"""Validate every record in TAS/data/*.ndjson and every example doc.

Each part library file is wrapped per-discriminator and validated against the
matching per-type schema. Converter docs validate against TAS.json.

Failures are aggregated into a single readable report; the test fails if any
record in a covered file does not validate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
PROTEUS = REPO.parent
DATA = REPO / "data"
EXAMPLES = REPO / "examples"
SCHEMA_DIR = REPO / "schemas"

# ---------------------------------------------------------------------------
# Registry for non-TAS part-library schemas (mirrors port_part_libraries.py).
# ---------------------------------------------------------------------------


def _walk(d: Path):
    for p in d.rglob("*.json"):
        try:
            yield p, json.loads(p.read_text())
        except json.JSONDecodeError:
            continue


def _build_full_registry() -> Registry:
    """All sibling repos (PEAS, SAS, CAS, RAS, MAS), with shim inlining for
    pure $ref schemas (e.g. CAS/utils.json -> PEAS/utils.json)."""
    by_id: dict[str, dict] = {}
    by_path: dict[Path, dict] = {}
    for repo_name in ("PEAS", "SAS", "CAS", "RAS", "MAS"):
        repo_dir = PROTEUS / repo_name / "schemas"
        if not repo_dir.is_dir():
            continue
        for path, schema in _walk(repo_dir):
            sid = schema.get("$id")
            path = path.resolve()
            by_path[path] = schema
            if sid:
                by_id[sid] = schema

    META_KEYS = {"$schema", "$id", "title", "description", "$comment"}
    for sid, schema in list(by_id.items()):
        body = set(schema.keys()) - META_KEYS
        if body != {"$ref"}:
            continue
        path = next((p for p, s in by_path.items() if s is schema), None)
        if path is None:
            continue
        target = (path.parent / schema["$ref"]).resolve()
        target_schema = by_path.get(target)
        if target_schema is None:
            continue
        inlined = {k: v for k, v in target_schema.items()
                   if k not in ("$id", "$schema")}
        inlined["$id"] = sid
        inlined["$schema"] = schema.get(
            "$schema", "https://json-schema.org/draft/2020-12/schema"
        )
        by_id[sid] = inlined

    resources = [
        (sid, Resource(contents=s, specification=DRAFT202012))
        for sid, s in by_id.items()
    ]
    return Registry().with_resources(resources)


def _build_tas_registry() -> Registry:
    """TAS-only registry plus the PEAS stub used by tests/test_schemas.py."""
    schemas = {}
    for name in ("TAS", "inputs", "topology", "outputs", "circuit", "utils"):
        s = json.loads((SCHEMA_DIR / f"{name}.json").read_text())
        schemas[s["$id"]] = s
    resources = [
        (sid, Resource(contents=s, specification=DRAFT202012))
        for sid, s in schemas.items()
    ]
    resources.append((
        "http://openconverters.com/schemas/PEAS/peas.json",
        Resource(contents={"type": "object"}, specification=DRAFT202012),
    ))
    return Registry().with_resources(resources)


@pytest.fixture(scope="session")
def part_library_validators():
    """{file_basename: (discriminator, Draft202012Validator)}."""
    reg = _build_full_registry()
    out = {}
    for fname, disc, repo, schema_file in [
        ("mosfets.ndjson",    "mosfet",    "SAS", "mosfet.json"),
        ("diodes.ndjson",     "diode",     "SAS", "diode.json"),
        ("igbts.ndjson",      "igbt",      "SAS", "igbt.json"),
        ("capacitors.ndjson", "capacitor", "CAS", "capacitor.json"),
        ("resistors.ndjson",  "resistor",  "RAS", "resistor.json"),
        ("magnetics.ndjson",  "magnetic",  "MAS", "magnetic.json"),
    ]:
        schema = json.loads((PROTEUS / repo / "schemas" / schema_file).read_text())
        out[fname] = (disc, Draft202012Validator(schema, registry=reg))
    return out


@pytest.fixture(scope="session")
def tas_validator():
    reg = _build_tas_registry()
    schema = json.loads((SCHEMA_DIR / "TAS.json").read_text())
    return Draft202012Validator(schema, registry=reg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_ndjson(path: Path):
    with path.open() as fh:
        for ln, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            yield ln, json.loads(line)


def _summarise_failures(fails: list[tuple[int, str]], cap: int = 5) -> str:
    lines = [f"  line {ln}: {msg}" for ln, msg in fails[:cap]]
    if len(fails) > cap:
        lines.append(f"  ... and {len(fails) - cap} more")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Part libraries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fname", [
    "mosfets.ndjson", "diodes.ndjson", "igbts.ndjson",
    "capacitors.ndjson", "resistors.ndjson", "magnetics.ndjson",
])
def test_part_library_records_validate(part_library_validators, fname):
    path = DATA / fname
    if not path.exists():
        pytest.skip(f"{fname} not present")
    disc, validator = part_library_validators[fname]
    fails: list[tuple[int, str]] = []
    n = 0
    for ln, rec in _iter_ndjson(path):
        n += 1
        if not isinstance(rec, dict) or list(rec.keys()) != [disc]:
            fails.append((ln, f"top-level keys != [{disc!r}]: {sorted(rec) if isinstance(rec, dict) else type(rec)}"))
            continue
        body = rec[disc]
        errs = list(validator.iter_errors(body))
        if errs:
            fails.append((ln, f"{errs[0].message} @ {list(errs[0].absolute_path)}"))
    assert not fails, (
        f"{n} records in {fname}, {len(fails)} failed:\n"
        + _summarise_failures(fails)
    )


# ---------------------------------------------------------------------------
# Converters (TAS docs)
# ---------------------------------------------------------------------------


def test_converters_records_validate(tas_validator):
    path = DATA / "converters.ndjson"
    if not path.exists():
        pytest.skip("converters.ndjson not present")
    fails: list[tuple[int, str]] = []
    n = 0
    for ln, rec in _iter_ndjson(path):
        n += 1
        errs = list(tas_validator.iter_errors(rec))
        if errs:
            fails.append((ln, f"{errs[0].message} @ {list(errs[0].absolute_path)}"))
    assert not fails, (
        f"{n} converter docs, {len(fails)} failed:\n"
        + _summarise_failures(fails)
    )


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")))
def test_example_validates(tas_validator, path):
    doc = json.loads(path.read_text())
    errs = list(tas_validator.iter_errors(doc))
    assert not errs, (
        f"{path.name} failed:\n"
        + "\n".join(f"  - {e.message} @ {list(e.absolute_path)}"
                    for e in errs[:5])
    )
