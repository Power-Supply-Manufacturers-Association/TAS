#!/usr/bin/env python3
"""Migrate data/converters.ndjson from the pre-v2.1 topology dialect to the current schema.

Old dialect -> v2.1 mapping (validated against schemas/topology.json):

  topology.interStageCircuit[]          -> topology.interStageConnections[]
    endpoints [{component, pin}]        ->   endpoints [{stage, port}] (stage-qualified;
                                             the owning stage's circuit gains a port wired
                                             to that pin, named after the inter-stage net)
  stage.inputPort  {type, wire}         -> {port: <wire>, type}          (portBinding)
  stage.outputPorts [{type, wire}]      -> non-isolation: singular outputPort
                                           isolation:     outputPorts[] of portBindings
  stage.circuit {components,connections}-> CIAS brick: + name, + ports[];
                                           connection `kind: "wire"` dropped (CIAS nets
                                           have no kind); `kind: "coupling"` connections
                                           DELETED (degenerate self-coupling markers -
                                           coupling lives inside the multi-winding
                                           magnetic component in the current model)
  control stage {circuit,senses,drives} -> virtualControl {controlImplementation:
                                           "virtual", senses, drives}; the placeholder
                                           controller circuit is dropped; senses
                                           {wire, signal} -> {net, signal}; drives
                                           {component, signal} -> {stage, component,
                                           signal}

The script refuses to guess: any record violating the corpus invariants (unknown
component in an endpoint, a portBinding wire with no inter-stage net, an inter-stage
net bridging two internal nets of one stage) raises instead of emitting a fabricated
structure.

Writes data/converters.ndjson.bak, then replaces the file atomically, then validates
every migrated record against TAS.json with the full sibling registry (hard exit gate).
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKSPACE = REPO.parent
DATA = REPO / "data" / "converters.ndjson"


def migrate_record(r: dict, lineno: int) -> tuple[dict, dict]:
    stats = {"couplings_dropped": 0, "control_circuits_dropped": 0, "ports_created": 0,
             "placeholder_uris": 0}
    t = r["topology"]

    # --- maps ---------------------------------------------------------------
    owner = {}    # component name -> stage name
    circ = {}     # stage name -> inline circuit dict
    for st in t["stages"]:
        c = st.get("circuit")
        if isinstance(c, dict):
            circ[st["name"]] = c
            for comp in c.get("components", []):
                if comp["name"] in owner:
                    raise ValueError(f"line {lineno}: duplicate component {comp['name']}")
                owner[comp["name"]] = st["name"]
                if "placeholder=" in str(comp.get("data", "")):
                    stats["placeholder_uris"] += 1

    # --- circuits: name, drop kinds, drop couplings --------------------------
    for st in t["stages"]:
        c = st.get("circuit")
        if not isinstance(c, dict):
            continue
        conns = []
        for conn in c.get("connections", []):
            kind = conn.pop("kind", None)
            if kind == "coupling":
                stats["couplings_dropped"] += 1
                continue  # self-coupling marker; the magnetic component owns coupling now
            conn.pop("couplingCoefficient", None)
            conns.append(conn)
        c["connections"] = conns
        if st.get("role") != "control":
            c.setdefault("name", st["name"])
            c.setdefault("ports", [])

    # --- inter-stage nets: synthesize circuit ports, stage-qualify endpoints -
    new_isc = []
    for e in t.pop("interStageCircuit"):
        net = e["name"]
        stage_endpoints = {}  # stage -> port name (one per stage per net; no bridging)
        for ep in e["endpoints"]:
            stg = owner[ep["component"]]
            c = circ[stg]
            # find the internal net already carrying this pin, if any
            hit = None
            for conn in c["connections"]:
                if any(x.get("component") == ep["component"] and x.get("pin") == ep["pin"]
                       for x in conn["endpoints"]):
                    hit = conn
                    break
            if stg in stage_endpoints:
                port = stage_endpoints[stg]
                target = next(cn for cn in c["connections"]
                              if any(x.get("port") == port for x in cn["endpoints"]))
                if hit is not None and hit is not target:
                    raise ValueError(f"line {lineno}: net {net} bridges two internal nets in {stg}")
                if hit is None:
                    target["endpoints"].append({"component": ep["component"], "pin": ep["pin"]})
                continue
            port = net
            if any(p["name"] == port for p in c["ports"]):
                raise ValueError(f"line {lineno}: port name collision {port} on stage {stg}")
            c["ports"].append({"name": port})
            stats["ports_created"] += 1
            if hit is not None:
                hit["endpoints"].append({"port": port})
            else:
                c["connections"].append({
                    "name": net,
                    "endpoints": [{"component": ep["component"], "pin": ep["pin"]},
                                  {"port": port}],
                })
            stage_endpoints[stg] = port

        entry = {"name": net, "kind": e["kind"],
                 "endpoints": [{"stage": s, "port": p} for s, p in stage_endpoints.items()]}
        if e["kind"] == "externalPort":
            entry = {"name": net, "kind": "externalPort", "direction": e["direction"],
                     "endpoints": entry["endpoints"]}
        new_isc.append(entry)
    t["interStageConnections"] = new_isc

    # --- stage port bindings + control stages --------------------------------
    for st in t["stages"]:
        if st.get("role") == "control":
            if "circuit" in st:
                st.pop("circuit")
                stats["control_circuits_dropped"] += 1
            st["controlImplementation"] = "virtual"
            st["senses"] = [{"net": s["wire"], "signal": s["signal"]} for s in st["senses"]]
            st["drives"] = [{"stage": owner[d["component"]], "component": d["component"],
                             "signal": d["signal"]} for d in st["drives"]]
            continue
        c = circ[st["name"]]
        def bind(p):
            port = p["wire"]
            if not any(x["name"] == port for x in c["ports"]):
                raise ValueError(f"line {lineno}: stage {st['name']} binds port {port} "
                                 f"but no inter-stage net created it")
            return {"port": port, "type": p["type"]}
        st["inputPort"] = bind(st["inputPort"])
        outs = [bind(p) for p in st.pop("outputPorts")]
        if st.get("role") == "isolation":
            st["outputPorts"] = outs
        else:
            if len(outs) != 1:
                raise ValueError(f"line {lineno}: non-isolation stage {st['name']} has "
                                 f"{len(outs)} output ports")
            st["outputPort"] = outs[0]

    return r, stats


def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    resources = []
    for repo in ["TAS", "CIAS", "PEAS", "MAS", "CAS", "SAS", "RAS", "AAS", "CTAS", "CONAS", "COAS"]:
        sdir = WORKSPACE / repo / "schemas"
        for p in sdir.rglob("*.json"):
            doc = json.loads(p.read_text())
            if "$id" in doc:
                resources.append((doc["$id"], Resource.from_contents(doc, default_specification=DRAFT202012)))
    registry = Registry().with_resources(resources)
    tas = json.loads((REPO / "schemas" / "TAS.json").read_text())
    return Draft202012Validator(tas, registry=registry)


def main():
    lines = DATA.read_text().splitlines()
    migrated, totals = [], {}
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        rec, stats = migrate_record(json.loads(line), i)
        migrated.append(rec)
        for k, v in stats.items():
            totals[k] = totals.get(k, 0) + v

    validator = build_validator()
    failures = 0
    for i, rec in enumerate(migrated, 1):
        errs = list(validator.iter_errors(rec))
        if errs:
            failures += 1
            print(f"INVALID record {i}: {errs[0].json_path}: {errs[0].message[:140]}")
    if failures:
        print(f"\nABORT: {failures}/{len(migrated)} migrated records invalid - nothing written")
        sys.exit(1)

    bak = DATA.with_suffix(".ndjson.bak")
    bak.write_text("\n".join(lines) + "\n")
    tmp = DATA.with_suffix(".ndjson.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in migrated))
    os.replace(tmp, DATA)
    print(f"migrated {len(migrated)} records -> {DATA.name} (backup: {bak.name})")
    print("stats:", totals)
    if totals.get("placeholder_uris"):
        print(f"NOTE: {totals['placeholder_uris']} placeholder component URIs remain "
              f"(?placeholder=...) - librarian backlog, unchanged by this migration.")


if __name__ == "__main__":
    main()
