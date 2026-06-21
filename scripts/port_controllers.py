#!/usr/bin/env python3
"""Migrate TAS/data/controllers.ndjson (legacy freeform) -> CTAS shape.

Each surviving record becomes ``{"controller": { ... }}`` validating against
``CTAS/schemas/controller.json`` (function.category required; datasheetInfo
nested under manufacturerInfo). The migration is deliberately CONSERVATIVE:

  * it classifies function.category from the freeform ``category`` -> ``type``
    -> ``technology`` signals (no part-number guessing);
  * it maps only UNIT-UNAMBIGUOUS fields (identity, part, intended topology,
    gate-drive / reference / CS-threshold volts, quiescent current, package,
    notes->description);
  * it DROPS fields that are converter-level (``vinRange``) or whose units are
    inconsistent across the catalog (``switchingFrequencyRange`` mixes kHz and
    Hz) rather than injecting wrong-magnitude physics;
  * nothing is silently lost: the full original file is backed up, and every
    record that is not migrated is written to a quarantine file WITH a reason.

Outputs (in TAS/data/):
  controllers.ndjson                       migrated, CTAS-valid, deduped
  controllers.pre-ctas.backup.ndjson       verbatim copy of the original
  controllers.quarantine_nonctrl.ndjson    not a control IC (module/eeprom/LDO/...)
  controllers.quarantine_sparse.ndjson     a controller, but no determinable category
  controllers.quarantine_duplicates.ndjson  (manufacturer, reference) already seen

Run:  python3 TAS/scripts/port_controllers.py [--apply]
Without --apply it does a dry run and only prints the report.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PSMA = REPO.parent
DATA = REPO / "data"
SRC = DATA / "controllers.ndjson"

# ---------------------------------------------------------------------------
# Classification maps (freeform -> CTAS controllerCategory)
# ---------------------------------------------------------------------------
SWITCHING_PWM = "pwmController"

# Freeform `category` -> (controllerCategory, [implied topologies], sync?)
CATEGORY_MAP = {
    "buck-pwm": (SWITCHING_PWM, ["buck"], None),
    "buck": (SWITCHING_PWM, ["buck"], None),
    "buck-controller": (SWITCHING_PWM, ["buck"], None),
    "sync-buck": (SWITCHING_PWM, ["buck"], "builtIn"),
    "boost-pwm": (SWITCHING_PWM, ["boost"], None),
    "boost": (SWITCHING_PWM, ["boost"], None),
    "buck-boost-pwm": (SWITCHING_PWM, [], None),
    "buck-boost": (SWITCHING_PWM, [], None),
    "multi-phase-pwm": ("multiphaseController", ["buck"], "builtIn"),
    "flyback-pwm": (SWITCHING_PWM, ["flyback"], None),
    "integrated-flyback": (SWITCHING_PWM, ["flyback"], None),
    "acf": (SWITCHING_PWM, ["flyback"], None),
    "integrated-gan": (SWITCHING_PWM, [], None),
    "battery-charger": (SWITCHING_PWM, [], None),
    "pfc": ("pfcController", ["pfc"], None),
    "pfc-led": ("pfcController", ["pfc"], None),
    "llc-pfc-combo": ("llcController", ["llc", "pfc"], None),
    "resonant-llc": ("llcController", ["llc"], None),
    "llc": ("llcController", ["llc"], None),
    "synchronous-rectifier": ("syncRectifierController", [], None),
    "half-bridge-gate-driver": ("gateDriver", [], None),
    "isolated-gate-driver": ("gateDriver", [], None),
    "3phase-gate-driver": ("gateDriver", [], None),
    "digital-controller": ("digitalController", [], None),
    "digital-led-driver": ("digitalController", [], None),
    "usb-pd": ("digitalController", [], None),
    "usb-pd-controller": ("digitalController", [], None),
    "power-sequencer": ("supervisor", [], None),
    "ovp-controller": ("supervisor", [], None),
    "shunt-regulator": ("shuntRegulator", [], None),
}

# Freeform `type` -> controllerCategory (used when `category` is absent/unmapped)
TYPE_MAP = {
    "pfc-controller": ("pfcController", ["pfc"], None),
    "sr-controller": ("syncRectifierController", [], None),
    "gate-driver": ("gateDriver", [], None),
}

# `technology` tag -> controllerCategory (last-resort signal)
TECH_MAP = {
    "pwm": (SWITCHING_PWM, [], None),
    "flyback": (SWITCHING_PWM, ["flyback"], None),
    "buck": (SWITCHING_PWM, ["buck"], None),
    "synchronous-buck": (SWITCHING_PWM, ["buck"], "builtIn"),
    "boost": (SWITCHING_PWM, ["boost"], None),
    "llc": ("llcController", ["llc"], None),
    "gate_driver": ("gateDriver", [], None),
    "usb-pd": ("digitalController", [], None),
    "innoswitch3-ep": (SWITCHING_PWM, ["flyback"], None),
}

# Device classes that are NOT control ICs -> quarantine_nonctrl
NONCTRL_CATEGORY = {"power-module", "linear-regulator", "ldo",
                    "energy-harvesting"}
NONCTRL_TYPE = {"power-module", "linear-regulator", "eeprom"}
NONCTRL_TECH = {"ldo"}

# Explicit denylist of motor-driver part-number families that the source catalog
# mislabels with technology=pwm. These are NOT power-supply control ICs (CTAS has
# no motor-driver category), so they are quarantined rather than mis-migrated as
# pwmController. Narrow + explicit (a verified denylist, not a part-number guess);
# none collide with ST's L65xx/L66xx power-supply controllers.
MOTOR_DRIVER_PREFIXES = (
    "L6225", "L6226", "L6227", "L6228", "L6229", "L6234", "L6258",
    "L6460", "L6462", "L6470", "L6472", "L6474", "L6480", "L6482",
)

# Legacy topology token -> PEAS topology enum value (omit unmapped: no guessing)
TOPO_MAP = {
    "flyback": "flybackConverter",
    "buck": "buckConverter",
    "boost": "boostConverter",
    "sepic": "sepicConverter",
    "cuk": "cukConverter",
    "zeta": "zetaConverter",
    "llc": "llcResonantConverter",
    "pfc": "powerFactorCorrection",
    "push-pull": "pushPullConverter",
    "pushpull": "pushPullConverter",
    "dual-active-bridge": "dualActiveBridgeConverter",
    "dab": "dualActiveBridgeConverter",
    "vienna": "viennaRectifierConverter",
}

STATUS_OK = {"production", "prototype", "nrnd", "obsolete", "preview"}
STATUS_REMAP = {"active": "production"}


def norm(x):
    return x.strip().lower() if isinstance(x, str) else x


def classify(rec):
    """-> (bucket, category, implied_topologies, sync). bucket in keep/nonctrl/sparse."""
    cat = norm(rec.get("category"))
    typ = norm(rec.get("type"))
    tech = norm(rec.get("technology"))
    part = rec.get("manufacturerInfo", {})
    if isinstance(part, dict):
        ptech = norm(part.get("datasheetInfo", {}).get("part", {}).get("technology"))
    else:
        ptech = None

    # explicit non-controller device classes first
    if cat in NONCTRL_CATEGORY or typ in NONCTRL_TYPE or tech in NONCTRL_TECH or ptech in NONCTRL_TECH:
        return ("nonctrl", None, [], None)

    # motor drivers mislabeled technology=pwm in the source -> not a CTAS controller
    ref = rec.get("name") or (rec.get("manufacturerInfo") or {}).get("reference") or ""
    if isinstance(ref, str) and ref.upper().startswith(MOTOR_DRIVER_PREFIXES):
        return ("nonctrl", None, [], None)

    for key, table in ((cat, CATEGORY_MAP), (typ, CATEGORY_MAP), (typ, TYPE_MAP),
                       (tech, TECH_MAP), (ptech, TECH_MAP)):
        if key in table:
            c, topos, sync = table[key]
            return ("keep", c, topos, sync)

    return ("sparse", None, [], None)


def topologies_for(rec, implied):
    out = []
    for t in (rec.get("topologies") or []):
        v = TOPO_MAP.get(norm(t))
        if v and v not in out:
            out.append(v)
    if not out:
        for t in implied:
            v = TOPO_MAP.get(t)
            if v and v not in out:
                out.append(v)
    return out


def build_controller(rec, category, implied, sync):
    manu = rec.get("manufacturer") or (rec.get("manufacturerInfo") or {}).get("name")
    legacy_mi = rec.get("manufacturerInfo") or {}
    legacy_part = (legacy_mi.get("datasheetInfo") or {}).get("part") or {}
    reference = legacy_mi.get("reference") or rec.get("name") or legacy_part.get("partNumber")
    if not manu or not reference:
        return None  # cannot form an identity -> caller sparses it

    function = {"category": category}
    topos = topologies_for(rec, implied)
    if topos:
        function["intendedTopologies"] = topos
    if sync:
        function["syncRectification"] = sync

    part = {"deviceType": "controller"}
    pn = legacy_part.get("partNumber") or reference
    if pn:
        part["partNumber"] = pn
    ptech = legacy_part.get("technology")
    if ptech and norm(ptech) not in ("", "unknown"):
        part["technology"] = ptech

    datasheet_info = {"function": function, "part": part}

    electrical = {}
    gd = {}
    v = rec.get("gateDriveVoltage")
    if isinstance(v, (int, float)):
        gd["driveVoltage"] = {"nominal": float(v), "unit": "V"}
    if gd:
        electrical["gateDrive"] = gd
    v = rec.get("currentSenseThresholdVoltage")
    if isinstance(v, (int, float)) and v >= 0:
        electrical["currentMode"] = {"maxThresholdVoltage": float(v)}
    v = rec.get("feedbackReferenceVoltage")
    if isinstance(v, (int, float)):
        electrical["referenceVoltage"] = {"nominal": float(v), "unit": "V"}
    v = rec.get("quiescentCurrent")
    if isinstance(v, (int, float)) and v >= 0:
        electrical["quiescentCurrent"] = float(v)
    if electrical:
        datasheet_info["electrical"] = electrical

    pkg = rec.get("package")
    if not pkg and isinstance(rec.get("packages"), list) and rec["packages"]:
        pkg = rec["packages"][0]
    if isinstance(pkg, str) and pkg:
        datasheet_info["mechanical"] = {"packageType": pkg}

    mi = {"name": manu, "reference": reference, "datasheetInfo": datasheet_info}
    status = STATUS_REMAP.get(norm(legacy_mi.get("status")), legacy_mi.get("status"))
    if status in STATUS_OK:
        mi["status"] = status
    url = legacy_mi.get("datasheetUrl") or rec.get("datasheetUrl")
    if isinstance(url, str) and url:
        mi["datasheetUrl"] = url
    desc = rec.get("notes") or rec.get("description")
    if isinstance(desc, str) and desc:
        mi["description"] = desc

    return {"controller": {"manufacturerInfo": mi}}


def main():
    apply = "--apply" in sys.argv
    # Read from the immutable backup once it exists, so re-runs are idempotent
    # (otherwise a second --apply would read the already-migrated file).
    backup = DATA / "controllers.pre-ctas.backup.ndjson"
    source = backup if backup.exists() else SRC
    records = []
    for ln, line in enumerate(source.open(), 1):
        line = line.strip()
        if not line:
            continue
        records.append((ln, json.loads(line)))

    migrated, nonctrl, sparse, dups = [], [], [], []
    seen = {}
    dropped_fields = {"vinRange": 0, "switchingFrequencyRange": 0,
                      "features": 0, "vccBypassCapacitance": 0, "softStartCurrent": 0}
    cat_counts = {}

    for ln, rec in records:
        for f in dropped_fields:
            if f in rec:
                dropped_fields[f] += 1
        bucket, category, implied, sync = classify(rec)
        if bucket == "nonctrl":
            nonctrl.append({**rec, "quarantineReason": "not a control IC (CTAS out of scope)"})
            continue
        if bucket == "sparse":
            sparse.append({**rec, "quarantineReason": "no determinable controller category"})
            continue
        out = build_controller(rec, category, implied, sync)
        if out is None:
            sparse.append({**rec, "quarantineReason": "missing manufacturer/reference identity"})
            continue
        mi = out["controller"]["manufacturerInfo"]
        key = (mi["name"], mi["reference"])
        if key in seen:
            dups.append({**rec, "quarantineReason": f"duplicate (manufacturer, reference) of line {seen[key]}"})
            continue
        seen[key] = ln
        cat_counts[category] = cat_counts.get(category, 0) + 1
        migrated.append(out)

    # ---- report ----
    print(f"source records          : {len(records)}")
    print(f"migrated (CTAS-valid)   : {len(migrated)}")
    print(f"  by category           : " + ", ".join(f"{k}={v}" for k, v in sorted(cat_counts.items())))
    print(f"quarantine nonctrl      : {len(nonctrl)}")
    print(f"quarantine sparse       : {len(sparse)}")
    print(f"quarantine duplicates   : {len(dups)}")
    print(f"dropped (non-CTAS) field occurrences: {dropped_fields}")

    if not apply:
        print("\n(dry run — pass --apply to write files)")
        return

    def write(path, rows):
        with path.open("w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    if not backup.exists():
        write(backup, [r for _, r in records])
    write(SRC, migrated)
    write(DATA / "controllers.quarantine_nonctrl.ndjson", nonctrl)
    write(DATA / "controllers.quarantine_sparse.ndjson", sparse)
    write(DATA / "controllers.quarantine_duplicates.ndjson", dups)
    print("\nwritten.")


if __name__ == "__main__":
    main()
