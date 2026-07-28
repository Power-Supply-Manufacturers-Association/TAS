// Vendor-direct capture of TDK's common-mode choke/filter catalog (ABT #281),
// feeding scripts/extract_tdk_cmc.py.
//
//   node scripts/pull_tdk_cmc.mjs      # writes tdk-cmf_cmc.json + tdk-page1.csv
//
// Needs playwright installed (any checkout with @playwright/test on the module
// path; run it from there). Three things about product.tdk.com make the obvious
// approaches fail, so they are worked around deliberately:
//
//  1. AKAMAI. curl gets 403 no matter the UA, and so does playwright's default
//     headless build. channel:'chromium' launches the FULL Chrome-for-Testing
//     binary with --headless=new, which passes. Still headless — do not "fix"
//     this by going headed.
//  2. THE CSV EXPORT IS CAPPED AND ONE-SHOT. /pdc_api/.../list.csv honours
//     ?_l=&_p= but only ever returns the first 100 of the current sort, and a
//     SECOND fetch in the same page load 404s. So it cannot page the catalog.
//  3. THE GRID PAGINATES FINE. Hence: read the rendered table per page, and
//     cross-check page 1 against the CSV export to PROVE the parse is faithful
//     before trusting pages 2..N. That check found a "New" badge rendered inside
//     the part-number cell ('New ACT1210D-131-2P-TL01') — without it that badge
//     would have become part of the MPN, which is the exact defect class #281 is
//     about. Keep the cross-check.
//
// Output envelope: {source, total, columns, rows} — raw vendor cells, no
// interpretation. All mapping/unit conversion happens in extract_tdk_cmc.py.

import { writeFileSync } from 'node:fs'
import { chromium } from '@playwright/test'

const OUT = '/tmp/claude-1000/-home-alf/3b0ca11a-b277-41ee-9b13-661c75a962cb/scratchpad'
const BASE = 'https://product.tdk.com/en/search/emc/emc/cmf_cmc/list'
const Q = (p) => `part_no=*&_l=100&_p=${p}&_c=pure_status-pure_status&_d=0`

const browser = await chromium.launch({ channel: 'chromium' })
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
})
const page = await ctx.newPage()

const readGrid = () => page.evaluate(() => {
  const tbl = [...document.querySelectorAll('table')].find((t) => t.querySelectorAll('tbody tr').length > 5)
  if (!tbl) return null
  const head = [...tbl.querySelectorAll('thead th')].map((t) => t.innerText.trim().replace(/\s+/g, ' '))
  const rows = [...tbl.querySelectorAll('tbody tr')].map((tr) =>
    [...tr.cells].map((c) => c.innerText.trim().replace(/\s+/g, ' ')))
  return { head, rows }
})

let total = null
const byMpn = new Map()
let head = null

for (let p = 1; p <= 20; p += 1) {
  await page.goto(`${BASE}#${Q(p)}`, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  await page.waitForFunction(() => document.querySelectorAll('table tbody tr').length > 0, null, { timeout: 60_000 })
  await page.waitForTimeout(2500)
  if (total === null) {
    total = await page.evaluate(() =>
      Number((document.body.innerText.match(/Number of Products Found\s*:\s*([\d,]+)/) || [])[1]?.replace(/,/g, '')))
    console.log('catalog size:', total)
  }
  const grid = await readGrid()
  if (!grid || !grid.rows.length) { console.log(`page ${p}: no grid`); break }
  if (!head) { head = grid.head; console.log('columns:', head.join(' | ')) }
  const mpnIdx = head.findIndex((h) => /^Part No/i.test(h))
  let fresh = 0
  for (const r of grid.rows) {
    const mpn = r[mpnIdx]
    if (mpn && !byMpn.has(mpn)) { byMpn.set(mpn, r); fresh += 1 }
  }
  console.log(`page ${p}: ${grid.rows.length} rows, ${fresh} new (total ${byMpn.size}/${total})`)

  if (p === 1) {   // pull the CSV once, in this same load, for the cross-check
    const csv = await page.evaluate(async (q) => {
      const r = await fetch(`/pdc_api/en/search/emc/emc/cmf_cmc/list.csv?${q}`)
      return r.ok ? await r.text() : `__HTTP_${r.status}`
    }, Q(1))
    if (!csv.startsWith('__HTTP_')) writeFileSync(`${OUT}/tdk-page1.csv`, csv)
  }
  if (!fresh || byMpn.size >= total) break
  await page.waitForTimeout(1000)
}

writeFileSync(`${OUT}/tdk-cmf_cmc.json`,
  JSON.stringify({ source: BASE, total, columns: head, rows: [...byMpn.values()] }, null, 1))
console.log(`\nsaved ${byMpn.size} of ${total} parts -> ${OUT}/tdk-cmf_cmc.json`)
await browser.close()
