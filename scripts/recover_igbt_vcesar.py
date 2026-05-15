#!/usr/bin/env python3
"""
Recover IGBT quarantine entries by fetching Vce(sat) from alldatasheet.com and other sources.

Strategy per MPN:
  1. alldatasheet.com view page (search) — fast, structured spec snippet
  2. DuckDuckGo search → first non-DDG URL → fetch + regex
  3. Give up and leave in quarantine

Writes recovered entries to TAS/data/igbts.ndjson (v2 igbt wrapper).
"""

import json
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

TAS_DIR = Path(__file__).parent.parent / "data"
QUARANTINE_FILE = TAS_DIR / "quarantine.ndjson"
IGBTS_FILE = TAS_DIR / "igbts.ndjson"

REQUEST_DELAY = 1.2  # seconds between web requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Accept": "text/html,*/*",
    "Accept-Language": "en-US,en;q=0.5",
}

# Vce(sat) extraction — all are "in Volts, 0.5–5.0 V range"
VCESAR_PATTERNS = [
    re.compile(r'[Cc]ollector\s+[Ee]mitter\s+[Ss]aturation\s+[Vv]oltage[^:]{0,30}:\s*(\d+\.?\d*)\s*[Vv]', re.IGNORECASE),
    re.compile(r'Vce\(on\)\s*:\s*(\d+\.?\d*)\s*[Vv]', re.IGNORECASE),
    re.compile(r'Vce\(sat\)[^:]{0,20}:\s*(\d+\.?\d*)\s*[Vv]', re.IGNORECASE),
    re.compile(r'V_?CE\s*\(?\s*sat\s*\)?\s*[=:≤]\s*(?:typ\.?\s*)?(\d+\.?\d*)\s*[Vv]\b', re.IGNORECASE),
    re.compile(r'[Ss]aturation\s+[Vv]oltage[^:]{0,30}:\s*(\d+\.?\d*)\s*[Vv]', re.IGNORECASE),
    # table: "VCE(sat) ... typ 1.85 ... V"
    re.compile(r'VCE\(sat\).*?(\d+\.\d+)\s*V', re.IGNORECASE | re.DOTALL),
]


def fetch(url: str, timeout: int = 12) -> str | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = r.headers.get("Content-Type", "")
            if "pdf" in ct.lower():
                return None
            return r.read(300_000).decode("utf-8", errors="replace")
    except Exception:
        return None


def strip_tags(html: str) -> str:
    clean = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', clean)


def extract_vcesar(text: str) -> float | None:
    for pat in VCESAR_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                val = float(m.group(1))
                if 0.5 <= val <= 5.0:
                    return val
            except ValueError:
                pass
    return None


def alldatasheet_search(mpn: str) -> float | None:
    """Search alldatasheet.com/view.jsp — returns spec snippets with Vce(sat)."""
    url = f"https://www.alldatasheet.com/view.jsp?Searchword={urllib.parse.quote(mpn)}"
    html = fetch(url)
    if not html:
        return None
    return extract_vcesar(strip_tags(html))


def ddg_search_first_url(mpn: str, mfr: str) -> str | None:
    """Search DuckDuckGo, return first non-DDG result URL."""
    query = urllib.parse.quote_plus(f"{mpn} datasheet Vce sat")
    html = fetch(f"https://html.duckduckgo.com/html/?q={query}")
    if not html:
        return None
    urls = re.findall(r'uddg=([^&\"\'>\s]+)', html)
    for u in urls:
        decoded = urllib.parse.unquote(u)
        if "duckduckgo" not in decoded and decoded.startswith("http"):
            return decoded
    return None


def try_recover(entry: dict) -> float | None:
    sem = entry.get("semiconductor", entry.get("igbt", {}))
    mi = sem.get("manufacturerInfo", {})
    mpn = mi.get("reference", "")
    mfr = mi.get("name", "")

    # 1. alldatasheet.com
    val = alldatasheet_search(mpn)
    if val:
        return val

    time.sleep(REQUEST_DELAY)

    # 2. DuckDuckGo → first result
    url = ddg_search_first_url(mpn, mfr)
    if url:
        time.sleep(REQUEST_DELAY)
        html = fetch(url)
        if html:
            val = extract_vcesar(strip_tags(html))
            if val:
                return val

    return None


ASSEMBLY_NORM = {"THT": "tht", "SMD": "smt", "SMT": "smt"}


def build_v2_igbt(entry: dict, vce_sat: float) -> dict:
    """Build v2 igbt wrapper entry with Vce(sat) populated."""
    import copy
    sem = entry.get("semiconductor", entry.get("igbt", {}))
    mi = copy.deepcopy(sem.get("manufacturerInfo", {}))
    di = mi.get("datasheetInfo", {})
    # Strip disallowed 'part.deviceType'
    part = di.get("part", {})
    part.pop("deviceType", None)
    # Normalize assemblyType to schema enum
    mech = di.get("mechanical", {})
    at = mech.get("assemblyType", "")
    if at in ASSEMBLY_NORM:
        mech["assemblyType"] = ASSEMBLY_NORM[at]
    # Set Vce(sat)
    elec = di.get("electrical", {})
    elec["collectorEmitterSaturation"] = vce_sat
    di["electrical"] = elec
    mi["datasheetInfo"] = di
    return {"igbt": {"manufacturerInfo": mi}}


def load_existing_mpns() -> set:
    mpns = set()
    if not IGBTS_FILE.exists():
        return mpns
    with open(IGBTS_FILE) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                ref = d.get("igbt", {}).get("manufacturerInfo", {}).get("reference", "")
                if ref:
                    mpns.add(ref.strip().upper())
            except Exception:
                pass
    return mpns


def main():
    # Parse quarantine
    quarantine_lines = [l for l in QUARANTINE_FILE.read_text().splitlines() if l.strip()]
    existing_mpns = load_existing_mpns()
    print(f"Quarantine entries : {len(quarantine_lines)}")
    print(f"Existing igbts.ndjson: {len(existing_mpns)} MPNs")

    igbt_candidates = []
    other_lines = []

    for line in quarantine_lines:
        try:
            d = json.loads(line)
        except Exception:
            other_lines.append(line)
            continue

        sem = d.get("semiconductor", {})
        igbt = d.get("igbt", {})
        wrapper = igbt if igbt else sem
        mi = wrapper.get("manufacturerInfo", {})
        di = mi.get("datasheetInfo", {})
        part = di.get("part", {})
        elec = di.get("electrical", {})
        device_type = part.get("deviceType", "")
        is_igbt = device_type == "igbt" or bool(igbt)
        ref = mi.get("reference", "")

        if (is_igbt and ref
                and elec.get("collectorEmitterVoltage")
                and elec.get("continuousCollectorCurrent")
                and not elec.get("collectorEmitterSaturation")):
            igbt_candidates.append((d, line))
        else:
            other_lines.append(line)

    total = len(igbt_candidates)
    print(f"IGBT recovery candidates: {total}")
    print(f"Other (untouched): {len(other_lines)}")
    print()

    recovered = 0
    skipped_dup = 0
    failed = 0
    remaining = []

    with open(IGBTS_FILE, "a") as out:
        for i, (entry, orig_line) in enumerate(igbt_candidates):
            sem = entry.get("semiconductor", entry.get("igbt", {}))
            mi = sem.get("manufacturerInfo", {})
            ref = mi.get("reference", "")
            ref_up = ref.strip().upper()

            prefix = f"[{i+1:4d}/{total}] {ref[:35]:<35s}"
            sys.stdout.write(f"\r{prefix}")
            sys.stdout.flush()

            if ref_up in existing_mpns:
                skipped_dup += 1
                remaining.append(orig_line)
                print(f"{prefix} SKIP dup")
                continue

            time.sleep(REQUEST_DELAY)
            vce_sat = try_recover(entry)

            if vce_sat is not None:
                v2 = build_v2_igbt(entry, vce_sat)
                out.write(json.dumps(v2, separators=(",", ":")) + "\n")
                out.flush()
                existing_mpns.add(ref_up)
                recovered += 1
                print(f"{prefix} OK  Vce(sat)={vce_sat}V")
            else:
                failed += 1
                remaining.append(orig_line)
                print(f"{prefix} FAIL")

            if (i + 1) % 100 == 0:
                print(f"\n  --- {i+1}/{total}: recovered={recovered} failed={failed} ---\n")

    # Rewrite quarantine
    all_remaining = other_lines + remaining
    print(f"\nRewriting quarantine: {len(all_remaining)} entries")
    with open(QUARANTINE_FILE, "w") as f:
        for line in all_remaining:
            f.write(line + "\n")

    print(f"\n{'='*60}")
    print(f"Recovered :  {recovered}")
    print(f"Skipped dup: {skipped_dup}")
    print(f"Failed :     {failed}")
    new_total = sum(1 for _ in open(IGBTS_FILE) if _.strip())
    new_q = sum(1 for _ in open(QUARANTINE_FILE) if _.strip())
    print(f"igbts.ndjson : {new_total} entries")
    print(f"quarantine   : {new_q} entries")


if __name__ == "__main__":
    main()
