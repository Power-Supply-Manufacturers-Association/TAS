#!/usr/bin/env python3
"""Find quarantined rows whose withdrawal reason has since stopped being true.

    python3 scripts/quarantine_recover.py [--out PATH] [--apply]

WHY. A quarantine verdict is a judgement made at a moment. Some of those moments
expire: on 2026-09-05, 792 rows were withdrawn as "misfiled: cable core, not the
catalogue's component type" -- and MAS had since DEFINED that component type, so the
stated reason was simply false and 607 of them belonged back in the catalogue. Nobody
would have noticed, because nothing re-reads a withdrawal.

WHAT IT DOES. Re-validates every quarantined row against the CURRENT schemas and
surfaces the ones that (a) validate today and (b) were withdrawn for a reason that
schema validity refutes. That is a CANDIDATE list, not a restore list.

WHY IT DOES NOT RESTORE BY DEFAULT -- two failures, both from real days:

  * SCHEMA-VALID IS NOT CORRECT. Of 327 cable cores restored on exactly this signal,
    65 were contradicted by their own vendors: Murata rows claiming an 18.7 mm cable
    fits through a 0.7 mm gap, TDK rows storing a range's lower bound as a maximum.
    All 327 validated cleanly, and Blade Runner gave every one of them the same single
    GEN_SPARSE finding before and after -- so neither gate could see the defect. Only
    the vendor's own document could.

  * A RESTORE CAN DUPLICATE. A previous pass "restored" rows without asking whether
    they were already live, and put 656 real Vishay capacitors in the live catalogue
    and the quarantine simultaneously. This script therefore checks liveness by
    identity and excludes any collision, loudly.

So the default output is a candidate file for a human or an agent to adjudicate
against vendor data. `--apply` exists for when that adjudication has happened, and it
still refuses anything live, anything on the fabrication denylist, and anything whose
reason class is not in the safe list.

FABRICATED DATA IS NEVER A CANDIDATE. An invented part is not a record with a problem;
it is not a record. Rows whose identity is on data/fabricated_denylist.ndjson, or whose
reason names fabrication, are excluded outright and are not counted as recoverable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
QUAR = DATA / "quarantine.ndjson"
DENYLIST = DATA / "fabricated_denylist.ndjson"

# Reason classes that SCHEMA VALIDITY can refute. A row withdrawn because the
# catalogue had no type for it is recoverable once the type exists. A row withdrawn
# because its VALUES are wrong is not -- validating proves nothing about a value.
REFUTABLE = re.compile(
    r"not the catalogue'?s? component type|misfiled|unsupported (?:sub)?type|"
    r"no schema for|schema (?:gap|mismatch|invalid)|subtype_mismatch|unmapped",
    re.I)

# Never recoverable, whatever else the row says.
NEVER = re.compile(r"fabricat|invented|synthetic|made[- ]up|generator|denylist", re.I)

# Reasons about VALUES: the part is real, the numbers are wrong. Schema validity is
# silent on these, so they stay withdrawn until someone re-sources them.
VALUE_DEFECT = re.compile(
    r"outside .*tolerance|contradicted by|wrong |impossible|out of range|"
    r"exceeds|implausible|re-?import|value", re.I)

DISCRIMINATORS = ("magnetic", "capacitor", "semiconductor", "resistor", "varistor",
                  "controller", "connector", "analog", "behavioral", "transmissionLine")

LIVE_FILES = ["magnetics", "capacitors", "resistors", "varistors", "mosfets", "diodes",
              "igbts", "bjts", "controllers", "analog_ics", "connectors",
              "timing_devices", "thermistors"]


def identity(rec):
    """(manufacturer, reference-or-partNumber). The guard learned the hard way that
    keying on one optional field lets whole batches through."""
    def walk(o):
        if isinstance(o, dict):
            mi = o.get("manufacturerInfo")
            if isinstance(mi, dict):
                ref = mi.get("reference")
                di = mi.get("datasheetInfo") or {}
                pn = ((di.get("part") or {}).get("partNumber")
                      if isinstance(di, dict) else None)
                if not pn:
                    pn = ((o.get("datasheetInfo") or {}).get("part") or {}).get("partNumber") \
                        if isinstance(o.get("datasheetInfo"), dict) else None
                key = ref or pn
                if key:
                    return (str(mi.get("name") or "").strip().lower(), str(key).strip())
            for v in o.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = walk(v)
                if r:
                    return r
        return None
    return walk(rec)


def load_live_identities():
    live = {}
    for stem in LIVE_FILES:
        p = DATA / f"{stem}.ndjson"
        if not p.exists():
            continue
        for raw in open(p, "rb"):
            try:
                ident = identity(json.loads(raw))
            except Exception:
                continue
            if ident:
                live.setdefault(ident, stem)
    return live


def load_denylist():
    deny = set()
    if not DENYLIST.exists():
        return deny
    for raw in open(DENYLIST, "rb"):
        try:
            d = json.loads(raw)
        except Exception:
            continue
        man = str(d.get("manufacturer") or d.get("name") or "").strip().lower()
        key = d.get("reference") or d.get("partNumber")
        if key:
            deny.add((man, str(key).strip()))
    return deny


def build_validators():
    """Per-discriminator validator against the CURRENT sibling schemas. If the
    registry cannot be built, that is a hard failure -- a gate that cannot run FAILS."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    parent = REPO.parent
    registry = Registry()
    for repo in ("PEAS", "CIAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS"):
        root = parent / repo / "schemas"
        if not root.is_dir():
            continue
        for p in root.rglob("*.json"):
            try:
                doc = json.loads(p.read_text())
            except Exception:
                continue
            if isinstance(doc, dict) and "$id" in doc:
                registry = registry.with_resource(doc["$id"], Resource.from_contents(doc))

    # Keyed by discriminator, and for semiconductors by the nested type -- SAS has a
    # schema per device type, and an unmapped discriminator is a BLIND SPOT, not a
    # pass: the first run of this script skipped 5,300 quarantined rows that way.
    targets = {
        "magnetic": "https://psma.com/mas/magnetic.json",
        "capacitor": "https://psma.com/cas/capacitor.json",
        "resistor": "https://psma.com/ras/resistor.json",
        "varistor": "https://psma.com/ras/varistor.json",
        "controller": "https://psma.com/ctas/controller.json",
        "connector": "https://psma.com/conas/connector.json",
        "analog": "https://psma.com/aas/AAS.json",
        "semiconductor.mosfet": "https://psma.com/sas/mosfet.json",
        "semiconductor.diode": "https://psma.com/sas/diode.json",
        "semiconductor.igbt": "https://psma.com/sas/igbt.json",
        "semiconductor.bjt": "https://psma.com/sas/bjt.json",
    }
    out = {}
    for disc, uri in targets.items():
        try:
            res = registry.get_or_retrieve(uri).value
            out[disc] = Draft202012Validator(res.contents, registry=registry)
        except Exception:
            pass
    if not out:
        raise SystemExit("FAIL: no schema could be resolved -- is the PSMA sibling "
                         "layout present next to this checkout?")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path,
                    default=REPO / "staging" / "integrity" / "quarantine_candidates.ndjson")
    ap.add_argument("--apply", action="store_true",
                    help="actually restore. Refuses anything live, denylisted, or "
                         "withdrawn for a value defect. Adjudicate against VENDOR data first.")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    if not QUAR.exists():
        print(f"no quarantine file at {QUAR}")
        return 0

    validators = build_validators()
    live = load_live_identities()
    deny = load_denylist()

    stats = Counter()
    candidates = []
    for raw in open(QUAR, "rb"):
        stats["rows"] += 1
        try:
            rec = json.loads(raw)
        except Exception:
            stats["unparseable"] += 1
            continue
        reason = " ".join(str(rec.get(k) or "") for k in
                          ("quarantineReason", "_quarantineSource", "reason"))
        if NEVER.search(reason):
            stats["fabricated_excluded"] += 1
            continue
        if not REFUTABLE.search(reason):
            stats["reason_not_refutable"] += 1
            continue
        if VALUE_DEFECT.search(reason):
            # says BOTH -- a value defect is the stronger signal, keep it withdrawn
            stats["value_defect"] += 1
            continue

        disc = next((d for d in DISCRIMINATORS if d in rec), None)
        if disc is None:
            # Not PEAS-wrapped. Two very different things hide here, and lumping them
            # under "no validator" was concealing both: CIAS bricks, which are
            # legitimately not PEAS documents, and raw vendor payloads that were
            # quarantined before anyone converted them to a schema shape at all.
            if {"name", "ports", "components", "connections"} <= set(rec):
                stats["cias_brick"] += 1
            elif not [k for k in rec if not k.startswith("_")
                      and k not in ("quarantineReason", "reason")]:
                stats["empty_row"] += 1
            else:
                stats["unwrapped_raw"] += 1
            continue
        key = disc
        if disc == "semiconductor" and isinstance(rec.get(disc), dict):
            sub = next((k for k in ("mosfet", "diode", "igbt", "bjt")
                        if k in rec[disc]), None)
            key = f"semiconductor.{sub}" if sub else disc
        v = validators.get(key)
        if v is None:
            stats["no_validator"] += 1
            continue
        body = {k: val for k, val in rec.items() if not k.startswith("_")
                and k not in ("quarantineReason", "reason")}
        payload = body.get(disc)
        if key != disc and isinstance(payload, dict):
            payload = payload.get(key.split(".", 1)[1], payload)
        if payload is None:
            stats["no_payload"] += 1
            continue
        if list(v.iter_errors(payload)):
            stats["still_invalid"] += 1
            continue

        ident = identity(rec)
        if ident is None:
            stats["unidentifiable"] += 1
            continue
        if ident in deny:
            stats["denylisted"] += 1
            continue
        if ident in live:
            stats["already_live"] += 1
            continue

        stats["candidate"] += 1
        candidates.append({"identity": list(ident), "discriminator": disc,
                           "quarantineReason": rec.get("quarantineReason"),
                           "record": body})
        if args.limit and len(candidates) >= args.limit:
            break

    print(f"quarantine rows scanned          {stats['rows']:>8,}")
    for k in ("fabricated_excluded", "reason_not_refutable", "value_defect",
              "still_invalid", "already_live", "denylisted", "unidentifiable",
              "cias_brick", "unwrapped_raw", "empty_row",
              "no_validator", "no_payload", "unparseable"):
        if stats[k]:
            print(f"  excluded: {k:28} {stats[k]:>8,}")
    print(f"RECOVERY CANDIDATES              {stats['candidate']:>8,}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as fh:
        for c in candidates:
            fh.write(json.dumps(c, separators=(",", ":"), ensure_ascii=False) + "\n")
    print(f"\nwritten: {args.out}")

    if stats["already_live"]:
        print(f"\nNOTE: {stats['already_live']:,} quarantined identities are ALSO LIVE. "
              "That is the both-files state a previous restore created for 656 real\n"
              "      Vishay capacitors. They are excluded here, but the duplication "
              "itself wants resolving.")

    if not args.apply:
        print("\n--apply not given: nothing restored. These are CANDIDATES.\n"
              "Schema validity is not correctness: of 327 cable cores restored on this\n"
              "signal, 65 were contradicted by their own vendors while validating\n"
              "cleanly and drawing an identical Blade Runner verdict. Adjudicate\n"
              "against the vendor's own document before restoring.")
        return 0

    print("\n--apply given: restoring is a data write and is deliberately not "
          "implemented as an unattended path.\nRun the candidates through an "
          "adjudication pass (vendor document per row), then append with the\n"
          "librarian promote pattern so concurrent appends are preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
