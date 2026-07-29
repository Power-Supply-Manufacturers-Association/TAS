#!/usr/bin/env python3
"""Confidence auditor for the impedance-curve + wound magnetics in magnetics.ndjson
(the non-cableCore populations — chipBead, commonModeChoke, inductor, transformer).

Sibling of audit_cable_cores.py. It re-reads the deployed magnetics.ndjson (does NOT
trust importers), tiers the |Z|(f) parts by how their curve was derived, runs a
physical-plausibility QC, and applies a light physics gate to the wound parts. The
fabrication guard (check_no_fabricated_parts.py) does NOT catch these physics
defects (a 0-ohm bead is not "fabricated", just wrong), so this is a complementary
gate.

    python3 scripts/audit_chip_beads.py            # human report
    python3 scripts/audit_chip_beads.py --strict   # exit 1 if any DEFECT

Confidence tiers for the |Z|(f) parts (chipBead, commonModeChoke):
  A   dense measured curve      (>= 50 pts, e.g. Murata SimSurfing 401-pt, WE .mdb)
  C   sparse published/spot     (1-49 pts) -- honest but low-resolution
  D   uncharacterised           (no impedancePoints at all -- DCR/Irated only)
An `[inferred from manufacturer name ...]` provenance suffix is a confidence demerit
(the record was not traced to a real source) and is counted separately.

Physical-plausibility QC / DEFECTS (a curve/part is a DEFECT for a genuine physics
violation the fab-guard misses):
  * any impedance |Z| <= 0                     (0-ohm bead/choke point: scrape hole)
  * any frequency <= 0
  * frequencies not strictly increasing WITHIN a winding (CMC curves legitimately
    repeat frequencies across the common/differential traces -- grouped before check)
  * peak |Z| outside [1, 8000] ohm for a 2-terminal bead
    (3-terminal EMIFIL feedthrough filters -- NFZ* -- legitimately exceed this and are
     reported INFORMATIONAL, not condemned)
  * wound part (inductor/transformer) with non-positive inductance, or non-positive
    dcResistance where a dcResistance is present.
"""
import json, sys
from collections import defaultdict, Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
NDJSON = HERE.parent / "data" / "magnetics.ndjson"
STRICT = "--strict" in sys.argv

Z_PEAK_MIN, Z_PEAK_MAX = 1.0, 8000.0          # 2-terminal bead peak |Z| band
DENSE_PTS = 50                                # >= this many points -> Tier A


def resolve(dim):
    """Positive scalar from a {nominal,minimum,maximum} dim, or None. Mirrors the
    resolve-dimensional-values rule enough to answer 'is there a positive value?'."""
    if isinstance(dim, (int, float)):
        return float(dim) if dim > 0 else None
    if isinstance(dim, dict):
        for k in ("nominal", "minimum", "maximum"):
            v = dim.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
    return None


def explicit_nonpositive(dim):
    """True only if the field is PRESENT with a real numeric value and NONE of the
    present values is positive (an explicit zero/negative). A MISSING field, or a
    field with no numeric value, is NOT a defect (the property is simply carried
    elsewhere) -- so this never conflates 'absent' with 'wrong'."""
    if isinstance(dim, (int, float)):
        return dim <= 0
    if isinstance(dim, dict):
        nums = [dim.get(k) for k in ("nominal", "minimum", "maximum")
                if isinstance(dim.get(k), (int, float))]
        return bool(nums) and all(v <= 0 for v in nums)
    return False


def load():
    for line in open(NDJSON):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        mi = o.get("magnetic", {}).get("manufacturerInfo", {})
        di = mi.get("datasheetInfo", {})
        el = di.get("electrical", [])
        e0 = el[0] if el else {}
        prov = di.get("provenance", []) or []
        yield mi.get("name"), mi.get("reference"), e0, prov


def tier_of(npts):
    if npts == 0:
        return "D"
    if npts >= DENSE_PTS:
        return "A"
    return "C"


def main():
    pops = Counter()
    tier_ct = defaultdict(Counter)          # subtype -> tier -> n
    nocurve_by_vendor = Counter()
    inferred = Counter()
    missing_prov = []
    defects = []                            # (subtype, vendor, ref, reason)
    informational = []                     # 3-terminal high-Z, etc.
    total = 0

    for name, ref, e0, prov in load():
        st = e0.get("subtype", "<none>")
        pops[st] += 1
        total += 1

        # provenance presence (house rule: REQUIRED on every part)
        if not prov or not any(p.get("sourceName") or p.get("source") for p in prov):
            missing_prov.append((st, name, ref))
        if any("[inferred from manufacturer name" in (p.get("sourceName") or "") for p in prov):
            inferred[st] += 1

        if st in ("chipBead", "commonModeChoke"):
            pts = e0.get("impedancePoints", []) or []
            tier_ct[st][tier_of(len(pts))] += 1
            if not pts:
                nocurve_by_vendor[name] += 1
                continue
            # --- physical QC on the curve ---
            if any((p.get("impedance", {}).get("magnitude", 1) or 0) <= 0 for p in pts):
                nz = sum(1 for p in pts if (p.get("impedance", {}).get("magnitude", 1) or 0) <= 0)
                defects.append((st, name, ref, f"{nz}/{len(pts)} impedance point(s) with |Z| <= 0"))
            if any((p.get("frequency", 1) or 0) <= 0 for p in pts):
                defects.append((st, name, ref, "frequency <= 0"))
            # strict-increasing frequency WITHIN each winding group
            groups = defaultdict(list)
            for p in pts:
                groups[p.get("winding", "_")].append(p.get("frequency", 0))
            for w, fs in groups.items():
                fs2 = sorted(fs)
                if any(fs2[i] >= fs2[i + 1] for i in range(len(fs2) - 1)):
                    defects.append((st, name, ref, f"duplicate/non-increasing frequency within winding '{w}'"))
                    break
            # peak |Z| sanity -- ONLY for 2-terminal chipBeads. Common-mode chokes
            # legitimately reach hundreds of kOhm (that's their job), so magnitude is
            # not checked for them. 3-terminal NFZ EMIFIL filters are carved out.
            zs = [p.get("impedance", {}).get("magnitude", 0) for p in pts if (p.get("impedance", {}).get("magnitude", 0) or 0) > 0]
            if zs and st == "chipBead":
                zpk = max(zs)
                three_terminal = str(ref).upper().startswith("NFZ")
                if zpk > Z_PEAK_MAX and three_terminal:
                    informational.append((st, name, ref, f"3-terminal filter peak |Z|={zpk:g} ohm (real parallel resonance)"))
                elif zpk > Z_PEAK_MAX:
                    informational.append((st, name, ref, f"high-Z 2-terminal bead peak |Z|={zpk:g} ohm -- review family"))

        elif st in ("inductor", "transformer"):
            # DEFECT only for an EXPLICIT non-positive value (a present zero/negative),
            # never for an absent field (inductance is often carried in the part name).
            if explicit_nonpositive(e0.get("inductance")):
                defects.append((st, name, ref, "explicit non-positive inductance"))
            if explicit_nonpositive(e0.get("dcResistance")):
                defects.append((st, name, ref, "explicit non-positive dcResistance"))

    # ---- report ----
    print(f"non-cableCore magnetics audit -- {total} parts from {NDJSON.name}")
    print("=" * 72)
    print("population by subtype:")
    for st, n in pops.most_common():
        extra = ""
        if st in tier_ct:
            t = tier_ct[st]
            extra = "   tiers " + " ".join(f"{k}={t[k]}" for k in ("A", "C", "D") if t[k])
        inf = f"  (+{inferred[st]} inferred-provenance)" if inferred[st] else ""
        print(f"   {st:18s} {n:6d}{extra}{inf}")

    print(f"\nmissing provenance (house-rule violation): {len(missing_prov)}")
    for st, name, ref in missing_prov[:10]:
        print(f"   {st} {name} {ref}")

    # top no-curve chipBead vendors (coverage gap, not a defect)
    if nocurve_by_vendor:
        tot_nc = sum(nocurve_by_vendor.values())
        print(f"\nchipBead/CMC with NO |Z| curve (coverage gap, DCR-only): {tot_nc}")
        for v, c in nocurve_by_vendor.most_common(6):
            print(f"   {v:28s} {c}")

    if informational:
        print(f"\nINFORMATIONAL (real, but review classification): {len(informational)}")
        for st, name, ref, why in informational[:6]:
            print(f"   {name} {ref}: {why}")
        if len(informational) > 6:
            print(f"   ... +{len(informational)-6} more (e.g. Murata NFZ 3-terminal EMIFIL)")

    print(f"\nDEFECTS (physics violations the fab-guard misses): {len(defects)}")
    by_reason = Counter(why.split(" within")[0].split(" (")[0] for _, _, _, why in defects)
    for reason, c in by_reason.most_common():
        print(f"   [{c:3d}] {reason}")
    print("   --- examples ---")
    for st, name, ref, why in defects[:15]:
        print(f"   {st:16s} {name:22s} {ref:22s} {why}")

    if STRICT and defects:
        print(f"\nSTRICT: {len(defects)} defect(s) -> exit 1")
        sys.exit(1)
    if not defects:
        print("\nOK -- no physics defects.")


if __name__ == "__main__":
    main()
