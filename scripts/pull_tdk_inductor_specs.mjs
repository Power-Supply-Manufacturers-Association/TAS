// Pull TDK per-part specs for the ABT #351 density-impossible queue, from the
// product.tdk.com parametric grids (ABT #351 fix pass 2).
//
//   node scripts/pull_tdk_inductor_specs.mjs refs.json out.json
//
// refs.json: ["TFM141208BLE-R47MTCA", ...]. For each family prefix the SMD
// inductor grid is queried with part_no=<prefix> (the grid filters server-side),
// and every row is captured verbatim. ACM12V parts are common-mode chokes, not
// inductors — for those the EMC automotive CMC category is probed by slug.
//
// Known traps handled: Akamai passes only with channel:'chromium' (full Chrome,
// still headless); a "New " badge is rendered INSIDE the part-number cell; the
// CSV export is one-shot-per-load and capped, so the GRID is read, not the CSV.
import { readFileSync, writeFileSync } from 'node:fs'
import { chromium } from '@playwright/test'

const refs = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const OUT = process.argv[3]

const famOf = (r) => {
  const i = r.indexOf('-')
  return i > 0 ? r.slice(0, i) : r.slice(0, 10)
}
const fams = [...new Set(refs.map(famOf))]
console.log(`families: ${fams.length} for ${refs.length} refs`)

const browser = await chromium.launch({ channel: 'chromium' })
const page = await (await browser.newContext({
  viewport: { width: 1440, height: 900 },
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
})).newPage()

const CATS = [
  'https://product.tdk.com/en/search/inductor/inductor/smd/list',
  'https://product.tdk.com/en/search/inductor/inductor/tht/list',
  // line-filter = the automotive/power-line CMC category (ACM12V, ...): found via
  // the site's own global search, not the lazy-loaded index pages.
  'https://product.tdk.com/en/search/emc/emc/line-filter/list',
  'https://product.tdk.com/en/search/emc/emc/cmf_cmc/list',
]

const readGrid = () => page.evaluate(() => {
  const tbl = [...document.querySelectorAll('table')].find((t) => t.querySelectorAll('tbody tr').length > 0)
  if (!tbl) return null
  const head = [...tbl.querySelectorAll('thead th')].map((h) => h.innerText.trim().replace(/\s+/g, ' '))
  const rows = [...tbl.querySelectorAll('tbody tr')].map((tr) =>
    [...tr.cells].map((c) => c.innerText.trim().replace(/\s+/g, ' ')))
  return { head, rows }
})

const found = {}
for (const fam of fams) {
  let got = 0
  for (const cat of CATS) {
    const url = `${cat}#part_no=${encodeURIComponent(fam)}&_l=100&_p=1`
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90_000 }).catch(() => {})
    const ok = await page.waitForFunction(
      () => document.querySelectorAll('table tbody tr').length > 0
            || /Number of Products Found\s*:\s*0/.test(document.body.innerText),
      null, { timeout: 30_000 }).catch(() => false)
    if (!ok) continue
    await page.waitForTimeout(2000)
    const grid = await readGrid()
    if (!grid || !grid.rows.length) continue
    const pnIdx = grid.head.findIndex((h) => /^Part No/i.test(h))
    for (const row of grid.rows) {
      const mpn = (row[pnIdx] || '').replace(/^New\s+/i, '').trim()
      if (mpn && !found[mpn]) {
        found[mpn] = { category: cat, head: grid.head, row }
        got += 1
      }
    }
    if (got) { console.log(`  ${fam}: +${got} rows from ${cat.split('/').slice(-2)[0]}`); break }
  }
  if (!got) {
    // family prefix too specific (SPM3015T vs grid SPM3015T-1R0M is fine, but
    // B82559A432 misses B82559A4322A033 listings keyed differently) — retry the
    // first six characters before giving up
    const short = fam.slice(0, 6)
    for (const cat of CATS) {
      const url = `${cat}#part_no=${encodeURIComponent(short)}&_l=100&_p=1`
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90_000 }).catch(() => {})
      const ok = await page.waitForFunction(
        () => document.querySelectorAll('table tbody tr').length > 0
              || /Number of Products Found\s*:\s*0/.test(document.body.innerText),
        null, { timeout: 30_000 }).catch(() => false)
      if (!ok) continue
      await page.waitForTimeout(2000)
      const grid = await readGrid()
      if (!grid || !grid.rows.length) continue
      const pnIdx = grid.head.findIndex((h) => /^Part No/i.test(h))
      for (const row of grid.rows) {
        const mpn = (row[pnIdx] || '').replace(/^New\s+/i, '').trim()
        if (mpn && !found[mpn]) { found[mpn] = { category: cat, head: grid.head, row }; got += 1 }
      }
      if (got) { console.log(`  ${fam} (as ${short}): +${got} rows from ${cat.split('/').slice(-2)[0]}`); break }
    }
    if (!got) console.log(`  ${fam}: NOT FOUND in any category`)
  }
}

const hits = refs.filter((r) => found[r]).length
console.log(`\nmatched ${hits} of ${refs.length} queue refs (grid rows captured: ${Object.keys(found).length})`)
writeFileSync(OUT, JSON.stringify(found, null, 1))
console.log(`-> ${OUT}`)
await browser.close()
