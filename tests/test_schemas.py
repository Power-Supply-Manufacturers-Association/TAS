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
      - simulation command without leading dot
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
EXAMPLES_DIR = REPO / "examples" / "v2"

SCHEMA_NAMES = ["TAS", "inputs", "topology", "outputs", "circuit", "utils"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def schemas():
    """Load every schema by $id."""
    out = {}
    for name in SCHEMA_NAMES:
        s = json.loads((SCHEMA_DIR / f"{name}.json").read_text())
        out[s["$id"]] = s
    return out


@pytest.fixture(scope="session")
def registry(schemas):
    """Registry with all TAS schemas + a stub for the external PEAS schema."""
    resources = [
        (sid, Resource(contents=s, specification=DRAFT202012))
        for sid, s in schemas.items()
    ]
    # Stub the external PEAS schema so $refs resolve in tests without a network call.
    resources.append((
        "http://openconverters.com/schemas/PEAS/peas.json",
        Resource(contents={"type": "object"}, specification=DRAFT202012),
    ))
    return Registry().with_resources(resources)


@pytest.fixture(scope="session")
def tas_validator(schemas, registry):
    return Draft202012Validator(
        schemas["http://openconverters.com/schemas/TAS/TAS.json"],
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

@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schema_parses(name):
    json.loads((SCHEMA_DIR / f"{name}.json").read_text())


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schema_meta_valid(schemas, name):
    sid = f"http://openconverters.com/schemas/TAS/{name}.json"
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
# topology.json — stage role conditionals
# ---------------------------------------------------------------------------

def test_control_stage_cannot_have_input_port(tas_validator, flyback_doc):
    ctrl = next(s for s in flyback_doc["topology"]["stages"] if s["role"] == "control")
    ctrl["inputPort"] = {"type": "dcBus", "wire": "Vin"}
    assert_invalid(tas_validator, flyback_doc)


def test_control_stage_cannot_have_output_ports(tas_validator, flyback_doc):
    ctrl = next(s for s in flyback_doc["topology"]["stages"] if s["role"] == "control")
    ctrl["outputPorts"] = [{"type": "dcBus", "wire": "x"}]
    assert_invalid(tas_validator, flyback_doc)


def test_control_stage_requires_senses_and_drives(tas_validator, flyback_doc):
    ctrl = next(s for s in flyback_doc["topology"]["stages"] if s["role"] == "control")
    del ctrl["senses"]
    assert_invalid(tas_validator, flyback_doc)


def test_power_stage_must_have_input_and_output_ports(tas_validator, flyback_doc):
    inv = flyback_doc["topology"]["stages"][0]
    assert inv["role"] == "inverter"
    del inv["inputPort"]
    assert_invalid(tas_validator, flyback_doc)


def test_power_stage_cannot_have_senses(tas_validator, flyback_doc):
    inv = flyback_doc["topology"]["stages"][0]
    inv["senses"] = [{"wire": "Vout", "signal": "voltage"}]
    assert_invalid(tas_validator, flyback_doc)


def test_only_isolation_may_have_multiple_output_ports(tas_validator, flyback_doc):
    # adding a 2nd output port to the inverter (role=inverter) → invalid
    inv = flyback_doc["topology"]["stages"][0]
    inv["outputPorts"].append({"type": "hfAc", "wire": "extra"})
    assert_invalid(tas_validator, flyback_doc)


def test_isolation_can_have_multiple_output_ports(tas_validator, flyback_doc):
    iso = next(s for s in flyback_doc["topology"]["stages"] if s["role"] == "isolation")
    iso["outputPorts"].append({"type": "hfAc", "wire": "sec_5V", "name": "5V"})
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
# circuit.json — connection kind conditionals
# ---------------------------------------------------------------------------

def _add_conn(flyback_doc, conn):
    flyback_doc["topology"]["stages"][0]["circuit"]["connections"].append(conn)


def test_coupling_requires_coupling_coefficient(tas_validator, flyback_doc):
    _add_conn(flyback_doc, {
        "name": "K_bad", "kind": "coupling",
        "endpoints": [{"component": "Q1"}, {"component": "C_in"}],
    })
    assert_invalid(tas_validator, flyback_doc)


def test_coupling_endpoint_must_not_have_pin(tas_validator, flyback_doc):
    _add_conn(flyback_doc, {
        "name": "K_bad", "kind": "coupling", "couplingCoefficient": 0.99,
        "endpoints": [{"component": "Q1", "pin": "D"}, {"component": "C_in", "pin": "1"}],
    })
    assert_invalid(tas_validator, flyback_doc)


def test_wire_endpoint_requires_pin(tas_validator, flyback_doc):
    _add_conn(flyback_doc, {
        "name": "net_bad", "kind": "wire",
        "endpoints": [{"component": "Q1"}, {"component": "C_in"}],
    })
    assert_invalid(tas_validator, flyback_doc)


def test_wire_cannot_have_coupling_coefficient(tas_validator, flyback_doc):
    _add_conn(flyback_doc, {
        "name": "net_bad", "kind": "wire", "couplingCoefficient": 0.5,
        "endpoints": [{"component": "Q1", "pin": "D"}, {"component": "C_in", "pin": "1"}],
    })
    assert_invalid(tas_validator, flyback_doc)


def test_external_port_requires_direction(tas_validator, flyback_doc):
    flyback_doc["topology"]["interStageCircuit"].append({
        "name": "EN", "kind": "externalPort",
        "endpoints": [{"component": "Q1", "pin": "G"}, {"component": "Q1", "pin": "S"}],
    })
    assert_invalid(tas_validator, flyback_doc)


def test_wire_cannot_have_direction(tas_validator, flyback_doc):
    _add_conn(flyback_doc, {
        "name": "net_bad", "kind": "wire", "direction": "input",
        "endpoints": [{"component": "Q1", "pin": "D"}, {"component": "C_in", "pin": "1"}],
    })
    assert_invalid(tas_validator, flyback_doc)


def test_coupling_coefficient_range(tas_validator, flyback_doc):
    base = {
        "name": "K_x", "kind": "coupling",
        "endpoints": [{"component": "Q1"}, {"component": "C_in"}],
    }
    for k in (-0.1, 0, 1.1):
        flyback_doc["topology"]["stages"][0]["circuit"]["connections"] = [
            dict(base, couplingCoefficient=k)
        ]
        assert_invalid(tas_validator, flyback_doc)


def test_unknown_connection_kind_rejected(tas_validator, flyback_doc):
    _add_conn(flyback_doc, {
        "name": "x", "kind": "telepathy",
        "endpoints": [{"component": "Q1", "pin": "D"}, {"component": "C_in", "pin": "1"}],
    })
    assert_invalid(tas_validator, flyback_doc)


# ---------------------------------------------------------------------------
# TAS.json — simulation block
# ---------------------------------------------------------------------------

def test_simulation_block_valid(tas_validator, flyback_doc):
    flyback_doc["simulation"] = {
        "models": [{"name": "Q1_model", "kind": "subckt", "text": ".subckt Q1 d g s\n.ends"}],
        "commands": [{"text": ".tran 1u 10m"}, {"text": ".ic V(Vout)=12"}],
    }
    assert_valid(tas_validator, flyback_doc)


def test_simulation_command_must_start_with_dot(tas_validator, flyback_doc):
    flyback_doc["simulation"] = {"commands": [{"text": "tran 1u 10m"}]}
    assert_invalid(tas_validator, flyback_doc)


def test_spice_model_kind_enum(tas_validator, flyback_doc):
    flyback_doc["simulation"] = {
        "models": [{"name": "x", "kind": "macromodel", "text": "..."}]
    }
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
