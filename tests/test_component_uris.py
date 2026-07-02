"""Component-URI integrity: every catalog URI in TAS/CIAS documents must resolve.

``stage.circuit`` and CIAS ``component.data`` admit bare URI strings
(``TAS/data/<catalog>.ndjson?<key>=<value>``) that JSON Schema only constrains to
``minLength: 1`` — dangling references are invisible to schema validation.
``scripts/check_component_uris.py`` defines the URI grammar (see its docstring)
and resolves every URI against the live catalogs; these tests run that pass.

There is no slow-marker convention in this suite, and the full pass (including
the 95 MB ``circuits.ndjson``) streams in well under two minutes, so everything
runs unconditionally.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from check_component_uris import URI_RE, resolve_catalog, run_check  # noqa: E402


# ---------------------------------------------------------------------------
# Grammar (pure unit tests, no data files)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uri,key,value", [
    ("TAS/data/mosfets.ndjson?partNumber=C3M0032120K", "partNumber", "C3M0032120K"),
    ("TAS/data/circuits.ndjson?name=half-bridge", "name", "half-bridge"),
    ("TAS/data/capacitors.ndjson?placeholder=C_in", "placeholder", "C_in"),
    ("TAS/data/diodes.ndjson?partNumber=SS34HE3_B/H", "partNumber", "SS34HE3_B/H"),
])
def test_grammar_accepts_observed_shapes(uri, key, value):
    m = URI_RE.match(uri)
    assert m and m["key"] == key and m["value"] == value


@pytest.mark.parametrize("uri", [
    "TAS/data/mosfets.ndjson",                       # no query at all
    "TAS/data/mosfets.ndjson?partNumber=",           # empty value
    "TAS/data/mosfets.ndjson?mpn=C3M0032120K",       # unknown key
    "TAS/data/mosfets.ndjson?partNumber=A&name=b",   # extra param
    "data/mosfets.ndjson?partNumber=C3M0032120K",    # not TAS/data/-rooted
    "TAS/data/mosfets.csv?partNumber=C3M0032120K",   # not .ndjson
    "TAS/data/sub/mosfets.ndjson?partNumber=X",      # nested path
    "",
])
def test_grammar_rejects_malformed(uri):
    assert URI_RE.match(uri) is None


# ---------------------------------------------------------------------------
# Resolution machinery sanity (the checker must FAIL on a dangling ref,
# not just pass clean catalogs) — mirrors test_integrity_catches_dangling_reference.
# ---------------------------------------------------------------------------


def test_resolver_finds_hits_and_reports_misses_and_duplicates(tmp_path):
    cat = tmp_path / "mini.ndjson"
    rec = {"semiconductor": {"mosfet": {"manufacturerInfo": {
        "name": "X", "reference": "PN-1",
        "datasheetInfo": {"part": {"partNumber": "PN-1"}}}}}}
    brick = {"name": "half-bridge", "ports": [], "components": [], "connections": []}
    cat.write_text(json.dumps(rec) + "\n" + json.dumps(rec) + "\n"
                   + json.dumps(brick) + "\n")
    hits = resolve_catalog(cat, {
        "partNumber": {"PN-1", "PN-MISSING"},
        "name": {"half-bridge", "no-such-brick"},
    })
    assert hits[("partNumber", "PN-1")] == [1, 2]        # duplicate detected
    assert ("partNumber", "PN-MISSING") not in hits      # dangling detected
    assert hits[("name", "half-bridge")] == [3]
    assert ("name", "no-such-brick") not in hits


# ---------------------------------------------------------------------------
# The real pass
# ---------------------------------------------------------------------------


def _run(include_circuits: bool):
    buf = io.StringIO()
    violations, stats = run_check(include_circuits=include_circuits, out=buf)
    return violations, stats, buf.getvalue()


def test_examples_and_converters_uris_resolve():
    """Always-on scope: examples/*.json + data/converters.ndjson."""
    violations, stats, report = _run(include_circuits=False)
    assert stats["total_uris"] > 0, "extraction found no URIs — extractor broken?"
    assert not violations, (
        f"{len(violations)} dangling/malformed component URIs "
        f"(examples + converters):\n{report}"
    )


def test_circuit_brick_uris_resolve():
    """Full scope including the data/circuits.ndjson brick library."""
    if not (REPO / "data" / "circuits.ndjson").exists():
        pytest.skip("circuits.ndjson not present")
    violations, _stats, report = _run(include_circuits=True)
    assert not violations, (
        f"{len(violations)} dangling/malformed component URIs (full scope):\n{report}"
    )
