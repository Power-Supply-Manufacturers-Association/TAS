#!/usr/bin/env python3
"""
Backfill the `provenance` list on TAS catalog records that lack it.

Goal: traceability — for every record, record WHERE the data came from, as a
list (`provenance[]`), with each entry {source, sourceName, sourceUrl,
retrievedDate}. Multi-source records get one entry per source.

Evidence used, in priority order (NO guessing — if none apply, the record is
left untouched and reported as unmapped):
  1. The record's own manufacturerInfo.datasheetUrl host  -> self-evidencing
     (the URL literally points at the source domain).
  2. DOMAIN_MAP refines the *method* (parametric API / .mdb / scrape / datasheet)
     from what we know about how that vendor's catalog was imported.
  3. MANUF_MAP is the fallback for records with no usable datasheetUrl.
Synthetic / placeholder records (example.com URLs, generator-built) are stamped
source="manual" so they are NOT misattributed to a real manufacturer.

provenance lives at: <component>[/<subtype>]/manufacturerInfo/datasheetInfo/provenance
Run with --dry-run first; it writes nothing and prints coverage + the unmapped tail.
"""
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
    """Return (source, sourceName, retrievedDate, sourceUrl) or None if untraceable."""
    h = host_of(url)
    if h:
        if any(s in h for s in SYNTHETIC_HOSTS):
            return ("manual", "SYNTHETIC / generated placeholder record (example-domain URL)", None, None)
        for key, (src, name, date) in DOMAIN_MAP:
            if key in h:
                return (src, name, date, url)
    # fallback on manufacturer name; keep the url as sourceUrl if it exists
    if manufacturer in MANUF_MAP:
        src, name, date = MANUF_MAP[manufacturer]
        return (src, name, date, url or None)
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


if __name__ == "__main__":
    main()
