#!/usr/bin/env python3
# Standards pinout tables for the connector contactSystem join (2026-08 fill campaign).
#
# HARD RULES (from the connector-EMC research round, Kelvin/docs/CONNECTOR_*):
#   * Assert a pinout ONLY where the pin ROLES are fixed by a public standard,
#     gated on family (+ coding) and an EXACT positions match, and never on parts
#     whose description names integrated LEDs or magnetics (extra pins).
#   * A bare form factor is NOT a protocol: D-Sub (9,510 parts) is deliberately
#     ABSENT — a DE-9 may carry RS-232, CAN, or anything; the harness fixes the
#     pinout, not the connector. Same for pin headers / board-to-board (the
#     designer chooses the pinout) and for ix Industrial / PCIe / SFP (v1 skips
#     what we cannot transcribe with certainty).
#   * Every table cites the standard that fixes it. Provenance on the record is
#     source='manual' + sourceName=<the citation> — this is a documented fact,
#     not a derivation.
#
# Roles use the PEAS pinFunction vocabulary (https://psma.com/peas/utils.json).

# (id, pinName, signalRole)
P = "differentialP"
N = "differentialN"
G = "ground"
PWR = "power"
S = "signal"
SH = "shield"

COAX = {
    "cite": "Coaxial interface: centre conductor + screen (IEC 61169-1 family)",
    "contacts": [("1", "center", S), ("SHELL", "body", SH)],
}

USB2_A = {
    "cite": "USB 2.0 Specification, Table 6-1 (Series A/B connector contact assignment)",
    "contacts": [("1", "VBUS", PWR), ("2", "D-", N), ("3", "D+", P), ("4", "GND", G),
                 ("SHELL", "shield", SH)],
}

USB_C = {
    "cite": "USB Type-C Cable and Connector Specification, Table 3-1 (receptacle interface)",
    "contacts": [
        ("A1", "GND", G), ("A2", "SSTXp1", P), ("A3", "SSTXn1", N), ("A4", "VBUS", PWR),
        ("A5", "CC1", S), ("A6", "Dp1", P), ("A7", "Dn1", N), ("A8", "SBU1", S),
        ("A9", "VBUS", PWR), ("A10", "SSRXn2", N), ("A11", "SSRXp2", P), ("A12", "GND", G),
        ("B1", "GND", G), ("B2", "SSTXp2", P), ("B3", "SSTXn2", N), ("B4", "VBUS", PWR),
        ("B5", "CC2", S), ("B6", "Dp2", P), ("B7", "Dn2", N), ("B8", "SBU2", S),
        ("B9", "VBUS", PWR), ("B10", "SSRXn1", N), ("B11", "SSRXp1", P), ("B12", "GND", G),
        ("SHELL", "shield", SH),
    ],
}

# Pair grouping (1,2)(3,6)(4,5)(7,8) per ANSI/TIA-568; in-pair polarity per
# IEEE 802.3 clause 40.8 MDI contact assignments (BI_DA+..BI_DD-).
RJ45 = {
    "cite": "ANSI/TIA-568 8P8C pair assignment; IEEE 802.3 cl. 40.8 MDI contact assignments",
    "contacts": [("1", "BI_DA+", P), ("2", "BI_DA-", N), ("3", "BI_DB+", P),
                 ("4", "BI_DC+", P), ("5", "BI_DC-", N), ("6", "BI_DB-", N),
                 ("7", "BI_DD+", P), ("8", "BI_DD-", N)],
}

HDMI_A = {
    "cite": "HDMI Specification 1.4, Type A receptacle pin assignment",
    "contacts": [
        ("1", "TMDS_D2+", P), ("2", "TMDS_D2_SH", G), ("3", "TMDS_D2-", N),
        ("4", "TMDS_D1+", P), ("5", "TMDS_D1_SH", G), ("6", "TMDS_D1-", N),
        ("7", "TMDS_D0+", P), ("8", "TMDS_D0_SH", G), ("9", "TMDS_D0-", N),
        ("10", "TMDS_CLK+", P), ("11", "TMDS_CLK_SH", G), ("12", "TMDS_CLK-", N),
        ("13", "CEC", S), ("14", "Utility/HEAC+", S), ("15", "SCL", S), ("16", "SDA", S),
        ("17", "DDC/CEC_GND", G), ("18", "+5V", PWR), ("19", "HPD", S),
        ("SHELL", "shield", SH),
    ],
}

DISPLAYPORT = {
    "cite": "VESA DisplayPort Standard, external connector pin assignment",
    "contacts": [
        ("1", "ML0+", P), ("2", "GND", G), ("3", "ML0-", N),
        ("4", "ML1+", P), ("5", "GND", G), ("6", "ML1-", N),
        ("7", "ML2+", P), ("8", "GND", G), ("9", "ML2-", N),
        ("10", "ML3+", P), ("11", "GND", G), ("12", "ML3-", N),
        ("13", "CONFIG1", S), ("14", "CONFIG2", S),
        ("15", "AUX+", P), ("16", "GND", G), ("17", "AUX-", N),
        ("18", "HPD", S), ("19", "RETURN", G), ("20", "DP_PWR", PWR),
        ("SHELL", "shield", SH),
    ],
}

SATA_DATA = {
    "cite": "Serial ATA revision 3.x, 7-pin signal segment (S1-S7)",
    "contacts": [("S1", "GND", G), ("S2", "A+", P), ("S3", "A-", N), ("S4", "GND", G),
                 ("S5", "B-", N), ("S6", "B+", P), ("S7", "GND", G)],
}

M12_D4 = {
    "cite": "IEC 61076-2-101 (M12 D-coding); IEC 61918 / IEEE 802.3 100BASE-TX assignment",
    "contacts": [("1", "TX+", P), ("2", "RX+", P), ("3", "TX-", N), ("4", "RX-", N)],
}

M12_X8 = {
    "cite": "IEC 61076-2-109 (M12 X-coding), pairs (1,2)(3,4)(5,6)(7,8)",
    "contacts": [("1", "DA+", P), ("2", "DA-", N), ("3", "DB+", P), ("4", "DB-", N),
                 ("5", "DC+", P), ("6", "DC-", N), ("7", "DD+", P), ("8", "DD-", N)],
}

TRS_35 = {
    "cite": "3.5 mm TRS phone connector: sleeve = common/ground (tip/ring = channels)",
    "contacts": [("T", "TIP(L)", S), ("R", "RING(R)", S), ("S", "SLEEVE", G)],
}

IEC60320_3 = {
    "cite": "IEC 60320-1 appliance coupler: line, neutral, protective earth",
    "contacts": [("L", "L", PWR), ("N", "N", PWR), ("PE", "PE", G)],
}

IEC60320_2 = {
    "cite": "IEC 60320-1 class II appliance coupler (C7/C8): line, neutral",
    "contacts": [("L", "L", PWR), ("N", "N", PWR)],
}


def match(family, interface_standard, positions, coding, description_lc):
    """Return (table, gate_name) or None. NEVER guesses: exact positions gate,
    LED/magnetics tokens refuse. Order matters only for readability."""
    if "led" in description_lc or "magjack" in description_lc or "magnetic" in description_lc:
        return None
    ifs = (interface_standard or "").upper().replace(" ", "")
    if family == "rf" and positions in (None, 1):
        return COAX, "rf-coax"
    if family == "acInlet":
        if positions == 3:
            return IEC60320_3, "iec60320-3"
        if positions == 2:
            return IEC60320_2, "iec60320-2"
        return None
    if family == "circular":
        if coding == "D" and positions == 4:
            return M12_D4, "m12-D"
        if coding == "X" and positions == 8:
            return M12_X8, "m12-X"
        return None
    if family != "dataInterface":
        return None
    if "FAKRA" in ifs and positions in (None, 1):
        return COAX, "fakra-coax"
    if ("TYPEC" in ifs or "TYPE-C" in ifs or ifs in ("USB-C", "USBC")) and positions == 24:
        return USB_C, "usb-c"
    if ifs in ("RJ45", "ETHERNET") and positions == 8:
        return RJ45, "rj45"
    if "HDMI" in ifs and positions == 19:
        return HDMI_A, "hdmi-a"
    if "DISPLAYPORT" in ifs and positions == 20:
        return DISPLAYPORT, "displayport"
    if "SATA" in ifs and positions == 7:
        return SATA_DATA, "sata-data"
    if ifs in ("USB2.0", "USBUSB2.0", "USB-A2.0", "USBA", "USB-A", "USBB", "USB-B") and positions == 4:
        return USB2_A, "usb2"
    if ifs == "3.5MMAUDIOJACK" and positions == 3:
        return TRS_35, "trs-3.5"
    return None
