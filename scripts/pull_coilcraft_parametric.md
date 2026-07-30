# Reading Coilcraft's parametric catalog (ABT #351)

Coilcraft is the one vendor in the #351 campaign whose data could not be reached
by any of the usual routes. It CAN be read, but only through a specific
combination — recorded here because every obvious attempt fails in a way that
looks permanent.

## What does not work

| route | result |
|---|---|
| `POST /en-us/api/productsearch/parametric` with curl | 403 |
| the same call from a headless-Chromium page (in-page `fetch`) | 403 — **Cloudflare** "Just a moment...", not CSRF |
| `coilcraft.com/pdfs/<series>.pdf` | 404 for most families (`ser2900` works, `pa4310`/`epl4012` do not) |
| the series product pages | render no server-side table; the grid is client-side only |
| `coilcraft.com/getmedia/<series>.pdf`, `…/#datasheet` | 404 / 403 |

The 403 on the API is easy to misread as a CSRF/token requirement. It is not —
the body is a Cloudflare interstitial.

## What works

The **Playwright MCP browser** (persistent Chrome-for-Testing profile) clears the
Cloudflare challenge, and an **in-page `fetch`** from a loaded coilcraft.com page
then reaches the API as same-origin:

```js
// after navigating to any https://www.coilcraft.com/en-us/products/... page
const body = {
  searchString: "*",            // NOTE: ignored by the server, see below
  facetFilters: [],
  orderBy: [{ columnId: "inductance", isAscending: true, displayName: "Inductance" }],
  page: 1,                      // this is the only pagination control that works
  path: "/Products/Power",
  resultsPerPage: 200,          // capped server-side at ~141
  typeId: "power",              // power | rf | emi | transformer
  currencyId: 6,
};
const r = await fetch('/en-us/api/productsearch/parametric', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body), credentials: 'include',
});
const j = await r.json();       // { columns, rows, facetFilters, count, ... }
```

Gotchas that cost time:

* The response array is **`rows`**, not `results`/`items`/`data`.
* **`searchString` is ignored.** Every value returns the same page of the full
  catalog, so filtering must be done client-side after paging.
* `resultsPerPage` is capped near 141 regardless of what you ask for; `count`
  reports the true total (4,633 for `power`).
* Each row is `{ skuName, cells: [{content}, ...] }` — `cells` is POSITIONAL and
  the order is given by `columns`. For `power` that is:

  `inductance(µH) | dcrmax(mΩ) | isat(A) | irms(A) | parttempmax(°C) |
   voltagerating(V) | length(mm) | width(mm) | height(mm) | mounting | aecgrade | price`

  Note `isat` and `irms` are separate columns — Coilcraft publishes both, so
  there is no need to guess which current a value represents.

## What this established for #351

Paging all four `typeId`s gave 3,539 unique part numbers, and **none of the 47
Coilcraft part numbers in the corpus appears among them**. Five of the six
families (EPL4012, EPL6028, PA4310, PA4342, PA6349) have no catalog member at
all under that base name; only SER2918 exists, under a different scheme
(`SER2918H-332`, not `SER2918-010ML`).

More usefully, the vendor row exposed what is ACTUALLY wrong with those corpus
rows, and it is not what the density metric appeared to say:

```
SER2918-010ML   DCR 0.008 ohm, Irms 30 A  ->  7.2 W
  corpus dims    7.4 x  4.6 x  2.5 mm  ->  1.28 cm2  ->  5.6 W/cm2   FLAGGED
  vendor dims   27.9 x 27.9 x 17.8 mm  -> 35.45 cm2  ->  0.20 W/cm2  fine
```

The electrical data is sound; the **mechanical dimensions understate the package
by 28x in surface area**, and that is what pushed the rows over a per-area
threshold. The corpus figure looks like a case-code misreading — "2918" taken as
0.29 x 0.18 inch (7.4 x 4.6 mm) when the real part is 27.9 x 17.8 mm.

So these rows need a DIMENSIONAL re-source, not a DCR/current repair and
certainly not quarantine. That is a defect class the campaign had not seen
before: wrong package geometry, invisible to every electrical check, and only
detectable because MAG_DISS_DENSITY relates power to size.
