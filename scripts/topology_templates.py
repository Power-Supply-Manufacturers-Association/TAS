"""Family topology templates for TAS v2 converter porting.

Each `template_<family>(refs)` returns a `topology` dict (the value of TAS.topology)
matching schemas/topology.json.

`refs` is a dict of {role: uri_string} where uri_string is either a real
TAS data URI ("TAS/data/mosfets.ndjson?partNumber=...") or a synthetic
placeholder URI ("TAS/data/mosfets.ndjson?placeholder=Q1"). All component
data fields are URI strings, never inline PEAS objects, because:
  - PEAS oneOf currently $refs a non-existent SAS/semiconductor.json (broken).
  - The TAS schema accepts URI strings as a valid `data` value.
  - Placeholders preserve the family topology so the v1 record's intent is
    captured even when no real partNumbers were supplied.

Each template guarantees that:
  - Every stage's port wires appear in topology.interStageCircuit.
  - Component names referenced by interStageCircuit endpoints exist in some
    stage's circuit.components.
  - Coupling endpoints carry no `pin`; wire/externalPort endpoints do.
  - Exactly one external input port (Vin) and one external output port (Vout).
  - Control stage references a controller component placed in its own circuit.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def placeholder(file: str, name: str) -> str:
    return f"TAS/data/{file}.ndjson?placeholder={name}"


def comp(name: str, uri: str) -> dict:
    return {"name": name, "data": uri}


def wire(name: str, *endpoints: tuple[str, str]) -> dict:
    return {"name": name, "kind": "wire",
            "endpoints": [{"component": c, "pin": p} for c, p in endpoints]}


def ext_port(name: str, direction: str, *endpoints: tuple[str, str]) -> dict:
    return {"name": name, "kind": "externalPort", "direction": direction,
            "endpoints": [{"component": c, "pin": p} for c, p in endpoints]}


def coupling(name: str, k: float, *components: str) -> dict:
    return {"name": name, "kind": "coupling", "couplingCoefficient": k,
            "endpoints": [{"component": c} for c in components]}


def control_stage(controller_uri: str, switch_components: list[str],
                  sense_wire: str = "Vout") -> dict:
    """Standard control stage: senses Vout, drives all listed switches' gates."""
    return {
        "name": "controller",
        "role": "control",
        "circuit": {
            "components": [comp("U1", controller_uri)],
            "connections": [],
        },
        "senses": [{"wire": sense_wire, "signal": "voltage"}],
        "drives": [{"component": s, "signal": "gate"} for s in switch_components],
    }


def _ref(refs: dict, role: str, file: str, default_name: str) -> str:
    """Get the URI for a role; fall back to a placeholder."""
    return refs.get(role, placeholder(file, default_name))


# ---------------------------------------------------------------------------
# Family templates
# ---------------------------------------------------------------------------

def template_buck(refs: dict) -> dict:
    """Non-isolated buck: switchingCell {Q_high, D_low, L, C_in, C_out} +
    control. Synchronous variant uses Q_low instead of D_low (template_syncBuck)."""
    Q1 = _ref(refs, "highSideSwitch", "mosfets", "Q1")
    D1 = _ref(refs, "lowSideDiode", "diodes", "D1")
    L1 = _ref(refs, "inductor", "magnetics", "L1")
    Cin = _ref(refs, "inputCap", "capacitors", "C_in")
    Cout = _ref(refs, "outputCap", "capacitors", "C_out")
    U1 = _ref(refs, "controller", "controllers", "U1")
    return {
        "stages": [
            {
                "name": "power_stage", "role": "switchingCell",
                "inputPort":  {"type": "dcBus",    "wire": "Vin"},
                "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
                "circuit": {
                    "components": [comp("Q1", Q1), comp("D1", D1),
                                   comp("L1", L1), comp("C_in", Cin),
                                   comp("C_out", Cout)],
                    "connections": [
                        wire("sw_node", ("Q1", "S"), ("D1", "K"), ("L1", "1")),
                    ],
                },
            },
            control_stage(U1, ["Q1"]),
        ],
        "interStageCircuit": [
            ext_port("Vin", "input",  ("C_in", "1"), ("Q1", "D")),
            ext_port("Vout", "output", ("L1", "2"), ("C_out", "1")),
        ],
    }


def template_syncBuck(refs: dict) -> dict:
    """Synchronous buck: switchingCell {Q_high, Q_low, L, C_in, C_out} + control."""
    Qh = _ref(refs, "highSideSwitch", "mosfets", "Q_high")
    Ql = _ref(refs, "lowSideSwitch",  "mosfets", "Q_low")
    L1 = _ref(refs, "inductor",       "magnetics", "L1")
    Cin = _ref(refs, "inputCap",      "capacitors", "C_in")
    Cout = _ref(refs, "outputCap",    "capacitors", "C_out")
    U1 = _ref(refs, "controller",     "controllers", "U1")
    return {
        "stages": [
            {
                "name": "power_stage", "role": "switchingCell",
                "inputPort":  {"type": "dcBus",    "wire": "Vin"},
                "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
                "circuit": {
                    "components": [comp("Q_high", Qh), comp("Q_low", Ql),
                                   comp("L1", L1), comp("C_in", Cin),
                                   comp("C_out", Cout)],
                    "connections": [
                        wire("sw_node", ("Q_high", "S"), ("Q_low", "D"),
                             ("L1", "1")),
                    ],
                },
            },
            control_stage(U1, ["Q_high", "Q_low"]),
        ],
        "interStageCircuit": [
            ext_port("Vin", "input",  ("C_in", "1"), ("Q_high", "D")),
            ext_port("Vout", "output", ("L1", "2"), ("C_out", "1")),
        ],
    }


def template_boost(refs: dict) -> dict:
    """Non-isolated boost: switchingCell {L, Q, D, C_in, C_out} + control."""
    Q1 = _ref(refs, "switch",     "mosfets", "Q1")
    D1 = _ref(refs, "diode",      "diodes", "D1")
    L1 = _ref(refs, "inductor",   "magnetics", "L1")
    Cin = _ref(refs, "inputCap",  "capacitors", "C_in")
    Cout = _ref(refs, "outputCap", "capacitors", "C_out")
    U1 = _ref(refs, "controller", "controllers", "U1")
    return {
        "stages": [
            {
                "name": "power_stage", "role": "switchingCell",
                "inputPort":  {"type": "dcBus",    "wire": "Vin"},
                "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
                "circuit": {
                    "components": [comp("L1", L1), comp("Q1", Q1),
                                   comp("D1", D1), comp("C_in", Cin),
                                   comp("C_out", Cout)],
                    "connections": [
                        wire("sw_node", ("L1", "2"), ("Q1", "D"), ("D1", "A")),
                    ],
                },
            },
            control_stage(U1, ["Q1"]),
        ],
        "interStageCircuit": [
            ext_port("Vin", "input",  ("C_in", "1"), ("L1", "1")),
            ext_port("Vout", "output", ("D1", "K"), ("C_out", "1")),
        ],
    }


def template_flyback(refs: dict, ac_input: bool = False) -> dict:
    """Flyback: [optional rectifier ->] inverter -> isolation -> outputRectifier
    -> outputFilter + control."""
    Q1 = _ref(refs, "switch",       "mosfets", "Q1")
    T1 = _ref(refs, "transformer",  "magnetics", "T1")
    D1 = _ref(refs, "outputDiode",  "diodes", "D1")
    Cin = _ref(refs, "inputCap",    "capacitors", "C_in")
    Cout = _ref(refs, "outputCap",  "capacitors", "C_out")
    U1 = _ref(refs, "controller",   "controllers", "U1")

    stages = []
    if ac_input:
        BR = _ref(refs, "bridge", "diodes", "BR1")
        stages.append({
            "name": "input_rectifier", "role": "rectifier",
            "inputPort":  {"type": "acLine", "wire": "Vac"},
            "outputPorts": [{"type": "pulsatingDc", "wire": "Vrect"}],
            "circuit": {
                "components": [comp("BR1", BR)],
                "connections": [
                    wire("br_internal", ("BR1", "AC1"), ("BR1", "AC2")),
                ],
            },
        })
    stages.extend([
        {
            "name": "inverter", "role": "inverter",
            "inputPort":  {"type": "dcBus" if not ac_input else "pulsatingDc",
                           "wire": "Vin" if not ac_input else "Vrect"},
            "outputPorts": [{"type": "hfAc", "wire": "sw_node"}],
            "circuit": {
                "components": [comp("Q1", Q1), comp("C_in", Cin)],
                "connections": [
                    wire("drain", ("Q1", "D"), ("C_in", "1")),
                ],
            },
        },
        {
            "name": "transformer", "role": "isolation",
            "inputPort":  {"type": "hfAc", "wire": "sw_node"},
            "outputPorts": [{"type": "hfAc", "wire": "sec"}],
            "circuit": {
                "components": [comp("T1", T1)],
                "connections": [coupling("K_T1", 0.99, "T1", "T1")],
            },
        },
        {
            "name": "rectifier", "role": "outputRectifier",
            "inputPort":  {"type": "hfAc", "wire": "sec"},
            "outputPorts": [{"type": "pulsatingDc", "wire": "rect_out"}],
            "circuit": {
                "components": [comp("D1", D1)],
                "connections": [
                    wire("anode_internal", ("D1", "A"), ("D1", "K")),
                ],
            },
        },
        {
            "name": "filter", "role": "outputFilter",
            "inputPort":  {"type": "pulsatingDc", "wire": "rect_out"},
            "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
            "circuit": {
                "components": [comp("C_out", Cout)],
                "connections": [
                    wire("cap_top", ("C_out", "1"), ("C_out", "2")),
                ],
            },
        },
        control_stage(U1, ["Q1"]),
    ])

    inter = []
    if ac_input:
        inter.append(ext_port("Vac", "input", ("BR1", "AC1"), ("BR1", "AC2")))
        inter.append(wire("Vrect", ("BR1", "DC+"), ("C_in", "1")))
    else:
        inter.append(ext_port("Vin", "input", ("C_in", "2"), ("Q1", "S")))
    inter.extend([
        ext_port("Vout", "output", ("C_out", "1"), ("C_out", "2")),
        wire("sw_node", ("Q1", "D"), ("T1", "P1")),
        wire("sec", ("T1", "S1"), ("D1", "A")),
        wire("rect_out", ("D1", "K"), ("C_out", "1")),
    ])
    return {"stages": stages, "interStageCircuit": inter}


def template_llc(refs: dict, ac_input: bool = True) -> dict:
    """LLC half-bridge: [rectifier ->] inverter (Q_high+Q_low+Lr+Cr) -> isolation
    -> outputRectifier (D1+D2 center-tap or full-bridge) -> outputFilter + control."""
    Qh = _ref(refs, "highSideSwitch", "mosfets", "Q_high")
    Ql = _ref(refs, "lowSideSwitch",  "mosfets", "Q_low")
    Lr = _ref(refs, "resonantInductor",  "magnetics", "Lr")
    Cr = _ref(refs, "resonantCap",       "capacitors", "Cr")
    T1 = _ref(refs, "transformer",       "magnetics", "T1")
    D1 = _ref(refs, "outputDiode1",      "diodes", "D1")
    D2 = _ref(refs, "outputDiode2",      "diodes", "D2")
    Cin = _ref(refs, "inputCap",         "capacitors", "C_in")
    Cout = _ref(refs, "outputCap",       "capacitors", "C_out")
    U1 = _ref(refs, "controller",        "controllers", "U1")

    stages = []
    if ac_input:
        BR = _ref(refs, "bridge", "diodes", "BR1")
        stages.append({
            "name": "input_rectifier", "role": "rectifier",
            "inputPort":  {"type": "acLine", "wire": "Vac"},
            "outputPorts": [{"type": "pulsatingDc", "wire": "Vrect"}],
            "circuit": {
                "components": [comp("BR1", BR)],
                "connections": [
                    wire("br_internal", ("BR1", "AC1"), ("BR1", "AC2")),
                ],
            },
        })
    stages.extend([
        {
            "name": "inverter", "role": "inverter",
            "inputPort":  {"type": "dcBus" if not ac_input else "pulsatingDc",
                           "wire": "Vin" if not ac_input else "Vrect"},
            "outputPorts": [{"type": "hfAc", "wire": "tank_out"}],
            "circuit": {
                "components": [comp("Q_high", Qh), comp("Q_low", Ql),
                               comp("Lr", Lr), comp("Cr", Cr),
                               comp("C_in", Cin)],
                "connections": [
                    wire("hb_mid", ("Q_high", "S"), ("Q_low", "D"), ("Lr", "1")),
                    wire("Lr_to_Cr", ("Lr", "2"), ("Cr", "1")),
                ],
            },
        },
        {
            "name": "transformer", "role": "isolation",
            "inputPort":  {"type": "hfAc", "wire": "tank_out"},
            "outputPorts": [{"type": "hfAc", "wire": "sec"}],
            "circuit": {
                "components": [comp("T1", T1)],
                "connections": [coupling("K_T1", 0.99, "T1", "T1")],
            },
        },
        {
            "name": "rectifier", "role": "outputRectifier",
            "inputPort":  {"type": "hfAc", "wire": "sec"},
            "outputPorts": [{"type": "pulsatingDc", "wire": "rect_out"}],
            "circuit": {
                "components": [comp("D1", D1), comp("D2", D2)],
                "connections": [
                    wire("anodes", ("D1", "A"), ("D2", "A")),
                ],
            },
        },
        {
            "name": "filter", "role": "outputFilter",
            "inputPort":  {"type": "pulsatingDc", "wire": "rect_out"},
            "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
            "circuit": {
                "components": [comp("C_out", Cout)],
                "connections": [
                    wire("cap_top", ("C_out", "1"), ("C_out", "2")),
                ],
            },
        },
        control_stage(U1, ["Q_high", "Q_low"]),
    ])

    inter = []
    if ac_input:
        inter.append(ext_port("Vac", "input", ("BR1", "AC1"), ("BR1", "AC2")))
        inter.append(wire("Vrect", ("BR1", "DC+"), ("C_in", "1")))
    else:
        inter.append(ext_port("Vin", "input", ("C_in", "1"), ("Q_high", "D")))
    inter.extend([
        ext_port("Vout", "output", ("C_out", "1"), ("C_out", "2")),
        wire("tank_out", ("Cr", "2"), ("T1", "P1")),
        wire("sec", ("T1", "S1"), ("D1", "A")),
        wire("rect_out", ("D1", "K"), ("C_out", "1")),
    ])
    return {"stages": stages, "interStageCircuit": inter}


def template_pfcBoost(refs: dict) -> dict:
    """Single-stage PFC boost (AC-DC): rectifier (bridge) -> pfc {L, Q, D, C_bulk}
    + control. Output is a regulated DC bus (typically 380-400V)."""
    BR = _ref(refs, "bridge",      "diodes", "BR1")
    L1 = _ref(refs, "inductor",    "magnetics", "L_pfc")
    Q1 = _ref(refs, "switch",      "mosfets", "Q_pfc")
    D1 = _ref(refs, "boostDiode",  "diodes", "D_boost")
    Cb = _ref(refs, "bulkCap",     "capacitors", "C_bulk")
    U1 = _ref(refs, "controller",  "controllers", "U_pfc")
    return {
        "stages": [
            {
                "name": "input_rectifier", "role": "rectifier",
                "inputPort":  {"type": "acLine", "wire": "Vac"},
                "outputPorts": [{"type": "pulsatingDc", "wire": "Vrect"}],
                "circuit": {
                    "components": [comp("BR1", BR)],
                    "connections": [
                        wire("br_internal", ("BR1", "AC1"), ("BR1", "AC2")),
                    ],
                },
            },
            {
                "name": "pfc_boost", "role": "pfc",
                "inputPort":  {"type": "pulsatingDc", "wire": "Vrect"},
                "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
                "circuit": {
                    "components": [comp("L_pfc", L1), comp("Q_pfc", Q1),
                                   comp("D_boost", D1), comp("C_bulk", Cb)],
                    "connections": [
                        wire("sw_node", ("L_pfc", "2"), ("Q_pfc", "D"),
                             ("D_boost", "A")),
                    ],
                },
            },
            control_stage(U1, ["Q_pfc"]),
        ],
        "interStageCircuit": [
            ext_port("Vac", "input", ("BR1", "AC1"), ("BR1", "AC2")),
            wire("Vrect", ("BR1", "DC+"), ("L_pfc", "1")),
            ext_port("Vout", "output", ("D_boost", "K"), ("C_bulk", "1")),
        ],
    }


def template_pfcLlc(refs: dict) -> dict:
    """Two-stage AC-DC: PFC boost front end -> LLC half-bridge isolation."""
    BR = _ref(refs, "bridge",       "diodes", "BR1")
    L_pfc = _ref(refs, "pfcInductor",  "magnetics", "L_pfc")
    Q_pfc = _ref(refs, "pfcSwitch",    "mosfets", "Q_pfc")
    D_pfc = _ref(refs, "pfcDiode",     "diodes", "D_boost")
    C_bulk = _ref(refs, "bulkCap",     "capacitors", "C_bulk")
    Qh = _ref(refs, "llcHigh",         "mosfets", "Q_high")
    Ql = _ref(refs, "llcLow",          "mosfets", "Q_low")
    Lr = _ref(refs, "resonantInductor","magnetics", "Lr")
    Cr = _ref(refs, "resonantCap",     "capacitors", "Cr")
    T1 = _ref(refs, "transformer",     "magnetics", "T1")
    D1 = _ref(refs, "outputDiode1",    "diodes", "D1")
    D2 = _ref(refs, "outputDiode2",    "diodes", "D2")
    Cout = _ref(refs, "outputCap",     "capacitors", "C_out")
    U_pfc = _ref(refs, "pfcController","controllers", "U_pfc")
    U_llc = _ref(refs, "llcController","controllers", "U_llc")
    return {
        "stages": [
            {
                "name": "input_rectifier", "role": "rectifier",
                "inputPort":  {"type": "acLine", "wire": "Vac"},
                "outputPorts": [{"type": "pulsatingDc", "wire": "Vrect"}],
                "circuit": {
                    "components": [comp("BR1", BR)],
                    "connections": [
                        wire("br_internal", ("BR1", "AC1"), ("BR1", "AC2")),
                    ],
                },
            },
            {
                "name": "pfc_boost", "role": "pfc",
                "inputPort":  {"type": "pulsatingDc", "wire": "Vrect"},
                "outputPorts": [{"type": "dcBus", "wire": "Vbulk"}],
                "circuit": {
                    "components": [comp("L_pfc", L_pfc), comp("Q_pfc", Q_pfc),
                                   comp("D_boost", D_pfc), comp("C_bulk", C_bulk)],
                    "connections": [
                        wire("pfc_sw", ("L_pfc", "2"), ("Q_pfc", "D"),
                             ("D_boost", "A")),
                    ],
                },
            },
            {
                "name": "llc_inverter", "role": "inverter",
                "inputPort":  {"type": "dcBus", "wire": "Vbulk"},
                "outputPorts": [{"type": "hfAc", "wire": "tank_out"}],
                "circuit": {
                    "components": [comp("Q_high", Qh), comp("Q_low", Ql),
                                   comp("Lr", Lr), comp("Cr", Cr)],
                    "connections": [
                        wire("hb_mid", ("Q_high", "S"), ("Q_low", "D"),
                             ("Lr", "1")),
                        wire("Lr_to_Cr", ("Lr", "2"), ("Cr", "1")),
                    ],
                },
            },
            {
                "name": "transformer", "role": "isolation",
                "inputPort":  {"type": "hfAc", "wire": "tank_out"},
                "outputPorts": [{"type": "hfAc", "wire": "sec"}],
                "circuit": {
                    "components": [comp("T1", T1)],
                    "connections": [coupling("K_T1", 0.99, "T1", "T1")],
                },
            },
            {
                "name": "rectifier", "role": "outputRectifier",
                "inputPort":  {"type": "hfAc", "wire": "sec"},
                "outputPorts": [{"type": "pulsatingDc", "wire": "rect_out"}],
                "circuit": {
                    "components": [comp("D1", D1), comp("D2", D2)],
                    "connections": [
                        wire("anodes", ("D1", "A"), ("D2", "A")),
                    ],
                },
            },
            {
                "name": "filter", "role": "outputFilter",
                "inputPort":  {"type": "pulsatingDc", "wire": "rect_out"},
                "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
                "circuit": {
                    "components": [comp("C_out", Cout)],
                    "connections": [
                        wire("cap_top", ("C_out", "1"), ("C_out", "2")),
                    ],
                },
            },
            control_stage(U_pfc, ["Q_pfc"]),
            {
                "name": "llc_controller", "role": "control",
                "circuit": {"components": [comp("U_llc", U_llc)],
                            "connections": []},
                "senses": [{"wire": "Vout", "signal": "voltage"}],
                "drives": [{"component": "Q_high", "signal": "gate"},
                           {"component": "Q_low", "signal": "gate"}],
            },
        ],
        "interStageCircuit": [
            ext_port("Vac", "input", ("BR1", "AC1"), ("BR1", "AC2")),
            wire("Vrect", ("BR1", "DC+"), ("L_pfc", "1")),
            wire("Vbulk", ("D_boost", "K"), ("C_bulk", "1"), ("Q_high", "D")),
            wire("tank_out", ("Cr", "2"), ("T1", "P1")),
            wire("sec", ("T1", "S1"), ("D1", "A")),
            wire("rect_out", ("D1", "K"), ("C_out", "1")),
            ext_port("Vout", "output", ("C_out", "1"), ("C_out", "2")),
        ],
    }


def template_dab(refs: dict) -> dict:
    """Dual Active Bridge (isolated, bidirectional, DC-DC): two full bridges
    around a transformer with a series leakage inductor."""
    Q1 = _ref(refs, "primaryQ1", "mosfets", "Q1")
    Q2 = _ref(refs, "primaryQ2", "mosfets", "Q2")
    Q3 = _ref(refs, "primaryQ3", "mosfets", "Q3")
    Q4 = _ref(refs, "primaryQ4", "mosfets", "Q4")
    Q5 = _ref(refs, "secondaryQ1", "mosfets", "Q5")
    Q6 = _ref(refs, "secondaryQ2", "mosfets", "Q6")
    Q7 = _ref(refs, "secondaryQ3", "mosfets", "Q7")
    Q8 = _ref(refs, "secondaryQ4", "mosfets", "Q8")
    Lk = _ref(refs, "leakageInductor", "magnetics", "Lk")
    T1 = _ref(refs, "transformer",     "magnetics", "T1")
    Cin = _ref(refs, "inputCap",       "capacitors", "C_in")
    Cout = _ref(refs, "outputCap",     "capacitors", "C_out")
    U1 = _ref(refs, "controller",      "controllers", "U1")
    return {
        "stages": [
            {
                "name": "primary_bridge", "role": "inverter",
                "inputPort":  {"type": "dcBus", "wire": "Vin"},
                "outputPorts": [{"type": "hfAc", "wire": "pri_ac"}],
                "circuit": {
                    "components": [comp("Q1", Q1), comp("Q2", Q2),
                                   comp("Q3", Q3), comp("Q4", Q4),
                                   comp("Lk", Lk), comp("C_in", Cin)],
                    "connections": [
                        wire("pri_a", ("Q1", "S"), ("Q2", "D"), ("Lk", "1")),
                        wire("pri_b", ("Q3", "S"), ("Q4", "D")),
                    ],
                },
            },
            {
                "name": "transformer", "role": "isolation",
                "inputPort":  {"type": "hfAc", "wire": "pri_ac"},
                "outputPorts": [{"type": "hfAc", "wire": "sec_ac"}],
                "circuit": {
                    "components": [comp("T1", T1)],
                    "connections": [coupling("K_T1", 0.99, "T1", "T1")],
                },
            },
            {
                "name": "secondary_bridge", "role": "outputRectifier",
                "inputPort":  {"type": "hfAc", "wire": "sec_ac"},
                "outputPorts": [{"type": "pulsatingDc", "wire": "Vsec_dc"}],
                "circuit": {
                    "components": [comp("Q5", Q5), comp("Q6", Q6),
                                   comp("Q7", Q7), comp("Q8", Q8)],
                    "connections": [
                        wire("sec_a", ("Q5", "S"), ("Q6", "D")),
                        wire("sec_b", ("Q7", "S"), ("Q8", "D")),
                    ],
                },
            },
            {
                "name": "filter", "role": "outputFilter",
                "inputPort":  {"type": "pulsatingDc", "wire": "Vsec_dc"},
                "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
                "circuit": {
                    "components": [comp("C_out", Cout)],
                    "connections": [
                        wire("cap_top", ("C_out", "1"), ("C_out", "2")),
                    ],
                },
            },
            control_stage(U1, ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8"]),
        ],
        "interStageCircuit": [
            ext_port("Vin", "input", ("C_in", "1"), ("Q1", "D")),
            ext_port("Vout", "output", ("C_out", "1"), ("C_out", "2")),
            wire("pri_ac", ("Lk", "2"), ("T1", "P1")),
            wire("sec_ac", ("T1", "S1"), ("Q5", "D")),
            wire("Vsec_dc", ("Q6", "S"), ("C_out", "1")),
        ],
    }


def template_forward(refs: dict, ac_input: bool = False) -> dict:
    """Single-switch forward: [rectifier ->] inverter (Q1) -> isolation (T1) ->
    outputRectifier (D1, D_freewheel) -> outputFilter (L_out, C_out) + control."""
    Q1 = _ref(refs, "switch",       "mosfets", "Q1")
    T1 = _ref(refs, "transformer",  "magnetics", "T1")
    D1 = _ref(refs, "rectifierDiode",   "diodes", "D1")
    Df = _ref(refs, "freewheelDiode",   "diodes", "D_fw")
    Lo = _ref(refs, "outputInductor",   "magnetics", "L_out")
    Cin = _ref(refs, "inputCap",        "capacitors", "C_in")
    Cout = _ref(refs, "outputCap",      "capacitors", "C_out")
    U1 = _ref(refs, "controller",       "controllers", "U1")

    stages = []
    if ac_input:
        BR = _ref(refs, "bridge", "diodes", "BR1")
        stages.append({
            "name": "input_rectifier", "role": "rectifier",
            "inputPort":  {"type": "acLine", "wire": "Vac"},
            "outputPorts": [{"type": "pulsatingDc", "wire": "Vrect"}],
            "circuit": {
                "components": [comp("BR1", BR)],
                "connections": [
                    wire("br_internal", ("BR1", "AC1"), ("BR1", "AC2")),
                ],
            },
        })
    stages.extend([
        {
            "name": "inverter", "role": "inverter",
            "inputPort":  {"type": "dcBus" if not ac_input else "pulsatingDc",
                           "wire": "Vin" if not ac_input else "Vrect"},
            "outputPorts": [{"type": "hfAc", "wire": "sw_node"}],
            "circuit": {
                "components": [comp("Q1", Q1), comp("C_in", Cin)],
                "connections": [
                    wire("drain", ("Q1", "D"), ("C_in", "1")),
                ],
            },
        },
        {
            "name": "transformer", "role": "isolation",
            "inputPort":  {"type": "hfAc", "wire": "sw_node"},
            "outputPorts": [{"type": "hfAc", "wire": "sec"}],
            "circuit": {
                "components": [comp("T1", T1)],
                "connections": [coupling("K_T1", 0.99, "T1", "T1")],
            },
        },
        {
            "name": "rectifier", "role": "outputRectifier",
            "inputPort":  {"type": "hfAc", "wire": "sec"},
            "outputPorts": [{"type": "pulsatingDc", "wire": "rect_out"}],
            "circuit": {
                "components": [comp("D1", D1), comp("D_fw", Df)],
                "connections": [
                    wire("rect_node", ("D1", "K"), ("D_fw", "K")),
                ],
            },
        },
        {
            "name": "filter", "role": "outputFilter",
            "inputPort":  {"type": "pulsatingDc", "wire": "rect_out"},
            "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
            "circuit": {
                "components": [comp("L_out", Lo), comp("C_out", Cout)],
                "connections": [
                    wire("filter_mid", ("L_out", "2"), ("C_out", "1")),
                ],
            },
        },
        control_stage(U1, ["Q1"]),
    ])

    inter = []
    if ac_input:
        inter.append(ext_port("Vac", "input", ("BR1", "AC1"), ("BR1", "AC2")))
        inter.append(wire("Vrect", ("BR1", "DC+"), ("C_in", "1")))
    else:
        inter.append(ext_port("Vin", "input", ("C_in", "2"), ("Q1", "S")))
    inter.extend([
        ext_port("Vout", "output", ("L_out", "2"), ("C_out", "2")),
        wire("sw_node", ("Q1", "D"), ("T1", "P1")),
        wire("sec", ("T1", "S1"), ("D1", "A")),
        wire("rect_out", ("D1", "K"), ("L_out", "1")),
    ])
    return {"stages": stages, "interStageCircuit": inter}


def template_ldo(refs: dict) -> dict:
    """Linear regulator: a single switchingCell containing the LDO IC + caps."""
    U1 = _ref(refs, "regulator",  "controllers", "U_ldo")
    Cin = _ref(refs, "inputCap",  "capacitors", "C_in")
    Cout = _ref(refs, "outputCap", "capacitors", "C_out")
    return {
        "stages": [
            {
                "name": "regulator", "role": "switchingCell",
                "inputPort":  {"type": "dcBus",    "wire": "Vin"},
                "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
                "circuit": {
                    "components": [comp("U1", U1), comp("C_in", Cin),
                                   comp("C_out", Cout)],
                    "connections": [
                        wire("U1_in", ("U1", "IN"), ("C_in", "1")),
                        wire("U1_out", ("U1", "OUT"), ("C_out", "1")),
                    ],
                },
            },
        ],
        "interStageCircuit": [
            ext_port("Vin", "input", ("C_in", "1"), ("U1", "IN")),
            ext_port("Vout", "output", ("C_out", "1"), ("U1", "OUT")),
        ],
    }


def template_module(refs: dict) -> dict:
    """Opaque DC-DC module: a single switchingCell containing one IC."""
    U1 = _ref(refs, "module", "controllers", "U_module")
    return {
        "stages": [
            {
                "name": "module", "role": "switchingCell",
                "inputPort":  {"type": "dcBus",    "wire": "Vin"},
                "outputPorts": [{"type": "dcOutput", "wire": "Vout"}],
                "circuit": {
                    "components": [comp("U1", U1)],
                    "connections": [
                        wire("module_internal", ("U1", "IN"), ("U1", "OUT")),
                    ],
                },
            },
        ],
        "interStageCircuit": [
            ext_port("Vin", "input", ("U1", "IN"), ("U1", "GND")),
            ext_port("Vout", "output", ("U1", "OUT"), ("U1", "GND")),
        ],
    }


# Family-name → (template, supports_ac_input)
TEMPLATES = {
    "buck":      (template_buck, False),
    "syncBuck":  (template_syncBuck, False),
    "boost":     (template_boost, False),
    "flyback":   (template_flyback, True),
    "llc":       (template_llc, True),
    "forward":   (template_forward, True),
    "pfcBoost":  (template_pfcBoost, False),  # PFC always implies AC input
    "pfcLlc":    (template_pfcLlc, False),
    "dab":       (template_dab, False),
    "ldo":       (template_ldo, False),
    "module":    (template_module, False),
}
