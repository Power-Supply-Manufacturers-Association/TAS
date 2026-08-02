#!/usr/bin/env python3
# Standards-pinout EXPANSION tables for the connector contactSystem join
# (2026-08 fill campaign, round 2). EXTENDS scripts/standards_pinouts.py — same
# gate discipline, same table/return contract, same PEAS pinFunction vocabulary.
#
# APPLY MECHANISM (identical to standards_pinouts.py):
#   The lead calls  match(family, interface_standard, positions, coding, description_lc)
#   -> (table, gate) | None,  exactly as scripts/fill_connector_pinout_geometry.py
#   already calls standards_pinouts.match (see its try_pinout). `table` is a dict
#   {"cite": <standard citation>, "contacts": [(id, pinName, signalRole), ...]}.
#   Chain this AFTER standards_pinouts.match (only parts still lacking contactSystem
#   reach here; nothing here overlaps the families standards_pinouts already covers).
#   Provenance the lead writes: source='manual',
#   sourceName=f"Standards pinout join: {table['cite']}".
#
# HARD RULES (unchanged): assert a pinout ONLY where the pin ROLES are fixed by a
# public standard, gated on family (+ the interfaceStandard string and/or the
# description discriminator) and an EXACT positions match; NEVER on parts naming
# integrated LEDs or magnetics. A bare form factor is NOT a protocol — D-Sub,
# DIN 41612, mini-DIN, generic board-to-board stay ABSENT. A wrong role is worse
# than no role: where the standard does not fix the per-pin role (HSIO cable /
# backplane PCIe, stacked/ganged SFP/QSFP cages, SAS SFF-8639 variants, USB4 on a
# non-24-pin body, ix Industrial Type B) this module returns None.
#
# Roles use the PEAS pinFunction vocabulary (https://psma.com/peas/utils.json).

P = "differentialP"
N = "differentialN"
G = "ground"
PWR = "power"
S = "signal"
SH = "shield"
NC = "noConnect"

# ---------------------------------------------------------------------------
# 1) PCI Express CEM card-edge connector (x1 / x4 / x8 / x16)
#    Cite: PCI Express Card Electromechanical (CEM) Specification, connector
#    pinout. The x16 (164-pin) map is canonical; the shorter widths are the
#    front portion of the SAME map (x1=pins 1..18/side, x4=1..32, x8=1..49,
#    x16=1..82) — every width ends with its PRSNT2# pair, which the slice
#    reproduces exactly (B17 / B31 / B48 / B81). So the positions FIELD alone
#    selects the width; noisy marketing text ("x16 ... 98 Positions") is ignored.
#
#    Side B (pins B1..B82) and Side A (pins A1..A82). Transmitter (system->card)
#    lanes are HSOp/HSOn = PETp/PETn; receiver lanes HSIp/HSIn = PERp/PERn.
_CEM_B = {
    1: ("+12V", PWR), 2: ("+12V", PWR), 3: ("+12V", PWR), 4: ("GND", G),
    5: ("SMCLK", S), 6: ("SMDAT", S), 7: ("GND", G), 8: ("+3.3V", PWR),
    9: ("JTAG_TRST#", S), 10: ("+3.3Vaux", PWR), 11: ("WAKE#", S), 12: ("RSVD", NC),
    13: ("GND", G), 14: ("PETp0", P), 15: ("PETn0", N), 16: ("GND", G),
    17: ("PRSNT2#", S), 18: ("GND", G), 19: ("PETp1", P), 20: ("PETn1", N),
    21: ("GND", G), 22: ("GND", G), 23: ("PETp2", P), 24: ("PETn2", N),
    25: ("GND", G), 26: ("GND", G), 27: ("PETp3", P), 28: ("PETn3", N),
    29: ("GND", G), 30: ("RSVD", NC), 31: ("PRSNT2#", S), 32: ("GND", G),
    33: ("PETp4", P), 34: ("PETn4", N), 35: ("GND", G), 36: ("GND", G),
    37: ("PETp5", P), 38: ("PETn5", N), 39: ("GND", G), 40: ("GND", G),
    41: ("PETp6", P), 42: ("PETn6", N), 43: ("GND", G), 44: ("GND", G),
    45: ("PETp7", P), 46: ("PETn7", N), 47: ("GND", G), 48: ("PRSNT2#", S),
    49: ("GND", G), 50: ("PETp8", P), 51: ("PETn8", N), 52: ("GND", G),
    53: ("GND", G), 54: ("PETp9", P), 55: ("PETn9", N), 56: ("GND", G),
    57: ("GND", G), 58: ("PETp10", P), 59: ("PETn10", N), 60: ("GND", G),
    61: ("GND", G), 62: ("PETp11", P), 63: ("PETn11", N), 64: ("GND", G),
    65: ("GND", G), 66: ("PETp12", P), 67: ("PETn12", N), 68: ("GND", G),
    69: ("GND", G), 70: ("PETp13", P), 71: ("PETn13", N), 72: ("GND", G),
    73: ("GND", G), 74: ("PETp14", P), 75: ("PETn14", N), 76: ("GND", G),
    77: ("GND", G), 78: ("PETp15", P), 79: ("PETn15", N), 80: ("GND", G),
    81: ("PRSNT2#", S), 82: ("RSVD#2", NC),
}
_CEM_A = {
    1: ("PRSNT1#", S), 2: ("+12V", PWR), 3: ("+12V", PWR), 4: ("GND", G),
    5: ("JTAG_TCK", S), 6: ("JTAG_TDI", S), 7: ("JTAG_TDO", S), 8: ("JTAG_TMS", S),
    9: ("+3.3V", PWR), 10: ("+3.3V", PWR), 11: ("PERST#", S), 12: ("GND", G),
    13: ("REFCLK+", P), 14: ("REFCLK-", N), 15: ("GND", G), 16: ("PERp0", P),
    17: ("PERn0", N), 18: ("GND", G), 19: ("RSVD", NC), 20: ("GND", G),
    21: ("PERp1", P), 22: ("PERn1", N), 23: ("GND", G), 24: ("GND", G),
    25: ("PERp2", P), 26: ("PERn2", N), 27: ("GND", G), 28: ("GND", G),
    29: ("PERp3", P), 30: ("PERn3", N), 31: ("GND", G), 32: ("RSVD", NC),
    33: ("RSVD", NC), 34: ("GND", G), 35: ("PERp4", P), 36: ("PERn4", N),
    37: ("GND", G), 38: ("GND", G), 39: ("PERp5", P), 40: ("PERn5", N),
    41: ("GND", G), 42: ("GND", G), 43: ("PERp6", P), 44: ("PERn6", N),
    45: ("GND", G), 46: ("GND", G), 47: ("PERp7", P), 48: ("PERn7", N),
    49: ("GND", G), 50: ("RSVD", NC), 51: ("GND", G), 52: ("PERp8", P),
    53: ("PERn8", N), 54: ("GND", G), 55: ("GND", G), 56: ("PERp9", P),
    57: ("PERn9", N), 58: ("GND", G), 59: ("GND", G), 60: ("PERp10", P),
    61: ("PERn10", N), 62: ("GND", G), 63: ("GND", G), 64: ("PERp11", P),
    65: ("PERn11", N), 66: ("GND", G), 67: ("GND", G), 68: ("PERp12", P),
    69: ("PERn12", N), 70: ("GND", G), 71: ("GND", G), 72: ("PERp13", P),
    73: ("PERn13", N), 74: ("GND", G), 75: ("GND", G), 76: ("PERp14", P),
    77: ("PERn14", N), 78: ("GND", G), 79: ("GND", G), 80: ("PERp15", P),
    81: ("PERn15", N), 82: ("GND", G),
}
# positions -> (lane label, per-side pin count)
_CEM_WIDTH = {36: ("x1", 18), 64: ("x4", 32), 98: ("x8", 49), 164: ("x16", 82)}


def _cem_table(positions):
    lane, k = _CEM_WIDTH[positions]
    contacts = []
    for n in range(1, k + 1):
        nameA, roleA = _CEM_A[n]
        contacts.append((f"A{n}", nameA, roleA))
        nameB, roleB = _CEM_B[n]
        contacts.append((f"B{n}", nameB, roleB))
    return {
        "cite": (f"PCI Express Card Electromechanical (CEM) Specification, "
                 f"{lane} card-edge connector pinout ({positions} positions)"),
        "contacts": contacts,
    }


# ---------------------------------------------------------------------------
# 2) VGA HD-15 (DE-15 high-density) — RGBHV + DDC2/E-DDC. Fixed by the
#    IBM VGA / VESA DDC de-facto standard. Only a 15-position part whose
#    description explicitly says VGA (an HD-15 is the sole VGA D-sub; a plain
#    DA-15 game/other port is excluded by requiring the 'vga' token).
VGA_HD15 = {
    "cite": "VGA HD-15 (DE-15) connector pinout, VESA DDC2 (RGBHV + DDC)",
    "contacts": [
        ("1", "RED", S), ("2", "GREEN", S), ("3", "BLUE", S), ("4", "ID2/RES", NC),
        ("5", "GND", G), ("6", "RED_RTN", G), ("7", "GREEN_RTN", G), ("8", "BLUE_RTN", G),
        ("9", "+5V", PWR), ("10", "GND", G), ("11", "ID0/RES", NC), ("12", "DDC_SDA", S),
        ("13", "HSYNC", S), ("14", "VSYNC", S), ("15", "DDC_SCL", S),
    ],
}

# ---------------------------------------------------------------------------
# 3) SFP / SFP+ single-port host edge connector (20 contacts) — SFF-8431/8432.
SFP_SFF8431 = {
    "cite": "SFF-8431 / SFF-8432 SFP/SFP+ host edge connector, 20-contact",
    "contacts": [
        ("1", "VeeT", G), ("2", "TX_Fault", S), ("3", "TX_Disable", S), ("4", "SDA", S),
        ("5", "SCL", S), ("6", "MOD_ABS", S), ("7", "RS0", S), ("8", "RX_LOS", S),
        ("9", "RS1", S), ("10", "VeeR", G), ("11", "VeeR", G), ("12", "RD-", N),
        ("13", "RD+", P), ("14", "VeeR", G), ("15", "VccR", PWR), ("16", "VccT", PWR),
        ("17", "VeeT", G), ("18", "TD+", P), ("19", "TD-", N), ("20", "VeeT", G),
    ],
}

# ---------------------------------------------------------------------------
# 4) microSD card socket (8 contacts) — SD Physical Layer Specification
#    (SD-bus default). Contacts are numbered 1..8 to match the card pads.
MICROSD = {
    "cite": "SD Physical Layer Specification, microSD 8-contact assignment (SD bus)",
    "contacts": [
        ("1", "DAT2", S), ("2", "CD/DAT3", S), ("3", "CMD", S), ("4", "VDD", PWR),
        ("5", "CLK", S), ("6", "VSS", G), ("7", "DAT0", S), ("8", "DAT1", S),
    ],
}


def match(family, interface_standard, positions, coding, description_lc):
    """Return (table, gate_name) or None. Same contract as
    standards_pinouts.match. EXACT positions gate; LED/magnetics refuse; no
    guessing. `description_lc` must be lower-cased (part.description +
    manufacturerInfo.description), as fill_connector_pinout_geometry builds it."""
    d = description_lc or ""
    if "led" in d or "magjack" in d or "magnetic" in d or "magnet" in d:
        return None
    ifs = (interface_standard or "")
    ifsl = ifs.lower()

    # 1) PCIe CEM card edge — cardEdge family (already an edge-card connector),
    #    description names PCI Express, positions field is an EXACT CEM width.
    #    (interfaceStandard is empty on these; description carries the protocol.)
    #    The exact widths 36/64/98/164 at 1.0 mm pitch are UNIQUELY the PCIe CEM
    #    slot — verified 0/198 such parts carry a competing edge-standard token
    #    (SFF-TA-1002/Gen-Z/EDSFF/U.2/U.3/NVMe/SAS/OcuLink live at other pin
    #    counts). So an explicit "card edge" word is NOT required — the family +
    #    pcie token + exact CEM width is a certain discriminator. Refuse any
    #    competing-standard token defensively in case future data mixes them in.
    if family == "cardEdge" and positions in _CEM_WIDTH:
        if ("pci express" in d or "pcie" in d) and not any(
            t in d for t in ("sff-ta", "sff ta", "gen-z", "genz", "gen z", "edsff",
                             "u.2", "u.3", "nvme", "8639", "8643", "oculink",
                             "mini-sas", "mini sas", "minisas")):
            lane, _ = _CEM_WIDTH[positions]
            return _cem_table(positions), f"pcie-cem-{lane}"

    if family != "dataInterface":
        return None

    # 2) VGA HD-15
    if positions == 15 and "vga" in d:
        return VGA_HD15, "vga-hd15"

    # 3) SFP / SFP+ single-port host edge (20 contacts)
    if ifs in ("SFP", "SFP+") and positions == 20:
        return SFP_SFF8431, "sfp-sff8431"

    # 4) microSD card socket (8 contacts)
    if "microsd" in ifsl and positions == 8:
        return MICROSD, "microsd"

    return None
