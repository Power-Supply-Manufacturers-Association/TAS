#!/usr/bin/env python3
"""ABT #400: route the Würth connectors that lack a per-contact current to the Seeker.

Of the 516 parts CONAS refused for a missing ratedCurrentPerContact, most are genuinely
contactless — D-Sub housings and hoods, lock screws, adhesive markers, pre-crimped wires —
and have no per-contact rating to find. Those are not enrichment candidates and are left
out deliberately; filing them would pad the queue with questions that have no answers.

What IS routed: the terminal blocks and board connectors, where the datasheet checked
publishes only a withstanding voltage because WE states the current rating at SERIES level
(in the series manual) rather than per order code. That is a findable fact, so it belongs
in the queue.

Records are written in the same shape as connectors.quarantine_phoenix_missing_current
.ndjson — everything that IS known, plus quarantineReason — so nothing harvested is lost.

  we_connectors_seeker.py [--apply]
"""
import argparse
import json
import re
import sys
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
STAGED = TAS / "staging" / "we" / "quarantine.ndjson"
OUT = TAS / "data" / "connectors.quarantine_we_missing_current.ndjson"
REASON = "we-series-level-current-rating"
RETRIEVED = "2026-07-31"

# Parts with contacts, whose rating WE publishes at series level.
CONNECTORISH = re.compile(r"^(TBL_|WTB_WR_MPC\d_)|CONTBL", re.I)
# Parts with no contacts at all — nothing to enrich.
NO_CONTACTS = re.compile(r"MARKER|SCREW|PRE_CRIMPED|TERMINALS|HOUSING|HOOD|LAN|CABLE", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from we_connectors_build import category_map, family_of, clean

    cats = category_map()
    keep, skipped = [], 0
    for ln in STAGED.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        q = json.loads(ln)
        if not q["quarantineReason"].startswith("no published rated current"):
            continue
        pl, cells = q["productLine"], q["cells"]
        title = cells.get("_title") or ""
        if NO_CONTACTS.search(pl) or NO_CONTACTS.search(title) or not CONNECTORISH.search(pl):
            skipped += 1
            continue
        fam, _, _ = family_of(pl, cats.get(pl), title)
        if not fam:
            skipped += 1
            continue
        spec = q.get("spec") or {}
        el, env, mech = {}, {}, {}
        for k in ("ratedVoltage", "dielectricWithstandingVoltage", "insulationResistance"):
            if k in spec:
                el[k] = spec[k]
        if "contactResistance" in spec:
            el["contactResistance"] = {"maximum": spec["contactResistance"]}
        if "operatingTemperature" in spec:
            env["operatingTemperature"] = spec["operatingTemperature"]
        if "matingCycles" in spec:
            mech["matingCycles"] = spec["matingCycles"]
        keep.append({"connector": {"manufacturerInfo": {
            "name": "Würth Elektronik", "reference": q["orderCode"], "status": "production",
            "datasheetUrl":
                f"https://www.we-online.com/components/products/datasheet/{q['orderCode']}.pdf",
            "datasheetInfo": {
                "part": {"partNumber": q["orderCode"], "description": title or None,
                         "series": (title.split()[0] if title else None)},
                "electrical": el, "mechanical": mech, "familyDetails": {"family": fam},
                **({"environmental": env} if env else {}),
                "provenance": [{
                    "source": "manufacturerParametric",
                    "sourceName": f"we-online.com product-line table {pl}",
                    "sourceUrl":
                        f"https://www.we-online.com/en/components/products/{pl}#{q['orderCode']}",
                    "retrievedDate": RETRIEVED}]}}},
            "quarantineReason": REASON})
        keep[-1]["connector"]["manufacturerInfo"]["datasheetInfo"]["part"] = {
            k: v for k, v in keep[-1]["connector"]["manufacturerInfo"]["datasheetInfo"]
            ["part"].items() if v}

    print(f"routed to Seeker : {len(keep)}")
    print(f"not routed       : {skipped} (contactless parts — no per-contact rating exists)")
    if not a.apply:
        print("\nDRY RUN — pass --apply to write")
        return 0
    with OUT.open("a", encoding="utf-8") as fh:
        for r in keep:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"appended {len(keep)} -> {OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
