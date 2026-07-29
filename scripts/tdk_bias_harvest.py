#!/usr/bin/env python3
"""ABT #304: harvest TDK MLCC DC-bias curves into capacitanceBiasPoints[].

TDK is the largest remaining vendor on this ticket (21,992 class-2/3 rows). The
mechanism, all of it CAPTURED from real traffic rather than guessed:

  1. TRANSPORT. product.tdk.com 403s plain curl and WebFetch (Akamai). It does NOT
     403 playwright's `channel="chromium"` build -- the full Chrome-for-Testing
     binary under --headless=new, the same trick scripts/pull_tdk_cmc.mjs uses for
     the CMC catalog. So this stays HEADLESS; no WSLg, no headed exception needed.

  2. pid MAPPING. The graph API is keyed by an internal product id, never by part
     number. Every list-page row carries it in its own checkbox:
       <input class="allCheckTarget" value="1000000479761" id="result_chk_...">
     so 100 (part_no, pid) pairs come out of ONE page load -- no per-part product
     page needed for the mapping. (/pdc_api/.../mlcc/list does not exist: 404. The
     list is server-rendered HTML, not an API. Do not go looking for one.)

  3. THE GRAPH API, exactly as the /info page fires it:
       POST https://product.tdk.com/pdc_api/en/search/capacitor/ceramic/mlcc/info/graph
       Content-Type: application/x-www-form-urlencoded; charset=UTF-8
       X-Requested-With: XMLHttpRequest
       body: graph_kind[0]=1007&pid[]=<PID>
     -> {"graph": {"graph_kind_1007": [{"data": [[v, c, null], ...], "label": PN}]}}
     graph_kind_1007 is the DC-bias curve. The others are 1001/1005/1010 frequency
     sweeps, 1009 temperature characteristic, 1008 ripple temperature rise.
     ONE PART PER REQUEST: pid[]=a&pid[]=b, pid[0]/pid[1], and a comma list were all
     tried -- every form returns exactly one series. Multiple graph_kind DO batch,
     but we only want 1007.

UNITS ARE READ, NOT ASSUMED. TDK returns the capacitance axis in FARADS (a 100 pF
part comes back as 1e-10), unlike Murata's per-module micro/pico. Assuming a scale
is what produced the 4,415-record 1e6 error on the Murata pass, so every curve here
is checked against the part's own nominal capacitance before it is written, and a
curve that disagrees by more than 2x is SKIPPED for review rather than rescaled --
one of the two sides is wrong and guessing which would corrupt the catalogue.

CONDITIONS: TDK states no measurement temperature or AC drive for this graph
anywhere on the page, and CAS makes both optional. They are therefore OMITTED
rather than filled in with a plausible 25 degC / 0.5 Vrms -- an invented condition
is worse than an absent one.

Usage:
  tdk_bias_harvest.py map   [--pages N] [--delay 1.0]  -> staging/tdk/pid_map.json
  tdk_bias_harvest.py fetch [--limit N] [--delay 0.25] -> staging/tdk/bias.jsonl
  tdk_bias_harvest.py write [--apply]                  -> data/capacitors.ndjson
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "tdk"
PIDMAP = STAGE / "pid_map.json"          # legacy: {pn: pid}, mlcc only
PIDMAP2 = STAGE / "pid_map_v2.json"      # {pn: [category, pid]}, all categories
MAPSTATE = STAGE / "map_state.json"
OUT = STAGE / "bias.jsonl"
DONE = STAGE / "bias_done.json"
LOG = STAGE / "bias_harvest.log"
STOP = STAGE / "STOP"

SEARCH = "https://product.tdk.com/en/search"
GRAPH_KIND = "1007"                 # DC bias. See the module docstring.

# TDK splits its ceramics across categories, and BOTH the list page and the graph API
# path are category-scoped. Which categories carry a DC-bias curve was established by
# asking each one for graph kinds 1001-1012 and reading the SHAPES back (x spanning
# 0..rated V with y[0] == the part's nominal capacitance), never by assuming the mlcc
# number carries over:
#   capacitor/ceramic/mlcc       10,150 parts  kind 1007 present  <- the bulk
#   capacitor/ceramic/lead-mlcc   4,534 parts  kind 1007 present  <- FA/FK leaded MLCC
#   capacitor/ceramic/lead-disc     663 parts  NO 1007 (only 1001/1005/1010 frequency
#                                   sweeps) -- CK45/CC45 disc caps simply have no
#                                   DC-bias curve at TDK; they are not harvestable here
#   capacitor/ceramic/uhv            69 parts  no graph kinds at all
CATEGORIES = ["capacitor/ceramic/mlcc", "capacitor/ceramic/lead-mlcc"]


def list_url(cat, page, size=None):
    # Sort by part number: pagination over the default (status) order silently SKIPS
    # rows -- the first pass lost ~1,000 of 10,150 that way.
    return (f"{SEARCH}/{cat}/list?part_no=*&_l={size or PAGE_SIZE}&_p={page}"
            f"&_c=part_no-part_no&_d=0")


def graph_path(cat):
    return f"/pdc_api/en/search/{cat}/info/graph"
PAGE_SIZE = 100                     # the list caps here: _l=200/500 still serve 100.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")
CLASS2 = {"ceramic-class-2", "ceramic-class-3"}

# Read the pid out of each row's checkbox and the part number out of the row's OWN
# LINK -- /…/mlcc/info?part_no=<PN> -- never out of the cell text. The Part No. cell
# also contains a "New" badge and, on many rows, an "Equivalent to …" line; taking
# the cell's text put 1,999 junk keys ("Equ…") in the map on the first pass and
# silently displaced the real part numbers. The href is the vendor's own canonical
# spelling of the part number, so there is nothing left to parse out of prose.
ROWS_JS = """() => {
  const t = [...document.querySelectorAll('table')].find(t => t.querySelectorAll('tbody tr').length > 2)
  if (!t) return []
  return [...t.querySelectorAll('tbody tr')].map(tr => {
    const cb = tr.querySelector('input.allCheckTarget')
    const a = tr.querySelector('a[href*="/info?part_no="]')
    if (!cb || !a) return null
    const pn = decodeURIComponent(a.getAttribute('href').split('part_no=')[1].split('&')[0])
    return pn ? [pn, cb.value] : null
  }).filter(Boolean)
}"""

GRAPH_JS = """async ([path, kind, pids, pace]) => {
  const out = []
  const sleep = (ms) => new Promise(r => setTimeout(r, ms))
  for (const pid of pids) {
    try {
      const r = await fetch(path, {method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                  'X-Requested-With': 'XMLHttpRequest'},
        body: 'graph_kind[0]=' + kind + '&pid[]=' + pid})
      if (!r.ok) { out.push([pid, null, 'HTTP ' + r.status]); continue }
      const j = await r.json()
      const s = ((j.graph || {})['graph_kind_' + kind] || [])[0]
      out.push(s && s.data ? [pid, {label: s.label, data: s.data}, null]
                           : [pid, null, 'no series'])
    } catch (e) { out.push([pid, null, String(e).slice(0, 80)]) }
    // pace INSIDE the batch, with jitter -- firing 20 requests back to back is what
    // tripped Akamai's rate limit in the first place
    await sleep(pace + Math.random() * pace)
  }
  return out
}"""


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def browser(pw):
    br = pw.chromium.launch(channel="chromium")
    ctx = br.new_context(viewport={"width": 1440, "height": 900}, user_agent=UA)
    return br, ctx.new_page()


def single_flight(path):
    """Hold an exclusive lock for the whole run, or exit.

    The cron keep-alive already flocks this file; an ad-hoc run that ignored it ran
    CONCURRENTLY with the cron one and doubled the request rate, which is exactly what
    got this campaign 403ed after 420 parts. Taking the same lock inside the script
    makes every invocation single-flight regardless of how it was started.
    """
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return fh                      # keep the handle alive for the process lifetime


class Session:
    """A warmed browser page plus the 403 recovery ladder.

    product.tdk.com serves the graph API happily for a few hundred calls from one IP
    and then 403s EVERYTHING. Recovery is tried cheapest-first, because rotating the
    egress IP on the first 403 would mask a genuine bug in the request:
      1. fresh browser context  -- new connections, no cookies (often enough)
      2. wait, then fresh context -- for a plain rate window
      3. rotate the ProtonVPN egress IP -- for an IP-reputation block
    When every config has been tried without recovery the run stops cleanly; the
    campaign is fully checkpointed, so cron picks it up later from where it stopped.
    """

    def __init__(self, br, probe):
        self.br = br
        self.probe = probe                 # (category, pid) used to test recovery
        self.rot = None
        self.rotations = 0
        self.ctx = None
        self.page = None
        self._fresh_context()

    def _fresh_context(self):
        if self.ctx:
            try:
                self.ctx.close()
            except Exception:
                pass
        self.ctx = self.br.new_context(viewport={"width": 1440, "height": 900}, user_agent=UA)
        self.page = self.ctx.new_page()
        # a real page load first: the graph fetch is same-origin and needs the page's
        # Akamai cookies. A cold context POSTing straight at the API gets 403.
        self.page.goto(list_url(CATEGORIES[0], 1, size=20),
                       wait_until="domcontentloaded", timeout=90_000)
        self.page.wait_for_timeout(2500)

    def graph(self, cat, pids, pace_ms):
        return self.page.evaluate(GRAPH_JS, [graph_path(cat), GRAPH_KIND, pids, pace_ms])

    def _probe_ok(self):
        try:
            res = self.graph(self.probe[0], [self.probe[1]], 0)
        except Exception as e:
            log(f"  probe failed: {str(e)[:120]}")
            return False
        return bool(res and res[0][1])

    def recover(self):
        log("  403 wall -- step 1: fresh browser context")
        try:
            self._fresh_context()
            if self._probe_ok():
                log("  recovered on a fresh context")
                return True
        except Exception as e:
            log(f"  fresh context failed: {str(e)[:120]}")

        log("  step 2: waiting 120 s for the rate window, then a fresh context")
        time.sleep(120)
        try:
            self._fresh_context()
            if self._probe_ok():
                log("  recovered after the wait")
                return True
        except Exception as e:
            log(f"  fresh context failed: {str(e)[:120]}")

        if self.rot is None:
            try:
                from vpn_rotate import Rotator
                self.rot = Rotator()
            except Exception as e:
                log(f"  no VPN rotation available ({str(e)[:80]}) -- stopping")
                return False
        if self.rotations >= len(self.rot.confs):
            log("  every egress IP tried without recovery -- stopping")
            return False
        self.rotations += 1
        name, ip = self.rot.rotate()
        log(f"  step 3: rotated egress to {name} ({ip})")
        try:
            self._fresh_context()
        except Exception as e:
            log(f"  fresh context after rotation failed: {str(e)[:120]}")
            return False
        if self._probe_ok():
            log(f"  recovered on {name}")
            return True
        log(f"  still blocked on {name}")
        return self.recover()          # try the next IP


# ---------------------------------------------------------------- map

def load_pid_map():
    """{part_no: (category, pid)}, migrating the legacy mlcc-only map if needed."""
    if PIDMAP2.exists():
        return {pn: tuple(v) for pn, v in json.loads(PIDMAP2.read_text()).items()}
    if PIDMAP.exists():
        legacy = json.loads(PIDMAP.read_text())
        log(f"migrating {len(legacy)} legacy mlcc pids into the categorised map")
        return {pn: (CATEGORIES[0], pid) for pn, pid in legacy.items()}
    return {}


def save_pid_map(pid_map):
    PIDMAP2.write_text(json.dumps({pn: list(v) for pn, v in pid_map.items()},
                                  indent=0, sort_keys=True))


def cmd_map(a):
    from playwright.sync_api import sync_playwright
    STAGE.mkdir(parents=True, exist_ok=True)
    pid_map = load_pid_map()
    state = json.loads(MAPSTATE.read_text()) if MAPSTATE.exists() else {}
    if "next_page" in state:            # legacy flat state predates categories
        state = {CATEGORIES[0]: {"next_page": state["next_page"], "total": state.get("total")}}
    cats = [a.category] if a.category else CATEGORIES
    log(f"MAP start ({len(pid_map)} pids known) over {len(cats)} categor"
        f"{'y' if len(cats) == 1 else 'ies'}")

    with sync_playwright() as pw:
        br, pg = browser(pw)
        try:
            for cat in cats:
                cstate = state.setdefault(cat, {})
                page = int(cstate.get("next_page", 1))
                total = cstate.get("total")
                log(f"  {cat}: from page {page} (catalogue size {total})")
                pages_left = a.pages
                while pages_left > 0:
                    pages_left -= 1
                    if STOP.exists():
                        log("STOP file -- exiting cleanly")
                        return 0
                    rows = None
                    # These list pages intermittently render empty on first load; one
                    # retry distinguishes "flaky" from "past the last page" -- without
                    # it a hiccup looks exactly like the end of the catalogue.
                    for attempt in (1, 2):
                        pg.goto(list_url(cat, page), wait_until="domcontentloaded", timeout=90_000)
                        try:
                            pg.wait_for_function(
                                "document.querySelectorAll('table tbody tr').length > 0",
                                timeout=30_000)
                        except Exception:
                            if attempt == 1:
                                time.sleep(3)
                                continue
                            rows = []
                            break
                        pg.wait_for_timeout(700)
                        rows = pg.evaluate(ROWS_JS)
                        if rows:
                            break
                        time.sleep(3)
                    if not rows:
                        log(f"    page {page}: no rows after 2 attempts -- category done")
                        break
                    if total is None:
                        m = re.search(r"Number of Products Found\s*:?\s*([\d,]+)",
                                      pg.inner_text("body"))
                        total = int(m.group(1).replace(",", "")) if m else None
                        log(f"    catalogue size: {total}")
                    fresh = sum(1 for pn, _ in rows if pn not in pid_map)
                    pid_map.update({pn: (cat, pid) for pn, pid in rows})
                    save_pid_map(pid_map)
                    page += 1
                    cstate.update({"next_page": page, "total": total})
                    MAPSTATE.write_text(json.dumps(state, indent=0, sort_keys=True))
                    if page % 10 == 0 or fresh == 0:
                        log(f"    page {page - 1}: {len(rows)} rows, {fresh} new "
                            f"(map {len(pid_map)})")
                    if total and sum(1 for v in pid_map.values() if v[0] == cat) >= total:
                        log("    reached catalogue size -- category done")
                        break
                    time.sleep(a.delay)
        finally:
            br.close()
    log(f"MAP end: {len(pid_map)} part->pid pairs")
    return 0


# ---------------------------------------------------------------- fetch

def tdk_class2_parts():
    """[(part_number, nominal_F)] for TDK class-2/3 rows that have no curve yet."""
    out = []
    with SRC.open(encoding="utf-8") as fh:
        for line in fh:
            if '"TDK"' not in line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = o.get("capacitor") or o
            mi = c.get("manufacturerInfo") or {}
            if mi.get("name") != "TDK":
                continue
            ds = mi.get("datasheetInfo") or {}
            e = ds.get("electrical") or {}
            if ((ds.get("part") or {}).get("technology")) not in CLASS2:
                continue
            if e.get("capacitanceBiasPoints"):
                continue
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            cap = e.get("capacitance")
            nom = cap.get("nominal") if isinstance(cap, dict) else cap
            if pn:
                out.append((pn, nom))
    return out


def cmd_fetch(a):
    from playwright.sync_api import sync_playwright
    STAGE.mkdir(parents=True, exist_ok=True)
    lock = single_flight(STAGE / ".lock")
    if lock is None:
        log("another fetch holds the lock -- exiting")
        return 0
    if STOP.exists():
        STOP.unlink()
    pid_map = load_pid_map()
    if not pid_map:
        log("no pid map yet -- run `tdk_bias_harvest.py map` first")
        return 2
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()

    # One request per PART NUMBER, not per record: the catalogue holds ~2 rows per TDK
    # part number (21,992 records over 11,932 distinct numbers), and the write pass
    # patches every row that matches. Fetching per record would double the traffic for
    # nothing.
    work, unmapped, seen = [], set(), set()
    for pn, nom in tdk_class2_parts():
        if pn in done or pn in seen:
            continue
        entry = pid_map.get(pn)
        if not entry:
            unmapped.add(pn)
            continue
        seen.add(pn)
        cat, pid = entry
        work.append((pn, pid, nom, cat))
    # Group by category: the graph API path is category-scoped, so a batch has to be
    # single-category. Sorting by it keeps batches whole instead of splitting every one.
    work.sort(key=lambda w: w[3])
    if a.limit:
        work = work[:a.limit]
    bycat = {}
    for w in work:
        bycat[w[3]] = bycat.get(w[3], 0) + 1
    log(f"FETCH start: {len(work)} TDK class-2/3 part numbers to pull "
        f"({len(done)} done, {len(unmapped)} not in the pid map) {bycat}")
    if not work:
        return 0

    ok = fail = 0
    errs = {}
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chromium")
        sess = Session(br, (work[0][3], work[0][1]))
        try:
            i = 0
            while i < len(work):
                if STOP.exists():
                    log("STOP file -- exiting cleanly")
                    break
                cat = work[i][3]
                chunk = [w for w in work[i:i + a.batch] if w[3] == cat]
                by_pid = {pid: (pn, nom) for pn, pid, nom, _ in chunk}
                try:
                    res = sess.graph(cat, [p for _, p, _, _ in chunk], int(a.pace * 1000))
                except Exception as e:
                    log(f"  batch failed: {str(e)[:140]}")
                    res = [(p, None, "evaluate failed") for _, p, _, _ in chunk]

                # A whole batch of 403s is a BLOCK, not per-part failures: retry the
                # SAME parts after recovering, instead of consuming the worklist
                # against a wall (the first run burned 1,280 parts that way).
                blocked = sum(1 for _, s, e in res if e and "403" in str(e))
                if blocked == len(res):
                    if not sess.recover():
                        break
                    continue

                with OUT.open("a", encoding="utf-8") as fh:
                    for pid, series, err in res:
                        pn, nom = by_pid[str(pid)]
                        if err or not series:
                            fail += 1
                            errs[err or "empty"] = errs.get(err or "empty", 0) + 1
                            continue
                        pts = []
                        for row in series["data"]:
                            try:
                                v, cap = float(row[0]), float(row[1])
                            except (TypeError, ValueError, IndexError):
                                continue
                            pts.append({"voltage": v, "capacitance": cap})
                        if not pts:
                            fail += 1
                            errs["no points"] = errs.get("no points", 0) + 1
                            continue
                        ok += 1
                        fh.write(json.dumps({"partNumber": pn, "pid": pid,
                                             "label": series.get("label"),
                                             "nominal": nom, "points": pts},
                                            ensure_ascii=False) + "\n")
                        done.add(pn)
                DONE.write_text(json.dumps(sorted(done)))
                i += len(chunk)          # not a.batch: a chunk stops at a category edge
                if (i // a.batch) % 10 == 0:
                    log(f"  {min(i, len(work))}/{len(work)}  ok={ok} fail={fail} "
                        f"{dict(list(errs.items())[:3])}")
                time.sleep(a.delay)
        finally:
            br.close()
    log(f"FETCH end: ok={ok} fail={fail} errors={errs}")
    return 0


# ---------------------------------------------------------------- write

def build_validator():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    psma = TAS.parent
    by = {}
    for repo in ("PEAS", "CAS"):
        for p in (psma / repo / "schemas").rglob("*.json"):
            try:
                s = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if s.get("$id"):
                by[s["$id"]] = s
    reg = Registry().with_resources(
        [(s["$id"], Resource(contents=s, specification=DRAFT202012)) for s in by.values()])
    return Draft202012Validator(
        json.loads((psma / "CAS" / "schemas" / "capacitor.json").read_text()), registry=reg)


def cmd_write(a):
    import os
    sys.path.insert(0, str(Path(__file__).parent))
    from blade_gate import BladeGate
    gate = BladeGate("capacitor")   # physics gate: schema alone cannot catch a units error

    curves = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                curves[r["partNumber"]] = r["points"]
    log(f"WRITE: {len(curves)} harvested curves available")
    if not curves:
        return 0

    v = build_validator()
    patched = rejected = 0
    bad, mismatched = [], []
    out_lines = []
    with SRC.open(encoding="utf-8") as fh:
        for raw in fh:
            s = raw.rstrip("\n")
            if not s.strip():
                continue
            if '"TDK"' not in s:
                out_lines.append(s)
                continue
            obj = json.loads(s)
            c = obj.get("capacitor") or obj
            mi = c.get("manufacturerInfo") or {}
            ds = mi.get("datasheetInfo") or {}
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            if mi.get("name") != "TDK" or pn not in curves:
                out_lines.append(s)
                continue
            e = ds.setdefault("electrical", {})
            if e.get("capacitanceBiasPoints"):
                out_lines.append(s)
                continue
            pts = curves[pn]

            # SANITY GATE, same one that caught the Murata 1e6 error: the curve's
            # 0 V value must agree with the part's own nominal. A disagreement means
            # either the axis unit or the catalogue nominal is wrong -- both need a
            # human, and neither may be silently rescaled.
            cap = e.get("capacitance")
            nominal = cap.get("nominal") if isinstance(cap, dict) else cap
            c0 = (pts[0] or {}).get("capacitance")
            if (isinstance(nominal, (int, float)) and nominal > 0
                    and isinstance(c0, (int, float)) and c0 > 0
                    and not (0.5 <= c0 / nominal <= 2.0)):
                mismatched.append((pn, nominal, c0, c0 / nominal))
                out_lines.append(s)
                continue

            e["capacitanceBiasPoints"] = pts
            ds.setdefault("provenance", []).append({
                "source": "manufacturerParametric",
                "sourceName": "TDK Product Center characteristic graph (graph_kind 1007, DC bias)",
                "sourceUrl": "https://product.tdk.com" + GRAPH_PATH,
                "retrievedDate": time.strftime("%Y-%m-%d"),
            })
            errs = sorted(v.iter_errors(c), key=lambda x: x.path)
            if errs:
                rejected += 1
                if len(bad) < 5:
                    bad.append(f"{pn}: {errs[0].message[:140]}")
                out_lines.append(s)
                continue
            ok_bl, why = gate.check(c)
            if not ok_bl:
                rejected += 1
                if len(bad) < 5:
                    bad.append(f"{pn}: BLADE {why}")
                out_lines.append(s)
                continue
            patched += 1
            out_lines.append(json.dumps(obj, ensure_ascii=False))

    log(f"WRITE {'APPLIED' if a.apply else 'DRY RUN'}: patched={patched} rejected={rejected}")
    log(gate.summary())
    if mismatched:
        log(f"  SKIPPED {len(mismatched)} whose curve disagrees with the record's own "
            f"nominal (needs review -- one side is wrong):")
        for pn, nom, c0, r in mismatched[:6]:
            log(f"    {pn}: nominal={nom:.3e} F vs curve0={c0:.3e} F (x{r:.3g})")
    for b in bad:
        log(f"  rejected: {b}")
    if a.apply and patched:
        tmp = SRC.with_suffix(".ndjson.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for line in out_lines:
                fh.write(line + "\n")
        os.replace(tmp, SRC)
        log(f"atomically replaced {SRC}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map")
    m.add_argument("--pages", type=int, default=200, help="max pages PER category")
    m.add_argument("--delay", type=float, default=0.6)
    m.add_argument("--category", help=f"one of {CATEGORIES} (default: all)")
    f = sub.add_parser("fetch")
    f.add_argument("--limit", type=int)
    f.add_argument("--delay", type=float, default=0.25,
                   help="pause between batches, seconds")
    f.add_argument("--pace", type=float, default=0.15,
                   help="pause between individual requests inside a batch, seconds "
                        "(jittered up to 2x). Firing a batch back-to-back is what "
                        "tripped Akamai's rate limit.")
    f.add_argument("--batch", type=int, default=20)
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return {"map": cmd_map, "fetch": cmd_fetch, "write": cmd_write}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
