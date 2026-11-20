#!/usr/bin/env python3
"""
Corpus-wide duplicate and cross-catalogue integrity audit (ABT #391 follow-on:
the TDK comment records "8,989 of those rows are DUPLICATES of each other --
a pre-existing data-quality problem worth its own ticket").

READ-ONLY. Streams data/*.ndjson, never loads a whole catalogue into memory,
never writes back to data/. Writes JSON reports under staging/.

Measures, per catalogue and corpus-wide:
  1. Duplicate (manufacturer, partNumber) within a catalogue -- byte-identical,
     provenance-only-diff, or genuinely CONFLICTING on an electrical value.
  2. The same part appearing in more than one catalogue.
  3. Records whose partNumber is empty/null/placeholder, or the same partNumber
     used by different manufacturers.
  4. Rows present in BOTH a live catalogue and data/quarantine.ndjson.
  5. Malformed lines: unparseable JSON, missing discriminator wrapper, or a
     record wrapped in the wrong discriminator for the file it lives in.

Usage:
    python3 scripts/audit_corpus_duplicates_integrity.py [--out-dir staging] [--sample N]
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")

# catalogue name -> (top-level discriminator key, set-of-allowed-subtype-keys-or-None)
# None means "no subtype nesting: manufacturerInfo lives directly under the
# discriminator value".
CATALOG_SPECS = {
    "mosfets":        ("semiconductor", {"mosfet"}),
    "diodes":         ("semiconductor", {"diode"}),
    "igbts":          ("semiconductor", {"igbt"}),
    "bjts":           ("semiconductor", {"bjt"}),
    "capacitors":     ("capacitor", None),
    "resistors":      ("resistor", None),
    "varistors":      ("varistor", None),
    "magnetics":      ("magnetic", None),
    "controllers":    ("controller", None),
    "connectors":     ("connector", None),
    "thermistors":    ("thermistor", None),
    "analog_ics":     ("analog", {
        "operationalAmplifier", "comparator", "dac", "adc", "analogSwitch",
        "multiplexer", "multiplier", "instrumentationAmplifier",
        "programmableGainAmplifier", "buffer", "differenceAmplifier",
    }),
    "timing_devices": ("timeBase", {"oscillator", "timer"}),
}

# Non-part catalogues: audited only for parse validity / structural sanity,
# never for manufacturer+partNumber duplicates (they have no such identity).
STRUCTURAL_ONLY = {
    "circuits":   {"name", "ports", "components", "connections"},
    "converters": {"inputs", "topology", "outputs", "simulation"},
}

METADATA_KEYS = {
    "provenance", "retrievedDate", "sourceName", "source", "_quarantineSource",
    "_triage", "distributorsInfo", "status",
}

PLACEHOLDER_PN_RE = re.compile(
    r"^(n/?a|tbd|todo|dummy|unknown|placeholder|xxx+|none|null|-|0+)$", re.IGNORECASE
)

KNOWN_CATALOG_STEMS = sorted(
    list(CATALOG_SPECS.keys()) + list(STRUCTURAL_ONLY.keys()), key=len, reverse=True
)


def norm_mfr(name):
    if not isinstance(name, str):
        return None
    n = " ".join(name.split()).strip().casefold()
    return n or None


def norm_pn(pn):
    if not isinstance(pn, str):
        return None
    n = " ".join(pn.split()).strip().casefold()
    return n or None


def is_placeholder(pn):
    if pn is None:
        return True
    s = str(pn).strip()
    if s == "":
        return True
    return bool(PLACEHOLDER_PN_RE.match(s))


def extract_identity(catalog, record):
    """Returns (status, mfr_raw, pn_raw, subtype_found, note) for one parsed record.

    status one of: OK, MISSING_DISCRIMINATOR, WRONG_DISCRIMINATOR,
                   MISSING_MANUFACTURER_INFO, MISSING_MFR_NAME, MISSING_PARTNUMBER
    """
    disc_key, subtype_set = CATALOG_SPECS[catalog]
    if not isinstance(record, dict):
        return ("NOT_AN_OBJECT", None, None, None, "top-level JSON value is not an object")

    top_keys = set(record.keys())
    if disc_key not in top_keys:
        return ("MISSING_DISCRIMINATOR", None, None, None,
                f"expected top-level key '{disc_key}', got {sorted(top_keys)}")
    extra_top = top_keys - {disc_key}
    wrapper_val = record[disc_key]
    if not isinstance(wrapper_val, dict):
        return ("MISSING_DISCRIMINATOR", None, None, None,
                f"'{disc_key}' value is not an object")

    note = None
    if extra_top:
        note = f"extra top-level keys: {sorted(extra_top)}"

    if subtype_set is None:
        inner = wrapper_val
        subtype_found = None
    else:
        present_subtypes = [k for k in wrapper_val.keys() if k in subtype_set]
        unknown_keys = [k for k in wrapper_val.keys() if k not in subtype_set]
        if not present_subtypes:
            return ("WRONG_DISCRIMINATOR", None, None, None,
                    f"none of expected subtypes {sorted(subtype_set)} present; "
                    f"found keys {sorted(wrapper_val.keys())}")
        if len(present_subtypes) > 1:
            note = ((note + "; ") if note else "") + f"multiple subtypes present: {present_subtypes}"
        subtype_found = present_subtypes[0]
        inner = wrapper_val[subtype_found]
        if unknown_keys:
            note = ((note + "; ") if note else "") + f"unknown sibling keys: {unknown_keys}"
        if not isinstance(inner, dict):
            return ("WRONG_DISCRIMINATOR", None, None, subtype_found,
                    f"subtype '{subtype_found}' value is not an object")

    mi = inner.get("manufacturerInfo")
    if not isinstance(mi, dict):
        return ("MISSING_MANUFACTURER_INFO", None, None, subtype_found, note)

    mfr_raw = mi.get("name")
    # IDENTITY PRECEDENCE: manufacturerInfo.reference is the true orderable SKU
    # and must win when both are present -- confirmed by inspection that it is
    # the field carrying real distinguishing suffixes that datasheetInfo.part.
    # partNumber drops (SiTime "SiT9501-V1" vs "SiT9501-V2" voltage/config
    # variants both share partNumber "SiT9501"; Vishay "IRF740PBF" (Pb-free
    # SKU) vs "IRF740" both share partNumber "IRF740"). Falling back to
    # part.partNumber only when reference is absent (e.g. capacitors/resistors
    # carry no top-level 'reference' at all) avoids manufacturing false
    # cross-line "duplicates" out of legitimately different SKUs.
    pn_raw = mi.get("reference")
    if not pn_raw:
        di = mi.get("datasheetInfo")
        if isinstance(di, dict):
            part = di.get("part")
            if isinstance(part, dict):
                pn_raw = part.get("partNumber")

    if not mfr_raw or not isinstance(mfr_raw, str) or not mfr_raw.strip():
        return ("MISSING_MFR_NAME", mfr_raw, pn_raw, subtype_found, note)
    if is_placeholder(pn_raw):
        return ("MISSING_PARTNUMBER", mfr_raw, pn_raw, subtype_found, note)

    return ("OK", mfr_raw, pn_raw, subtype_found, note)


def strip_metadata(obj):
    """Recursively drop provenance/metadata-ish keys so we can tell whether two
    duplicate records differ only in how they were sourced vs. in the physics."""
    if isinstance(obj, dict):
        return {
            k: strip_metadata(v) for k, v in sorted(obj.items())
            if k not in METADATA_KEYS
        }
    if isinstance(obj, list):
        return [strip_metadata(v) for v in obj]
    return obj


def diff_leaves(a, b, path=""):
    diffs = []
    if isinstance(a, dict) and isinstance(b, dict):
        keys = set(a.keys()) | set(b.keys())
        for k in sorted(keys):
            diffs.extend(diff_leaves(a.get(k, "<MISSING>"), b.get(k, "<MISSING>"), f"{path}.{k}" if path else k))
    elif isinstance(a, list) and isinstance(b, list):
        if a != b:
            diffs.append((path, a, b))
    else:
        if a != b:
            diffs.append((path, a, b))
    return diffs


def line_hash(line):
    return hashlib.blake2b(line.encode("utf-8", "replace"), digest_size=8).hexdigest()


def quarantine_catalog_for_source(src):
    s = src
    if s.startswith("discarded/"):
        s = s[len("discarded/"):]
    for stem in KNOWN_CATALOG_STEMS:
        if s == stem or s.startswith(stem + ".") or s.startswith(stem + "_"):
            return stem
    return "unknown"


def pass1_catalog(catalog, path, sample_cap):
    """First streaming pass over one catalogue file. Returns a dict of stats
    and a dedup index: key -> {"count": n, "hashes": set(), "lines": [line_no,...]}
    (line number lists are only kept for keys that turn out to have >1 occurrence
    at report time we cap how many we chase in detail, but we must keep all line
    numbers for every key to know the count -- this is O(#records) ints, not
    O(file size))."""
    stats = {
        "total_lines": 0,
        "parse_errors": [],          # (line_no, exc str, snippet)
        "missing_discriminator": [],
        "wrong_discriminator": [],
        "missing_manufacturer_info": [],
        "missing_mfr_name": [],
        "missing_partnumber": [],
        "not_an_object": [],
        "notes_sample": [],
        "ok_count": 0,
    }
    index = defaultdict(lambda: {"count": 0, "hashes": set(), "lines": []})
    # also collect a raw-string index restricted to same-partNumber-different-manufacturer
    pn_only_index = defaultdict(set)  # pn_norm -> set(mfr_norm)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            stats["total_lines"] += 1
            raw = line.rstrip("\n")
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except Exception as e:
                if len(stats["parse_errors"]) < sample_cap:
                    stats["parse_errors"].append((line_no, str(e), raw[:200]))
                continue

            status, mfr_raw, pn_raw, subtype, note = extract_identity(catalog, record)

            if note and len(stats["notes_sample"]) < sample_cap:
                stats["notes_sample"].append((line_no, note))

            if status == "NOT_AN_OBJECT":
                if len(stats["not_an_object"]) < sample_cap:
                    stats["not_an_object"].append((line_no, raw[:200]))
                continue
            if status == "MISSING_DISCRIMINATOR":
                if len(stats["missing_discriminator"]) < sample_cap:
                    stats["missing_discriminator"].append((line_no, note))
                continue
            if status == "WRONG_DISCRIMINATOR":
                if len(stats["wrong_discriminator"]) < sample_cap:
                    stats["wrong_discriminator"].append((line_no, note))
                continue
            if status == "MISSING_MANUFACTURER_INFO":
                if len(stats["missing_manufacturer_info"]) < sample_cap:
                    stats["missing_manufacturer_info"].append((line_no, note))
                continue
            if status == "MISSING_MFR_NAME":
                if len(stats["missing_mfr_name"]) < sample_cap:
                    stats["missing_mfr_name"].append((line_no, {"mfr": mfr_raw, "pn": pn_raw}))
                continue
            if status == "MISSING_PARTNUMBER":
                if len(stats["missing_partnumber"]) < sample_cap:
                    stats["missing_partnumber"].append((line_no, {"mfr": mfr_raw, "pn": pn_raw}))
                continue

            stats["ok_count"] += 1
            mfr_n = norm_mfr(mfr_raw)
            pn_n = norm_pn(pn_raw)
            key = (mfr_n, pn_n)
            entry = index[key]
            entry["count"] += 1
            entry["hashes"].add(line_hash(raw))
            entry["lines"].append(line_no)

            pn_only_index[pn_n].add(mfr_n)

    return stats, index, pn_only_index


def fetch_lines(path, wanted_line_numbers):
    """One more streaming pass, returns {line_no: raw_line} for a target set."""
    wanted = set(wanted_line_numbers)
    out = {}
    if not wanted:
        return out
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            if line_no in wanted:
                out[line_no] = line.rstrip("\n")
                if len(out) == len(wanted):
                    break
    return out


def classify_dup_group(catalog, key, entry, lines_by_no, max_conflict_diffs=8):
    """Classifies one duplicate group using an already-fetched {line_no: raw} map
    (callers batch the fetch for an entire catalogue into one file pass)."""
    records = []
    for ln in entry["lines"]:
        raw = lines_by_no.get(ln)
        if raw is None:
            continue
        try:
            records.append((ln, json.loads(raw)))
        except Exception:
            continue
    if len(records) < 2:
        return {"classification": "UNKNOWN (could not re-read lines)", "lines": entry["lines"]}

    canon = [strip_metadata(r) for _, r in records]
    if all(c == canon[0] for c in canon[1:]):
        return {
            "classification": "PROVENANCE_OR_METADATA_ONLY",
            "manufacturer": key[0], "partNumber": key[1],
            "lines": [ln for ln, _ in records],
            "byte_identical": len(entry["hashes"]) == 1,
        }

    # conflict: find leaf diffs between first record and the others
    diffs_all = []
    base_ln, base_rec = records[0]
    base_canon = canon[0]
    for (ln, rec), c in zip(records[1:], canon[1:]):
        d = diff_leaves(base_canon, c)
        if d:
            diffs_all.append({"line_a": base_ln, "line_b": ln, "diffs": d[:max_conflict_diffs],
                               "diff_count": len(d)})
    return {
        "classification": "CONFLICT",
        "manufacturer": key[0], "partNumber": key[1],
        "lines": [ln for ln, _ in records],
        "byte_identical": False,
        "sample_diffs": diffs_all[:3],
    }


def audit_structural_only(catalog, path, expected_keys, sample_cap):
    stats = {"total_lines": 0, "parse_errors": [], "unexpected_top_keys": [], "ok_count": 0}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            stats["total_lines"] += 1
            raw = line.rstrip("\n")
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except Exception as e:
                if len(stats["parse_errors"]) < sample_cap:
                    stats["parse_errors"].append((line_no, str(e), raw[:200]))
                continue
            if not isinstance(record, dict):
                if len(stats["unexpected_top_keys"]) < sample_cap:
                    stats["unexpected_top_keys"].append((line_no, "not an object"))
                continue
            extra = set(record.keys()) - expected_keys
            if extra:
                if len(stats["unexpected_top_keys"]) < sample_cap:
                    stats["unexpected_top_keys"].append((line_no, sorted(extra)))
                continue
            stats["ok_count"] += 1
    return stats


def audit_quarantine(path, sample_cap):
    """Streams quarantine.ndjson, building
    {catalog: {(mfr_norm, pn_norm): {"lines": [...], "duplicate_sourced": bool}}}.

    duplicate_sourced is True only if EVERY _quarantineSource entry for every
    line sharing that key contains "duplicate" -- those rows were quarantined
    BECAUSE they were extra copies of a part that is correctly kept live once
    elsewhere, so finding their key still live is the intended, successful
    outcome of that dedup pass, not a bug. Any other quarantine reason
    (fabricated/incomplete/synthetic/invalid_physics/unverified/...) sharing a
    key with a live row IS the dangerous case this audit is meant to catch.
    """
    parse_errors = []
    per_catalog_keys = defaultdict(lambda: defaultdict(lambda: {"lines": [], "duplicate_sourced": True}))
    total = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            total += 1
            raw = line.rstrip("\n")
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except Exception as e:
                if len(parse_errors) < sample_cap:
                    parse_errors.append((line_no, str(e), raw[:200]))
                continue
            qsrc = record.get("_quarantineSource")
            src_list = qsrc if isinstance(qsrc, list) else [qsrc] if qsrc else []
            catalogs_here = set()
            for s in src_list:
                if isinstance(s, str):
                    catalogs_here.add(quarantine_catalog_for_source(s))
            if not catalogs_here:
                catalogs_here = {"unknown"}

            # find the discriminator key that isn't metadata
            disc_candidates = [k for k in record.keys()
                                if k not in ("_quarantineSource", "quarantineReason", "_triage")]
            mfr_n = pn_n = None
            for dk in disc_candidates:
                val = record[dk]
                if not isinstance(val, dict):
                    continue
                # try direct manufacturerInfo, or one level of subtype nesting
                mi = val.get("manufacturerInfo")
                if not isinstance(mi, dict):
                    for sub_v in val.values():
                        if isinstance(sub_v, dict) and isinstance(sub_v.get("manufacturerInfo"), dict):
                            mi = sub_v["manufacturerInfo"]
                            break
                if isinstance(mi, dict):
                    mfr_raw = mi.get("name")
                    # same reference-first precedence as extract_identity() --
                    # see the comment there.
                    pn_raw = mi.get("reference")
                    if not pn_raw:
                        di = mi.get("datasheetInfo")
                        if isinstance(di, dict):
                            part = di.get("part")
                            if isinstance(part, dict):
                                pn_raw = part.get("partNumber")
                    mfr_n, pn_n = norm_mfr(mfr_raw), norm_pn(pn_raw)
                    break

            if mfr_n is None or pn_n is None or is_placeholder(pn_n):
                continue
            is_dup_reason = all("duplicate" in s.casefold() for s in src_list if isinstance(s, str)) \
                if src_list else False
            for cat in catalogs_here:
                entry = per_catalog_keys[cat][(mfr_n, pn_n)]
                entry["lines"].append(line_no)
                entry["duplicate_sourced"] = entry["duplicate_sourced"] and is_dup_reason

    return {"total_lines": total, "parse_errors": parse_errors}, per_catalog_keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(REPO_ROOT, "staging"))
    ap.add_argument("--sample", type=int, default=50, help="max sample rows kept per bucket")
    ap.add_argument("--conflict-detail-cap", type=int, default=40,
                     help="max duplicate groups to re-read and classify per catalogue")
    args = ap.parse_args()

    t_start = time.time()
    start_wall = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    os.makedirs(args.out_dir, exist_ok=True)

    report = {
        "measurement_started_utc": start_wall,
        "note": "READ-ONLY audit. Catalogues are being edited concurrently by other "
                "agents this session; counts are a snapshot as of the start time above. "
                "Headline numbers are re-checked at the end (see measurement_finished_utc "
                "and any *_recheck fields).",
        "catalogs": {},
    }

    per_catalog_index = {}       # catalog -> index dict (kept only for cross-catalog + dup detail)
    per_catalog_path = {}
    global_line_counts = {}

    # ---- Pass 1: per-catalog identity/structure sweep ----
    for catalog, (disc_key, subtype_set) in CATALOG_SPECS.items():
        path = os.path.join(DATA_DIR, f"{catalog}.ndjson")
        if not os.path.exists(path):
            report["catalogs"][catalog] = {"error": "file not found"}
            continue
        per_catalog_path[catalog] = path
        stats, index, pn_only_index = pass1_catalog(catalog, path, args.sample)
        global_line_counts[catalog] = stats["total_lines"]

        dup_keys = {k: v for k, v in index.items() if v["count"] > 1}
        total_dup_rows = sum(v["count"] for v in dup_keys.values())
        excess_rows = total_dup_rows - len(dup_keys)
        byte_identical_groups = sum(1 for v in dup_keys.values() if len(v["hashes"]) == 1)
        needs_check_groups = len(dup_keys) - byte_identical_groups

        # classify a capped sample of the non-byte-identical groups (the
        # potentially dangerous ones) plus a small sample of byte-identical
        # ones for spot-checking.
        conflict_samples = []
        provenance_only_samples = []
        non_identical_items = sorted(
            ((k, v) for k, v in dup_keys.items() if len(v["hashes"]) > 1),
            key=lambda kv: -kv[1]["count"],
        )
        detail_targets = non_identical_items[: args.conflict_detail_cap]
        wanted_lines = set()
        for _, v in detail_targets:
            wanted_lines.update(v["lines"])
        lines_by_no = fetch_lines(path, wanted_lines)  # one pass, not one per group
        for k, v in detail_targets:
            result = classify_dup_group(catalog, k, v, lines_by_no)
            if result["classification"] == "CONFLICT":
                conflict_samples.append(result)
            elif result["classification"] == "PROVENANCE_OR_METADATA_ONLY":
                provenance_only_samples.append(result)

        # same partNumber, different manufacturers (rule 3, cross-manufacturer collision)
        cross_mfr_pn = {pn: sorted(mfrs) for pn, mfrs in pn_only_index.items()
                         if pn and len(mfrs) > 1}
        cross_mfr_sample = dict(list(cross_mfr_pn.items())[: args.sample])

        report["catalogs"][catalog] = {
            "total_lines": stats["total_lines"],
            "ok_records": stats["ok_count"],
            "parse_errors": {"count": len(stats["parse_errors"]), "sample": stats["parse_errors"]},
            "not_an_object": {"count": len(stats["not_an_object"]), "sample": stats["not_an_object"]},
            "missing_discriminator": {"count": len(stats["missing_discriminator"]),
                                       "sample": stats["missing_discriminator"]},
            "wrong_discriminator": {"count": len(stats["wrong_discriminator"]),
                                     "sample": stats["wrong_discriminator"]},
            "missing_manufacturer_info": {"count": len(stats["missing_manufacturer_info"]),
                                           "sample": stats["missing_manufacturer_info"]},
            "missing_mfr_name": {"count": len(stats["missing_mfr_name"]),
                                  "sample": stats["missing_mfr_name"]},
            "missing_partnumber": {"count": len(stats["missing_partnumber"]),
                                    "sample": stats["missing_partnumber"]},
            "structural_notes_sample": stats["notes_sample"],
            "duplicates": {
                "distinct_keys_with_duplicates": len(dup_keys),
                "total_rows_involved_in_duplicates": total_dup_rows,
                "excess_rows_if_collapsed_to_one_each": excess_rows,
                "byte_identical_groups": byte_identical_groups,
                "groups_needing_content_check": needs_check_groups,
                "conflict_groups_found_in_sample": len(conflict_samples),
                "provenance_only_groups_found_in_sample": len(provenance_only_samples),
                "conflict_samples": conflict_samples,
                "provenance_only_samples": provenance_only_samples[:10],
                "detail_sample_cap": args.conflict_detail_cap,
            },
            "cross_manufacturer_partnumber_collisions": {
                "count": len(cross_mfr_pn),
                "sample": cross_mfr_sample,
            },
        }

        # keep index in memory for cross-catalog pass (only key->count/lines, small)
        per_catalog_index[catalog] = index
        print(f"[{catalog}] {stats['total_lines']} lines, {stats['ok_count']} ok, "
              f"{len(dup_keys)} dup keys, {total_dup_rows} dup rows", file=sys.stderr)

    # ---- structural-only catalogues (circuits, converters) ----
    report["structural_only_catalogs"] = {}
    for catalog, expected_keys in STRUCTURAL_ONLY.items():
        path = os.path.join(DATA_DIR, f"{catalog}.ndjson")
        if not os.path.exists(path):
            report["structural_only_catalogs"][catalog] = {"error": "file not found"}
            continue
        stats = audit_structural_only(catalog, path, expected_keys, args.sample)
        global_line_counts[catalog] = stats["total_lines"]
        report["structural_only_catalogs"][catalog] = stats
        print(f"[{catalog}] (structural only) {stats['total_lines']} lines, "
              f"{stats['ok_count']} ok, {len(stats['parse_errors'])} parse errors "
              f"(sample cap {args.sample})", file=sys.stderr)

    # ---- Cross-catalogue same-part check ----
    global_index = defaultdict(list)  # key -> [(catalog, line_no), ...]
    for catalog, index in per_catalog_index.items():
        for key, entry in index.items():
            if key[0] is None or key[1] is None:
                continue
            for ln in entry["lines"]:
                global_index[key].append((catalog, ln))

    cross_catalog_hits = {}
    for key, occurrences in global_index.items():
        catalogs_seen = {c for c, _ in occurrences}
        if len(catalogs_seen) > 1:
            cross_catalog_hits[key] = occurrences

    sampled_hits = list(cross_catalog_hits.items())[: args.sample]
    # batch: one file pass per catalogue for all evidence lines needed, instead
    # of re-scanning a (possibly 600 MB) file once per sampled key.
    wanted_by_catalog = defaultdict(set)
    for key, occurrences in sampled_hits:
        by_cat = defaultdict(list)
        for c, ln in occurrences:
            by_cat[c].append(ln)
        for c, lns in by_cat.items():
            wanted_by_catalog[c].add(lns[0])
    fetched_by_catalog = {
        c: fetch_lines(per_catalog_path[c], lns) for c, lns in wanted_by_catalog.items()
    }

    cross_catalog_samples = []
    for key, occurrences in sampled_hits:
        by_cat = defaultdict(list)
        for c, ln in occurrences:
            by_cat[c].append(ln)
        evidence = {}
        for c, lns in by_cat.items():
            raw = fetched_by_catalog.get(c, {}).get(lns[0])
            if raw is not None:
                try:
                    rec = json.loads(raw)
                except Exception:
                    rec = raw[:300]
                evidence[c] = {"line": lns[0], "record_excerpt": rec}
        cross_catalog_samples.append({
            "manufacturer_norm": key[0], "partNumber_norm": key[1],
            "catalogs": sorted(by_cat.keys()),
            "occurrence_counts": {c: len(v) for c, v in by_cat.items()},
            "evidence": evidence,
        })

    report["cross_catalog_same_part"] = {
        "distinct_keys_in_more_than_one_catalog": len(cross_catalog_hits),
        "sample": cross_catalog_samples,
    }
    print(f"[cross-catalog] {len(cross_catalog_hits)} parts appear in >1 catalogue",
          file=sys.stderr)

    # ---- Quarantine cross-check (rule 4) ----
    quarantine_path = os.path.join(DATA_DIR, "quarantine.ndjson")
    quarantine_report = {}
    if os.path.exists(quarantine_path):
        q_stats, q_per_catalog_keys = audit_quarantine(quarantine_path, args.sample)
        global_line_counts["quarantine"] = q_stats["total_lines"]
        # "expected": every _quarantineSource for this key says "duplicate" --
        # that quarantine batch's whole point was to remove extra copies while
        # leaving exactly one canonical row live, so finding it live is the
        # SUCCESSFUL outcome, not a bug.
        # "dangerous": at least one quarantine reason for this key is NOT a
        # duplicate reason (fabricated/incomplete/synthetic/invalid_physics/
        # unverified/...) yet the part is still live -- this is rule 4's
        # "a quarantine that did not happen".
        expected_total, dangerous_total = 0, 0
        expected_by_catalog, dangerous_by_catalog = {}, {}
        for catalog, qkeys in q_per_catalog_keys.items():
            live_index = per_catalog_index.get(catalog)
            if live_index is None:
                continue
            expected_hits, dangerous_hits = [], []
            for key, qinfo in qkeys.items():
                if key in live_index:
                    rec = {
                        "manufacturer_norm": key[0], "partNumber_norm": key[1],
                        "quarantine_lines": qinfo["lines"],
                        "live_lines": live_index[key]["lines"],
                    }
                    (expected_hits if qinfo["duplicate_sourced"] else dangerous_hits).append(rec)
            if expected_hits:
                expected_by_catalog[catalog] = {"count": len(expected_hits), "sample": expected_hits[: args.sample]}
                expected_total += len(expected_hits)
            if dangerous_hits:
                dangerous_by_catalog[catalog] = {"count": len(dangerous_hits), "sample": dangerous_hits[: args.sample]}
                dangerous_total += len(dangerous_hits)
        quarantine_report = {
            "total_lines": q_stats["total_lines"],
            "parse_errors": {"count": len(q_stats["parse_errors"]), "sample": q_stats["parse_errors"]},
            "distinct_source_catalogs_matched": sorted(q_per_catalog_keys.keys()),
            "still_live_EXPECTED_duplicate_dedup_kept_one_copy": {
                "note": "quarantine reason was itself 'duplicates' for every matching row -- "
                        "this is successful dedup (one copy correctly kept live), not a defect.",
                "total_distinct_parts": expected_total,
                "by_catalog": expected_by_catalog,
            },
            "still_live_DANGEROUS_non_duplicate_reason": {
                "note": "quarantined for fabricated/incomplete/synthetic/invalid_physics/"
                        "unverified/etc, yet the SAME (manufacturer, partNumber) is still live -- "
                        "a quarantine that did not happen.",
                "total_distinct_parts": dangerous_total,
                "by_catalog": dangerous_by_catalog,
            },
        }
        print(f"[quarantine] {q_stats['total_lines']} lines, "
              f"{expected_total} expected (dedup) still-live, "
              f"{dangerous_total} DANGEROUS still-live", file=sys.stderr)
    else:
        quarantine_report = {"error": "data/quarantine.ndjson not found"}
    report["quarantine_cross_check"] = quarantine_report

    # ---- Re-check headline line counts at the end (files move under us) ----
    t_end = time.time()
    end_wall = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    recheck = {}
    for catalog in list(CATALOG_SPECS.keys()) + list(STRUCTURAL_ONLY.keys()) + ["quarantine"]:
        path = os.path.join(DATA_DIR, f"{catalog}.ndjson")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                n = sum(1 for _ in f)
        except Exception as e:
            n = f"error: {e}"
        recheck[catalog] = {
            "line_count_at_measurement_start": global_line_counts.get(catalog),
            "line_count_at_report_end": n,
            "changed_during_run": (global_line_counts.get(catalog) != n),
        }
    report["measurement_finished_utc"] = end_wall
    report["measurement_wall_seconds"] = round(t_end - t_start, 1)
    report["line_count_recheck"] = recheck

    out_path = os.path.join(args.out_dir, "abt391_duplicate_integrity_audit.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nWrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
