#!/usr/bin/env python3
"""Scrape Panasonic Industrial parametric catalogs -> raw JSONL (one file per category).

Data is server-rendered in the first <table> on each /models page. We capture the
full header->value dict per row plus the part-number, datasheet-PDF and (for EOL)
recommended-replacement hrefs. Mapping to TAS schemas happens in a separate pass.

Robustness:
- per_page=100, pages 0-indexed (page=0 is the first page). The per-page selector
  only offers 50/100, so per_page=100 is valid for every category.
- stops on the "Sorry, there are no products" sentinel, an empty page, or a short
  (<per_page) final page.
- per-page retries; ~0.6s pacing; resume via per-category .done marker.
"""
import asyncio, json, os, sys
from playwright.async_api import async_playwright

EXE = "/home/alf/.cache/ms-playwright/chromium-1226/chrome-linux64/chrome"
OUT = "/home/alf/PSMA/TAS/data/staging/panasonic_raw"
SENTINEL = "no products matching"

# (slug, expected_count, tas_type, eol)
CATS = [
 ("sp-cap", 238, "capacitor", False),
 ("poscap", 313, "capacitor", False),
 ("os-con", 918, "capacitor", False),
 ("hybrid-aluminum", 458, "capacitor", False),
 ("aluminum-cap-smd", 2432, "capacitor", False),
 ("aluminum-cap-lead", 4350, "capacitor", False),
 ("film-cap-electroequip", 6548, "capacitor", False),
 ("automotive-film-cap", 979, "capacitor", False),
 ("high-temperature-chip-resistors", 3933, "resistor", False),
 ("high-precision-chip-resistors", 19102, "resistor", False),
 ("current-sensing-chip-resistors", 4867, "resistor", False),
 ("small-and-high-power-chip-resistors", 9173, "resistor", False),
 ("anti-sulfurated-chip-resistors", 31731, "resistor", False),
 ("general-purpose-chip-resistors", 11062, "resistor", False),
 ("resistor-network-array", 2621, "resistor", False),
 ("automotive-inductors", 152, "magnetic", False),
 ("inductors-for-consumer", 13, "magnetic", False),
 ("voltage-stepup-coils", 82, "magnetic", True),
 ("multilayer-inductors", 11, "magnetic", True),
 ("choke-coils", 465, "magnetic", True),
 ("chip-inductors", 789, "magnetic", True),
 ("noise-filters", 61, "magnetic", False),
 ("chip-varistor", 94, "varistor", False),
 ("surge-components", 1376, "varistor", False),
]

def base_url(slug, eol):
    seg = "eol" if eol else "products"
    return f"https://industrial.panasonic.com/ww/{seg}/pt/{slug}/models"

EXTRACT_JS = r"""() => {
  const t = document.querySelector('table');
  if (!t) return {sentinel:false, headers:[], rows:[]};
  const headers = [...t.querySelectorAll('thead th, thead td')]
      .map(e => e.innerText.trim().replace(/\s+/g,' '));
  const rows = [];
  let sentinel = false;
  for (const tr of t.querySelectorAll('tbody tr')) {
    const cells = [...tr.querySelectorAll('td,th')];
    const texts = cells.map(c => c.innerText.trim().replace(/\s+/g,' '));
    if (texts.join(' ').toLowerCase().includes('no products matching')) { sentinel = true; continue; }
    // hrefs per cell (first anchor)
    const hrefs = cells.map(c => { const a = c.querySelector('a'); return a ? a.href : null; });
    rows.push({texts, hrefs});
  }
  return {sentinel, headers, rows};
}"""

async def fetch_page(pg, url, tries=3):
    for i in range(tries):
        try:
            await pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            await pg.wait_for_timeout(2300)
            d = await pg.evaluate(EXTRACT_JS)
            return d
        except Exception as e:
            if i == tries-1:
                print(f"    !! page failed {url}: {e}", flush=True)
                return None
            await pg.wait_for_timeout(1500)
    return None

def row_to_record(headers, row, slug, ttype, eol):
    texts, hrefs = row["texts"], row["hrefs"]
    rec = {"category": slug, "type": ttype, "eol": eol, "specs": {}}
    for h, txt, href in zip(headers, texts, hrefs):
        hl = h.lower()
        if hl.startswith("parts no"):
            rec["part"] = txt
            rec["part_url"] = href
        elif "datasheet" in hl or "catalog" in hl:
            if href: rec["datasheet"] = href
        elif "cad data" in hl or "stock check" in hl or hl == "choice":
            continue
        elif "recommended replacement" in hl:
            rec["replacement"] = txt
        else:
            rec["specs"][h] = txt
    return rec

async def scrape_category(b, slug, expected, ttype, eol):
    done_path = os.path.join(OUT, slug + ".done")
    out_path = os.path.join(OUT, slug + ".jsonl")
    if os.path.exists(done_path):
        print(f"[skip] {slug} (done)", flush=True)
        return
    base = base_url(slug, eol)
    pg = await b.new_page()
    records = []
    per_page = 100
    page = 0  # site pages are 0-indexed
    while True:
        url = f"{base}?per_page={per_page}&page={page}"
        d = await fetch_page(pg, url)
        if d is None:
            print(f"    [{slug}] page {page} unrecoverable, stopping", flush=True)
            break
        nrows = len(d["rows"])
        if nrows == 0 or d["sentinel"]:
            break
        for r in d["rows"]:
            rec = row_to_record(d["headers"], r, slug, ttype, eol)
            if rec.get("part"): records.append(rec)
        print(f"    [{slug}] page {page}: +{nrows} (total {len(records)}/{expected})", flush=True)
        if nrows < per_page:
            break
        page += 1
        await pg.wait_for_timeout(600)
    await pg.close()
    with open(out_path, "w") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(done_path, "w") as f:
        f.write(f"{len(records)}\texpected={expected}\n")
    flag = "OK" if abs(len(records)-expected) <= max(2, expected*0.02) else "MISMATCH"
    print(f"[done] {slug}: {len(records)} scraped (expected {expected}) {flag}", flush=True)

async def main(only=None):
    cats = CATS
    if only:
        only = set(only)
        cats = [c for c in CATS if c[0] in only]
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=EXE, headless=True,
            args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
        for slug, expected, ttype, eol in cats:
            try:
                await scrape_category(b, slug, expected, ttype, eol)
            except Exception as e:
                print(f"[ERROR] {slug}: {e}", flush=True)
        await b.close()
    print("=== SCRAPE COMPLETE ===", flush=True)

if __name__ == "__main__":
    only = sys.argv[1:] or None
    asyncio.run(main(only))
