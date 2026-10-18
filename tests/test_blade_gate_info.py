"""BladeGate tallies OK-severity notes as info, not as suspicious findings.

Blade Runner has no INFO severity; MOS_AMB_PD_UNTIMED is emitted as Severity::Ok. The gate
summary used to list every non-IMPOSSIBLE finding under "suspicious (not blocking)".
"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from blade_gate import BladeGate  # noqa: E402


class FakeTV:
    def __init__(self, findings):
        self.findings = findings

    def validate(self, rec):
        return NS(findings=self.findings)


def gate_with(findings):
    g = BladeGate.__new__(BladeGate)
    import collections
    g.impossible, g.suspicious, g.info = (collections.Counter() for _ in range(3))
    g.checked = g.blocked = 0
    g.disc = ["semiconductor", "mosfet"]
    g.tv = FakeTV(findings)
    return g


def test_ok_severity_is_info_not_suspicious():
    g = gate_with([NS(code="MOS_AMB_PD_UNTIMED", severity="Severity.OK", message=""),
                   NS(code="MOS_VTH_WINDOW", severity="Severity.SUSPICIOUS", message="")])
    ok, _ = g.check({})
    assert ok
    assert g.suspicious == {"MOS_VTH_WINDOW": 1}
    assert g.info == {"MOS_AMB_PD_UNTIMED": 1}
    s = g.summary()
    assert "suspicious (not blocking): MOS_VTH_WINDOWx1" in s
    assert "info: MOS_AMB_PD_UNTIMEDx1" in s


def test_impossible_still_blocks():
    g = gate_with([NS(code="X", severity="Severity.IMPOSSIBLE", message="m")])
    ok, why = g.check({})
    assert not ok and why.startswith("X")
