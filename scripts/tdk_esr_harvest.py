#!/usr/bin/env python3
"""ABT #390: harvest TDK MEASURED ESR-vs-frequency curves into esrPoints[].

Fourth vendor of the ESR re-harvest. Reuses tdk_bias_harvest.py wholesale — its pid map
(already built, 14,684 parts), its warmed browser Session, and its 403 recovery ladder —
and changes only the graph kind and where the result is stored.

WHICH KIND IS ESR: graph_kind_1005. Settled by reading the numbers for
C3216X7R2A104K160AA (100 nF), all three sweeps spanning 1 kHz..3 GHz:

  1001  starts 1540 Ω   -> Xc at 1 kHz for 100 nF is 1592 Ω, so this is |Z|
  1005  starts 10.55 Ω  -> 10.55/1592 = tanδ 0.0066, exactly right for an X7R  <- ESR
  1010  starts 1.03e-7  -> the part's nominal capacitance in farads, and it goes
                           NEGATIVE past resonance, which only effective C does

Three mutually-confirming readings, none of them a guess about column order.

  tdk_esr_harvest.py fetch [--limit N]   -> staging/tdk/esr.jsonl
  tdk_esr_harvest.py write [--apply]     -> data/capacitors.ndjson
"""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tdk_bias_harvest as tdk          # pid map, Session, 403 ladder, list_url, graph_path

TAS = Path(__file__).resolve().parent.parent
SRC = TAS / "data" / "capacitors.ndjson"
STAGE = TAS / "staging" / "tdk"
OUT = STAGE / "esr.jsonl"
DONE = STAGE / "esr_done.json"
NODATA = STAGE / "esr_nodata.json"
LOG = STAGE / "esr_harvest.log"
STOP = STAGE / "STOP"

ESR_KIND = "1005"
REF_HZ = 100_000.0
CLASS_ANY = None                        # ESR is worth having for every ceramic, not just class-2


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def tdk_parts():
    """[(part_number, nominal_F)] for TDK ceramics with no ESR curve yet."""
    out, seen = [], set()
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
            if e.get("esrPoints"):
                continue
            pn = (ds.get("part") or {}).get("partNumber") or mi.get("reference")
            cap = e.get("capacitance")
            nom = cap.get("nominal") if isinstance(cap, dict) else cap
            if pn and pn not in seen:
                seen.add(pn)
                out.append((pn, nom))
    return out


def cmd_fetch(a):
    from playwright.sync_api import sync_playwright
    import fcntl
    STAGE.mkdir(parents=True, exist_ok=True)
    lock = (STAGE / ".lock_esr").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another ESR fetch holds the lock -- exiting")
        return 0
    if STOP.exists():
        STOP.unlink()

    pid_map = tdk.load_pid_map()
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    nodata = json.loads(NODATA.read_text()) if NODATA.exists() else {}

    work, unmapped = [], 0
    for pn, nom in tdk_parts():
        if pn in done or pn in nodata:
            continue
        entry = pid_map.get(pn)
        if not entry:
            unmapped += 1
            continue
        cat, pid = entry
        work.append((pn, pid, nom, cat))
    work.sort(key=lambda w: w[3])       # batches must be single-category
    if a.limit:
        work = work[:a.limit]
    log(f"ESR FETCH start: {len(work)} TDK parts ({len(done)} done, {unmapped} unmapped, "
        f"{len(nodata)} with no published graph)")
    if not work:
        return 0

    ok = fail = 0
    errs = {}
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chromium")
        sess = tdk.Session(br, (work[0][3], work[0][1]))
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
                    res = sess.page.evaluate(
                        tdk.GRAPH_JS,
                        [tdk.graph_path(cat), ESR_KIND, [p for _, p, _, _ in chunk],
                         int(a.pace * 1000)])
                except Exception as e:
                    log(f"  batch failed: {str(e)[:120]}")
                    res = [(p, None, "evaluate failed") for _, p, _, _ in chunk]
                if sum(1 for _, s, err in res if err and "403" in str(err)) == len(res):
                    if not sess.recover():
                        break
                    continue
                with OUT.open("a", encoding="utf-8") as fh:
                    for pid, series, err in res:
                        pn, nom = by_pid[str(pid)]
                        if err or not series:
                            fail += 1
                            key = err or "no series"
                            errs[key] = errs.get(key, 0) + 1
                            if key in ("no series", "empty"):
                                nodata[pn] = key
                                NODATA.write_text(json.dumps(nodata, indent=0, sort_keys=True))
                            continue
                        pts = []
                        for row in series["data"]:
                            try:
                                f, esr = float(row[0]), float(row[1])
                            except (TypeError, ValueError, IndexError):
                                continue
                            if f > 0 and esr > 0:
                                pts.append((f, esr))
                        if len(pts) < 8:
                            fail += 1
                            errs["too few points"] = errs.get("too few points", 0) + 1
                            continue
                        pts.sort()
                        ok += 1
                        fh.write(json.dumps({"partNumber": pn, "nominal": nom,
                                             "points": pts}, ensure_ascii=False) + "\n")
                        done.add(pn)
                DONE.write_text(json.dumps(sorted(done)))
                i += len(chunk)
                if (i // max(a.batch, 1)) % 10 == 0:
                    log(f"  {min(i, len(work))}/{len(work)}  ok={ok} fail={fail} "
                        f"{dict(list(errs.items())[:3])}")
                time.sleep(a.delay)
        finally:
            br.close()
    log(f"ESR FETCH end: ok={ok} fail={fail} errors={errs}")
    return 0


def interp(points, f):
    if f < points[0][0] or f > points[-1][0]:
        return None
    for (f0, e0), (f1, e1) in zip(points, points[1:]):
        if f0 <= f <= f1:
            if f1 == f0 or e0 <= 0 or e1 <= 0:
                return e0
            t = (math.log10(f) - math.log10(f0)) / (math.log10(f1) - math.log10(f0))
            return 10 ** (math.log10(e0) + t * (math.log10(e1) - math.log10(e0)))
    return None


def cmd_write(a):
    from blade_gate import BladeGate
    gate = BladeGate("capacitor")
    v = tdk.build_validator()
    curves = {}
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                curves[r["partNumber"]] = r["points"]
    log(f"WRITE: {len(curves)} ESR curves available")
    if not curves:
        return 0

    patched = rejected = 0
    bad, replaced, tand = [], [], []
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
            pts = curves.get(pn)
            if mi.get("name") != "TDK" or pts is None:
                out_lines.append(s)
                continue
            e = ds.setdefault("electrical", {})
            if e.get("esrPoints"):
                out_lines.append(s)
                continue
            pts = [(float(f), float(x)) for f, x in pts]
            e["esrPoints"] = {"xData": [f for f, _ in pts], "yData": [x for _, x in pts]}
            at_ref = interp(pts, REF_HZ)
            cap = e.get("capacitance")
            nominal = cap.get("nominal") if isinstance(cap, dict) else cap
            if at_ref is not None:
                if isinstance(e.get("esr"), (int, float)):
                    replaced.append({"partNumber": pn, "oldEsr": e["esr"],
                                     "oldEsrFrequency": e.get("esrFrequency"),
                                     "newEsr": at_ref})
                e["esr"] = at_ref
                e["esrFrequency"] = REF_HZ
                if isinstance(nominal, (int, float)) and nominal > 0:
                    tand.append(at_ref / (1.0 / (2 * math.pi * REF_HZ * nominal)))
            e.pop("_esrWarning", None)
            ds.setdefault("provenance", []).append({
                "source": "manufacturerParametric",
                "sourceName": ("TDK Product Center measured ESR-vs-frequency curve "
                               f"(graph_kind {ESR_KIND})"),
                "sourceUrl": "https://product.tdk.com" + tdk.graph_path("capacitor/ceramic/mlcc"),
                "retrievedDate": time.strftime("%Y-%m-%d"),
            })
            errs = sorted(v.iter_errors(c), key=lambda x: x.path)
            if errs:
                rejected += 1
                if len(bad) < 5:
                    bad.append(f"{pn}: {errs[0].message[:130]}")
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

    log(f"WRITE {'APPLIED' if a.apply else 'DRY RUN'}: patched={patched} rejected={rejected} "
        f"(scalar replaced on {len(replaced)})")
    log(gate.summary())
    if tand:
        tand.sort()
        log(f"  implied tan-delta @{REF_HZ:g}Hz: median={tand[len(tand)//2]:.4g} "
            f"max={tand[-1]:.4g}")
    for b in bad:
        log(f"  rejected: {b}")
    if a.apply and patched:
        (STAGE / "esr_replaced_audit.json").write_text(json.dumps(replaced, indent=1))
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
    f = sub.add_parser("fetch")
    f.add_argument("--limit", type=int)
    f.add_argument("--delay", type=float, default=0.4)
    f.add_argument("--pace", type=float, default=0.2)
    f.add_argument("--batch", type=int, default=20)
    w = sub.add_parser("write")
    w.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return cmd_fetch(a) if a.cmd == "fetch" else cmd_write(a)


if __name__ == "__main__":
    sys.exit(main())
