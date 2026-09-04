#!/usr/bin/env python3
"""RETIRED 2026-07-31 — THIS SCRIPT MANUFACTURED PROVENANCE. It will not run.

It back-filled `provenance[]` onto records that lacked it, inferring the source
from the record's own datasheetUrl host and stamping a concrete `source`,
`sourceName` and `retrievedDate`. It contained no HTTP call of any kind: it never
fetched a single URL it vouched for.

That is not traceability, it is the appearance of it. A record only had to CARRY a
plausible vendor URL string to be credited with having been scraped from that vendor
on a specific date, and 318,391 records across every catalogue ended up asserting a
retrieval nobody had performed.

WHAT IT COST. It is the step that made five separate batches of fabricated parts
indistinguishable from sourced ones:

  ABT #247   177 invented magnetics, found in production by a user
  ABT #256   17,183 phase2-5 generator records
  ABT #351   195 invented Coilcraft parts, whose stamp was the ONLY thing that made
             them look sourced
  ABT #391   3,782 invented Murata parts — stamped "Murata parametric (SimSurfing
             export)", byte-identical to the stamp on genuinely sourced Murata rows,
             which is exactly why they survived four previous fabrication audits

The hole was found once before, in #247, and closed on only half of it: 21c7ae3
relabelled the 8,008 manufacturer-name stamps and left the URL path classified as
"self-evidencing", still asserting dates.

WHY IT IS RETIRED RATHER THAN FIXED. There is no correct version of this script. Any
tool that writes provenance for records whose origin nobody recorded is inventing
evidence — the inference may be a good guess, but a good guess is precisely what
provenance must not be. Provenance records an act that occurred, and anything
derivable from the record itself proves nothing.

WHAT TO DO INSTEAD. Provenance is written by whatever actually fetched the data, at
the moment it fetched it. Where it is missing, the part is RE-SOURCED, not
re-labelled; where it cannot be re-sourced, the gap is left visible. To check
existing claims, use scripts/verify_provenance_urls.py, which fetches every cited URL
and records what is really there, and verify_provenance_content.py, which checks the
document actually names the part.

Kept in-tree as the forensic record of how five fabrication batches passed for real.
The maps below are preserved because relabel_url_inferred_provenance.py imports them
to IDENTIFY this script's output — they are a fingerprint now, not a tool.

2026-09-04 HARDENING (ABT #391 item 4). Retiring main() left classify() — the
function that actually computes a source/sourceName/retrievedDate stamp from a bare
URL host or manufacturer name — importable and callable on its own. It already had
zero live callers (main() is retired; nothing else in this repo calls classify()
directly), so it is now refused too: calling it raises unconditionally, in the
execution/write path, the same way main() does. This is belt-and-braces, not a
behaviour change — nothing that ran before still runs.

DOMAIN_MAP and MANUF_MAP are UNCHANGED, still exactly these names, still plain
readable data: relabel_url_inferred_provenance.py imports them by these exact names
to build the (sourceName, retrievedDate) fingerprint that finds this script's own
contaminated rows, and tests/test_no_fabricated_parts.py pins that contract. Making
them unreachable would blind the corpus's ability to find its own contamination,
which is worse than the defect this hardening exists to close — so the refusal is
in the code path that WRITES a stamp (main, classify), never on the data itself.

A residual risk was found and is recorded here rather than silently patched around:
scripts/quarantine_unverified.py (commit 14d9ea8, a completed one-off migration, not
called by anything today) does `import backfill_provenance as B` and reimplements
classify()'s exact rule in its own local resolve() function, reading B.DOMAIN_MAP /
B.MANUF_MAP directly rather than calling B.classify() — so it does not go through
either refusal above. That script is outside this file's ownership; it is flagged
to the maintainers of this ticket rather than edited here.
"""
import sys as _sys

_RETIRED = (
    "backfill_provenance.py is RETIRED and will not run.\n"
    "It invented provenance for 318,391 records and is how five batches of\n"
    "fabricated parts came to look sourced (ABT #247, #256, #351, #391).\n"
    "Provenance is written by whatever fetched the data, at the time it fetched it.\n"
    "To check existing claims: scripts/verify_provenance_urls.py\n"
    "To re-source a vendor:    scripts/resource_<vendor>_citations.py\n"
)
import json, sys, argparse
from urllib.parse import urlparse
from collections import Counter

DATA = "/home/alf/PSMA/TAS/data"

# component path per file -> where manufacturerInfo lives
PATHS = {
    "mosfets": ("semiconductor", "mosfet"), "diodes": ("semiconductor", "diode"),
    "igbts": ("semiconductor", "igbt"), "bjts": ("semiconductor", "bjt"),
    "capacitors": ("capacitor",), "magnetics": ("magnetic",), "resistors": ("resistor",),
    "varistors": ("varistor",), "connectors": ("connector",), "controllers": ("controller",),
    "analog_ics": ("operationalAmplifier",),
}

# FINGERPRINT ONLY — this data must stay exactly as-is, under exactly this name.
# relabel_url_inferred_provenance.py imports DOMAIN_MAP by this name to identify
# this script's own contaminated output; do not repurpose it to build NEW
# provenance (that is what classify() used to do, and it now refuses instead).
#
# host-substring -> (source enum, sourceName, retrievedDate). First match wins.
# source enum: manufacturerDatasheet|manufacturerParametric|manufacturerDatabase|distributor|librarianEnrichment|scrape|manual
DOMAIN_MAP = [
    ("infineon.com",        ("manufacturerParametric", "Infineon parametric finder (xlsx export)", "2026-06-24")),
    ("vishay.com",          ("manufacturerParametric", "Vishay parametric (__NEXT_DATA__ webtable)", "2026-06-25")),
    ("ti.com",              ("manufacturerParametric", "TI selectionmodel parametric API", "2026-06-24")),
    ("st.com",              ("manufacturerParametric", "STMicroelectronics parametric export (xlsx)", "2026-06-24")),
    ("onsemi.com",          ("manufacturerParametric", "onsemi parametric export (CSV/open-search API)", "2026-06-24")),
    ("nexperia.com",        ("manufacturerParametric", "Nexperia parametric export (.xls)", "2026-06-25")),
    ("toshiba",             ("manufacturerParametric", "Toshiba parametric CSV export (param_*.csv)", "2026-06-24")),
    ("rohm.com",            ("manufacturerDatasheet",  "ROHM datasheet (rohm.com)", None)),
    ("eaton.com",           ("manufacturerDatasheet",  "Eaton datasheet (eaton.com)", None)),
    ("littelfuse.com",      ("manufacturerDatasheet",  "Littelfuse datasheet (littelfuse.com)", None)),
    ("wolfspeed.com",       ("manufacturerDatasheet",  "Wolfspeed datasheet (wolfspeed.com)", None)),
    ("yageogroup.com",      ("scrape",                 "Yageo Group base-part API (yageogroup.com)", "2026-06-22")),
    ("yageo.com",           ("scrape",                 "Yageo Group base-part API", "2026-06-22")),
    ("tdk.com",             ("manufacturerDatabase",   "TDK Meister database (TstDB.tmdb)", "2026-06-22")),
    ("tdk-electronics",     ("manufacturerDatabase",   "TDK Meister database (TstDB.tmdb)", "2026-06-22")),
    ("murata.com",          ("manufacturerParametric", "Murata parametric (SimSurfing export)", "2026-06-20")),
    ("panasonic.com",       ("scrape",                 "Panasonic Industrial parametric catalog (Playwright scrape)", "2026-06-23")),
    ("taiyo-yuden",         ("manufacturerParametric", "Taiyo Yuden TY-COMPAS CSV export", "2026-06-20")),
    ("ty-top.com",          ("manufacturerParametric", "Taiyo Yuden TY-COMPAS CSV export", "2026-06-20")),
    ("rubycon",             ("manufacturerParametric", "Rubycon ProductList CSV export", "2026-06-23")),
    ("we-online",           ("manufacturerDatabase",   "Wuerth Elektronik database (.mdb)", "2026-06-24")),
    ("bourns.com",          ("manufacturerParametric", "Bourns parametric Excel export", "2026-06-20")),
    ("abracon.com",         ("scrape",                 "Abracon parametric API (scraped JSON)", "2026-06-22")),
    ("coilcraft.com",       ("scrape",                 "Coilcraft parametric API (scraped JSON)", "2026-06-22")),
    ("molex.com",           ("scrape",                 "Molex search Solr API", "2026-06-24")),
    ("cyntec.com",          ("manufacturerParametric", "Cyntec inductor parametric export (xlsx)", "2026-06-22")),
    ("hirose",              ("manufacturerParametric", "Hirose product CSV", "2026-06-25")),
    ("wima",                ("manufacturerParametric", "WIMA param-search JSON API", "2026-06-24")),
    ("kemet.com",           ("manufacturerDatasheet",  "KEMET datasheet (kemet.com)", None)),
    ("monolithicpower",     ("manufacturerDatasheet",  "MPS datasheet (monolithicpower.com)", None)),
    ("analog.com",          ("manufacturerDatasheet",  "Analog Devices datasheet (analog.com)", None)),
    ("maximintegrated",     ("manufacturerDatasheet",  "Maxim datasheet (maximintegrated.com)", None)),
    ("samsungsem",          ("manufacturerDatasheet",  "Samsung Electro-Mechanics datasheet", None)),
    ("koa",                 ("manufacturerDatasheet",  "KOA Speer datasheet", None)),
    ("nichicon",            ("manufacturerDatasheet",  "Nichicon datasheet (nichicon.co.jp)", None)),
    ("chemi-con",           ("manufacturerDatasheet",  "Nippon Chemi-Con datasheet", None)),
    ("seielect.com",        ("manufacturerDatasheet",  "SEI Stackpole datasheet (seielect.com)", None)),
    ("kyocera-avx",         ("manufacturerDatasheet",  "KYOCERA AVX datasheet", None)),
    ("sumida",              ("manufacturerDatasheet",  "Sumida datasheet (sumida.com)", None)),
    ("ohmite",              ("manufacturerDatasheet",  "Ohmite datasheet (ohmite.com)", None)),
    ("caddock.com",         ("manufacturerDatasheet",  "Caddock datasheet (caddock.com)", None)),
    ("yageo.com",           ("scrape",                 "Yageo Group base-part API", "2026-06-22")),
    ("microchip.com",       ("manufacturerDatasheet",  "Microchip datasheet (microchip.com)", None)),
    ("knowlescapacitors",   ("manufacturerDatasheet",  "Knowles datasheet (knowlescapacitors.com)", None)),
    ("epc-co.com",          ("manufacturerParametric", "EPC parametric/datasheet (epc-co.com)", None)),
    ("passivecomponent",    ("manufacturerDatasheet",  "Walsin datasheet (passivecomponent.com)", None)),
    ("te.com",              ("manufacturerDatasheet",  "TE Connectivity datasheet (te.com)", None)),
    ("murata-ps.com",       ("manufacturerDatasheet",  "Murata Power Solutions datasheet", None)),
    ("murata.co.jp",        ("manufacturerParametric", "Murata parametric (murata.co.jp)", "2026-06-20")),
    ("diodes.com",          ("manufacturerDatasheet",  "Diodes Inc datasheet (diodes.com)", None)),
    ("aosmd.com",           ("manufacturerDatasheet",  "Alpha & Omega datasheet (aosmd.com)", None)),
    ("navitassemi.com",     ("manufacturerDatasheet",  "Navitas datasheet (navitassemi.com)", None)),
    ("gansystems.com",      ("manufacturerDatasheet",  "GaN Systems datasheet (gansystems.com)", None)),
    ("sunlordinc.com",      ("manufacturerDatasheet",  "Sunlord datasheet (sunlordinc.com)", None)),
    ("sanken",              ("manufacturerDatasheet",  "Sanken datasheet (sanken-ele.co.jp)", None)),
    ("smc-diodes.com",      ("manufacturerDatasheet",  "SMC Diode Solutions datasheet", None)),
    ("yuden.co.jp",         ("manufacturerParametric", "Taiyo Yuden TY-COMPAS", "2026-06-20")),
    ("pulseelectronics",    ("manufacturerDatasheet",  "Pulse Electronics datasheet", None)),
    ("johansontechnology",  ("manufacturerDatasheet",  "Johanson Technology datasheet", None)),
    # distributor-hosted datasheet URL -> the URL itself evidences a distributor source
    ("mouser.com",          ("distributor",            "datasheet via Mouser listing", None)),
    ("digikey.com",         ("distributor",            "datasheet via Digi-Key listing", None)),
    ("arrow.com",           ("distributor",            "datasheet via Arrow listing", None)),
    ("lcsc.com",            ("distributor",            "datasheet via LCSC listing", None)),
    # third-party aggregator — honest: NOT the manufacturer's own host
    ("datasheetpdf.com",    ("scrape",                 "third-party datasheet aggregator (datasheetpdf.com)", None)),
    ("alldatasheet",        ("scrape",                 "third-party datasheet aggregator (alldatasheet)", None)),
]

# placeholder / synthetic-fingerprint hosts -> generated data, not sourced
SYNTHETIC_HOSTS = ("example.com", "example.org", "example.net")

# FINGERPRINT ONLY — same rule as above. Not for building new provenance.
# fallback when datasheetUrl is missing/uninformative: manufacturer name -> entry
MANUF_MAP = {
    "Infineon": ("manufacturerParametric", "Infineon parametric finder (xlsx export)", "2026-06-24"),
    "Vishay": ("manufacturerParametric", "Vishay parametric (__NEXT_DATA__ webtable)", "2026-06-25"),
    "onsemi": ("manufacturerParametric", "onsemi parametric export (CSV/open-search API)", "2026-06-24"),
    "ON Semiconductor": ("manufacturerParametric", "onsemi parametric export (CSV/open-search API)", "2026-06-24"),
    "STMicroelectronics": ("manufacturerParametric", "STMicroelectronics parametric export (xlsx)", "2026-06-24"),
    "Texas Instruments": ("manufacturerParametric", "TI selectionmodel parametric API", "2026-06-24"),
    "Nexperia": ("manufacturerParametric", "Nexperia parametric export (.xls)", "2026-06-25"),
    "Toshiba": ("manufacturerParametric", "Toshiba parametric CSV export (param_*.csv)", "2026-06-24"),
    "ROHM": ("manufacturerDatasheet", "ROHM datasheet (rohm.com)", None),
    "Eaton": ("manufacturerDatasheet", "Eaton datasheet (eaton.com)", None),
    "Littelfuse": ("manufacturerDatasheet", "Littelfuse datasheet (littelfuse.com)", None),
    "Wolfspeed": ("manufacturerDatasheet", "Wolfspeed datasheet (wolfspeed.com)", None),
    "KEMET": ("scrape", "Yageo Group base-part API (KEMET brand)", "2026-06-22"),
    "TDK": ("manufacturerDatabase", "TDK Meister database (TstDB.tmdb)", "2026-06-22"),
    "Murata": ("manufacturerParametric", "Murata parametric (SimSurfing export)", "2026-06-20"),
    "Panasonic": ("scrape", "Panasonic Industrial parametric catalog (Playwright scrape)", "2026-06-23"),
    "Taiyo Yuden": ("manufacturerParametric", "Taiyo Yuden TY-COMPAS CSV export", "2026-06-20"),
    "Rubycon": ("manufacturerParametric", "Rubycon ProductList CSV export", "2026-06-23"),
    "Wuerth Elektronik": ("manufacturerDatabase", "Wuerth Elektronik database (.mdb)", "2026-06-24"),
    "Würth Elektronik": ("manufacturerDatabase", "Wuerth Elektronik database (.mdb)", "2026-06-24"),
    "Wurth Elektronik": ("manufacturerDatabase", "Wuerth Elektronik database (.mdb)", "2026-06-24"),
    "YAGEO": ("scrape", "Yageo Group base-part API (yageogroup.com)", "2026-06-22"),
    "Yageo": ("scrape", "Yageo Group base-part API (yageogroup.com)", "2026-06-22"),
    "Bourns": ("manufacturerParametric", "Bourns parametric Excel export", "2026-06-20"),
    "Bourns Inc.": ("manufacturerParametric", "Bourns parametric Excel export", "2026-06-20"),
    "Abracon": ("scrape", "Abracon parametric API (scraped JSON)", "2026-06-22"),
    "Coilcraft": ("scrape", "Coilcraft parametric API (scraped JSON)", "2026-06-22"),
    "Molex": ("scrape", "Molex search Solr API", "2026-06-24"),
    "Cyntec": ("manufacturerParametric", "Cyntec inductor parametric export (xlsx)", "2026-06-22"),
    "WIMA": ("manufacturerParametric", "WIMA param-search JSON API", "2026-06-24"),
    "Vanguard Electronics": ("scrape", "Vanguard Electronics WooCommerce API (ve1)", "2026-06-22"),
    "iNRCORE": ("scrape", "iNRCORE WooCommerce API", "2026-06-22"),
    "Monolithic Power Systems": ("manufacturerDatasheet", "MPS datasheet (monolithicpower.com)", None),
    "Maxim Integrated": ("manufacturerDatasheet", "Maxim datasheet (maximintegrated.com)", None),
    "Analog Devices": ("manufacturerDatasheet", "Analog Devices datasheet (analog.com)", None),
}


def host_of(url):
    if not url or not isinstance(url, str):
        return ""
    try:
        h = urlparse(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def classify(manufacturer, url):
    """RETIRED 2026-09-04 (ABT #391 item 4) — refuses unconditionally. Do not un-retire.

    This used to return (source, sourceName, retrievedDate, sourceUrl) inferred purely
    from a URL's host or a manufacturer's name, with no fetch behind either — the exact
    anti-pattern this whole module is retired for. It already had zero live callers
    (main() is retired; nothing else in this repo calls classify() — the one script
    that reused this logic, scripts/quarantine_unverified.py, copied the maps into its
    own local function instead), so refusing here changes no running behaviour. It is
    a second lock on a door main()'s refusal already blocked, for whatever calls this
    directly instead of going through main().

    The original body is kept below, unreachable, as the forensic record of exactly
    what it computed — same reason main()'s original body is kept.
    """
    raise RuntimeError(
        "backfill_provenance.classify() is retired and will not run.\n"
        "It infers a source, sourceName and retrievedDate from a record's own URL "
        "host or manufacturer name, without ever fetching anything — the anti-pattern "
        "behind ABT #247, #256, #351 and #391.\n"
        "Fetch the source for real: scripts/verify_provenance_urls.py, then let "
        "scripts/promote_verified_provenance.py write the verdict."
    )
    h = host_of(url)
    if h:
        if any(s in h for s in SYNTHETIC_HOSTS):
            return ("manual", "SYNTHETIC / generated placeholder record (example-domain URL)", None, None)
        for key, (src, name, date) in DOMAIN_MAP:
            if key in h:
                return (src, name, date, url)
    # Fallback on manufacturer name alone. This is an INFERENCE, not a trace: nothing
    # about THIS record was verified against that source — we only know who makes the
    # part. Asserting "Wuerth Elektronik database (.mdb), retrieved 2026-06-24" here
    # claims a provenance that was never established, which is exactly how 177
    # fabricated parts came to look legitimately sourced and reached production (found
    # by a user, 2026-07-20). So label the inference as such and leave retrievedDate
    # null: that date belongs to the campaign, not to this record.
    if manufacturer in MANUF_MAP:
        src, name, campaign_date = MANUF_MAP[manufacturer]
        if url:
            return (src, name, campaign_date, url)
        return (src, f"{name} [inferred from manufacturer name — this record was not traced to that source]",
                None, None)
    return None


def get_di(record, path):
    o = record
    for k in path:
        if not isinstance(o, dict) or k not in o:
            return None, None
        o = o[k]
    mi = o.get("manufacturerInfo") if isinstance(o, dict) else None
    if not isinstance(mi, dict):
        return None, None
    di = mi.get("datasheetInfo")
    return mi, di


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="comma list of files e.g. mosfets,resistors")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None

    grand = Counter()
    unmapped = Counter()
    src_hist = Counter()
    for f, path in PATHS.items():
        if only and f not in only:
            continue
        fn = f"{DATA}/{f}.ndjson"
        try:
            lines = open(fn, encoding="utf-8").read().splitlines()
        except FileNotFoundError:
            continue
        out = []
        n_stamp = n_skip_has = n_no_di = n_unmapped = 0
        for line in lines:
            if not line.strip():
                out.append(line); continue
            rec = json.loads(line)
            mi, di = get_di(rec, path)
            if di is None:
                n_no_di += 1; out.append(line); continue
            if "provenance" in di:
                n_skip_has += 1; out.append(line); continue
            res = classify(mi.get("name", ""), mi.get("datasheetUrl"))
            if res is None:
                n_unmapped += 1
                unmapped[f"{mi.get('name','?')} | {host_of(mi.get('datasheetUrl')) or '(no url)'}"] += 1
                out.append(line); continue
            src, name, date, url = res
            entry = {"source": src, "sourceName": name}
            if url:
                entry["sourceUrl"] = url
            if date:
                entry["retrievedDate"] = date
            di["provenance"] = [entry]
            src_hist[src] += 1
            n_stamp += 1
            out.append(json.dumps(rec, ensure_ascii=False))
        grand["stamped"] += n_stamp
        grand["already"] += n_skip_has
        grand["no_datasheetInfo"] += n_no_di
        grand["unmapped"] += n_unmapped
        print(f"{f:12} stamp={n_stamp:7d}  already={n_skip_has:7d}  unmapped={n_unmapped:6d}  no_di={n_no_di:5d}")
        if not args.dry_run and n_stamp:
            with open(fn, "w", encoding="utf-8") as fh:
                fh.write("\n".join(out) + "\n")

    print("\n=== TOTALS ===")
    for k, v in grand.items():
        print(f"  {k:18} {v}")
    print("\n=== source distribution of newly-stamped ===")
    for k, v in src_hist.most_common():
        print(f"  {v:8d}  {k}")
    print("\n=== top UNMAPPED (manufacturer | host) — left untouched, NOT guessed ===")
    for k, v in unmapped.most_common(30):
        print(f"  {v:7d}  {k}")
    print(f"\n(total distinct unmapped buckets: {len(unmapped)})")
    rep = f"{DATA}/../scripts/provenance_unmapped_report.json"
    with open(rep, "w", encoding="utf-8") as fh:
        json.dump({"total_unmapped": sum(unmapped.values()),
                   "buckets": dict(unmapped.most_common())}, fh, indent=1, ensure_ascii=False)
    print(f"unmapped report -> {rep}")
    if args.dry_run:
        print("\nDRY RUN — nothing written to data.")


def main():  # noqa: F811 — deliberately shadows the original, which is kept above
    """Refuse. See the module docstring; the original main() is retained as record."""
    _sys.stderr.write(_RETIRED)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
