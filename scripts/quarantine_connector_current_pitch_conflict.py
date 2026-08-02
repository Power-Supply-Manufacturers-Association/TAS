#!/usr/bin/env python3
"""ABT #486: split connectors whose per-contact current belongs to a different
contact than their pitch out of data/connectors.ndjson.

THE DEFECT. Vendor parametric feeds publish one "current rating" and one
"centerline" per part. On a hybrid connector those two attributes describe
DIFFERENT contacts — the current is the power terminal's, the centerline is the
signal field's — and the importers copied both into a record whose CONAS scalar
electrical.ratedCurrentPerContact is defined as "maximum continuous current per
contact". TE 5-6450120-9 is the sample Qarlos caught: 42 A against pitch 0.00254
on a header whose own description reads "2.54 mm / 6.35 mm / 7.62 mm Centerline".
The cross-reference ranker read that as a verified 10x current upgrade over a
3.9 A Samtec part, correctly, because nothing in the record says otherwise.

WHY QUARANTINE AND NOT REPAIR. Both numbers are faithful copies of vendor
parametric values; neither is a units slip or a typo. Which contact the current
belongs to is not recoverable from the row, and CONAS's scalar pitch cannot hold
"2.54 and 6.35" — the multi-pitch fact only survives in the description prose. A
record could be repaired by re-sourcing the datasheet and writing the power
contact into contactSystem.contacts[].currentRating (which Blade Runner's
CONN_CURRENT_VS_PITCH accepts as the hybrid declaration), but inventing either
number here would be worse than withholding the part.

The records are moved intact, so the vendor values stay auditable and a
re-sourcing pass can reinstate them.

  quarantine_connector_current_pitch_conflict.py            # dry run
  quarantine_connector_current_pitch_conflict.py --apply
"""
import json
import os
import sys
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "connectors.ndjson"
QUARANTINE = TAS / "data" / "connectors.quarantine_current_pitch_conflict.ndjson"
REASON = "per-contact-current-belongs-to-a-different-contact-than-the-pitch"

# The boundary Blade Runner's CONN_CURRENT_VS_PITCH uses, restated here so the
# split and the gate cannot drift: above 10 A a contact is a power terminal, and a
# power terminal's body does not fit a pitch of 2.54 mm or finer. Measured over all
# 392,346 records: >10 A is 0.04-3.26% of parts at or below 2.54 mm and 21-93%
# above it.
POWER_CONTACT_A = 10.0
POWER_CONTACT_PITCH_M = 2.54e-3


def declares_per_contact_current(connector):
    """A record that states which contact carries the current explains itself and
    is not in conflict — the same exemption CONN_CURRENT_VS_PITCH applies."""
    contacts = ((connector.get("contactSystem") or {}).get("contacts")) or []
    return any(isinstance(c, dict) and isinstance(c.get("currentRating"), (int, float))
               and not isinstance(c.get("currentRating"), bool) for c in contacts)


def in_conflict(rec):
    connector = rec.get("connector")
    if not isinstance(connector, dict):
        return False
    di = ((connector.get("manufacturerInfo") or {}).get("datasheetInfo")) or {}
    current = (di.get("electrical") or {}).get("ratedCurrentPerContact")
    pitch = (di.get("mechanical") or {}).get("pitch")
    for v in (current, pitch):
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return False
    if not (current > POWER_CONTACT_A and 0 < pitch <= POWER_CONTACT_PITCH_M):
        return False
    return not declares_per_contact_current(connector)


def main():
    apply = "--apply" in sys.argv
    if not SRC.exists():
        print(f"no catalogue at {SRC}", file=sys.stderr)
        return 2

    tmp = SRC.with_suffix(".ndjson.abt486.tmp")
    kept = moved = 0
    by_vendor = {}
    # Append to the quarantine file: it is the destination of record for this
    # defect, and a second pass over a re-imported vendor must add to it, never
    # replace what an earlier pass put there.
    mode = "a" if QUARANTINE.exists() else "w"
    with SRC.open(encoding="utf-8") as fin, tmp.open("w", encoding="utf-8") as fkeep, \
            QUARANTINE.open(mode, encoding="utf-8") as fq:
        for line in fin:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # Not ours to drop: keep it byte-identical and let the gate report it.
                fkeep.write(line if line.endswith("\n") else line + "\n")
                kept += 1
                continue
            if not in_conflict(rec):
                fkeep.write(line if line.endswith("\n") else line + "\n")
                kept += 1
                continue
            mi = (rec["connector"].get("manufacturerInfo") or {})
            by_vendor[mi.get("name")] = by_vendor.get(mi.get("name"), 0) + 1
            rec["quarantineReason"] = REASON
            if apply:
                fq.write(json.dumps(rec, ensure_ascii=False) + "\n")
            moved += 1

    if apply:
        os.replace(tmp, SRC)
    else:
        os.remove(tmp)
        if mode == "w":
            QUARANTINE.unlink()  # a dry run leaves no artifact

    print(f"{'APPLIED' if apply else 'DRY RUN'} — ABT #486 current/pitch conflict")
    print(f"  kept {kept}   quarantined {moved} -> {QUARANTINE.name}")
    print("  by vendor: " + ", ".join(f"{k}={v}" for k, v in
                                      sorted(by_vendor.items(), key=lambda kv: -kv[1])))
    if not apply:
        print("\n(dry run — re-run with --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
