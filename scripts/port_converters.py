"""Port TAS data/converters.ndjson v1 records to TAS v2 docs.

Each input record (~238) is transformed into a TAS v2 document validating
against schemas/TAS.json:

    {
      "inputs":   {"designRequirements": {...}, "operatingPoints": [...]},
      "topology": {"stages": [...], "interStageCircuit": [...]}
    }

Strategy
--------

1. Dedupe by `id`, keeping the latest by `createdAt`.

2. Classify the converter family from `tas.inputs.name` / `tas.inputs.type`
   using ordered keyword regex (most-specific first). Anything unclassifiable
   is quarantined.

3. Build `inputs.designRequirements` from `tas.inputs.{efficiencyTarget,
   inputVoltage, outputVoltage, switchingFrequency, isolationVoltage}`.

   Strict per project policy ("no fallbacks, no defaults, no silent shortcuts"):

   * Missing `efficiencyTarget` -> quarantine ("missing-required: efficiency").
   * AC input without `lineFrequency` -> quarantine ("missing-required:
     lineFrequency").
   * Missing `inputVoltage` / `outputVoltage` / output power-or-current
     -> quarantine.

4. Build a single nominal `operatingPoint` from designRequirements:
   `{name: "nominal", inputVoltage: nominal_in, ambientTemperature: 25,
     outputs: [{name, power|current}]}`.

5. Instantiate the family `topology` from `scripts/topology_templates.py`.
   Component refs are URI strings:

   * If the v1 record has a matching `tas.topology.components[]` entry with
     a `manufacturerInfo.reference` partNumber, the URI is
     `TAS/data/<file>.ndjson?partNumber=<pn>`.
   * Otherwise a placeholder URI is used:
     `TAS/data/<file>.ndjson?placeholder=<rolename>`.

6. Validate the assembled doc against TAS.json (with a stub for the external
   PEAS schema, exactly like tests/test_schemas.py). Failures go to
   `data/quarantine.ndjson` with a `quarantineReason`.

The original `data/converters.ndjson` is moved to
`data/converters.v1.backup.ndjson` (if not already present) and replaced
atomically with the v2 ndjson.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

# Reuse the part-libraries porter helpers we already validated.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from port_part_libraries import quarantine_line  # noqa: E402

import topology_templates as TPL  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
SCHEMA_DIR = REPO / "schemas"
SCHEMAS = ["TAS", "inputs", "topology", "outputs", "circuit", "utils"]
SRC = DATA / "converters.ndjson"
BACKUP = DATA / "converters.v1.backup.ndjson"
OUT = DATA / "converters.v2.ndjson"


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def build_validator() -> Draft202012Validator:
    schemas = {}
    for name in SCHEMAS:
        s = json.loads((SCHEMA_DIR / f"{name}.json").read_text())
        schemas[s["$id"]] = s
    resources = [
        (sid, Resource(contents=s, specification=DRAFT202012))
        for sid, s in schemas.items()
    ]
    # Stub the external PEAS schema (same trick as tests/test_schemas.py).
    resources.append((
        "http://openconverters.com/schemas/PEAS/peas.json",
        Resource(contents={"type": "object"}, specification=DRAFT202012),
    ))
    registry = Registry().with_resources(resources)
    return Draft202012Validator(
        schemas["http://openconverters.com/schemas/TAS/TAS.json"],
        registry=registry,
    )


# ---------------------------------------------------------------------------
# Family classification
# ---------------------------------------------------------------------------

# Order matters: most specific first. (regex_pattern, family).
# All matched against `name + ' || ' + type` (case-insensitive).
FAMILY_RULES = [
    # Two-stage AC-DC: PFC + LLC (or PFC + Flyback degenerates to PFC+iso below).
    (r"\bpfc\b.{0,12}\bllc\b|\bllc\b.{0,12}\bpfc\b", "pfcLlc"),
    (r"\bpfc\b.{0,15}flyback|flyback.{0,15}\bpfc\b", "pfcLlc"),  # treat as 2-stage
    # Dual Active Bridge (isolated bidirectional).
    (r"\bdab\b|dual active bridge|bidirectional.*\bdab\b", "dab"),
    # PFC alone.
    (r"\bpfc\b|power factor", "pfcBoost"),
    # LLC resonant.
    (r"\bllc\b|llc resonant|resonant\b.*\bllc\b|interleaved llc", "llc"),
    # Forward (single-switch / HBCT / sync forward / ACF).
    (r"\bforward\b|\bhbct\b|active clamp forward|\bacf\b", "forward"),
    # Flyback (incl. QR, GaN flyback, PSR flyback, DCM/CCM flyback).
    (r"flyback|quasi-resonant|\bqr\b\s+flyback|\bqr\b.{0,20}gan|\bpsr\b", "flyback"),
    # Synchronous buck variants (incl. interleaved sync buck).
    (r"sync(\w*)?\s*buck|synchronous\s+buck|interleaved\s+sync\s+buck", "syncBuck"),
    # Generic buck / buck IC / battery charger buck etc.
    (r"\bbuck\b(?!\-?boost)", "buck"),
    # Boost.
    (r"\bboost\b(?!\s*pfc)", "boost"),
    # LDO.
    (r"\bldo\b|linear regulator", "ldo"),
    # Generic adapter (90-264Vac → ...) without a more specific family. Most
    # of these are AC-DC flybacks; classify as flyback.
    (r"\badapter\b", "flyback"),
    # Opaque DC-DC modules.
    (r"dc-dc module|\bmodule\b", "module"),
    # CrM LED driver - treat as flyback (single-stage isolated AC-DC).
    (r"led driver", "flyback"),
    # Isolated buck-boost - treat as flyback (single-switch isolated).
    (r"isolated\s+buck.?boost", "flyback"),
]


def classify(record: dict) -> str | None:
    ti = record.get("tas", {}).get("inputs", {}) or {}
    name = (ti.get("name") or "").lower()
    typ = (ti.get("type") or "").lower()
    topo_field = (ti.get("topology") or "").lower()
    blob = f"{name} || {typ} || {topo_field}"
    for pat, fam in FAMILY_RULES:
        if re.search(pat, blob):
            return fam
    return None


# ---------------------------------------------------------------------------
# AC input detection
# ---------------------------------------------------------------------------

def is_ac_input(record: dict) -> bool:
    ti = record.get("tas", {}).get("inputs", {}) or {}
    name = (ti.get("name") or "").lower()
    if "vac" in name or "ac-dc" in name or "ac/dc" in name:
        return True
    if re.search(r"\d+\s*-\s*\d+\s*v\s*ac", name):
        return True
    iv = ti.get("inputVoltage") or {}
    if isinstance(iv, dict):
        if str(iv.get("type", "")).lower() in ("ac", "acsinglephase"):
            return True
        # Heuristic: Vin range up to >=200V suggests universal AC mains.
        mx = iv.get("maximum")
        if isinstance(mx, (int, float)) and mx >= 200:
            return True
    return False


# ---------------------------------------------------------------------------
# Inputs builder
# ---------------------------------------------------------------------------

def _coerce_voltage(v) -> dict | None:
    """v1 voltage may be {nominal,minimum,maximum} dict or scalar; return
    {nominal,...} dict or None."""
    if isinstance(v, dict):
        out = {k: v[k] for k in ("nominal", "minimum", "maximum") if k in v}
        return out or None
    if isinstance(v, (int, float)):
        return {"nominal": float(v)}
    return None


def _coerce_freq(v) -> dict | None:
    return _coerce_voltage(v)


def build_inputs(record: dict, ac: bool, family: str) -> dict:
    """Build {designRequirements, operatingPoints}, or raise ValueError with a
    quarantine reason."""
    ti = record.get("tas", {}).get("inputs", {}) or {}

    eff = ti.get("efficiencyTarget")
    if not isinstance(eff, (int, float)):
        raise ValueError("missing-required: efficiency (no efficiencyTarget)")
    if not (0 < float(eff) <= 1):
        raise ValueError(f"out-of-range: efficiency={eff}")

    iv = _coerce_voltage(ti.get("inputVoltage"))
    if not iv or "nominal" not in iv:
        raise ValueError("missing-required: inputVoltage.nominal")

    ov = _coerce_voltage(ti.get("outputVoltage"))
    if not ov or "nominal" not in ov:
        raise ValueError("missing-required: outputVoltage.nominal")

    out_power = ti.get("outputPower")
    out_curr = ti.get("outputCurrent")
    op_p = op_p_dict = None
    if isinstance(out_power, dict) and "nominal" in out_power:
        op_p = float(out_power["nominal"])
    elif isinstance(out_power, (int, float)):
        op_p = float(out_power)
    op_c = None
    if isinstance(out_curr, dict) and "nominal" in out_curr:
        op_c = float(out_curr["nominal"])
    elif isinstance(out_curr, (int, float)):
        op_c = float(out_curr)
    if op_p is None and op_c is None:
        raise ValueError("missing-required: outputPower or outputCurrent")

    dr: dict = {
        "efficiency": float(eff),
        "inputType": "acSinglePhase" if ac else "dc",
        "inputVoltage": iv,
        "outputs": [{
            "name": "out1",
            "voltage": ov,
            "regulation": "voltage",
        }],
    }

    if ac:
        lf = _coerce_freq(ti.get("lineFrequency"))
        if not lf:
            raise ValueError("missing-required: lineFrequency (AC input)")
        dr["lineFrequency"] = lf

    sf = _coerce_freq(ti.get("switchingFrequency"))
    if sf:
        dr["switchingFrequency"] = sf

    iso = ti.get("isolationVoltage")
    if isinstance(iso, (int, float)) and iso > 0:
        dr["isolationVoltage"] = float(iso)
    elif isinstance(iso, dict) and isinstance(iso.get("nominal"), (int, float)) \
            and iso["nominal"] > 0:
        dr["isolationVoltage"] = float(iso["nominal"])

    # One nominal operating point.
    op_out = {"name": "out1"}
    if op_p is not None:
        op_out["power"] = op_p
    else:
        op_out["current"] = op_c
    op = {
        "name": "nominal",
        "inputVoltage": float(iv["nominal"]),
        "ambientTemperature": 25.0,
        "outputs": [op_out],
    }

    return {"designRequirements": dr, "operatingPoints": [op]}


# ---------------------------------------------------------------------------
# Component refs from v1 topology
# ---------------------------------------------------------------------------

# v1 component role -> (template-role-key, library-file-stem)
ROLE_MAP = {
    "highSideSwitch":      ("highSideSwitch", "mosfets"),
    "lowSideSwitch":       ("lowSideSwitch",  "mosfets"),
    "primarySwitch":       ("switch",         "mosfets"),
    "synchronousRectifier":("lowSideSwitch",  "mosfets"),
    "freewheelDiode":      ("lowSideDiode",   "diodes"),
    "boostDiode":          ("boostDiode",     "diodes"),
    "outputRectifier":     ("outputDiode",    "diodes"),
    "rectifierDiode":      ("rectifierDiode", "diodes"),
    "pfcSwitch":           ("pfcSwitch",      "mosfets"),
    "mainTransformer":     ("transformer",    "magnetics"),
    "transformer":         ("transformer",    "magnetics"),
    "mainInductor":        ("inductor",       "magnetics"),
    "outputCapacitor":     ("outputCap",      "capacitors"),
    "bulkCapacitor":       ("bulkCap",        "capacitors"),
    "inputCapacitor":      ("inputCap",       "capacitors"),
    "clampCapacitor":      ("clampCap",       "capacitors"),
    "controller":          ("controller",     "controllers"),
    "auxiliarySwitch":     ("auxSwitch",      "mosfets"),
    "primaryBridgeSwitch": ("primaryQ1",      "mosfets"),
    "secondaryBridgeSwitch":("secondaryQ1",   "mosfets"),
    "fastLegHigh":         ("highSideSwitch", "mosfets"),
    "fastLegLow":          ("lowSideSwitch",  "mosfets"),
    "slowLeg":             ("auxSwitch",      "mosfets"),
    "gateResistor":        ("gateResistor",   "resistors"),
}

LIBRARY_KEYS = ("semiconductor", "mosfet", "diode", "igbt",
                "capacitor", "magnetic", "resistor", "controller")


def extract_refs(record: dict) -> dict[str, str]:
    """Map template-role -> URI for any v1 component with a recognisable
    partNumber. Components without a recognised role are ignored (placeholder
    URIs will be used)."""
    refs: dict[str, str] = {}
    topo = record.get("tas", {}).get("topology") or {}
    if not isinstance(topo, dict):
        return refs
    comps = topo.get("components") or []
    for c in comps:
        if not isinstance(c, dict):
            continue
        role = c.get("role")
        mapped = ROLE_MAP.get(role)
        if not mapped:
            continue
        tpl_role, lib = mapped
        d = c.get("data") or {}
        if not isinstance(d, dict):
            continue
        pn = None
        for k in LIBRARY_KEYS:
            v = d.get(k)
            if isinstance(v, dict):
                mi = v.get("manufacturerInfo")
                if isinstance(mi, dict):
                    pn = mi.get("reference") or mi.get("partNumber")
                    if pn:
                        break
        if not pn:
            continue
        if tpl_role not in refs:  # don't overwrite (first wins)
            refs[tpl_role] = f"TAS/data/{lib}.ndjson?partNumber={pn}"
    return refs


# ---------------------------------------------------------------------------
# Top-level transform
# ---------------------------------------------------------------------------

def transform(record: dict) -> dict:
    """Return the TAS v2 doc, or raise ValueError with a quarantine reason."""
    family = classify(record)
    if family is None:
        raise ValueError("unclassified-family")

    template_fn, supports_ac = TPL.TEMPLATES[family]
    ac = is_ac_input(record)
    if ac and not supports_ac:
        # Family doesn't have an AC-input variant (buck, syncBuck, boost,
        # ldo, dab, module, pfc*). For pfcBoost/pfcLlc the template hardcodes
        # AC input (rectifier stage). For dc-only families with AC input,
        # quarantine — don't silently force a DC-input topology.
        if family not in ("pfcBoost", "pfcLlc"):
            raise ValueError(f"family-input-mismatch: {family} has no AC variant")

    inputs = build_inputs(record, ac, family)
    refs = extract_refs(record)

    # Pass ac flag to families that accept it.
    if family in ("flyback", "llc", "forward"):
        topo = template_fn(refs, ac_input=ac)
    else:
        topo = template_fn(refs)

    return {"inputs": inputs, "topology": topo}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def dedupe(records: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """Keep latest by createdAt for each id; preserve first-seen order otherwise."""
    by_id: dict[str, tuple[str, dict]] = {}
    no_id: list[tuple[str, dict]] = []
    for line, rec in records:
        rid = rec.get("id")
        if not rid:
            no_id.append((line, rec))
            continue
        prev = by_id.get(rid)
        if prev is None:
            by_id[rid] = (line, rec)
        else:
            cur_ts = rec.get("createdAt") or ""
            prev_ts = prev[1].get("createdAt") or ""
            if cur_ts >= prev_ts:
                by_id[rid] = (line, rec)
    return list(by_id.values()) + no_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="don't write outputs or quarantine; just report")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--id", default=None,
                    help="process only the record with this id")
    ap.add_argument("--verbose-errors", action="store_true",
                    help="print full schema error messages for each failure")
    args = ap.parse_args()

    if not SRC.exists():
        print(f"missing source file: {SRC}", file=sys.stderr)
        return 1

    validator = build_validator()

    # Load every line (small file, ~238 records).
    raw: list[tuple[str, dict]] = []
    with SRC.open() as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                if not args.dry_run:
                    quarantine_line(line, f"json-parse: {e}", "converters.ndjson")
                continue
            raw.append((line, rec))

    deduped = dedupe(raw)
    if args.id:
        deduped = [(l, r) for l, r in deduped if r.get("id") == args.id]

    n_in = len(deduped)
    n_ok = n_quar = 0
    reasons: dict[str, int] = {}
    fam_ok: dict[str, int] = {}
    fam_quar: dict[str, int] = {}

    if not args.dry_run:
        out_fh = OUT.open("w")
    else:
        out_fh = None

    for i, (line, rec) in enumerate(deduped):
        if args.limit and i >= args.limit:
            break
        fam_attempt = classify(rec) or "?"
        try:
            doc = transform(rec)
        except ValueError as e:
            n_quar += 1
            key = str(e).split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
            fam_quar[fam_attempt] = fam_quar.get(fam_attempt, 0) + 1
            if not args.dry_run:
                quarantine_line(line, f"transform: {e}", "converters.ndjson")
            continue

        errs = list(validator.iter_errors(doc))
        if errs:
            n_quar += 1
            top = errs[0].message
            if args.verbose_errors:
                print(f"[{rec.get('id')}] {fam_attempt}: "
                      f"{len(errs)} schema errors:")
                for e in errs[:5]:
                    print(f"    - {e.message} @ {list(e.absolute_path)}")
            key = "schema: " + top.split(" ")[0]
            reasons[key] = reasons.get(key, 0) + 1
            fam_quar[fam_attempt] = fam_quar.get(fam_attempt, 0) + 1
            if not args.dry_run:
                msg = "; ".join(
                    f"{e.message} @ {list(e.absolute_path)}" for e in errs[:3]
                )
                quarantine_line(line, f"schema: {msg}", "converters.ndjson")
            continue

        n_ok += 1
        fam_ok[fam_attempt] = fam_ok.get(fam_attempt, 0) + 1
        if out_fh:
            out_fh.write(json.dumps(doc) + "\n")

    if out_fh:
        out_fh.close()

    print(f"\n[converters.ndjson] in={n_in} ok={n_ok} quar={n_quar}")
    print("\n  reasons:")
    for r, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"    {n:5d}  {r}")
    print("\n  family ok:")
    for f, n in sorted(fam_ok.items(), key=lambda kv: -kv[1]):
        print(f"    {n:5d}  {f}")
    print("\n  family quar:")
    for f, n in sorted(fam_quar.items(), key=lambda kv: -kv[1]):
        print(f"    {n:5d}  {f}")

    if args.dry_run:
        OUT.unlink(missing_ok=True)
        return 0

    # Promote.
    if not BACKUP.exists():
        SRC.rename(BACKUP)
    else:
        SRC.unlink()
    OUT.rename(SRC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
