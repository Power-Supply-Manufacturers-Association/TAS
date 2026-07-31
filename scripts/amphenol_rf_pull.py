#!/usr/bin/env python3
"""Pull the full Amphenol RF parametric catalogue.

Endpoint (Kentico Xperience parametric search, discovered 2026-07-30 by driving the real
UI at https://www.amphenolrf.com/en-us/products/rf-connectors/ and capturing the XHR):

    POST https://www.amphenolrf.com/api/search/parametric
    Content-Type: application/json
    {"page":1,"resultsPerPage":500,"searchString":"","inStock":false,"isActive":false,
     "isNew":false,"facetFilters":[],"orderBy":[],"nodeAliasPath":"","npiPackageId":0,
     "application":"","searchCulture":"en-US"}

nodeAliasPath "" = whole catalogue; "/Products/Categories/RF-Connectors" scopes to a branch.
Response: {results:[{partNumber, description, productCategory, categoryPageUrl, pageUrl,
customerDrawing:{assetUrl}, additionalDictionary:{...~60 parametric fields...}}],
facetFilters:[...], resultColumns:[...], count:N}

Raw curl is 403 (Akamai); the request must be issued from an in-page fetch() in a real
chromium so it inherits the session. Writes raw pages to <OUT>/page_NNN.json.
"""
import json, os, sys
from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/amphenol_rf"
RPP = 500
os.makedirs(OUT, exist_ok=True)

JS = """
async ([page, rpp]) => {
  const body = {page: page, resultsPerPage: rpp, searchString: "", inStock: false,
                isActive: false, isNew: false, facetFilters: [], orderBy: [],
                nodeAliasPath: "", npiPackageId: 0, application: "", searchCulture: "en-US"};
  const r = await fetch('/api/search/parametric', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (r.status !== 200) return {status: r.status};
  const j = await r.json();
  return {status: 200, count: j.count, results: j.results};
}
"""

def main():
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chromium", headless=True)
        ctx = b.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                       "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
        p = ctx.new_page()
        p.goto("https://www.amphenolrf.com/en-us/products/rf-connectors/",
               wait_until="domcontentloaded", timeout=90000)
        p.wait_for_timeout(5000)
        page = 1
        total = None
        got = 0
        while True:
            res = p.evaluate(JS, [page, RPP])
            if res.get("status") != 200:
                print("HTTP", res.get("status"), "on page", page, flush=True)
                break
            total = res.get("count", total)
            rs = res.get("results") or []
            if not rs:
                break
            with open(f"{OUT}/page_{page:03d}.json", "w") as fo:
                json.dump(rs, fo)
            got += len(rs)
            print(f"page {page}: {len(rs)} (total so far {got} / {total})", flush=True)
            if total is not None and got >= total:
                break
            if len(rs) < RPP:
                break
            page += 1
        b.close()
    print(json.dumps({"pages": page, "records": got, "count": total}))

if __name__ == "__main__":
    main()
