#!/usr/bin/env python3
"""Pull the Amphenol CS / ICC (amphenol-cs.com) parametric catalogue via its Magento GraphQL API.

Discovered 2026-07-30 by driving the real storefront and capturing its XHRs. The site is a
Magento PWA behind a Cloudflare *managed challenge*: raw curl and plain headless playwright
both get 403 on /graphql. A HEADED persistent-context chromium clears the challenge once and
the profile keeps the cf_clearance cookie, after which every in-page fetch() works.

Endpoint:   GET https://www.amphenol-cs.com/graphql?query=<urlencoded>&variables=<urlencoded>
Headers:    Content-Type: application/json, Store: default

Category tree (1139 nodes; the catalogue lives under the "product-series" node, path 1/2/277):
  {categories(filters:{},pageSize:300,currentPage:N){total_count items{uid name url_path level
    product_count children_count path}}}
  NOTE: the parent node itself returns 0 products (not an anchor category) -- you must iterate
  its LEAF children (children_count == 0, product_count > 0).

Per-part parametrics (the money query -- custom_attributesV2 in a *bulk* products query):
  {products(filter:{category_uid:{eq:"<uid>"}} pageSize:500 currentPage:<n> sort:{position:ASC}){
     total_count
     items{ sku display_pn name url_key part_status
            custom_attributesV2{items{code __typename
              ... on AttributeValue{value}
              ... on AttributeSelectedOptions{selected_options{label}}}}}}}
  ~90 attribute codes per part: current_rating_percntct, voltage_rating, number_of_contacts,
  number_of_rows, pitch, gender, orientation, termination_style, durability_mate_cycles,
  operating_temperature_range, mates_with, be_l1_cat..be_l4_cat (taxonomy), datasheet, ...
  pageSize 500 is the ceiling (1000 -> 503 from the Varnish front end).

Writes NDJSON (one product per line) to <OUT>/products.ndjson and a resume log of finished
category pages to <OUT>/done.json.
"""
import json, os, sys, time
from playwright.sync_api import sync_playwright

S = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/amphenol_cs"
CATS = sys.argv[2] if len(sys.argv) > 2 else os.path.join(OUT, "categories.json")
PROFILE = os.path.join(OUT, "profile")
PAGE_SIZE = 500
CONC = 4
os.makedirs(OUT, exist_ok=True)
os.makedirs(PROFILE, exist_ok=True)

GQ_CATS = ('{categories(filters:{},pageSize:300,currentPage:%d){total_count items{uid name '
           'url_path level product_count children_count path}}}')

GQ_PROD = ('{products(filter:{category_uid:{eq:"%s"}} pageSize:%d currentPage:%d '
           'sort:{position:ASC}){total_count items{sku display_pn name url_key part_status '
           'custom_attributesV2{items{code __typename ... on AttributeValue{value} '
           '... on AttributeSelectedOptions{selected_options{label}}}}}}}')

JS_BATCH = """
async (queries) => {
  const one = async (q) => {
    try {
      const r = await fetch('/graphql?query=' + encodeURIComponent(q),
                            {headers: {'Content-Type': 'application/json', 'Store': 'default'}});
      if (r.status !== 200) return {http: r.status};
      const t = await r.text();
      try { return JSON.parse(t); } catch (e) { return {http: r.status, raw: t.slice(0, 200)}; }
    } catch (e) { return {err: String(e)}; }
  };
  return await Promise.all(queries.map(one));
}
"""

def flatten(item):
    attrs = {}
    for a in ((item.get("custom_attributesV2") or {}).get("items") or []):
        code = a.get("code")
        if a.get("__typename") == "AttributeValue":
            attrs[code] = a.get("value")
        else:
            opts = [o.get("label") for o in (a.get("selected_options") or [])]
            attrs[code] = "|".join(x for x in opts if x)
    return {"sku": item.get("sku"), "display_pn": item.get("display_pn"),
            "name": item.get("name"), "url_key": item.get("url_key"),
            "part_status": item.get("part_status"), "attrs": attrs}

def main():
    done_path = os.path.join(OUT, "done.json")
    done = set(json.load(open(done_path))) if os.path.exists(done_path) else set()
    seen = set()
    out_path = os.path.join(OUT, "products.ndjson")
    if os.path.exists(out_path):
        with open(out_path) as f:
            for line in f:
                try: seen.add(json.loads(line)["sku"])
                except Exception: pass
    print(f"resume: {len(done)} pages done, {len(seen)} skus already on disk", flush=True)

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE, channel="chromium", headless=os.environ.get("HEADLESS") == "1",
            args=["--disable-blink-features=AutomationControlled"],
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/151.0.0.0 Safari/537.36",
            viewport={"width": 1400, "height": 900})
        p = ctx.pages[0] if ctx.pages else ctx.new_page()
        p.set_default_timeout(600000)
        def clearance(max_tries=24):
            """Cloudflare managed challenge: reload until /graphql answers 200."""
            for i in range(max_tries):
                try:
                    r = p.evaluate(JS_BATCH, ["{storeConfig{store_code}}"])[0]
                except Exception as e:
                    r = {"err": str(e)}
                if (r.get("data") or {}).get("storeConfig"):
                    return True
                print(f"  waiting for cloudflare clearance ({i+1}) {json.dumps(r)[:80]}", flush=True)
                p.wait_for_timeout(6000)
                if i % 4 == 3:
                    try:
                        p.goto("https://www.amphenol-cs.com/", wait_until="domcontentloaded",
                               timeout=120000)
                    except Exception:
                        pass
            return False

        p.goto("https://www.amphenol-cs.com/", wait_until="domcontentloaded", timeout=120000)
        p.wait_for_timeout(8000)
        if not clearance():
            raise SystemExit("could not clear cloudflare; run HEADED (no HEADLESS=1) "
                             "and make sure a display is available")

        # --- categories -----------------------------------------------------
        if os.path.exists(CATS):
            cats = json.load(open(CATS))
        else:
            cats, pg = [], 1
            while True:
                res = p.evaluate(JS_BATCH, [GQ_CATS % pg])[0]
                items = ((res.get("data") or {}).get("categories") or {}).get("items") or []
                cats += items
                if len(items) < 300: break
                pg += 1
            json.dump(cats, open(CATS, "w"))
        for c in cats:
            c["product_count"] = int(c.get("product_count") or 0)
            c["children_count"] = int(c.get("children_count") or 0)
        # The catalogue proper hangs off the "product-series" node (1/2/277). ALL_LEAVES=1
        # additionally sweeps the marketing/application category trees, which mostly
        # re-list the same SKUs but occasionally carry a few that are not in a series.
        leaves = [c for c in cats
                  if c["product_count"] > 0 and c["children_count"] == 0
                  and (os.environ.get("ALL_LEAVES") == "1"
                       or c["path"].startswith("1/2/277/"))]
        leaves.sort(key=lambda c: c["product_count"])
        print(f"{len(leaves)} leaf series categories, {sum(c['product_count'] for c in leaves)} "
              f"product slots", flush=True)

        # --- build the full page worklist -----------------------------------
        # breadth-first: page 1 of every series before page 2 of any -- gives full
        # catalogue *coverage* early, so a partial run is representative rather than
        # 19k modular jacks and nothing else.
        work = []
        maxpages = max((c["product_count"] + PAGE_SIZE - 1) // PAGE_SIZE for c in leaves)
        for i in range(1, maxpages + 1):
            for c in leaves:
                if (c["product_count"] + PAGE_SIZE - 1) // PAGE_SIZE < i:
                    continue
                key = f"{c['uid']}#{i}"
                if key not in done:
                    work.append((key, c["uid"], i))
        print(f"{len(work)} page requests to run", flush=True)

        fo = open(out_path, "a")
        t0 = time.time()
        for i in range(0, len(work), CONC):
            batch = work[i:i + CONC]
            queries = [GQ_PROD % (uid, PAGE_SIZE, pgno) for _, uid, pgno in batch]
            try:
                results = p.evaluate(JS_BATCH, queries)
            except Exception as e:
                print("evaluate failed:", e, flush=True)
                time.sleep(20)
                continue
            if all(r.get("http") in (403, 503, 524) for r in results):
                # lost the cloudflare clearance (or got rate-limited): re-open the site,
                # re-clear, and retry this batch instead of burning through the worklist.
                print("  batch fully blocked -> re-clearing", flush=True)
                try:
                    p.goto("https://www.amphenol-cs.com/", wait_until="domcontentloaded",
                           timeout=120000)
                except Exception:
                    pass
                p.wait_for_timeout(5000)
                if not clearance():
                    raise SystemExit("cloudflare clearance lost and not recoverable")
                try:
                    results = p.evaluate(JS_BATCH, queries)
                except Exception as e:
                    print("retry failed:", e, flush=True)
                    continue
            n_new = 0
            for (key, uid, pgno), res in zip(batch, results):
                prods = ((res.get("data") or {}).get("products") or {}).get("items")
                if prods is None:
                    print(f"  MISS {key}: {json.dumps(res)[:200]}", flush=True)
                    continue
                for it in prods:
                    sku = it.get("sku")
                    if not sku or sku in seen:
                        continue
                    seen.add(sku)
                    fo.write(json.dumps(flatten(it), ensure_ascii=False) + "\n")
                    n_new += 1
                done.add(key)
            fo.flush()
            json.dump(sorted(done), open(done_path, "w"))
            el = time.time() - t0
            frac = (i + len(batch)) / len(work)
            print(f"[{i+len(batch)}/{len(work)}] +{n_new} new, total {len(seen)} skus, "
                  f"{el/60:.1f} min, eta {(el/max(frac,1e-9) - el)/60:.0f} min", flush=True)
        fo.close()
    print(json.dumps({"skus": len(seen), "pages_done": len(done)}))

if __name__ == "__main__":
    main()
