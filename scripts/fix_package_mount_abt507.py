#!/usr/bin/env python3
"""ABT #507 — mechanical.assemblyType contradicting the package named in
mechanical.case, and the fabricated SiC-diode ladder that exposed it.

    python3 scripts/fix_package_mount_abt507.py [--dry-run]

Two phases, in this order:

A. QUARANTINE the "wave 2" SiC-diode ladder. The 58 TO-252/tht diodes named in
   the ticket are not mislabelled real parts — they are one third of a 174-record
   synthetic block (commit 334fb83, an untracked bulk agent add) in which every
   stored value is a closed-form function of the row index:

       surgeCurrent = 20*If          forwardVoltageAt = If//2
       powerDissipation = 0.8*Vf*If  thermalResistanceJunctionCase = max(C/If, floor)
       Vf = base + 0.01*k(If)        (k depends on If ALONE — identical across all
                                      three package variants and both voltage classes)

   with a contiguous 2,3,4..30 A ladder repeated for 600 V and 1200 V in each of
   three "packages" (DPAK/smt, TO-252/tht, SO-8/smt) that share one 10x8x4 mm body
   and one die. Real Infineon SiC Schottky numbering puts the voltage class in the
   token (IDH06S60C, IDH03G65C6, IDH05G120C5 -> 60/65/120); this block minted
   10C/11C/12C/20C/21C/22C, and its Vf 0.66..0.82 V is silicon-Schottky, not the
   1.3..1.8 V the genuine IDH*G* records in the same file carry.

   Correcting assemblyType on such a record would launder it: cross-reference would
   go on offering a nonexistent 30 A 1200 V DPAK as a *good* substitute. Quarantine
   preserves both the record and the finding.

B. CORRECT mechanical.assemblyType wherever it contradicts the package outline, in
   every semiconductor catalogue. These ARE real vendor-sourced parts (Nexperia, ST,
   onsemi, Toshiba, ROHM, TI, IXYS, ...) whose mount class was simply written wrong;
   the value is definitional, so the repair is mechanical.

   The set of records to repair and the value to write are both read back out of
   Blade Runner's own GEN_PACKAGE_MOUNT finding rather than from a second table
   here, so the data and the gate cannot drift apart.

   "smt" and "tht" are the same length, so phase B is a byte-for-byte in-place
   patch of the affected lines — the file is never rewritten and concurrent
   appends are untouched.
"""
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "validator" / "build"))

from blade_gate import BladeGate  # noqa: E402
import tas_validator  # noqa: E402

DATE = "2026-08-02"

# Phase A -------------------------------------------------------------------
DIODES = REPO / "data" / "diodes.ndjson"
QUAR = REPO / "data" / "diodes.quarantine_fabricated.ndjson"
AUDIT = REPO / "staging" / "abt507_package_mount_audit.json"
LADDER = re.compile(r"^IDH\d{2}S[GO]?(?:1[0-2]|2[0-2])C$")
REASON = ("fabricated part - synthetic SiC-diode ladder added by the untracked "
          "'wave 2' bulk agent add (commit 334fb83), not sourced from a datasheet "
          "(ABT #507). Every field is a closed-form function of the row index and "
          "the 2..30 A ladder is contiguous across three package variants of one "
          "die; the IDH..S/SG/SO + 10C/11C/12C/20C/21C/22C numbering matches no "
          "Infineon product (real tokens are 60/65/120).")
CODES = ["GEN_FABRICATED_MPN"]

# Phase B -------------------------------------------------------------------
FILES = {
    "diodes.ndjson": ("semiconductor", "diode"),
    "igbts.ndjson": ("semiconductor", "igbt"),
    "mosfets.ndjson": ("semiconductor", "mosfet"),
    "bjts.ndjson": ("semiconductor", "bjt"),
}
# "mechanical.case 'TO-252' is a smt package outline but mechanical.assemblyType is 'tht'"
WANTED = re.compile(r"is a (smt|tht) package outline")


def codes_of(record):
    """IMPOSSIBLE check codes Blade Runner reports for a record."""
    v = tas_validator.validate(json.dumps(record))
    return {f.code for f in v.findings if str(f.severity) == "IMPOSSIBLE"}


def package_mount_fix(record):
    """The mount Blade Runner says this record's package outline requires, or None."""
    v = tas_validator.validate(json.dumps(record))
    for f in v.findings:
        if f.code == "GEN_PACKAGE_MOUNT":
            m = WANTED.search(f.message)
            if m is None:
                raise RuntimeError(f"unparseable GEN_PACKAGE_MOUNT message: {f.message}")
            return m.group(1)
    return None


def phase_a(dry):
    """Move the wave-2 SiC ladder out of diodes.ndjson."""
    tmp = DIODES.with_suffix(".ndjson.tmp")
    taken, kept, out_q = [], 0, []
    with open(DIODES, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            rec = json.loads(raw) if b'"IDH' in raw else None
            info = (rec or {}).get("semiconductor", {}).get("diode", {}).get("manufacturerInfo", {})
            ids = [str(info.get("reference", "")),
                   str(info.get("datasheetInfo", {}).get("part", {}).get("partNumber", ""))]
            if rec is not None and any(LADDER.match(i) for i in ids):
                rec["_validatorQuarantine"] = {
                    "date": DATE, "reason": REASON, "codes": CODES,
                    "messages": [f"part number '{ids[0]}' matches the wave2 SiC diode ladder "
                                 f"generator template — it was invented, not sourced"],
                }
                out_q.append(json.dumps(rec, separators=(",", ":")))
                taken.append({"reference": ids[0],
                              "case": info["datasheetInfo"]["mechanical"].get("case"),
                              "assemblyType": info["datasheetInfo"]["mechanical"].get("assemblyType")})
            else:
                out.write(raw)
                kept += 1
        out.flush()
        os.fsync(out.fileno())

    print(f"phase A: quarantining {len(taken)} fabricated rows, keeping {kept}")
    if dry:
        tmp.unlink(missing_ok=True)
        return taken
    with open(QUAR, "a", encoding="utf-8") as q:
        for line in out_q:
            q.write(line + "\n")
        q.flush()
        os.fsync(q.fileno())
    os.replace(tmp, DIODES)
    print(f"         appended -> {QUAR.name}")
    return taken


def phase_b(dry):
    """Byte-patch assemblyType wherever it contradicts the package outline."""
    fixed, blocked = [], []
    for name, disc in FILES.items():
        path = REPO / "data" / name
        if not path.is_file():
            continue
        gate = BladeGate(disc)
        patches = []  # (byte offset of the assemblyType value, new mount)
        with open(path, "rb") as fh:
            offset = 0
            for raw in fh:
                if b'"assemblyType"' in raw:
                    rec = json.loads(raw)
                    comp = rec
                    for k in disc:
                        comp = comp.get(k, {})
                    want = package_mount_fix(rec) if comp else None
                    if want:
                        info = comp["manufacturerInfo"]
                        mech = info["datasheetInfo"]["mechanical"]
                        before = codes_of(rec)
                        mech["assemblyType"] = want
                        after = codes_of(rec)
                        new = after - before
                        if new:
                            blocked.append({"file": name, "reference": info.get("reference"),
                                            "newImpossible": sorted(new)})
                        else:
                            gate.check(comp)  # count it in the gate's tally
                            # smt/tht are the same width -> patch the value in place.
                            rel = raw.index(b'"assemblyType"')
                            val = raw.index(b'"', raw.index(b":", rel) + 1) + 1
                            assert raw[val:val + 3] in (b"smt", b"tht"), raw[val:val + 3]
                            patches.append((offset + val, want.encode()))
                            fixed.append({"file": name, "reference": info.get("reference"),
                                          "case": mech.get("case"), "assemblyType": want})
                offset += len(raw)
        if patches and not dry:
            with open(path, "r+b") as fh:
                for at, val in patches:
                    fh.seek(at)
                    fh.write(val)
                fh.flush()
                os.fsync(fh.fileno())
        print(f"phase B: {name:16} {len(patches):4} assemblyType values patched"
              f"{'  (dry-run)' if dry else ''}")
        print(f"         {gate.summary()}")
    return fixed, blocked


def main(argv):
    dry = "--dry-run" in argv
    taken = phase_a(dry)
    fixed, blocked = phase_b(dry)
    print(f"\nquarantined {len(taken)}   assemblyType corrected {len(fixed)}   "
          f"left alone (would introduce a new IMPOSSIBLE) {len(blocked)}")
    for b in blocked:
        print("  BLOCKED", b)
    if not dry:
        AUDIT.parent.mkdir(exist_ok=True)
        AUDIT.write_text(json.dumps(
            {"ticket": "ABT #507", "date": DATE, "quarantineFile": QUAR.name,
             "reason": REASON, "codes": CODES, "quarantined": taken,
             "assemblyTypeCorrected": fixed, "blocked": blocked}, indent=1))
        print(f"audit -> {AUDIT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
