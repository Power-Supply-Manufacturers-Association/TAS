// Vendor-direct capture of Laird's common-mode choke catalog (ABT #286),
// feeding scripts/extract_laird_cmc.py.
//
//   node scripts/pull_laird_cmc.mjs
//
// Why this vendor matters to #286: the corrupted corpus rows stored Laird's
// |Z| @ 100 MHz in dcResistance (CM5441Z161B-10 -> "160 ohm DCR" at 75 A, i.e.
// 900 kW). Laird's own table publishes BOTH columns separately — "Impedance
// (Ohm) at 100 MHz" and "DCR (Ω) Max" — so the vendor-direct pull is exactly
// what disentangles them.
//
// Notes:
//  * laird.com is not bot-gated for content pages, but /products/search
//    rate-limits (429 nginx). Do not hammer it; this script only reads the
//    category page and pages it politely.
//  * channel:'chromium' (full Chrome, --headless=new) is used for consistency
//    with the TDK puller. Always headless.
import { writeFileSync } from 'node:fs'
import { chromium } from '@playwright/test'

const OUT = process.argv[2] || 'laird-cmc.json'
const URL = 'https://www.laird.com/products/inductive-components-emc-components-and-ferrite-cores/common-mode-chokes'

const browser = await chromium.launch({ channel: 'chromium' })
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
})
const page = await ctx.newPage()
await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 90_000 })
await page.waitForTimeout(7000)

const readPage = () => page.evaluate(() => {
  const tbl = [...document.querySelectorAll('table')].find((t) => t.querySelectorAll('tbody tr').length > 1)
  if (!tbl) return null
  const head = [...tbl.querySelectorAll('th')].map((h) => h.innerText.trim().replace(/\s+/g, ' '))
  const rows = [...tbl.querySelectorAll('tbody tr')].map((tr) => {
    const cells = [...tr.cells].map((c) => c.innerText.trim().replace(/\s+/g, ' '))
    // the datasheet link is the part's authoritative document; keep it for provenance
    const ds = [...tr.querySelectorAll('a[href]')].map((a) => a.href).find((h) => /\.pdf/i.test(h)) || null
    return { cells, datasheet: ds }
  })
  return { head, rows }
})

const seen = new Map()
let head = null
for (let p = 1; p <= 30; p += 1) {
  const grid = await readPage()
  if (!grid || !grid.rows.length) { console.log(`page ${p}: no table`); break }
  if (!head) { head = grid.head; console.log('columns:', head.join(' | ')) }
  const nameIdx = head.findIndex((h) => /product name/i.test(h))
  let fresh = 0
  for (const r of grid.rows) {
    const name = r.cells[nameIdx]
    if (name && !seen.has(name)) { seen.set(name, r); fresh += 1 }
  }
  console.log(`page ${p}: ${grid.rows.length} rows, ${fresh} new (total ${seen.size})`)

  // advance via the pager's "next" control, if there is one
  const advanced = await page.evaluate(() => {
    const next = [...document.querySelectorAll('a,button')].find((b) =>
      /^(next|›|»)$/i.test((b.textContent || '').trim()) && !b.hasAttribute('disabled'))
    if (!next) return false
    next.click(); return true
  })
  if (!advanced) { console.log('no further pages'); break }
  await page.waitForTimeout(4000)
}

writeFileSync(OUT, JSON.stringify(
  { source: URL, columns: head, rows: [...seen.values()] }, null, 1))
console.log(`\nsaved ${seen.size} Laird common-mode chokes -> ${OUT}`)
await browser.close()
