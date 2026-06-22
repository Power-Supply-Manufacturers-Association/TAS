"""Validation tests for the TAS v2 schemas.

Run with:   pytest tests/
or:         python -m pytest tests/test_schemas.py -v

Covers:
  * every schema parses and passes Draft 2020-12 meta-validation
  * cross-document $refs resolve
  * a real example doc (examples/v2/*.json) validates
  * negative cases for each role-conditional rule:
      - control stage with inputPort/outputPorts (forbidden)
      - power-chain stage without inputPort/outputPorts (required)
      - opPoint with both / neither outputCurrent and outputPower
      - non-isolation stage with multiple outputPorts
      - coupling missing couplingCoefficient
      - coupling endpoint with pin (forbidden)
      - wire/externalPort with couplingCoefficient (forbidden)
      - externalPort without direction (required)
      - simulator-agnostic analysis with unknown type / missing required field
      - dimensionWithTolerance with no fields
"""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO / "schemas"
CIAS_SCHEMA_DIR = REPO.parent / "CIAS" / "schemas"
PEAS_SCHEMA_DIR = REPO.parent / "PEAS" / "schemas"
EXAMPLES_DIR = REPO / "examples"

TAS_SCHEMA_NAMES = ["TAS", "inputs", "outputs", "utils", "topology"]
CIAS_SCHEMA_NAMES = ["CIAS"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def schemas():
    """Load TAS, CIAS and PEAS schemas by $id."""
    out = {}
    for name in TAS_SCHEMA_NAMES:
        s = json.loads((SCHEMA_DIR / f"{name}.json").read_text())
        out[s["$id"]] = s
    for name in CIAS_SCHEMA_NAMES:
        s = json.loads((CIAS_SCHEMA_DIR / f"{name}.json").read_text())
        out[s["$id"]] = s
    for path in PEAS_SCHEMA_DIR.rglob("*.json"):
        s = json.loads(path.read_text())
        out[s["$id"]] = s
    aas_dir = REPO.parent / "AAS" / "schemas"
    if aas_dir.is_dir():
        for path in aas_dir.rglob("*.json"):
            s = json.loads(path.read_text())
            out[s["$id"]] = s
    return out


@pytest.fixture(scope="session")
def registry(schemas):
    """Registry with TAS + CIAS + PEAS schemas."""
    resources = [
        (sid, Resource(contents=s, specification=DRAFT202012))
        for sid, s in schemas.items()
    ]
    # Stub the external PEAS peas.json root so component $refs resolve.
    if "https://psma.com/peas/peas.json" not in schemas:
        resources.append((
            "https://psma.com/peas/peas.json",
            Resource(contents={"type": "object"}, specification=DRAFT202012),
        ))
    return Registry().with_resources(resources)


@pytest.fixture(scope="session")
def tas_validator(schemas, registry):
    return Draft202012Validator(
        schemas["https://psma.com/tas/TAS.json"],
        registry=registry,
    )


@pytest.fixture
def flyback_doc():
    """Fresh deep-copy of the canonical positive example for each test."""
    return json.loads((EXAMPLES_DIR / "01_flyback_48v_to_12v.json").read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def assert_valid(validator, doc):
    errs = sorted(validator.iter_errors(doc), key=lambda e: e.path)
    assert not errs, "expected valid, got errors:\n" + "\n".join(
        f"  - {e.message} @ {list(e.absolute_path)}" for e in errs
    )


def assert_invalid(validator, doc, *, contains=None):
    errs = list(validator.iter_errors(doc))
    assert errs, "expected invalid, got no errors"
    if contains:
        joined = " | ".join(e.message for e in errs)
        assert contains in joined, f"expected error containing {contains!r}, got: {joined}"


# ---------------------------------------------------------------------------
# Schema-level tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", TAS_SCHEMA_NAMES)
def test_tas_schema_parses(name):
    json.loads((SCHEMA_DIR / f"{name}.json").read_text())


@pytest.mark.parametrize("name", CIAS_SCHEMA_NAMES)
def test_cias_schema_parses(name):
    json.loads((CIAS_SCHEMA_DIR / f"{name}.json").read_text())


@pytest.mark.parametrize("name", TAS_SCHEMA_NAMES)
def test_tas_schema_meta_valid(schemas, name):
    sid = f"https://psma.com/tas/{name}.json"
    Draft202012Validator.check_schema(schemas[sid])


@pytest.mark.parametrize("name", CIAS_SCHEMA_NAMES)
def test_cias_schema_meta_valid(schemas, name):
    sid = f"https://psma.com/cias/{name}.json"
    Draft202012Validator.check_schema(schemas[sid])


# ---------------------------------------------------------------------------
# Positive: the canonical example doc validates
# ---------------------------------------------------------------------------

def test_flyback_example_validates(tas_validator, flyback_doc):
    assert_valid(tas_validator, flyback_doc)


# ---------------------------------------------------------------------------
# inputs.json
# ---------------------------------------------------------------------------

def test_oppoint_output_must_have_exactly_one_of_current_or_power(tas_validator, flyback_doc):
    out = flyback_doc["inputs"]["operatingPoints"][0]["outputs"][0]

    # both → invalid
    out["current"] = 2.0
    assert_invalid(tas_validator, flyback_doc)

    # neither → invalid
    del out["current"]
    del out["power"]
    assert_invalid(tas_validator, flyback_doc)

    # current only → valid
    out["current"] = 2.0
    assert_valid(tas_validator, flyback_doc)


def test_efficiency_out_of_range(tas_validator, flyback_doc):
    flyback_doc["inputs"]["designRequirements"]["efficiency"] = 1.5
    assert_invalid(tas_validator, flyback_doc)
    flyback_doc["inputs"]["designRequirements"]["efficiency"] = 0.0
    assert_invalid(tas_validator, flyback_doc)


def test_switching_frequency_optional(tas_validator, flyback_doc):
    del flyback_doc["inputs"]["designRequirements"]["switchingFrequency"]
    assert_valid(tas_validator, flyback_doc)


def test_dimension_with_tolerance_requires_at_least_one_field(tas_validator, flyback_doc):
    flyback_doc["inputs"]["designRequirements"]["switchingFrequency"] = {}
    assert_invalid(tas_validator, flyback_doc)


# ---------------------------------------------------------------------------
# topology.json — stage variants (oneOf: power / isolation / virtual+physical control)
# ---------------------------------------------------------------------------

def _ctrl(flyback_doc):
    return next(s for s in flyback_doc["topology"]["stages"] if s["role"] == "control")


def test_virtual_control_is_logical(tas_validator, flyback_doc):
    # the flyback controller is a virtualControl: senses/drives, no circuit, no ports
    ctrl = _ctrl(flyback_doc)
    assert ctrl["controlImplementation"] == "virtual"
    assert "circuit" not in ctrl
    assert_valid(tas_validator, flyback_doc)


def test_virtual_control_cannot_have_circuit(tas_validator, flyback_doc):
    ctrl = _ctrl(flyback_doc)
    ctrl["circuit"] = {"name": "x", "ports": [], "components": [], "connections": []}
    assert_invalid(tas_validator, flyback_doc)


def test_virtual_control_cannot_have_input_port(tas_validator, flyback_doc):
    ctrl = _ctrl(flyback_doc)
    ctrl["inputPort"] = {"port": "x", "type": "dcBus"}
    assert_invalid(tas_validator, flyback_doc)


def test_virtual_control_requires_senses_and_drives(tas_validator, flyback_doc):
    ctrl = _ctrl(flyback_doc)
    del ctrl["senses"]
    assert_invalid(tas_validator, flyback_doc)


def test_virtual_control_may_reference_a_model(tas_validator, flyback_doc):
    ctrl = _ctrl(flyback_doc)
    ctrl["model"] = "type3-compensator"
    assert_valid(tas_validator, flyback_doc)


def test_physical_control_valid(tas_validator, flyback_doc):
    # swap the virtual controller for a physical one: a real brick, ports typed 'control'
    stages = flyback_doc["topology"]["stages"]
    idx = next(i for i, s in enumerate(stages) if s["role"] == "control")
    stages[idx] = {
        "name": "controller", "role": "control", "controlImplementation": "physical",
        "circuit": {
            "name": "uc-controller",
            "ports": [{"name": "fb"}, {"name": "gate"}],
            "components": [{"name": "U1", "data": "TAS/data/controllers.ndjson?partNumber=UCC28C44"}],
            "connections": [
                {"name": "f", "endpoints": [{"component": "U1", "pin": "FB"}, {"port": "fb"}]},
                {"name": "g", "endpoints": [{"component": "U1", "pin": "OUT"}, {"port": "gate"}]},
            ],
        },
        "ports": [
            {"port": "fb", "type": "control"},
            {"port": "gate", "type": "control"},
        ],
    }
    # wire the controller gate to the switch's exposed gate port
    flyback_doc["topology"]["interStageConnections"].append({
        "name": "gate_drive", "kind": "wire",
        "endpoints": [{"stage": "controller", "port": "gate"}, {"stage": "inverter", "port": "gate"}],
    })
    assert_valid(tas_validator, flyback_doc)


def test_physical_control_cannot_have_drives(tas_validator, flyback_doc):
    stages = flyback_doc["topology"]["stages"]
    idx = next(i for i, s in enumerate(stages) if s["role"] == "control")
    stages[idx] = {
        "name": "controller", "role": "control", "controlImplementation": "physical",
        "circuit": {"name": "c", "ports": [{"name": "g"}],
                    "components": [{"name": "U1", "data": "x"}],
                    "connections": [{"name": "n", "endpoints": [{"component": "U1", "pin": "O"}, {"port": "g"}]}]},
        "ports": [{"port": "g", "type": "control"}],
        "drives": [{"stage": "inverter", "component": "Q1", "signal": "gate"}],
    }
    assert_invalid(tas_validator, flyback_doc)


def test_unknown_control_implementation_rejected(tas_validator, flyback_doc):
    _ctrl(flyback_doc)["controlImplementation"] = "telepathic"
    assert_invalid(tas_validator, flyback_doc)


def test_power_stage_must_have_input_and_output_ports(tas_validator, flyback_doc):
    inv = flyback_doc["topology"]["stages"][0]
    assert inv["role"] == "inverter"
    del inv["inputPort"]
    assert_invalid(tas_validator, flyback_doc)


def test_power_stage_cannot_have_senses(tas_validator, flyback_doc):
    inv = flyback_doc["topology"]["stages"][0]
    inv["senses"] = [{"net": "Vout", "signal": "voltage"}]
    assert_invalid(tas_validator, flyback_doc)


def test_power_stage_cannot_have_multiple_outputs(tas_validator, flyback_doc):
    # a single-output power stage uses outputPort; an outputPorts array is isolation-only
    inv = flyback_doc["topology"]["stages"][0]
    inv["outputPorts"] = [inv["outputPort"], {"port": "extra", "type": "hfAc"}]
    del inv["outputPort"]
    assert_invalid(tas_validator, flyback_doc)


def test_isolation_can_have_multiple_output_ports(tas_validator, flyback_doc):
    iso = next(s for s in flyback_doc["topology"]["stages"] if s["role"] == "isolation")
    iso["outputPorts"].append({"port": "sec2", "type": "hfAc"})
    assert_valid(tas_validator, flyback_doc)


def test_unknown_stage_role_rejected(tas_validator, flyback_doc):
    flyback_doc["topology"]["stages"][0]["role"] = "magicStage"
    assert_invalid(tas_validator, flyback_doc)


def test_unknown_port_type_rejected(tas_validator, flyback_doc):
    flyback_doc["topology"]["stages"][0]["inputPort"]["type"] = "hyperspaceDc"
    assert_invalid(tas_validator, flyback_doc)


def test_phase_count_must_be_at_least_one(tas_validator, flyback_doc):
    flyback_doc["topology"]["stages"][0]["phaseCount"] = 0
    assert_invalid(tas_validator, flyback_doc)


# ---------------------------------------------------------------------------
# CIAS.json (via a stage's brick) — connection / endpoint rules
# ---------------------------------------------------------------------------

def _add_conn(flyback_doc, conn):
    flyback_doc["topology"]["stages"][0]["circuit"]["connections"].append(conn)


def test_connection_needs_at_least_two_endpoints(tas_validator, flyback_doc):
    _add_conn(flyback_doc, {"name": "dangling", "endpoints": [{"component": "Q1", "pin": "G"}]})
    assert_invalid(tas_validator, flyback_doc)


def test_wire_endpoint_must_be_pin_or_port(tas_validator, flyback_doc):
    # a bare {component} endpoint matches neither pinEndpoint nor portEndpoint
    _add_conn(flyback_doc, {
        "name": "net_bad",
        "endpoints": [{"component": "Q1"}, {"component": "C_in"}],
    })
    assert_invalid(tas_validator, flyback_doc)


def test_wire_endpoint_may_expose_a_port(tas_validator, flyback_doc):
    # a portEndpoint is a valid endpoint (it exposes the net at a brick terminal)
    _add_conn(flyback_doc, {
        "name": "extra_net",
        "endpoints": [{"component": "C_in", "pin": "1"}, {"port": "sw"}],
    })
    assert_valid(tas_validator, flyback_doc)


def test_connection_rejects_unknown_field(tas_validator, flyback_doc):
    # coupling is gone: a couplingCoefficient (or any extra key) is rejected
    _add_conn(flyback_doc, {
        "name": "net_bad", "couplingCoefficient": 0.5,
        "endpoints": [{"component": "Q1", "pin": "D"}, {"component": "C_in", "pin": "1"}],
    })
    assert_invalid(tas_validator, flyback_doc)


# ---------------------------------------------------------------------------
# topology.json — inter-stage connection variants (stage-qualified endpoints)
# ---------------------------------------------------------------------------

def test_external_net_requires_direction(tas_validator, flyback_doc):
    flyback_doc["topology"]["interStageConnections"].append({
        "name": "EN", "kind": "externalPort",
        "endpoints": [{"stage": "inverter", "port": "dc+"}],
    })
    assert_invalid(tas_validator, flyback_doc)


def test_internal_net_cannot_have_direction(tas_validator, flyback_doc):
    flyback_doc["topology"]["interStageConnections"].append({
        "name": "bad", "kind": "wire", "direction": "input",
        "endpoints": [{"stage": "inverter", "port": "sw"}, {"stage": "transformer", "port": "pri"}],
    })
    assert_invalid(tas_validator, flyback_doc)


def test_inter_stage_endpoint_must_be_stage_qualified(tas_validator, flyback_doc):
    # a brick-local {component, pin} endpoint is rejected at the inter-stage scope
    flyback_doc["topology"]["interStageConnections"].append({
        "name": "bad", "kind": "wire",
        "endpoints": [{"component": "Q1", "pin": "D"}, {"component": "C_in", "pin": "1"}],
    })
    assert_invalid(tas_validator, flyback_doc)


def test_inter_stage_coupling_kind_rejected(tas_validator, flyback_doc):
    # coupling is no longer a valid inter-stage kind (coupling lives inside a PEAS part)
    flyback_doc["topology"]["interStageConnections"].append({
        "name": "K_cross", "kind": "coupling", "couplingCoefficient": 0.5,
        "endpoints": [{"stage": "inverter", "component": "Q1"}, {"stage": "transformer", "component": "T1"}],
    })
    assert_invalid(tas_validator, flyback_doc)


# ---------------------------------------------------------------------------
# TAS.json — simulator-agnostic simulation block
# ---------------------------------------------------------------------------

def test_simulation_block_valid(tas_validator, flyback_doc):
    flyback_doc["simulation"] = {
        "analyses": [
            {"type": "transient", "stopTime": 0.01, "maximumTimeStep": 1e-6},
            {"type": "ac", "sweep": "decade", "startFrequency": 1, "stopFrequency": 1e6, "pointsPerInterval": 50},
        ],
        "models": [
            {"name": "Q1_model", "format": "spice-subcircuit", "definition": ".subckt Q1 d g s\n.ends"},
        ],
        "overrides": [
            {"stage": "inverter", "component": "Q1", "model": "Q1_model", "parameters": [{"name": "Rds_on", "value": 0.032}]},
        ],
    }
    assert_valid(tas_validator, flyback_doc)


def test_analysis_requires_known_type(tas_validator, flyback_doc):
    # An analysis with an unknown type matches none of the oneOf variants.
    flyback_doc["simulation"] = {"analyses": [{"type": "montecarlo"}]}
    assert_invalid(tas_validator, flyback_doc)


def test_transient_requires_stop_time(tas_validator, flyback_doc):
    flyback_doc["simulation"] = {"analyses": [{"type": "transient", "maximumTimeStep": 1e-6}]}
    assert_invalid(tas_validator, flyback_doc)


def test_simulation_model_format_enum(tas_validator, flyback_doc):
    flyback_doc["simulation"] = {
        "models": [{"name": "x", "format": "macromodel", "definition": "..."}]
    }
    assert_invalid(tas_validator, flyback_doc)


def test_simulation_rejects_raw_spice_commands(tas_validator, flyback_doc):
    # The old SPICE-specific 'commands' key is gone; the block is closed.
    flyback_doc["simulation"] = {"commands": [{"text": ".tran 1u 10m"}]}
    assert_invalid(tas_validator, flyback_doc)


# ---------------------------------------------------------------------------
# outputs.json — typed result tree
# ---------------------------------------------------------------------------

def test_outputs_validates(tas_validator, flyback_doc):
    flyback_doc["outputs"] = {
        "metrics": {"volume": 3.5e-5, "mass": 0.12},
        "operatingPoints": [
            {
                "name": "full_load_Vin_nom",
                "efficiency": 0.89,
                "switchingFrequency": 100000,
                "inputCurrent": {"rms": 0.55, "peak": 1.2, "ripplePkPk": 0.4},
                "outputResults": [
                    {"name": "12V", "voltageMean": 12.01, "voltageRipplePkPk": 0.08, "currentMean": 2.0}
                ],
                "stageResults": [
                    {
                        "stage": "inverter",
                        "loss": 1.4,
                        "dutyCycle": 0.42,
                        "componentResults": [
                            {"component": "Q1", "loss": 1.1, "temperature": 78.0},
                            {"component": "C_in", "loss": 0.3, "temperature": 45.0},
                        ],
                    }
                ],
            }
        ],
    }
    assert_valid(tas_validator, flyback_doc)


def test_outputs_negative_loss_rejected(tas_validator, flyback_doc):
    flyback_doc["outputs"] = {
        "operatingPoints": [{
            "name": "x",
            "stageResults": [{"stage": "inverter", "loss": -0.1}],
        }]
    }
    assert_invalid(tas_validator, flyback_doc)


# ---------------------------------------------------------------------------
# inputs.json — designRequirements: outputs[], inputType, isolation, AC fields
# ---------------------------------------------------------------------------

def test_design_outputs_required(tas_validator, flyback_doc):
    del flyback_doc["inputs"]["designRequirements"]["outputs"]
    assert_invalid(tas_validator, flyback_doc)


def test_design_outputs_min_one(tas_validator, flyback_doc):
    flyback_doc["inputs"]["designRequirements"]["outputs"] = []
    assert_invalid(tas_validator, flyback_doc)


def test_design_output_regulation_enum(tas_validator, flyback_doc):
    flyback_doc["inputs"]["designRequirements"]["outputs"][0]["regulation"] = "magic"
    assert_invalid(tas_validator, flyback_doc)


def test_design_output_negative_voltage_allowed(tas_validator, flyback_doc):
    # Polarity is encoded by sign of the voltage.
    flyback_doc["inputs"]["designRequirements"]["outputs"][0]["voltage"] = {"nominal": -12.0}
    assert_valid(tas_validator, flyback_doc)


def test_input_type_required(tas_validator, flyback_doc):
    del flyback_doc["inputs"]["designRequirements"]["inputType"]
    assert_invalid(tas_validator, flyback_doc)


def test_dc_input_forbids_line_frequency(tas_validator, flyback_doc):
    flyback_doc["inputs"]["designRequirements"]["lineFrequency"] = {"nominal": 50}
    assert_invalid(tas_validator, flyback_doc)


def test_dc_input_forbids_power_factor(tas_validator, flyback_doc):
    flyback_doc["inputs"]["designRequirements"]["powerFactorMinimum"] = 0.95
    assert_invalid(tas_validator, flyback_doc)


def test_dc_input_forbids_hold_up_time(tas_validator, flyback_doc):
    flyback_doc["inputs"]["designRequirements"]["holdUpTimeMinimum"] = 0.02
    assert_invalid(tas_validator, flyback_doc)


def test_ac_input_requires_line_frequency(tas_validator, flyback_doc):
    flyback_doc["inputs"]["designRequirements"]["inputType"] = "acSinglePhase"
    flyback_doc["inputs"]["designRequirements"]["inputVoltage"] = {"nominal": 230}
    # missing lineFrequency
    assert_invalid(tas_validator, flyback_doc)


def test_ac_input_with_pfc_and_holdup_valid(tas_validator, flyback_doc):
    dr = flyback_doc["inputs"]["designRequirements"]
    dr["inputType"] = "acSinglePhase"
    dr["inputVoltage"] = {"minimum": 90, "nominal": 230, "maximum": 264}
    dr["lineFrequency"] = {"minimum": 47, "nominal": 50, "maximum": 63}
    dr["powerFactorMinimum"] = 0.95
    dr["holdUpTimeMinimum"] = 0.020
    dr["bidirectional"] = False
    assert_valid(tas_validator, flyback_doc)


def test_isolation_voltage_optional_and_positive(tas_validator, flyback_doc):
    dr = flyback_doc["inputs"]["designRequirements"]
    del dr["isolationVoltage"]
    assert_valid(tas_validator, flyback_doc)
    dr["isolationVoltage"] = 0
    assert_invalid(tas_validator, flyback_doc)
    dr["isolationVoltage"] = 1500
    assert_valid(tas_validator, flyback_doc)


def test_power_factor_max_one(tas_validator, flyback_doc):
    dr = flyback_doc["inputs"]["designRequirements"]
    dr["inputType"] = "acSinglePhase"
    dr["inputVoltage"] = {"nominal": 230}
    dr["lineFrequency"] = {"nominal": 50}
    dr["powerFactorMinimum"] = 1.5
    assert_invalid(tas_validator, flyback_doc)


# ---------------------------------------------------------------------------
# inputs.json — operatingPoint.outputs[]
# ---------------------------------------------------------------------------

def test_oppoint_outputs_required(tas_validator, flyback_doc):
    del flyback_doc["inputs"]["operatingPoints"][0]["outputs"]
    assert_invalid(tas_validator, flyback_doc)


def test_oppoint_output_voltage_setpoint_optional(tas_validator, flyback_doc):
    flyback_doc["inputs"]["operatingPoints"][0]["outputs"][0]["voltage"] = 12.5
    assert_valid(tas_validator, flyback_doc)


def test_oppoint_output_current_alternative(tas_validator, flyback_doc):
    out = flyback_doc["inputs"]["operatingPoints"][0]["outputs"][0]
    del out["power"]
    out["current"] = 2.0
    assert_valid(tas_validator, flyback_doc)


def test_oppoint_dutyCycle_no_longer_in_oppoint(tas_validator, flyback_doc):
    # dutyCycle was moved out of operatingPoint into outputs.stageResults.
    flyback_doc["inputs"]["operatingPoints"][0]["dutyCycle"] = 0.5
    assert_invalid(tas_validator, flyback_doc)


# ---------------------------------------------------------------------------
# outputs.json — new result fields
# ---------------------------------------------------------------------------

def test_output_result_negative_ripple_rejected(tas_validator, flyback_doc):
    flyback_doc["outputs"] = {
        "operatingPoints": [{
            "name": "x",
            "outputResults": [
                {"name": "12V", "voltageMean": 12.0, "voltageRipplePkPk": -0.05, "currentMean": 2.0}
            ],
        }]
    }
    assert_invalid(tas_validator, flyback_doc)


def test_input_current_rejects_negative(tas_validator, flyback_doc):
    flyback_doc["outputs"] = {
        "operatingPoints": [{
            "name": "x",
            "inputCurrent": {"rms": -0.1},
        }]
    }
    assert_invalid(tas_validator, flyback_doc)


def test_achieved_power_factor_max_one(tas_validator, flyback_doc):
    flyback_doc["outputs"] = {
        "operatingPoints": [{"name": "x", "achievedPowerFactor": 1.5}]
    }
    assert_invalid(tas_validator, flyback_doc)


def test_stage_result_dutyCycle_range(tas_validator, flyback_doc):
    for d in (0, 1, -0.1, 1.1):
        flyback_doc["outputs"] = {
            "operatingPoints": [{
                "name": "x",
                "stageResults": [{"stage": "inverter", "loss": 0.5, "dutyCycle": d}],
            }]
        }
        assert_invalid(tas_validator, flyback_doc)


def test_metrics_no_longer_accepts_cost(tas_validator, flyback_doc):
    flyback_doc["outputs"] = {"metrics": {"cost": 5.0}}
    assert_invalid(tas_validator, flyback_doc)
