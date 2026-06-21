"""Validate every record in TAS/data/*.ndjson and every example doc.

Each part library file is wrapped per-discriminator and validated against the
matching per-type schema. Converter docs validate against TAS.json.

Failures are aggregated into a single readable report; the test fails if any
record in a covered file does not validate.
"""
from __future__ import annotations

import json
import sys
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

sys.path.insert(0, str(REPO / "scripts"))
from validate_topology import validate_tas, validate_cias_brick  # noqa: E402

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
    for repo_name in ("PEAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "CONAS"):
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
    """TAS + CIAS + PEAS registry for validating TAS converter documents."""
    schemas = {}
    for name in ("TAS", "inputs", "outputs", "utils", "topology"):
        s = json.loads((SCHEMA_DIR / f"{name}.json").read_text())
        schemas[s["$id"]] = s
    cias_dir = REPO.parent / "CIAS" / "schemas"
    for name in ("CIAS",):
        s = json.loads((cias_dir / f"{name}.json").read_text())
        schemas[s["$id"]] = s
    peas_dir = REPO.parent / "PEAS" / "schemas"
    for path in peas_dir.rglob("*.json"):
        s = json.loads(path.read_text())
        schemas[s["$id"]] = s
    resources = [
        (sid, Resource(contents=s, specification=DRAFT202012))
        for sid, s in schemas.items()
    ]
    if "https://psma.com/peas/peas.json" not in schemas:
        resources.append((
            "https://psma.com/peas/peas.json",
            Resource(contents={"type": "object"}, specification=DRAFT202012),
        ))
    return Registry().with_resources(resources)


@pytest.fixture(scope="session")
def part_library_validators():
    """{file_basename: (discriminator_path, Draft202012Validator)}.

    The discriminator path is the chain of keys to unwrap before validating
    against the family schema. Under the v2 model semiconductors carry a
    two-level wrap ``{"semiconductor": {"mosfet": ...}}`` (the ``semiconductor``
    branch is a full SAS document), while magnetics/capacitors/resistors keep
    a single-level wrap.
    """
    reg = _build_full_registry()
    out = {}
    for fname, disc_path, repo, schema_file in [
        ("mosfets.ndjson",    ["semiconductor", "mosfet"], "SAS", "mosfet.json"),
        ("diodes.ndjson",     ["semiconductor", "diode"],  "SAS", "diode.json"),
        ("igbts.ndjson",      ["semiconductor", "igbt"],   "SAS", "igbt.json"),
        ("capacitors.ndjson", ["capacitor"],               "CAS", "capacitor.json"),
        ("resistors.ndjson",  ["resistor"],                "RAS", "resistor.json"),
        ("varistors.ndjson",  ["varistor"],                "RAS", "varistor.json"),
        ("magnetics.ndjson",  ["magnetic"],                "MAS", "magnetic.json"),
        ("controllers.ndjson", ["controller"],             "CTAS", "controller.json"),
    ]:
        schema = json.loads((PROTEUS / repo / "schemas" / schema_file).read_text())
        out[fname] = (disc_path, Draft202012Validator(schema, registry=reg))
    return out


@pytest.fixture(scope="session")
def tas_validator():
    reg = _build_tas_registry()
    schema = json.loads((SCHEMA_DIR / "TAS.json").read_text())
    return Draft202012Validator(schema, registry=reg)


@pytest.fixture(scope="session")
def cias_validator():
    reg = _build_tas_registry()
    schema = json.loads((REPO.parent / "CIAS" / "schemas" / "CIAS.json").read_text())
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
    "capacitors.ndjson", "resistors.ndjson", "varistors.ndjson",
    "magnetics.ndjson", "controllers.ndjson",
])
def test_part_library_records_validate(part_library_validators, fname):
    path = DATA / fname
    if not path.exists():
        pytest.skip(f"{fname} not present")
    disc_path, validator = part_library_validators[fname]
    fails: list[tuple[int, str]] = []
    n = 0
    for ln, rec in _iter_ndjson(path):
        n += 1
        body = rec
        bad = False
        for depth, key in enumerate(disc_path):
            if not isinstance(body, dict) or list(body.keys()) != [key]:
                expected = "/".join(disc_path[: depth + 1])
                got = sorted(body) if isinstance(body, dict) else type(body)
                fails.append((ln, f"keys at {expected!r} != [{key!r}]: {got}"))
                bad = True
                break
            body = body[key]
        if bad:
            continue
        errs = list(validator.iter_errors(body))
        if errs:
            fails.append((ln, f"{errs[0].message} @ {list(errs[0].absolute_path)}"))
    assert not fails, (
        f"{n} records in {fname}, {len(fails)} failed:\n"
        + _summarise_failures(fails)
    )


def _manufacturer_ref(rec):
    """(name, reference) reached through the 1-level (capacitor/magnetic/...) or
    2-level (semiconductor/{mosfet,diode,igbt}) discriminator wrap, or None."""
    if not isinstance(rec, dict) or len(rec) != 1:
        return None
    body = next(iter(rec.values()))
    if not isinstance(body, dict):
        return None
    mi = body.get("manufacturerInfo")
    if not isinstance(mi, dict):
        for v in body.values():
            if isinstance(v, dict) and isinstance(v.get("manufacturerInfo"), dict):
                mi = v["manufacturerInfo"]
                break
    if not isinstance(mi, dict):
        return None
    name, ref = mi.get("name"), mi.get("reference")
    return (name, ref) if name and ref else None


@pytest.mark.parametrize("fname", [
    "mosfets.ndjson", "diodes.ndjson", "igbts.ndjson",
    "capacitors.ndjson", "resistors.ndjson", "varistors.ndjson",
    "magnetics.ndjson", "controllers.ndjson",
])
def test_part_library_references_unique(fname):
    """No (manufacturer, reference) may appear more than once in a part library.

    Append-only imports have historically stacked several conflicting records
    under one part number; schema validation cannot catch that because it checks
    each record independently. This guards against re-accumulation."""
    path = DATA / fname
    if not path.exists():
        pytest.skip(f"{fname} not present")
    first: dict[tuple, int] = {}
    dups: list[tuple[int, str]] = []
    for ln, rec in _iter_ndjson(path):
        key = _manufacturer_ref(rec)
        if key is None:
            continue
        if key in first:
            dups.append((ln, f"{key[0]} {key[1]!r} also at line {first[key]}"))
        else:
            first[key] = ln
    assert not dups, (
        f"{fname}: {len(dups)} duplicate (manufacturer, reference) records:\n"
        + _summarise_failures(dups)
    )


# ---------------------------------------------------------------------------
# CIAS brick library
# ---------------------------------------------------------------------------


def test_circuit_bricks_validate(cias_validator):
    path = DATA / "circuits.ndjson"
    if not path.exists():
        pytest.skip("circuits.ndjson not present")
    fails: list[tuple[int, str]] = []
    n = 0
    for ln, rec in _iter_ndjson(path):
        n += 1
        errs = list(cias_validator.iter_errors(rec))
        if errs:
            fails.append((ln, f"{errs[0].message} @ {list(errs[0].absolute_path)}"))
    assert not fails, (
        f"{n} CIAS bricks, {len(fails)} failed:\n"
        + _summarise_failures(fails)
    )


# ---------------------------------------------------------------------------
# Referential integrity (cross-checks JSON Schema cannot express)
# ---------------------------------------------------------------------------


def test_bricks_referential_integrity():
    path = DATA / "circuits.ndjson"
    if not path.exists():
        pytest.skip("circuits.ndjson not present")
    fails: list[tuple[int, str]] = []
    for ln, brick in _iter_ndjson(path):
        for e in validate_cias_brick(brick, where=f"{brick.get('name')}"):
            fails.append((ln, e))
    assert not fails, "brick integrity errors:\n" + _summarise_failures(fails)


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")))
def test_example_referential_integrity(path):
    doc = json.loads(path.read_text())
    errs, _notes = validate_tas(doc)
    assert not errs, f"{path.name} integrity errors:\n" + "\n".join(f"  - {e}" for e in errs)


def test_integrity_catches_dangling_reference():
    # sanity: the validator must FAIL on a broken doc, not just pass clean ones
    doc = json.loads((EXAMPLES / "01_flyback_48v_to_12v.json").read_text())
    doc["topology"]["stages"][0]["inputPort"]["port"] = "does_not_exist"
    errs, _ = validate_tas(doc)
    assert errs


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
