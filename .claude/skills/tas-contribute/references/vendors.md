# Vendor notes for contributors

What has worked, what is blocked, and what is not worth chasing, per manufacturer. Use it to
pick a route before you write a fetcher. Everything here comes from earlier pulls; sites change,
so re-check robots.txt and one sample page before you commit to a route.

Rules that apply to every vendor (see also [sourcing.md](sourcing.md)):

- The manufacturer's own page, parametric data or datasheet is the evidence. A distributor page or an aggregator row is only a lead.
- Read the vendor's robots.txt, on the API host as well as on the www host, and honour `Disallow` and `Crawl-delay`. One fetcher per host,
  at most about 1 request per second unless the vendor asks for more.
- Where plain `curl` is refused (403, a challenge page, a reset connection), open the vendor's own page in a headless browser and read the
  page or call the page's own data endpoint from inside that page. Keep it to one browser and one page at a time. Do not rotate addresses.
- "Not found" usually means "not listed under that spelling or no longer listed", not "does not exist". Report it as not found; never guess
  a replacement.

Vendors are alphabetical.

## Amphenol

- Route: the Amphenol Aerospace MIL-DTL-38999 catalogue PDF (https://www.amphenol-aerospace.com/resources/literature/view/38999-catalog). It
  has the insert arrangement chart, the service-rating table (working and test voltage) and the contact test current table. Records were
  built from the chart, not from a parametric page.
- Bot gate: the site is behind a bot gate for `curl`; fetch the PDF once through a browser.
- Traps: the chart rows are multi-line and easy to mis-assign. Always cross-check the contact count of an arrangement against an independent
  count (for example the position count in the part description) and drop the part when they disagree. Some arrangements print the service
  rating as two letters; that is ambiguous, so leave the voltage out or skip the part.
- Coax and twinax contacts have no printed test current. Do not take a minimum over the other contact sizes; state a rated current only when
  the catalogue prints one for every contact size of the insert.
- Usually not listed: MIL-DTL-5015 and MIL-26482 style circular connectors, M83723, contacts, accessories, terminal blocks from the Shenzhen
  unit, heavy-duty and RF cable assemblies have no catalogue that decodes their numbers on the public site. Skip them.

## Analog Devices (and Maxim)

- Route: the parametric-search tables behind analog.com, served as static JSON-like scripts under
  `https://www.analog.com/cdp/pst2/data/standard/<categoryId>.js` (plus `data/special` and `view/en`). Plain `curl` works on `/cdp/`.
  Category ids are numeric; the useful ones sit between about 2380 and 3045 (op amps, comparators, ADCs, DACs, references, regulators,
  switches, supervisors, hot swap, gate drivers). Maxim parts sit in the same tables (legacy company MXM).
- Bot gate: product HTML pages and datasheet PDFs are Akamai-gated and get rate-blocked after a burst of page loads, even in a browser. Open
  few pages, slowly.
- Identity: the record is the generic device name (`ADP1706`), not the ordering code (`ADP1706ARDZ-3.3-R7`). Resolve an ordering code to its
  generic by exact match ignoring space, hyphen, underscore and slash; otherwise by the unique longest generic prefix. A digit after a
  generic that ends in a digit, or a dot, blocks a prefix match.
- Column traps: slew rate is stored in V/us whatever the display unit says (check the displayed value against the stored one).
  CMRR/PSRR/gain come with mixed sign conventions (negative dB, values like 280 dB); keep only values that are plausible. "Last Time Buy"
  can differ between a parent table and its leaf table; use the leaf table. Dropout voltage has no test current in the tables, so leave it
  out unless a datasheet gives it.
- Usually not listed or not usable: DACs have no architecture field (the schema needs one), matched transistor pairs have no per-die data,
  and most obsolete parts are in no table. Digital potentiometers, RF amplifiers and power modules have no home in the schemas.

## Bourns

- Route: series datasheets at `https://www.bourns.com/docs/product-datasheets/<name>.pdf`. `curl` gets the PDFs; the HTML pages return 403
  to `curl` but load in a browser. Extract with `pdftotext -raw`; `-layout` mixes drawing numbers into the rows. Obsolete parts have PDFs
  under `https://www.bourns.com/docs/technical-documents/obsolete-parts/<NAME>_obsolete.pdf`; a 302 to an error page means no such file.
  Many obsolete sheets are scanned images (read the page images).
- robots.txt: Crawl-delay 5 s. The part-level search and inventory pages (`/search`, `/*inventory-search-results*`, `/*product/product*`)
  are disallowed, so there is no part-level parametric route; category pages list series only.
- Traps: the parametric Excel export for diodes has a column headed `IRRM (mA)` that is in microamps. Cross-check against a datasheet before
  trusting a unit heading. The element of a trimmer (cermet, wirewound, conductive plastic) must be read from each datasheet title, never
  set per family.
- Series datasheets do not define most order codes, so a code can be decoded only when the datasheet's own "How to order" and range tables
  print that suffix and value. Trimmer order codes that end in a suffix the sheet does not define are not decodable.
- Usually not listed: the 91-series potentiometer codes ending `R51`, panel and precision pots, resistor networks and arrays (no schema
  home), PPTC, telecom protection (TISP, TBU) and kits.

## Coilcraft

- Route: series pages on https://www.coilcraft.com, found through the site's own sitemap (`sitemap-en-us.xml`). Each page prints a parts
  table per code: L, tolerance, DCR, SRF, Isat at a stated inductance drop, Irms at a stated temperature rise.
- Bot gate: `curl` is blocked; a headless browser with a fresh context per request and at least a couple of seconds between requests works.
  Honour robots.txt.
- Identity: a printed code may contain a `_` placeholder for the tolerance letter, or end in termination and packaging letters. Accept a
  variant only when the page's own option legend lists both the termination and the packaging letter and the leading part matches a printed
  row. When you build a regex from a placeholder, remember that on current Python `re.escape` no longer escapes `_`, so a naive replace
  never matches.
- Traps: Isat and Irms columns are defined by a drop percentage or a temperature rise stated in the header or a footnote; store that
  condition with the value. Take SRF as printed (min or typ) and say which. Tunable inductors (L min/nom/max by core position) and tables
  for coupled inductors, transformers and common-mode chokes need per-winding data the inductor shape lacks.
- Usually not listed: old codes with termination B or X, packaging U, W or B, and withdrawn K/M/N tolerance letters (Coilcraft marks them
  obsolete and the current pages never print them), series with no parts table (discontinued overview pages), and EMI beads that belong to a
  different category.

## Diodes Incorporated

- Route: the parametric collections and `https://www.diodes.com/part/view/<BASE>` pages (the page carries the parameters as JSON-LD).
- Bot gate: gated for `curl`; a single headless browser session works at under 1 page per second (back off 90 to 120 s on a 403). robots.txt
  disallows `/assets/Datasheets/`, so datasheet PDFs are off limits.
- Identity: the live catalogue is keyed on the base type (`2N7002`), not the orderable variants (`-7`, `-13`, `-7-F`). Do not re-spell a
  base to get past a duplicate check. Parts that differ by a dot are different parts (`SMCJ90A` is not `SMCJ9.0A`); some other makers use a
  hyphen as the decimal point, so keep the vendor's spelling.
- Column traps: some collections publish only a bare "Forward Voltage VF @ IF" column with no Max/Typ label; do not treat it as the maximum.
  A label such as "Maximum Clamping Voltage @ Max Peak Pulse Current" contains the words "Peak Pulse Current"; a loose label match then
  stores the clamping voltage (volts) as the peak pulse current (amperes). Match labels exactly. Store R_DS(on) and Qg at the same V_GS (the
  schema has no gate-voltage field for Qg), and leave the part out when the vendor prints no Qg at that V_GS. Multi-die MOSFETs and BJTs
  have no single-die home.
- The same generic part number is often sold by several makers (SMBJ60A, ES1A, B120). A candidate that collides with another maker's live
  row needs a decision on the catalogue key; flag it rather than renaming.
- Usually not listed: most distributor-spelled orderable codes, the many `-7`/`-13` tape variants of an already-present base, and old
  reference and Hall-sensor parts.

## Eaton

- Route: product pages `https://www.eaton.com/.../skuPage.<MPN>.html`, which robots.txt allows. Crawl-delay is 20 s;
  `skuPage*.specification`, `*.resources` and `*/SearchResults/*` must not be fetched.
- Traps: 404 on the page pattern means only "no page at this URL form"; the sitemap is not an existence oracle either. Hyphen can be
  identity: `10BJ6-0A` is the 6.0 V part and `10BJ60A` the 60 V part, both printed that way by Eaton. Keep the exact spelling and never
  merge by stripping hyphens. Several inductor pages print no inductance at all, and the supercapacitor pages never say "EDLC" (take hybrid
  or EDLC from the product type). Leakage current has no test condition on the page.
- Usually not listed or out of scope: fuses, breakers, fuse holders, surge protectors, resettable PTC fuses (a PTC thermistor in our schema
  is not a resettable fuse), DC-DC modules. Most connector and switch codes did not resolve to a page.

## EPC (GaN)

- Route: the selector guide at https://epc-co.com/epc/products/gan-fets-and-ics renders its table into the page itself (no separate data
  request).
- Trap: parts absent from the selector are end-of-life and their datasheet URLs 404.

## Infineon

- Route: product pages `https://www.infineon.com/part/<name>` carry the parameters, variants and documents as JSON-LD. Discover names
  through the part sitemap (`en.sitemap.part-row-sitemap.xml`). Plain `curl` works at about 1 to 2 requests per second. robots.txt allows
  `/part/` and disallows `*/search*`.
- Existence trap: `/part/` covers active parts only and answers 404 for real obsolete parts (`IKW25T120`, `SGW25N120` are real). A 200
  confirms a part; a 404 proves nothing. Never use it to condemn a part.
- Identity: one record per product page under the page's sales name (`IPB042N03L G`); an ordering code counts as confirmed only if it equals
  the page name or one of the page's listed variants.
- Column traps: the Finder spreadsheet exports put the unit inside each cell (`"40 uA"`, `"243 ns"`), so a bare number regex drops the
  prefix; and a "prefer max" column lookup can resolve `IR` to the `IR max` column. Single values labelled `VCE(sat)` or `VGS(th) range` are
  typical values, not maxima. SiC parts do not publish the gate voltage for R_DS(on).
- Usually not listed: obsolete parts, IGBT and diode modules, IPM, bare die, RF parts, depletion and dual MOSFETs, smart switches (PROFET,
  HITFET) and motor or PMIC parts have no match in the discrete schemas. The "relay" rows in old lists are mostly other vendors' solid-state
  relays.

## KEMET (YAGEO Group)

- Route: KEMET data is served by https://yageogroup.com. A batch lookup (`POST /search/query/base-part-from-pn`) names which codes exist;
  the per-part lookup (`GET /search/unique-parts/<PN>`) returns the full parameter set and resolves aliases and MIL codes. Part pages are
  `https://yageogroup.com/products/<Capacitors|Relays|Varistors|Inductors>/part/<PN>` (the category segment is ignored). robots.txt allows
  these; about 1 request per second, one fetcher. Series datasheets live under
  `https://yageogroup.com/content/datasheet/asset/file/<KEM_...>`.
- Traps: many codes are "batch-only": the batch lookup lists them but the part page says "Can't find data". There is then nothing to cite as
  a part page; the series datasheet with a series-level verification is the only honest source, and the code must be one the batch lookup
  named (never assemble codes yourself). Radial leaded parts' `case` field holds the vendor's series code, not an EIA case. A vendor `W` on
  a can is a terminal dimension, not body width. AC-only rated parts (X/Y, motor-run) carry an AC rating and no DC rating. Verify each
  capacitance code and voltage code against the vendor's value. Some series datasheets disagree with the API (for example switching power on
  small relays, or a rated voltage), in which case leave the field out.
- The vendor does not print a mounting or lead style for many leaded parts; the lead style comes from the series datasheet title.
- Usually not listed: most codes spelled in old distributor lists (about 41,000 of one 45,000-row list were null), engineering kits,
  flex-suppressor and EMI sheets, and current sensors.

## Littelfuse

- Route: Littelfuse's public search index (Coveo, the one behind littelfuse.com) holds one parametric document per orderable part; call it
  from inside a headless browser on the littelfuse.com origin. A part page's `childpartnumbers` lists packaging variants.
- Bot gate: `curl` is blocked.
- Traps: the peak pulse power column (`watts`) disagrees with the 10/1000 us power on about a hundred TVS rows; require Ipp x Vc to be
  within 0.7 to 1.3 x the stated power and skip the rest. Varistor min/max columns are swapped on some series (AUMOV, HMOV, HA). "Surge
  energy" for varistors is a 2 ms square-wave energy, not the 10/1000 us value the schema describes. Typical capacitance is measured at 1
  MHz, not 1 kHz. A variant is acceptable only if the page lists it as a child part and it is the base code plus a packaging suffix (`-B`,
  `-T<n>`).
- Usually not listed: SIDACtor thyristors, resettable PTC fuses, gas discharge tubes, fuse holders, polymer ESD and battery protectors are
  other categories; packaging suffix spellings (`-TB`, `-TR`) and numeric fuse codes are mostly absent from the index.

## Molex

- Route: the per-part JSON endpoint `https://search.molex.com/api/search/productdetails?q=<PN>` (robots.txt allows exactly that path). It is
  not bot-gated for `curl` but is slow; one fetcher, about 2 requests per second at most. Cite the part page
  `https://www.molex.com/en-us/products/part-detail/<display PN>`.
- Identity: a part appears as a 10-digit SAP number (`0395066006`) in some lists but Molex's display number drops the leading zeros
  (`395066006`). Use the display number and match zero-stripped, or you will re-add parts already present.
- Traps: voltage strings such as `125V AC` must be parsed, not dropped. Many housings list no current per contact; the connector shape needs
  one, so a bare housing is filed as a housing accessory only when Molex lists no contact metal, plating, termination style or "terminals
  preloaded". Parts printing several different per-contact currents (power plus signal) do not fit the single-current shape and stay out.
- Usually not listed: most remaining leads are not on molex.com (about half of a random sample returned an empty product list). Cable
  assemblies, tooling, wire and cable and mains wiring devices are out of scope.

## Monolithic Power Systems

- Route: product pages on https://www.monolithicpower.com; robots.txt Crawl-delay is about 21 s, so a full pull takes days. Data pages are
  bot-gated: a plain `curl` receives a roughly 3 KB "Client Challenge" page with a 200 status.
- Citation trap: document URLs of the form `.../pub/media/document/<PART>_r1p00.pdf` look like datasheets but may be dead. A status,
  content-type or redirect check from `curl` tells you nothing (the challenge page returns 200 HTML). In a browser the URL either lands on a
  part-specific `documentview/productdocument/.../sku/<PART>/...` page (good) or falls back to a `catalogsearch/result/?q=<PART>` search
  page (dead). The revision suffix does not predict which. An in-page `fetch()` fails; navigate to the URL.
- Usually not listed: older parts and many revision-suffixed documents; Murata Power Solutions DC-DC modules (a different company) are not
  controllers.

## Murata

- Route: the public product API `https://pimapi.murata.com/public/api/pim/v1/products/search`, dumped one whole category at a time (ceramic
  capacitors, inductors, ferrite beads, NTC and PTC thermistors, ceramic resonators, silicon and leaded three-terminal capacitors, and
  others). A part is emitted only if its exact number appears in an entry's part number list. Product pages are
  `https://www.murata.com/en-us/products/productdetail?partno=<PN>`. About 1 request per second. Murata also publishes an MLCC product list
  (CSV) with full parameters for chip capacitors.
- Traps: the API's tolerance fields (`capacitanceTolerance`, `inductanceTolerance`, impedance tolerance) carry the VALUE in the value slot
  (a 220 pF part shows `220 %`); read the tolerance from the search description string next to the value and cross-check it. The stored
  thickness maximum is the nominal thickness. Capacitance printed in the part number's three-digit code must equal the vendor value for
  every candidate; use that as a check. Packaging letters matter: a code with `J` where only `D` is offered is not a Murata part (check the
  entry's carrier packaging list). Temperature-coefficient conventions differ: class 1 is a ppm/K coefficient, class 2 is a percent band,
  and Murata's own codes (X8G, X8L and so on) have no EIA decoding. A placeholder is a coefficient object holding only a nominal of 0; a
  real C0G zero or a banded class-2 value is not. Safety capacitors print AC ratings per class (X1, Y1, Y2) and may carry two classes at
  once; read the class and AC voltage from the vendor's per-class fields, not from the part number.
- Usually not listed: Murata no longer lists trimmer potentiometers or the RGL/RGS/RGH network codes anywhere; the product page answers
  "page could not be found". Many GRM, GQM, DRD and RPE variants and old inductor codes are not in the API. A search of the API's
  cross-category endpoint that returns nothing is a usable existence check (it resolves known parts). Murata Power Solutions modules are
  DC-DC converters, not controllers.

## Nexperia

- Route: the product page's ordering tab lists the orderable codes (`product-data.nexperia.com` option list), and each product has a
  datasheet PDF on `assets.nexperia.com` with per-type tables ("Limiting values", "Characteristics per type"). Parse Min/Typ/Max by header.
- robots.txt disallows the electrical parametric endpoint under `www.nexperia.com/.rest/parametrics`; do not use it.
- Bot gate: `curl` is refused even for the asset host; a headless browser calling the lookups from inside the page works at 1 request per
  second.
- Identity: the vendor writes some orderable codes with a comma (`PMEG3010EJ,115`) where lists drop it; match with that in mind. Unresolved
  spellings are not found.
- Traps: some parts have inconsistent datasheet values (clamping voltage times peak current not matching the stated peak power); leave those
  out. Multi-diode packages and multi-line ESD devices need per-element data.
- Usually not listed: ordering variants Nexperia's option list does not print (for example `BC807-25W/ZLX`).

## onsemi

- Route: family parametric tables on https://www.onsemi.com (one table per family page, columns mapped by header name), plus family
  datasheets at `/download/data-sheet/pdf/<slug>-d.pdf` for test conditions.
- robots.txt Crawl-delay is 10 s. `curl` gets 403 on product and datasheet paths; a headless browser is the way in. A parametrics CSV export
  also exists.
- Traps: multi-channel R_DS(on) is a string (`Q1=Q2=95`, `Q1: 62.0, Q2: 62.0`, `Q1 = 42, Q2 = 1.4`); a bare number regex returns the `1` of
  `Q1`. The column headed Qg Typ at V_GS = 10 V is sometimes the gate-source charge: cross-check against Qgd (must be smaller) and Ciss (Qg
  at least 0.4 x Ciss x 10 V). Match the exact orderable code; a family row whose base differs by a suffix is a different part. Quoted VCC
  Max and op-amp fields are unreliable and were dropped where datasheets disagreed.
- Usually not listed: many old family members have no family page or no row with that exact code.

## Panasonic

- Passive components: server-rendered model listings at `https://industrial.panasonic.com/ww/{products,eol}/pt/<category>/models` (100 rows
  per page), then the detail page of every part, which prints the exact part number and the full specification table. The European product
  finder API (`https://eu-p-industry-productfinder.wd.pisceu.panasonic.eu/api/productfinder`) needs no browser and covers relays, switches,
  coils, circuit protection, capacitors and resistors. Its paging is odd: use 20 rows per request with offsets in steps of 20, and a single
  query is capped at 10,000 rows (split by a 3-character search prefix).
- Traps: `0` and `-` in the finder are placeholders for missing; units are sometimes glued to the value (`20 mohm`). A bare "0.5A" rated
  current on a connector page does not say per contact or total; skip it. Film capacitor dielectrics PEN and MPET+PP hybrids have no schema
  value. Zero-ohm jumpers print tolerance "Not specified" and no resistance.
- Panasonic stopped posting semiconductors on 2022-09-30 (business transferred), so discrete semiconductor and IC part numbers are not on
  its site; do not substitute the successor's data.
- Usually not listed: MLCC (ECJ), tantalum, snap-in and screw-terminal aluminium (EEG, EET), ceramic disc (ECK, ECC), many ERJ and ERA
  variants; trimmer potentiometers, timing devices and varistor data are mostly absent. Hyphen-insensitive matching is needed because lists
  spell part numbers with hyphens that the vendor omits.

## Phoenix Contact

- Route: product pages `https://www.phoenixcontact.com/us/products/<article>`, which redirect to the canonical en-us page; the
  technical-data tables carry the values.
- Bot gate: `curl` and headless browsers get 403; a headed browser on the vendor's own page works. robots.txt Crawl-delay is 5 s, so a large
  crawl takes many hours at one fetcher.
- Identity: a 6-digit all-digit article number is often a 7-digit one with its leading zero dropped by the list; retry zero-padded.
- Traps: a connector needs one unambiguous rated current; pages with several current labels have to be skipped. Feed-through terminal blocks
  are not the same family as plain terminal blocks. Keep AC/DC suffixes on voltages. Relay sockets, bases and contactors are not relay
  modules.
- Usually not listed: about a fifth of sampled articles returned 404.

## ROHM

- Route: ROHM's parametric search data (one record set per product template, units from the column definitions), series datasheets on
  `fscdn.rohm.com`, and sitemap product pages.
- Traps: pre-biased digital transistors print VCC as "supply voltage" and never VCEO, so VCEO cannot be filled from VCC. For TVS and ESD
  parts, store only the 10/1000 us rating; parts with only an 8/20 us rating stay out. Take MOSFET R_DS(on) as the max at the drive voltage,
  never the typical as a stand-in, and require a Qg that is tied to the same V_GS. Datasheet URLs move between `datasheet/` and
  `datasheet-nrnd/`; check each cited URL returns 200. Dual diodes: keep a rating only where the PDF prints it per diode.
- Usually not listed: many chip-resistor series datasheet URLs now 404 and most series sheets do not state the resistive element technology
  (required), so those resistors cannot be built.

## Rubycon

- Route: the Product Search CSV export at https://tools.rubycon.co.jp/product-search/ (aluminium, film, polymer multilayer, hybrid). No
  robots.txt on that host. Capacitance is in uF, ESR in mOhm, ripple in mArms.
- robots.txt on `rubycon.co.jp` disallows the catalogue PDFs under `/wp-content/uploads/`.
- Trap: the redistribution terms on the CSV are restrictive; read them before you copy values. The per-part page URLs under
  `rubycon.co.jp/en/catalog/<part>` return 404 now, including for listed parts, so do not cite them.
- The complete vendor list is already in TAS. Suffix, taping and forming variants (G4, T8, CC, TA, NB), dotless case sizes (`125X20` for
  12.5X20) and old codes are not on the vendor's list.

## STMicroelectronics

- Route: the product selector grids, JSON per sub-class at `https://www.st.com/bin/st/selectors/cxst/en.cxst-ps-grid.html/<SCxxxx>.json`
  (columns plus rows; each row links to a product folder), found through the sitemap (`sitemap1..10.xml`) and the product class pages.
- Bot gate: `curl` is reset; only a headless browser works, and only about one session in three is usable. A usable session answers in-page
  requests; open a fresh session when every call resets. Keep at least 2 s between calls.
- Traps: the grids list only active and NRND families; obsolete parts have no page or row. A value is trustworthy only when the family
  datasheet prints the same number next to a matching label. Selector VF mixes Tj = 25 C and 125 C values; keep VF only at 25 C maximum and
  with its test current. The selector's "adjustable" flag is per family, not per part. The selector response time has no overdrive
  condition, so it is not a propagation delay. The family page prints orderable codes, but the citation rule still needs a URL that contains
  the code, so it stays series-level.
- Usually not listed: obsolete devices, thyristors, EMI filters and current-sensing grids are other categories; most MOSFET selector rows
  have no gate-threshold column.

## TDK

- Route: the part-search endpoints behind https://product.tdk.com (category-scoped POSTs returning 100 rows per page).
- Bot gate: `curl` and plain page requests get 403; a full headless browser can call the endpoints. Back off hard after a 403 burst (25
  minutes). Use a generic browser user agent with no personal data.
- Traps: capacitance in the part number code must match the vendor value. Film dielectric may be blank; derive it from the style only when
  the datasheet says so, and skip MKD or MKK rows with no dielectric. AC-only film capacitors carry an AC rating and no DC rating. Truncated
  codes (`C1608X7R1H104K`) are prefixes of longer TDK codes; do not expand them unless every expansion has identical data.
- Usually not listed: parts still "In Development", and truncated or distributor-spelled codes.

## TE Connectivity

- robots.txt: `api.te.com` disallows `/api` for all agents. Never call `api.te.com`, and never fetch under `www.te.com/commerce`. The
  robots-disallowed spec PDFs are also off limits. Use only `https://www.te.com/en/product-<number>.html` product pages, read in a headless
  browser (the site is behind a bot gate for `curl`), taking the values from the visible text. Check robots.txt of the API host itself, not
  just the www host: a page's own scripts calling an API do not license you to call it.
- Identity: TE has an internal number (the part number in the URL) and a marketing or customer number. Cite the page for the internal
  number; the marketing number is an order code. Marketing numbers alone often do not resolve.
- Traps: hybrid power-plus-signal headers fail a current-versus-pitch plausibility check and should be reviewed; obsolete parts' pages still
  serve and carry a status.
- Usually not listed: many magnetics-labelled codes are actually power entry modules and filters; contacts, terminals, housings and hardware
  are accessories, not connectors; a large share of customer numbers return 404.

## Texas Instruments

- Route: the selection API
  `https://www.ti.com/selectionmodel/api/gpn/result-list?destinationId=<id>&destinationType=GPT&mode=parametric&locale=en-US` returns a
  category's active and preview parts with values in SI under `paramList[].value.base` (percent values stay percent). Orderable tables on
  `https://www.ti.com/product/<GPN>` map an ordering code to its generic part number.
- Bot gate: raw `curl` gets 404 on the selection API; call it from a browser on ti.com.
- Traps: some calls cap a result at 200 rows with no paging; split by sub-category. Records are keyed by the device name (GPN, for example
  `TLV2404`), never the ordering code (`TLV2404IDR`). `/NOPB` and a trailing `G4` are spelling noise. Mosfet Qg, Qgs and Qgd are separate
  parameter ids; check them. Dropout voltage has no test current in the lists.
- Usually not listed: only active and preview parts are in the lists and obsolete product pages 404, so legacy SN, UC and PT families, and
  parts like TPS5210 or TLV1549 that have a page but no list entry, have no parametric values. Digital potentiometers carry no
  total-resistance column.

## Toshiba

- Route: product pages on https://toshiba.semicon-storage.com, one directory per class (bipolar transistors, MOSFETs, diodes, power
  management ICs). A bogus part shows a soft 404 ("Sorry, the page").
- Bot gate: `curl` is blocked; a headless browser works.
- Traps: bias-resistor transistors are stored as plain transistors without R1 and R2. Diode VF is the max row at the highest test current;
  leakage only when its VR equals the rated voltage. Bound P-channel threshold voltages numerically (`minimum` is the most negative).
- Usually not listed: logic, bus and analog switches, motor and solenoid drivers, photocouplers and varactors are other categories; some
  quarantined Toshiba MOSFETs only had incomplete data.

## Vishay

- Route, parts and values: series datasheets at `https://www.vishay.com/docs/<docid>/<file>.pdf` (plain `curl` works; `pdftotext -layout`
  reads the front-page "Product summary" cleanly). A real document id returns a PDF; a made-up one 404s. A 200 PDF proves nothing about
  which document you got: `https://www.vishay.com/doc?<n>` serves real PDFs but some (47140) are the site terms. Check that the PDF names
  the part. The list of every orderable code per document is on the product page's design-tools tab
  (`/en/product/<doc>/tab/designtools-ppg/`, `qualityTabResults`, `MAT_NO`), and that list is what lets you accept a suffix variant the
  datasheet does not print.
- Route, discovery: product pages `https://www.vishay.com/en/product/<docid>/` carry `pageProps.pCorResults[0]` with the document id, file
  name and a headline listing every part number the datasheet covers. Sitemaps (`sitemap.xml.gz`, then `sitemap1.xml.gz`) list the product
  pages. The older parametric grids sit inline in the page's `__NEXT_DATA__` (no separate data request) on leaf selector pages only: rows in
  `props.pageProps.webtableResults`, column map in `getPageData[0].node.gatewaySelector[0].gatewayConfig[0].tableColumns`. Some category
  pages no longer render these, so confirm a sample before relying on the route. The MOSFET tree has a separate automotive sub-tree that is
  not linked from the main one.
- No bot gate: plain `curl` with a normal browser user agent returns 200. robots.txt disallows `/search`; use the sitemaps.
- Column traps: grid columns are keyed by opaque `P70xx` ids that mean different things in different grid views, and the column map shipped
  with a page can itself be wrong (an id labelled as a gate resistance held the drain current). Decode the map on every pull and check one
  value by eye. Dual devices publish one row per channel, so tie a record to a row by its own R_DS(on), not by part number or position. A
  heading with several part numbers is a comma list, one row, many parts (`IRF730, SiHF730`).
- Identity: store the device name the datasheet prints (`Si7942DP`), not the ordering code (`Si7942DP-T1-E3`). Accept an order code only if
  its suffix is printed attached to a part name in the series datasheet or the vendor's own part list for that document names it.
- Value traps: PRIMARY CHARACTERISTICS blocks print VF at 125 C; take VF from the 25 C row of the electrical table, the maximum column, at
  the highest test current not above the rated current. Dual and centre-tap rectifiers: the schema holds per-diode values, so convert "2 x
  10 A" to 10 A. Resistor power ratings may be given at several characteristics (CW: U and V); document the choice. Marking-code columns
  shift between datasheets, so read the header.
- Usually not listed: legacy inductor families (IM, IR, IMS, MS, WVL, PC) have no datasheet on vishay.com; resistor packaging letters the
  datasheet does not define stay out unless the vendor's part list names the code; codes outside a datasheet's stated range or E-series are
  held back.

## Traps that recur across vendors

- Part-number identity: the device name and the ordering code are different things. Store the name the datasheet title prints; the live
  catalogue treats an ordering-code variant of a live part as already present. Packaging, reel and tape suffixes are spelling noise only
  where the vendor says so.
- A dot is identity (`SMCJ90A` is not `SMCJ9.0A`). A hyphen can be identity too: Eaton prints the decimal point of a standoff voltage as a
  hyphen (`10BJ6-0A` is 6.0 V, `10BJ60A` is 60 V). A normalising key that strips hyphens, spaces and slashes collides on such vendors.
  Before trusting a normalised join, count the distinct raw spellings per key in the catalogue and require an exact match where more than
  one exists. Anchor identifier matches at both ends: a prefix of a real code is not that code. A join key tested only against the rows you
  hold measures your sample, not the vendor's catalogue.
- A column that looks 1,000 times off is usually the wrong column, not a unit slip. Before rescaling, check what the column really holds (a
  can diameter stored as ESL, a gate-source charge under a Qg heading, a clamping voltage under a pulse-current label). Unit prefixes inside
  cells (`"40 uA"`) and unit headings that lie (`IRRM (mA)` in microamps) both exist; read the unit off what remains after the number, not
  by substring (the letters `ma` are in `if [max] (a)`).
- Multi-valued cells: `Q1=Q2=95`, comma lists of part numbers, min/typ/max in one cell, two ratings separated by a slash. Parse the
  structure, do not grab the first number.
- Rating conditions: store the condition with the value and take the one the schema defines (continuous drain current and power at T_C = 25
  C, not T_A). Never mix conditions inside one record. If a datasheet prints only a different condition, leave the field out.
- Series and parallel connections: a magnetics datasheet that prints series and parallel columns needs one electrical entry per connection,
  named by the sheet's own header. A single entry mixes connections (parallel inductance with a series current rating).
- Mechanical dimensions have no fixed axis convention: the same package stores its long tab axis under `height` in one record and its
  thickness under `height` in another. Bound geometry on the smallest of the three dimensions, never on a single named axis or on lead span.
- Temperature-coefficient conventions: class 1 capacitors use a ppm/K coefficient, class 2 use a percent band, some vendors print a bare
  plus-minus percent magnitude. A zero is a legitimate midpoint of a banded class-2 or a real C0G value; tell a placeholder from real data
  by what the object contains (only a nominal key), not by the value.
- A cited URL must name the part. A category listing, a search result, a distributor page or a document that redirects to a search box is
  not a citation. A 200 status and a PDF content type do not show you got the right document; open it and check it prints the part.
- Existence checks need controls: before you use a vendor page as an oracle, test it with parts you know are real but obsolete. If those
  404, the route measures current catalogue membership, not existence, and a 404 cannot support a verdict.
- Real browser needed: when a bot challenge answers 200 with a small HTML page, status, size, content-type and redirect checks from `curl`
  all pass while telling you nothing. Judge in a browser.
- Same number, different maker: generic numbers (`SMBJ60A`, `ES1A`, `B120`, `TL431`) are sold by several manufacturers. Flag collisions in
  the pull request instead of renaming or merging.
- Required-by-schema fields are not optional for you: if the vendor does not print a required value, the part stays out with a reason. Do
  not substitute a typical value, a neighbouring series value, or a value computed from another field unless it is arithmetic stated as such
  in the provenance.
- When a source gives only a series document for many parts, mark it as a series-level verification, not as a document that names the part.
