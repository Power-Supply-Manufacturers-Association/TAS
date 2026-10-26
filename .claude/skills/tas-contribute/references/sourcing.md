# Sourcing rules

## What counts as evidence

- **Only the manufacturer**: its product page, its parametric/selector table or export, its
  datasheet, its own catalogue database or API (where robots.txt allows it).
- **Never a value source**: distributors, aggregators and archives (datasheet archive sites,
  alldatasheet-style mirrors, octopart/findchips-style search, web.archive.org copies). They
  tell you where to look. A part is not real because an aggregator lists it.
- A **category or search-results page is never a part citation**, even if the part appears
  on it (a results page echoes whatever you searched for). A series product page that prints
  the exact orderable code is fine.
- A series datasheet shared by many parts is honest evidence, stamped `seriesConfirmed`
  unless its text prints the exact part number (then `partNamed`).
- The cited URL is the document you read. Never construct a URL from a pattern and cite it
  without fetching it; a templated URL that 404s, or a filler id like `docs/99999/...`, is a
  fabricated citation.

## Judging a citation

Status code, content type and size cannot tell you whether a citation is good:
- a bot-challenge page answers 200 `text/html` (not dead, not a document);
- a vendor can answer 200 `application/pdf` with the wrong document (its terms of use);
- some vendor PDF links silently redirect to their own search page when the document is gone.

The only sound test is: **does the fetched document print this part number?** Match the part
as a whole token, anchored at both ends: IRF540 is not named by a page that prints only IRF540N.

## Fetching etiquette (binding)

- **robots.txt is binding** for every host you touch, including API subdomains (check the
  API host's own robots.txt). Honour `Disallow` and `Crawl-delay`. A vendor's own page
  scripts calling an API does not license you to call it.
- **Never** `api.te.com` or `te.com/.../commerce` (both disallowed); for TE use the public
  `www.te.com/en/product-<pn>.html` pages only.
- **One fetcher per host**, about **one request per 2 s** (slower if robots.txt says so).
  Back off on 403/429 and stop if blocked. Never rotate IPs or use proxies/VPNs to get round a
  block. Never scrape search-engine result pages.
- **User-Agent: `TAS-catalogue-research/1.0`.** Never put your name, e-mail, account ids,
  cookies or any personal data in a User-Agent, header, URL or payload.
- Prefer a vendor's JSON/CSV/parametric export over scraping rendered pages. A browser's
  network panel (or a headless browser) shows the data endpoint a parametric page uses; if
  that endpoint is allowed by robots.txt, fetch it directly at the same polite rate.
- Do not use distributor APIs for bulk work; they are quota-limited and not evidence anyway.
- No logins, paywalls or CAPTCHA solving. Do not start cron jobs or long-running supervisors
  on shared infrastructure.

## Disk

Keep raw downloads compressed, delete raw HTML once parsed, keep PDFs only while extracting
unless you cite them as registered evidence (see checks.md), keep the cache under 2 GB.

## Proving a part exists (existence oracles)

Before you use any vendor URL or listing as proof that parts exist (or do not), test it:
- with a **real but obsolete** part: some vendor `/part/<mpn>` routes cover only the active
  catalogue and 404 on real obsolete parts. Such a route can CONFIRM a part (200) but never
  condemn one (404).
- with a **fabricated code** you made up in that vendor's grammar: it must come back not found.
  An oracle that "finds" your fake is matching the query text, not the catalogue.
Report both controls in REPORT.md.
