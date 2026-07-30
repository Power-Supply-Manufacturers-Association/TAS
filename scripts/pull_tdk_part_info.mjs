// Per-part spec pull from product.tdk.com's DETAILED INFORMATION pages
// (ABT #351 endgame — the rows no parametric grid could reach).
//
//   node scripts/pull_tdk_part_info.mjs refs.json out.json
//
// The grid puller matches parts by family prefix inside a category, which fails
// when a family lives in a category the index pages never link (flat-wire /
// line-filter) or when the grid row is unusable. The info page is per part and
// carries the full spec table, including the two distinctions that matter most
// for this ticket:
//
//   Rated Current (L Change)          <- SATURATION current  (the F2 trap)
//   Rated Current (Temperature Rise)  <- the THERMAL rating   (what ratedCurrents means)
//   DC Resistance [Max.]              <- with an explicit unit, often µΩ
//
// BCM605040-57N is why this exists: its corpus row had L 1000x too large, DCR
// 1000x too large, AND the saturation current stored as the rated current — and
// the info page states all three correctly on one page.
//
// Category is resolved per part through TDK's own global search (the index pages
// lazy-load and hide several categories), then cached per family prefix.
import { readFileSync, writeFileSync } from 'node:fs'
import { chromium } from '@playwright/test'

const refs = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const OUT = process.argv[3]

const browser = await chromium.launch({ channel: 'chromium' })
const page = await (await browser.newContext({
  viewport: { width: 1440, height: 900 },
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
})).newPage()

const catCache = new Map()
const famKey = (r) => r.slice(0, 8)

async function resolveCategory(ref) {
  const key = famKey(ref)
  if (catCache.has(key)) return catCache.get(key)
  await page.goto(`https://product.tdk.com/en/search/list#pn=${encodeURIComponent(ref)}&_l=20&_p=1`,
                  { waitUntil: 'domcontentloaded', timeout: 90_000 }).catch(() => {})
  await page.waitForTimeout(8000)
  const cat = await page.evaluate(() => {
    const a = [...document.querySelectorAll('a[href]')].map((x) => x.href)
      .find((h) => /\/search\/[a-z_-]+\/[a-z_-]+\/[a-z0-9_-]+\/info\?part_no=/.test(h))
    return a ? a.match(/\/search\/([a-z_-]+\/[a-z_-]+\/[a-z0-9_-]+)\/info/)[1] : null
  })
  catCache.set(key, cat)
  return cat
}

const specs = {}
for (const ref of refs) {
  const cat = await resolveCategory(ref)
  if (!cat) { console.log(`  ${ref}: no category from global search`); continue }
  const url = `https://product.tdk.com/en/search/${cat}/info?part_no=${encodeURIComponent(ref)}`
  const r = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90_000 }).catch(() => null)
  if (!r || r.status() >= 400) { console.log(`  ${ref}: info page ${r ? r.status() : 'failed'} (${cat})`); continue }
  await page.waitForTimeout(4500)
  const rows = await page.evaluate(() => {
    const out = {}
    for (const tr of document.querySelectorAll('tr')) {
      const c = [...tr.cells].map((x) => x.innerText.trim().replace(/\s+/g, ' '))
      if (c.length >= 2 && c[0] && c[1]) out[c[0]] = c[1]
    }
    return out
  })
  if (!Object.keys(rows).length) { console.log(`  ${ref}: no spec table`); continue }
  specs[ref] = { category: cat, url, fields: rows }
  console.log(`  ${ref}: ${Object.keys(rows).length} fields from ${cat.split('/').pop()}`)
}

console.log(`\ncaptured ${Object.keys(specs).length} of ${refs.length}`)
writeFileSync(OUT, JSON.stringify(specs, null, 1))
console.log(`-> ${OUT}`)
await browser.close()
