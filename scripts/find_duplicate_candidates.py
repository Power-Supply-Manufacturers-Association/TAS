#!/usr/bin/env python3
"""
Cross-catalogue duplicate-candidate builder (jev_integration_plan.md section B,
item 12: "Duplicates across ~960k parts: group by manufacturer and part-number
stem, then Jev decides same part / packaging variant / different part").

READ-ONLY. Streams data/*.ndjson (all live catalogues except circuits,
converters, quarantine and fabricated_denylist -- those have no
manufacturer+partNumber identity, or are not real catalogue rows). Never
loads a whole file into memory, never writes to data/.

Reuses (does not replace) scripts/audit_corpus_duplicates_integrity.py's
identity extraction: CATALOG_SPECS, extract_identity, norm_mfr, norm_pn,
is_placeholder, fetch_lines. Extends it with:

  * four catalogues missing from CATALOG_SPECS (relays, switches,
    potentiometers, connector_accessories -- same "(discriminator, None)"
    shape, confirmed by sampling).
  * a manufacturer alias table on top of norm_mfr(), collapsing legal-suffix
    spelling variants (Vishay Siliconix -> vishay) AND known corporate
    consolidations that legitimately produce the same part under two names
    in our data (EPCOS -> tdk, Intersil -> renesas, Maxim Integrated ->
    analog devices, Microsemi -> microchip, IXYS -> littelfuse).
  * a conservative part-number STEM: strip at most two trailing tokens that
    match a whitelisted set of generic tape/reel/RoHS packaging codes
    (TR, PBF, LF, HF, G, E3, E4, GS08, ...), requiring a delimiter for most
    tokens and only allowing a handful of very distinctive tokens (PBF, LF,
    HF, TR) to be stripped when glued directly onto the base string.
    Bare reel-size digit suffixes ("-7", "-13") are only stripped for the
    semiconductor catalogues (mosfets/diodes/igbts/bjts), where that scheme
    is documented (Vishay, Diodes Inc.); passives keep their trailing digits
    untouched (there they usually encode tolerance/voltage, not packaging).
    The suffix removed is always kept alongside the stem for the pair record
    -- nothing is discarded, only used for grouping.

Two categories of duplicate candidate, per the plan:
  * EXACT: same (manufacturer_norm, partNumber_norm) appearing more than
    once (within one catalogue or across catalogues). Decided by CODE --
    "same_part", no Jev call (task rule 3).
  * STEM: same (manufacturer_norm, stem) with more than one DISTINCT
    partNumber_norm. Ambiguous -- goes to Jev as same_part / packaging_variant
    / different_part (task rule 4). Stem groups larger than
    --stem-group-cap distinct part numbers are NOT paired (a group that big
    almost always means the stem heuristic over-stripped); they are logged
    to oversized_stem_groups.json for manual review instead.

Output (under --out-dir, default staging/jev/):
  manufacturer_census.json   raw manufacturer name -> count, canonicalisation table used
  oversized_stem_groups.json stem groups skipped for being too large
  pairs.jsonl                one candidate pair per line, ready for the code/Jev decision
                              stage (scripts/jev_duplicate_pass.py); each pair carries both
                              rows' key fields (extracted, not the whole record) plus a
                              provenance_score for each side.
  candidate_summary.json     counts per catalogue, for the report

Usage:
    python3 scripts/find_duplicate_candidates.py [--out-dir staging/jev] [--stem-group-cap 10]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")

sys.path.insert(0, SCRIPTS_DIR)
import audit_corpus_duplicates_integrity as _audit  # noqa: E402
from audit_corpus_duplicates_integrity import (  # noqa: E402
    CATALOG_SPECS,
    extract_identity,
    fetch_lines,
    is_placeholder,
    norm_mfr,
    norm_pn,
)

# Catalogues audit_corpus_duplicates_integrity.py does not cover, but which
# are live part catalogues with the same "(discriminator, None)" shape --
# confirmed 2026-09-26 by sampling data/{relays,switches,potentiometers,
# connector_accessories}.ndjson's first record.
EXTRA_CATALOG_SPECS = {
    "relays": ("relay", None),
    "switches": ("switch", None),
    "potentiometers": ("potentiometer", None),
    "connector_accessories": ("connectorAccessory", None),
}
ALL_CATALOG_SPECS = {**CATALOG_SPECS, **EXTRA_CATALOG_SPECS}
# extract_identity() (imported above) reads the module-level CATALOG_SPECS by
# name, not a parameter -- extend the audit module's own dict in place so it
# recognises the four extra catalogues too. Does not modify the file on disk.
_audit.CATALOG_SPECS.update(EXTRA_CATALOG_SPECS)

# Catalogues explicitly out of scope per the task: circuits (CIAS bricks, no
# manufacturer+partNumber identity), converters (full TAS documents),
# quarantine (already-flagged rows) and fabricated_denylist (known-bad, kept
# only as a negative list).
EXCLUDED = {"circuits", "converters", "quarantine", "fabricated_denylist"}

# ---------------------------------------------------------------------------
# Manufacturer canonicalisation: norm_mfr() (casefold + whitespace collapse,
# from the audit script) first, then strip a generic corporate-form suffix,
# then apply the alias table below for known spelling variants and
# consolidations. Conservative: only entities we can name with confidence.
# ---------------------------------------------------------------------------
_CORP_SUFFIX_RE = re.compile(
    r",?\s*(incorporated|inc\.?|corporation|corp\.?|co\.,?\s*ltd\.?|co\.?\s*ltd\.?|"
    r"electronics co\.,?\s*ltd\.?|llc|gmbh|s\.a\.|ag|ltd\.?|company)\.?\s*$",
    re.IGNORECASE,
)

MFR_ALIASES = {
    # spelling / division variants of the same legal entity
    "vishay siliconix": "vishay",
    "vishay dale": "vishay",
    "vishay intertechnology": "vishay",
    "vishay general semiconductor - diodes division": "vishay",
    "alpha and omega": "aos",
    "alpha & omega semiconductor": "aos",
    "alpha and omega semiconductor": "aos",
    "micro commercial components": "mcc",
    "micro commercial components (mcc)": "mcc",
    "mcc (micro commercial components)": "mcc",
    "on semiconductor": "onsemi",
    "onsemi": "onsemi",
    "stmicroelectronics": "st",
    "st microelectronics": "st",
    "texas instruments": "ti",
    "sanken electric usa": "sanken electric",
    "wurth elektronik": "wurth elektronik",  # diacritic-stripped form, see normalize()
    "renesas / intersil": "renesas",
    "renesas electronics": "renesas",
    # corporate consolidations that legitimately re-badge the SAME part
    "intersil": "renesas",             # Renesas acquired Intersil, 2017
    "maxim integrated": "analog devices",  # ADI acquired Maxim, 2021
    "microsemi": "microchip",          # Microchip acquired Microsemi, 2018
    "ixys": "littelfuse",              # Littelfuse acquired IXYS, 2018
    "ixys integrated circuits": "littelfuse",
    "ixys/littelfuse": "littelfuse",
    "epcos": "tdk",                    # EPCOS became "TDK Electronics", 2018
}


def normalize_mfr(raw):
    n = norm_mfr(raw)
    if n is None:
        return None
    n = n.replace("&", "and")
    n = n.replace("ü", "u")  # ü -> u, so Würth variants line up
    stripped = _CORP_SUFFIX_RE.sub("", n).strip()
    candidate = stripped or n
    return MFR_ALIASES.get(candidate, MFR_ALIASES.get(n, candidate))


# ---------------------------------------------------------------------------
# Part-number stem: strip at most two trailing packaging/RoHS tokens.
# ---------------------------------------------------------------------------
DELIM_RE = re.compile(r"[-_/\s]+")

# Tokens only stripped when a delimiter precedes them (dash/underscore/slash/
# space) -- generic industry tape-reel & RoHS packaging codes.
DELIM_SUFFIX_TOKENS = {
    "TR", "T", "R", "RL", "CT", "TA", "TB", "REEL", "BULK", "TUBE", "AMMO",
    "PBF", "LF", "HF", "ROHS", "G", "G1", "G2", "G3",
    "GS08", "GS18", "GS27", "GS33", "E3", "E4", "E1", "GE3", "GE4",
    "ND", "CUT", "TAPE",
}
# Reel-size digit codes (Vishay / Diodes Inc. convention: "-7", "-13", "-16",
# "-18") -- only meaningful, and only applied, for the semiconductor
# catalogues; passives use trailing digits for tolerance/voltage, not reels.
DELIM_REEL_DIGITS = {"7", "13", "16", "18"}
SEMICONDUCTOR_CATALOGS = {"mosfets", "diodes", "igbts", "bjts"}

# A handful of tokens distinctive enough to strip even with no delimiter
# (e.g. "IRF740PBF"). Deliberately short list -- single letters are NOT here,
# they are too likely to be part of the real base part number.
GLUED_SUFFIX_TOKENS = ("PBF", "LF", "HF", "TR")


def compute_stem(pn_norm, catalog):
    """Returns (stem, suffixes_removed_list). pn_norm is already norm_pn()'d
    (casefold, whitespace-collapsed) by the caller."""
    if not pn_norm:
        return pn_norm, []
    s = pn_norm.upper()
    tokens = [t for t in DELIM_RE.split(s) if t]
    removed = []
    # pop up to 2 trailing delimited tokens
    for _ in range(2):
        if len(tokens) <= 1:
            break
        last = tokens[-1]
        if last in DELIM_SUFFIX_TOKENS:
            removed.append(tokens.pop())
            continue
        if catalog in SEMICONDUCTOR_CATALOGS and last in DELIM_REEL_DIGITS:
            removed.append(tokens.pop())
            continue
        break
    base = "".join(tokens)
    # one glued strip on what remains
    for tok in GLUED_SUFFIX_TOKENS:
        if base.endswith(tok) and len(base) - len(tok) >= 4:
            base = base[: -len(tok)]
            removed.append(tok)
            break
    return base, removed


# ---------------------------------------------------------------------------
# Provenance scoring -- a comparative heuristic ("which row has better
# provenance"), not an authoritative measure. Higher is better.
# ---------------------------------------------------------------------------
def _count_leaves(obj):
    if isinstance(obj, dict):
        return sum(_count_leaves(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_count_leaves(v) for v in obj)
    return 0 if obj is None else 1


def _find_all(obj, key):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            out.extend(_find_all(v, key))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_find_all(v, key))
    return out


def provenance_score(record):
    score = _count_leaves(record)
    for prov in _find_all(record, "provenance"):
        if isinstance(prov, list):
            for p in prov:
                if isinstance(p, dict):
                    score += 5
                    if p.get("sourceUrl"):
                        score += 3
    for url in _find_all(record, "datasheetUrl"):
        if url:
            score += 5
    for status in _find_all(record, "status"):
        if isinstance(status, str) and status.lower() in ("active", "in production"):
            score += 2
        elif isinstance(status, str) and status.lower() in ("obsolete", "nrnd", "discontinued"):
            score -= 5
    return score


# ---------------------------------------------------------------------------
# Key-field extraction for the Jev state / worklist (not the whole record).
# ---------------------------------------------------------------------------
def unwrap_inner(catalog, record):
    disc_key, subtype_set = ALL_CATALOG_SPECS[catalog]
    wrapper_val = record.get(disc_key)
    if not isinstance(wrapper_val, dict):
        return None, None
    if subtype_set is None:
        return wrapper_val, None
    for k, v in wrapper_val.items():
        if k in subtype_set and isinstance(v, dict):
            return v, k
    return None, None


def key_fields(catalog, subtype, record):
    inner, _ = unwrap_inner(catalog, record)
    mi = (inner or {}).get("manufacturerInfo") or {}
    part_number = None
    di = mi.get("datasheetInfo")
    if isinstance(di, dict):
        part = di.get("part")
        if isinstance(part, dict):
            part_number = part.get("partNumber")
    return {
        "catalog": catalog,
        "subtype": subtype,
        "manufacturer": mi.get("name"),
        "reference": mi.get("reference"),
        "partNumber_field": part_number,
        "family": mi.get("family"),
        "description": mi.get("description"),
        "status": mi.get("status"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(REPO_ROOT, "staging", "jev"))
    ap.add_argument("--stem-group-cap", type=int, default=10,
                     help="max distinct partNumbers in a stem group before it is "
                          "skipped as likely over-stripped (default 10)")
    ap.add_argument("--exact-group-cap", type=int, default=12,
                     help="max occurrences of one (mfr,pn) key to pair up (default 12)")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    t0 = time.time()
    catalogs = sorted(set(ALL_CATALOG_SPECS) - EXCLUDED)
    print(f"Catalogues in scope ({len(catalogs)}): {catalogs}", file=sys.stderr)

    census = Counter()
    per_catalog_counts = {}
    # index_exact: (mfr_norm, pn_norm) -> [(catalog, line_no, subtype), ...]
    index_exact = defaultdict(list)
    # index_stem: (mfr_norm, catalog_bucket_stem_key) -> {pn_norm: (catalog, line_no, subtype, suffixes)}
    # NOTE: stem key does NOT include catalog, so cross-catalogue stem matches
    # are found too (e.g. same part re-catalogued under a different family).
    index_stem = defaultdict(dict)
    per_catalog_path = {}

    for catalog in catalogs:
        path = os.path.join(DATA_DIR, f"{catalog}.ndjson")
        if not os.path.exists(path):
            print(f"  [skip] {catalog}: file not found", file=sys.stderr)
            continue
        per_catalog_path[catalog] = path
        ok = skipped = 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                raw = line.rstrip("\n")
                if not raw.strip():
                    continue
                try:
                    record = json.loads(raw)
                except Exception:
                    skipped += 1
                    continue
                status, mfr_raw, pn_raw, subtype, _note = extract_identity(catalog, record)
                if status != "OK":
                    skipped += 1
                    continue
                census[mfr_raw] += 1
                mfr_n = normalize_mfr(mfr_raw)
                pn_n = norm_pn(pn_raw)
                if mfr_n is None or pn_n is None or is_placeholder(pn_n):
                    skipped += 1
                    continue
                ok += 1
                index_exact[(mfr_n, pn_n)].append((catalog, line_no, subtype))
                stem, suffixes = compute_stem(pn_n, catalog)
                skey = (mfr_n, stem)
                # keep first occurrence per distinct pn_norm under this stem
                index_stem[skey].setdefault(pn_n, (catalog, line_no, subtype, suffixes))
        per_catalog_counts[catalog] = {"total_lines": line_no if 'line_no' in dir() else None,
                                        "ok": ok, "skipped_no_identity": skipped}
        print(f"  [{catalog}] ok={ok} skipped={skipped}", file=sys.stderr)

    exact_groups = {k: v for k, v in index_exact.items() if len(v) > 1}
    stem_groups_all = {k: v for k, v in index_stem.items() if len(v) > 1}

    stem_groups = {}
    oversized = []
    for k, members in stem_groups_all.items():
        if len(members) > args.stem_group_cap:
            oversized.append({"manufacturer_norm": k[0], "stem": k[1],
                               "distinct_partnumbers": len(members),
                               "sample_partnumbers": list(members.keys())[:15]})
        else:
            stem_groups[k] = members

    with open(os.path.join(args.out_dir, "oversized_stem_groups.json"), "w") as f:
        json.dump(oversized, f, indent=2)

    # ---- fetch full records needed to build pairs (exact groups capped at
    # --exact-group-cap occurrences; stem groups already capped above) ----
    wanted_by_catalog = defaultdict(set)
    for k, occ in exact_groups.items():
        for c, ln, _st in occ[: args.exact_group_cap]:
            wanted_by_catalog[c].add(ln)
    for k, members in stem_groups.items():
        for pn_n, (c, ln, st, suf) in members.items():
            wanted_by_catalog[c].add(ln)

    fetched = {}
    for c, lns in wanted_by_catalog.items():
        fetched[c] = fetch_lines(per_catalog_path[c], lns)
        print(f"  [fetch] {c}: {len(lns)} lines", file=sys.stderr)

    def get_record(c, ln):
        raw = fetched.get(c, {}).get(ln)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    pairs_path = os.path.join(args.out_dir, "pairs.jsonl")
    n_exact_pairs = n_stem_pairs = 0
    with open(pairs_path, "w") as out:
        # EXACT: canonical = best provenance score, pair against every other occurrence
        for (mfr_n, pn_n), occ in exact_groups.items():
            occ = occ[: args.exact_group_cap]
            scored = []
            for c, ln, st in occ:
                rec = get_record(c, ln)
                if rec is None:
                    continue
                scored.append((provenance_score(rec), c, ln, st, rec))
            if len(scored) < 2:
                continue
            scored.sort(key=lambda t: -t[0])
            canon = scored[0]
            for other in scored[1:]:
                pair = {
                    "pair_id": f"exact:{mfr_n}|{pn_n}|{canon[1]}:{canon[2]}|{other[1]}:{other[2]}",
                    "type": "exact",
                    "manufacturer_norm": mfr_n,
                    "partNumber_norm": pn_n,
                    "row_a": {"catalog": canon[1], "line": canon[2],
                              **key_fields(canon[1], canon[3], canon[4]),
                              "provenance_score": canon[0]},
                    "row_b": {"catalog": other[1], "line": other[2],
                              **key_fields(other[1], other[3], other[4]),
                              "provenance_score": other[0]},
                }
                out.write(json.dumps(pair) + "\n")
                n_exact_pairs += 1

        # STEM: all pairs among distinct partNumbers sharing (mfr, stem)
        for (mfr_n, stem), members in stem_groups.items():
            items = list(members.items())
            recs = []
            for pn_n, (c, ln, st, suf) in items:
                rec = get_record(c, ln)
                if rec is None:
                    continue
                recs.append((pn_n, c, ln, st, suf, rec, provenance_score(rec)))
            for i in range(len(recs)):
                for j in range(i + 1, len(recs)):
                    a = recs[i]
                    b = recs[j]
                    pair = {
                        "pair_id": f"stem:{mfr_n}|{stem}|{a[1]}:{a[2]}|{b[1]}:{b[2]}",
                        "type": "stem",
                        "manufacturer_norm": mfr_n,
                        "stem": stem,
                        "row_a": {"catalog": a[1], "line": a[2], "partNumber_norm": a[0],
                                  "suffix_removed": a[4], **key_fields(a[1], a[3], a[5]),
                                  "provenance_score": a[6]},
                        "row_b": {"catalog": b[1], "line": b[2], "partNumber_norm": b[0],
                                  "suffix_removed": b[4], **key_fields(b[1], b[3], b[5]),
                                  "provenance_score": b[6]},
                    }
                    out.write(json.dumps(pair) + "\n")
                    n_stem_pairs += 1

    with open(os.path.join(args.out_dir, "manufacturer_census.json"), "w") as f:
        json.dump({"raw_name_counts": census.most_common(),
                   "note": "canonicalisation is normalize_mfr() in this script "
                           "(norm_mfr + corp-suffix strip + MFR_ALIASES)"}, f, indent=2)

    summary = {
        "catalogs_in_scope": catalogs,
        "per_catalog": per_catalog_counts,
        "exact_dup_keys": len(exact_groups),
        "exact_pairs_written": n_exact_pairs,
        "stem_groups_total": len(stem_groups_all),
        "stem_groups_paired": len(stem_groups),
        "stem_groups_oversized_skipped": len(oversized),
        "stem_pairs_written": n_stem_pairs,
        "wall_seconds": round(time.time() - t0, 1),
    }
    with open(os.path.join(args.out_dir, "candidate_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
