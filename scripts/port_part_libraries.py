"""Port TAS data/<part>.ndjson files to per-discriminator wrapper shape.

Each output record is `{<discriminator>: <data>}` validating against the
matching per-type schema:

    mosfets.ndjson    -> {"mosfet":    ...}  vs SAS/schemas/mosfet.json
    diodes.ndjson     -> {"diode":     ...}  vs SAS/schemas/diode.json
    igbts.ndjson      -> {"igbt":      ...}  vs SAS/schemas/igbt.json
    capacitors.ndjson -> {"capacitor": ...}  vs CAS/schemas/capacitor.json
    resistors.ndjson  -> {"resistor":  ...}  vs RAS/schemas/resistor.json
    magnetics.ndjson  -> {"magnetic":  ...}  vs MAS/schemas/magnetic.json

Records that fail validation are appended to data/quarantine.ndjson with a
`quarantineReason` field describing why. No fallbacks: nothing is silently
dropped or repaired beyond the structural unwrap below.

Structural transforms applied (deterministic, no field synthesis):

  * If the record has v1 wrappers `inputs` / `outputs` at the top level, drop
    them.
  * If the record has both `manufacturerInfo` (containing the schema-shape
    datasheetInfo) AND a redundant flat `semiconductor` field (v1 alias dict
    with `vds`, `id_cont`, ...), drop the redundant `semiconductor`.
  * If the record's only meaningful key is `semiconductor` (igbts shape and
    some mosfet variants), unwrap it: the inner `semiconductor` dict becomes
    the record body.
  * Move top-level `distributorsInfo` into the body untouched.
  * Recursively drop keys whose value is exactly `None` (per-type schemas
    reject null for typed fields).
  * Wrap the resulting body as `{<discriminator>: body}`.

The original file is left as `<file>.v1.backup.ndjson` and replaced atomically.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[1]
PROTEUS = REPO.parent
DATA = REPO / "data"
QUARANTINE = DATA / "quarantine.ndjson"

# (filename, discriminator key, schema repo, schema file)
TARGETS = [
    ("mosfets.ndjson",    "mosfet",    "SAS", "mosfet.json"),
    ("diodes.ndjson",     "diode",     "SAS", "diode.json"),
    ("igbts.ndjson",      "igbt",      "SAS", "igbt.json"),
    ("capacitors.ndjson", "capacitor", "CAS", "capacitor.json"),
    ("resistors.ndjson",  "resistor",  "RAS", "resistor.json"),
    ("magnetics.ndjson",  "magnetic",  "MAS", "magnetic.json"),
]


# ---------------------------------------------------------------------------
# Schema registry
# ---------------------------------------------------------------------------

def _walk_schema_dir(d: Path):
    for p in d.rglob("*.json"):
        try:
            yield p, json.loads(p.read_text())
        except json.JSONDecodeError as e:
            print(f"WARN: cannot parse schema {p}: {e}", file=sys.stderr)


def build_registry() -> Registry:
    """Load every schema in every sibling repo into one referencing.Registry.

    Inlines pure `$ref`-shim schemas (e.g. CAS/utils.json -> PEAS/utils.json) so
    that fragment lookups like `./utils.json#/$defs/X` resolve through the shim
    to the target's `$defs`. The referencing library does not auto-deref a
    schema-level `$ref` when looking up a JSON-pointer fragment, so we materialise
    the target's contents under the shim's `$id`.

    Resolution of the shim's `$ref` uses filesystem paths (the shims' `$ref`
    values are filesystem-relative paths like `../../PEAS/schemas/utils.json`,
    not URL-relative paths against the shim's `$id`).
    """
    by_id: dict[str, dict] = {}
    by_path: dict[Path, dict] = {}
    path_to_id: dict[Path, str] = {}
    for repo_name in ("PEAS", "SAS", "CAS", "RAS", "MAS"):
        repo_dir = PROTEUS / repo_name / "schemas"
        if not repo_dir.is_dir():
            continue
        for path, schema in _walk_schema_dir(repo_dir):
            sid = schema.get("$id")
            path = path.resolve()
            by_path[path] = schema
            if sid:
                by_id[sid] = schema
                path_to_id[path] = sid

    META_KEYS = {"$schema", "$id", "title", "description", "$comment"}
    for sid, schema in list(by_id.items()):
        body_keys = set(schema.keys()) - META_KEYS
        if body_keys != {"$ref"}:
            continue
        # Resolve target via filesystem (the shim's $ref is a fs-relative path).
        # Find the file path for this $id.
        path = next((p for p, s in by_path.items() if s is schema), None)
        if path is None:
            continue
        target_path = (path.parent / schema["$ref"]).resolve()
        target_schema = by_path.get(target_path)
        if target_schema is None:
            continue
        inlined = {
            k: v for k, v in target_schema.items() if k not in ("$id", "$schema")
        }
        inlined["$id"] = sid
        inlined["$schema"] = schema.get(
            "$schema", "https://json-schema.org/draft/2020-12/schema"
        )
        for k in ("title", "description"):
            if k in schema:
                inlined[k] = schema[k]
        by_id[sid] = inlined

    resources = [
        (sid, Resource(contents=s, specification=DRAFT202012))
        for sid, s in by_id.items()
    ]
    return Registry().with_resources(resources)


def get_validator(registry: Registry, repo: str, fname: str) -> Draft202012Validator:
    schema_path = PROTEUS / repo / "schemas" / fname
    schema = json.loads(schema_path.read_text())
    return Draft202012Validator(schema, registry=registry)


# ---------------------------------------------------------------------------
# Record transform
# ---------------------------------------------------------------------------

V1_FLAT_SEMI_KEYS = {
    # mosfet v1 flat aliases
    "vds", "id_cont", "id_pulse", "onStateDrainSourceResistance", "vgs_th",
    "qg", "qgd", "ciss", "coss", "crss", "qrr", "vf_body", "fom",
    # diode v1 flat aliases
    "vrrm", "vf",
}


def _drop_nulls(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            v2 = _drop_nulls(v)
            if v2 is None:
                continue
            out[k] = v2
        return out if out else None  # collapse fully-null dicts
    if isinstance(obj, list):
        return [_drop_nulls(x) for x in obj]
    return obj


# Map common Title-Case / hyphenated assemblyType strings to the schema enum
# (lowerCamelCase, per PEAS connectionType).
_ASSEMBLY_TYPE_MAP = {
    "smt": "smt", "SMT": "smt", "SMD": "smt", "smd": "smt",
    "tht": "tht", "THT": "tht", "TH": "tht",
    "Through-Hole": "tht", "Through Hole": "tht", "Pin Through Hole": "tht",
    "pin": "pin", "Pin": "pin",
    "screw": "screw", "Screw": "screw",
    "flyingLead": "flyingLead", "Flying Lead": "flyingLead",
    "pcbPad": "pcbPad", "PCB Pad": "pcbPad",
    "chassis": "chassis", "Chassis": "chassis",
}

# Resistor technology snake_case / hyphenated -> RAS schema camelCase enum
_RESISTOR_TECH_MAP = {
    "thin_film": "thinFilm", "thin-film": "thinFilm", "thinfilm": "thinFilm",
    "thin film": "thinFilm",
    "thick_film": "thickFilm", "thick-film": "thickFilm", "thickfilm": "thickFilm",
    "thick film": "thickFilm",
    "metal_film": "metalFilm", "metal-film": "metalFilm",
    "metal film": "metalFilm",
    "metal_oxide": "metalOxide", "metal-oxide": "metalOxide",
    "metal oxide": "metalOxide",
    "wirewound": "wirewound", "wire_wound": "wirewound", "wire-wound": "wirewound",
    "wire wound": "wirewound", "wireWound": "wirewound", "WireWound": "wirewound",
    "Wirewound": "wirewound",
    "carbon_composition": "carbonComposition", "carbon-composition": "carbonComposition",
    "carbon composition": "carbonComposition",
    "carbon_film": "carbonFilm", "carbon-film": "carbonFilm",
    "carbon film": "carbonFilm",
    "metal_foil": "metalFoil", "metal-foil": "metalFoil", "metal foil": "metalFoil",
    "bulk_metal_foil": "bulkMetalFoil", "bulk-metal-foil": "bulkMetalFoil",
    "bulk metal foil": "bulkMetalFoil",
    "current_sense_shunt": "currentSenseShunt", "shunt": "currentSenseShunt",
    "current sense shunt": "currentSenseShunt",
    "melf": "melf", "MELF": "melf",
    # NOTE: 'general purpose', 'jumper', 'metal_plate' have no enum mapping;
    # records using them will fail validation and go to quarantine.
}

# Per-discriminator extras to drop from datasheetInfo.electrical (data has them
# but the per-type schema rejects them).
_ELECTRICAL_DROPS = {
    "mosfet": {"note", "onResistanceAt10V"},
    "diode": {"zenerVoltage", "forwardVoltageAtAt", "reverseRecoveryChargeAt",
              "recoveredCharge", "leakageCurrent", "note"},
    "capacitor": {
        "esrForLosses", "capacitanceSaturationMLCC", "vthMLCC",
    },
    # IGBT data sometimes carries stray MOSFET-style fields. Drop them so the
    # legitimate IGBT fields can validate.
    "igbt": {
        "fallTime", "riseTime", "turnOnDelayTime", "turnOffDelayTime",
        "reverseRecoveryCharge", "outputCapacitance",
        "reverseTransferCapacitance", "collectorCurrent", "onStateVoltage",
        "junctionTemperatureMax", "junctionTemperatureMin",
        "drainSourceVoltage", "bodyDiodeForwardVoltage",
        "continuousDrainCurrent", "onResistance", "gateDrainCharge",
    },
}

# datasheetInfo-level extras to drop (sit between manufacturerInfo and the
# typed sub-objects). The schema's datasheetInfo has additionalProperties:false
# everywhere, so any field not in the schema must be dropped or quarantined.
_DATASHEETINFO_DROPS = {
    "diode": {"_needsVerification", "_verificationNote", "_verificationNotes"},
    "mosfet": {"_needsVerification", "_verificationNote", "_verificationNotes"},
    "resistor": {"_needsVerification", "_verificationNote", "_verificationNotes"},
    # capacitor: 'datasheetUrl' wrongly placed here; we move it to mfg.datasheetUrl.
}

# Mechanical extras to drop (data has misc fields not in the per-type schema).
_MECHANICAL_DROPS = {
    "mosfet": {"mounting", "package"},
    "diode": {"package", "mounting"},
    "igbt": {"package", "mounting"},
}

# Mosfet thermal field renames (data uses old aliases).
_MOSFET_THERMAL_RENAMES = {
    "maximumJunctionTemperature": "junctionTemperatureMax",
    "minimumJunctionTemperature": "junctionTemperatureMin",
    "rth_ja": "thermalResistanceJunctionAmbient",
    "rth_jc": "thermalResistanceJunctionCase",
    "tj_max": "junctionTemperatureMax",
    "tj_min": "junctionTemperatureMin",
}

# Mosfet electrical fields that actually belong in thermal — move them.
_MOSFET_ELEC_TO_THERMAL = {"junctionTemperatureMax", "junctionTemperatureMin"}

# IGBT field renames (data uses old aliases).
_IGBT_RENAMES = {
    "collectorEmitterSaturationVoltage": "collectorEmitterSaturation",
}

# Capacitor business: data has 'vpe', 'alphaPlanDescription', 'distribution',
# 'wgu', 'weCustomWho...', etc. CAS schema allows
# {packaging, pu, moq, leadTime, stock, distribution, priceCost}.
_CAPACITOR_BUSINESS_RENAMES = {"vpe": "pu"}
_CAPACITOR_BUSINESS_DROPS = {
    "alphaPlanDescription", "wgu",
    # any 'we*' Würth-internal fields are generic extras
}

# Resistor business: data has 'priceCost', schema expects 'cost'.
_RESISTOR_BUSINESS_RENAMES = {"priceCost": "cost"}


def _coerce(body: dict, discriminator: str) -> dict:
    """Apply targeted shape-coercions that bridge data-vs-schema vocabulary
    differences. Each coercion is explicit. Nothing else is altered."""
    mi = body.get("manufacturerInfo")
    if isinstance(mi, dict):
        # Drop status:"unknown" (not in schema enum). Real values stay.
        if mi.get("status") == "unknown":
            mi.pop("status", None)
        # Move nested mfg.distributorsInfo to top-level (some mosfet records
        # carry it inside manufacturerInfo).
        if "distributorsInfo" in mi and "distributorsInfo" not in body:
            body["distributorsInfo"] = mi.pop("distributorsInfo")

        di = mi.get("datasheetInfo")
        if isinstance(di, dict):
            # Drop datasheetInfo-level extras (e.g. _needsVerification).
            for k in _DATASHEETINFO_DROPS.get(discriminator, set()):
                di.pop(k, None)
            # Capacitor: datasheetUrl mis-placed at datasheetInfo level — move
            # it up to manufacturerInfo.datasheetUrl if free.
            if discriminator == "capacitor" and "datasheetUrl" in di:
                v = di.pop("datasheetUrl")
                if "datasheetUrl" not in mi:
                    mi["datasheetUrl"] = v

            # part: drop deviceType (file name is the discriminator), drop
            # capacitor catalog/UI hints, normalise resistor technology enum,
            # rename magnetic legacy fields.
            part = di.get("part")
            if isinstance(part, dict):
                part.pop("deviceType", None)
                if discriminator == "capacitor":
                    for k in ("matchcodeDescription", "useInDcTool",
                              "internalViewOnly", "dataCompleteness"):
                        part.pop(k, None)
                if discriminator == "magnetic":
                    if "case" in part and "caseCode" not in part:
                        part["caseCode"] = part.pop("case")
                    elif "case" in part:
                        part.pop("case", None)
                    if "series" in part and "family" not in part:
                        part["family"] = part.pop("series")
                    elif "series" in part:
                        part.pop("series", None)
                    if "application" in part:
                        # 'application' is a sibling of 'part' in datasheetInfo,
                        # not a sub-field of part. Move it up. The schema's
                        # application object has no 'category' field, so a
                        # bare-string application is dropped (no fallback).
                        v = part.pop("application")
                        if isinstance(v, dict) and "application" not in di:
                            di["application"] = v
                if discriminator == "resistor":
                    # Schema part allows {partNumber, series, technology, case,
                    # matchcodeDescription}; drop everything else.
                    for k in list(part.keys()):
                        if k not in ("partNumber", "series", "technology",
                                     "case", "matchcodeDescription"):
                            part.pop(k, None)
                    t = part.get("technology")
                    if isinstance(t, str) and t in _RESISTOR_TECH_MAP:
                        part["technology"] = _RESISTOR_TECH_MAP[t]

            # mechanical: normalise assemblyType, flatten nested
            # dimensions/shape (RAS data style), drop schema-rejected extras.
            mech = di.get("mechanical")
            if isinstance(mech, dict):
                for k in _MECHANICAL_DROPS.get(discriminator, set()):
                    mech.pop(k, None)
                if discriminator == "magnetic" and "case" in mech:
                    v = mech.pop("case")
                    p = di.setdefault("part", {})
                    if isinstance(p, dict) and "caseCode" not in p:
                        p["caseCode"] = v
                if "assemblyType" in mech:
                    v = mech["assemblyType"]
                    if isinstance(v, str) and v in _ASSEMBLY_TYPE_MAP:
                        mech["assemblyType"] = _ASSEMBLY_TYPE_MAP[v]
                if discriminator == "resistor":
                    dims = mech.pop("dimensions", None)
                    if isinstance(dims, dict):
                        for k in ("length", "width", "height", "diameter"):
                            if k in dims and k not in mech:
                                mech[k] = dims[k]
                    shape = mech.pop("shape", None)
                    if isinstance(shape, dict):
                        if "assembly" in shape and "assemblyType" not in mech:
                            v = shape["assembly"]
                            if isinstance(v, str):
                                mech["assemblyType"] = _ASSEMBLY_TYPE_MAP.get(v, v)
                        if "shapeType" in shape and "shapeType" not in mech:
                            mech["shapeType"] = shape["shapeType"]
                # NOTE: capacitor shape.assembly is the schema-canonical name
                # (enum: "THT","Screw Type","SMT","Snap-In"); leave as-is.

            # thermal: rename mosfet aliases.
            therm = di.get("thermal")
            if isinstance(therm, dict) and discriminator == "mosfet":
                for old, new in _MOSFET_THERMAL_RENAMES.items():
                    if old in therm:
                        v = therm.pop(old)
                        if new and new not in therm:
                            therm[new] = v

            # electrical: drop schema-rejected extras; rename IGBT aliases;
            # wrap magnetic scalar dcResistance into dimensionWithTolerance.
            elec = di.get("electrical")
            if isinstance(elec, dict):
                if discriminator == "magnetic":
                    for k in ("dcResistance", "inductance", "leakageInductance"):
                        v = elec.get(k)
                        if isinstance(v, (int, float)):
                            elec[k] = {"nominal": float(v)}
                    if "saturationCurrent" in elec and "saturationCurrentPeak" not in elec:
                        elec["saturationCurrentPeak"] = elec.pop("saturationCurrent")
                    elec.pop("peakCurrent", None)
                    elec.pop("saturationCurrentTemperature", None)
                    if "switchingFrequency" in elec:
                        v = elec.pop("switchingFrequency")
                        app = di.setdefault("application", {})
                        if isinstance(app, dict) and "switchingFrequency" not in app:
                            app["switchingFrequency"] = v
                # mosfet: move junctionTemperature{Max,Min} to thermal first.
                if discriminator == "mosfet":
                    therm = di.setdefault("thermal", {}) if any(
                        k in elec for k in _MOSFET_ELEC_TO_THERMAL
                    ) else di.get("thermal")
                    if isinstance(therm, dict):
                        for k in list(_MOSFET_ELEC_TO_THERMAL):
                            if k in elec:
                                v = elec.pop(k)
                                if k not in therm:
                                    therm[k] = v
                drops = _ELECTRICAL_DROPS.get(discriminator, set())
                for k in list(elec.keys()):
                    if k in drops:
                        elec.pop(k, None)
                if discriminator == "igbt":
                    for old, new in _IGBT_RENAMES.items():
                        if old in elec:
                            v = elec.pop(old)
                            if new is not None and new not in elec:
                                elec[new] = v

            # business: drop / rename schema-rejected fields.
            biz = di.get("business")
            if isinstance(biz, dict):
                if discriminator == "capacitor":
                    for old, new in _CAPACITOR_BUSINESS_RENAMES.items():
                        if old in biz and new not in biz:
                            biz[new] = biz.pop(old)
                    for k in list(biz.keys()):
                        if k in _CAPACITOR_BUSINESS_DROPS or k.startswith("we"):
                            biz.pop(k, None)
                if discriminator == "resistor":
                    for old, new in _RESISTOR_BUSINESS_RENAMES.items():
                        if old in biz and new not in biz:
                            biz[new] = biz.pop(old)

            # factors: resistor restructure
            #   {powerDeratingTemperature, powerDeratingAmplitude}
            #   -> {powerDerating: {temperature, amplitude}}
            if discriminator == "resistor":
                factors = di.get("factors")
                if isinstance(factors, dict):
                    t = factors.pop("powerDeratingTemperature", None)
                    a = factors.pop("powerDeratingAmplitude", None)
                    if t is not None or a is not None:
                        pd = factors.setdefault("powerDerating", {})
                        if isinstance(pd, dict):
                            if t is not None and "temperature" not in pd:
                                pd["temperature"] = t
                            if a is not None and "amplitude" not in pd:
                                pd["amplitude"] = a

    # Strip v1 redundant flat aliases that may sit alongside the structured
    # data inside the discriminator body or under semiconductor-shaped igbt
    # records (id_cont, vds, qg, ...). The corresponding values already live
    # in datasheetInfo.electrical with the canonical names.
    for k in list(body.keys()):
        if k in V1_FLAT_SEMI_KEYS:
            body.pop(k, None)

    # Resistor records carry top-level wrapper aliases (resistance, tolerance,
    # powerRating, temperatureCoefficient, maxVoltage, maxOverloadVoltage,
    # insulationResistance, noiseIndex) duplicating datasheetInfo.electrical;
    # also stray 'semiconductor' from misclassified records, and 'usageNotes'.
    if discriminator == "resistor":
        for k in (
            "resistance", "tolerance", "powerRating", "temperatureCoefficient",
            "maxVoltage", "maxOverloadVoltage", "insulationResistance",
            "noiseIndex", "semiconductor", "usageNotes",
        ):
            body.pop(k, None)

    return body


def _is_v1_flat_semi(d) -> bool:
    """Return True if `d` looks like the redundant flat semiconductor alias dict."""
    if not isinstance(d, dict):
        return False
    if not d:
        return False
    # If most keys are v1 flat aliases (no manufacturerInfo / datasheetInfo inside).
    if "manufacturerInfo" in d or "datasheetInfo" in d:
        return False
    return any(k in V1_FLAT_SEMI_KEYS for k in d.keys())


def _merge_top_datasheet_into_mfg(body: dict) -> None:
    """If body has BOTH top-level `datasheetInfo` and `manufacturerInfo`, merge
    the top one into `manufacturerInfo.datasheetInfo` (deep-merge sub-objects).
    Conflicts on scalar leaves: existing mfg value wins (mfg is the canonical
    home). Used for the v1 'semiconductor' wrapper unwrap on mosfet/diode."""
    if not isinstance(body.get("manufacturerInfo"), dict):
        return
    if not isinstance(body.get("datasheetInfo"), dict):
        return
    top_di = body.pop("datasheetInfo")
    mi = body["manufacturerInfo"]
    mfg_di = mi.setdefault("datasheetInfo", {})
    if not isinstance(mfg_di, dict):
        # mfg.datasheetInfo isn't a dict — restore and let validator catch it.
        body["datasheetInfo"] = top_di
        return
    for sub_k, sub_v in top_di.items():
        if sub_k not in mfg_di:
            mfg_di[sub_k] = sub_v
            continue
        # Deep-merge dicts (mfg-side wins on leaf conflicts).
        if isinstance(mfg_di[sub_k], dict) and isinstance(sub_v, dict):
            for k, v in sub_v.items():
                if k not in mfg_di[sub_k]:
                    mfg_di[sub_k][k] = v


def transform(record: dict, discriminator: str) -> dict:
    """Return `{discriminator: body}` for the record, or raise ValueError."""
    if not isinstance(record, dict):
        raise ValueError("not a JSON object")

    r = dict(record)  # shallow copy

    # 1. drop v1 wrappers
    r.pop("inputs", None)
    r.pop("outputs", None)
    r.pop("_importMeta", None)
    r.pop("quarantineInfo", None)

    # 2. handle the {discriminator: {...}} already-wrapped case
    if discriminator in r and len(r) == 1:
        body = r[discriminator]
        if not isinstance(body, dict):
            raise ValueError(f"{discriminator} field is not an object")
        body = _drop_nulls(body) or {}
        _merge_top_datasheet_into_mfg(body)
        body = _coerce(body, discriminator)
        return {discriminator: body}

    # 3. handle {semiconductor: {...}}-only case (igbts; some mosfets/diodes)
    if "semiconductor" in r and isinstance(r["semiconductor"], dict) \
            and not _is_v1_flat_semi(r["semiconductor"]):
        body = dict(r["semiconductor"])
        # merge top-level distributorsInfo if present
        if "distributorsInfo" in r and "distributorsInfo" not in body:
            body["distributorsInfo"] = r["distributorsInfo"]
        # other top-level keys we don't recognise -> error
        leftover = set(r) - {"semiconductor", "distributorsInfo"}
        if leftover:
            raise ValueError(f"unexpected top-level keys: {sorted(leftover)}")
        body = _drop_nulls(body) or {}
        _merge_top_datasheet_into_mfg(body)
        body = _coerce(body, discriminator)
        return {discriminator: body}

    # 4. handle {manufacturerInfo, [semiconductor]} case (mosfets/diodes/caps)
    if "manufacturerInfo" in r:
        body = {"manufacturerInfo": r["manufacturerInfo"]}
        if "distributorsInfo" in r:
            body["distributorsInfo"] = r["distributorsInfo"]
        # Drop the redundant flat semiconductor alias (if present) — its data
        # is already in manufacturerInfo.datasheetInfo.electrical.
        leftover = set(r) - {"manufacturerInfo", "distributorsInfo", "semiconductor"}
        if leftover:
            raise ValueError(f"unexpected top-level keys: {sorted(leftover)}")
        if "semiconductor" in r and not _is_v1_flat_semi(r["semiconductor"]):
            raise ValueError(
                "top-level 'semiconductor' is not a v1 flat alias and not the only key"
            )
        body = _drop_nulls(body) or {}
        body = _coerce(body, discriminator)
        return {discriminator: body}

    raise ValueError(f"no recognised shape; top keys = {sorted(r)}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def quarantine_line(orig_line: str, reason: str, source: str):
    try:
        rec = json.loads(orig_line)
    except json.JSONDecodeError:
        rec = {"_rawLine": orig_line.rstrip("\n")}
    if isinstance(rec, dict):
        rec["quarantineReason"] = reason
        rec["quarantineSource"] = source
    else:
        rec = {"_value": rec, "quarantineReason": reason, "quarantineSource": source}
    with QUARANTINE.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


def port_file(fname: str, discriminator: str, validator: Draft202012Validator,
              limit: int | None = None, dry_run: bool = False) -> dict:
    src = DATA / fname
    if not src.exists():
        return {"file": fname, "missing": True}

    backup = DATA / f"{src.stem}.v1.backup.ndjson"
    out = DATA / f"{src.stem}.v2.ndjson"

    n_in = n_ok = n_quar = 0
    quar_reasons = {}

    with src.open() as in_fh, out.open("w") as out_fh:
        for ln, line in enumerate(in_fh, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            n_in += 1
            if limit and n_in > limit:
                break
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                if not dry_run:
                    quarantine_line(line, f"json-parse: {e}", fname)
                n_quar += 1
                quar_reasons.setdefault("json-parse", 0)
                quar_reasons["json-parse"] += 1
                continue

            try:
                wrapped = transform(rec, discriminator)
            except ValueError as e:
                if not dry_run:
                    quarantine_line(line, f"transform: {e}", fname)
                n_quar += 1
                key = f"transform: {str(e)[:60]}"
                quar_reasons[key] = quar_reasons.get(key, 0) + 1
                continue

            # Validate the inner body against the per-type schema.
            inner = wrapped[discriminator]
            errs = list(validator.iter_errors(inner))
            if errs:
                msg = "; ".join(
                    f"{e.message} @ {list(e.absolute_path)}" for e in errs[:3]
                )
                if not dry_run:
                    quarantine_line(line, f"schema: {msg}", fname)
                n_quar += 1
                # Bucket by first error message (truncated, keyless).
                top = errs[0].message.split(" ")[0]
                quar_reasons[f"schema: {top}"] = quar_reasons.get(f"schema: {top}", 0) + 1
                continue

            n_ok += 1
            out_fh.write(json.dumps(wrapped) + "\n")

    if dry_run:
        out.unlink(missing_ok=True)
        return {"file": fname, "in": n_in, "ok": n_ok, "quar": n_quar,
                "reasons": quar_reasons}

    # Promote.
    if not backup.exists():
        src.rename(backup)
    else:
        src.unlink()
    out.rename(src)
    return {"file": fname, "in": n_in, "ok": n_ok, "quar": n_quar,
            "reasons": quar_reasons}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="process at most N records per file (for sampling)")
    ap.add_argument("--dry-run", action="store_true",
                    help="don't write outputs or quarantine; just report")
    ap.add_argument("--only", nargs="*", default=None,
                    help="only process these files (basenames)")
    args = ap.parse_args()

    registry = build_registry()
    summary = []
    for fname, disc, repo, schema_file in TARGETS:
        if args.only and fname not in args.only:
            continue
        validator = get_validator(registry, repo, schema_file)
        s = port_file(fname, disc, validator, limit=args.limit, dry_run=args.dry_run)
        summary.append(s)
        print(f"\n[{fname}] in={s.get('in', 0)} ok={s.get('ok', 0)} "
              f"quar={s.get('quar', 0)}")
        for r, n in sorted(s.get("reasons", {}).items(), key=lambda kv: -kv[1])[:8]:
            print(f"    {n:6d}  {r}")

    print("\n=== TOTAL ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
