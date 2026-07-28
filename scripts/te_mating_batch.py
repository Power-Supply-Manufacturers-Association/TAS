#!/usr/bin/env python3
"""ABT #249: hand out / ingest TE mating pull batches (resumable checkpoint).

Replaces the local work-server idea: Chrome will not let the https://te.com page
reach a loopback address even with Access-Control-Allow-Private-Network, so batches
are inlined into each browser_evaluate instead and the saved result file is ingested
here.

  te_mating_batch.py next  [N]      -> print the next N unpulled part numbers as JSON
  te_mating_batch.py ingest FILE    -> append a saved browser result file to the
                                       checkpoint and mark those parts done
  te_mating_batch.py status         -> progress counters
"""
import json
import sys
from pathlib import Path

STAGE = Path.home() / "PSMA" / "TAS" / "staging" / "te"
WORKLIST = STAGE / "te_worklist.json"
DONE = STAGE / "te_done.json"
RESULTS = STAGE / "te_mates_raw.jsonl"
LIMIT = STAGE / "te_limit.json"


def state():
    wl = json.loads(WORKLIST.read_text())
    lim = json.loads(LIMIT.read_text())["limit"] if LIMIT.exists() else len(wl)
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    wl = wl[:lim]
    return wl, done, [p for p in wl if p not in done]


def main(argv):
    cmd = argv[0] if argv else "status"
    wl, done, todo = state()

    if cmd == "status":
        print(f"tranche {len(wl)} | done {len(done)} | remaining {len(todo)}")
        if RESULTS.exists():
            print(f"raw records: {sum(1 for _ in RESULTS.open())}")
        return 0

    if cmd == "next":
        n = int(argv[1]) if len(argv) > 1 else 250
        print(json.dumps(todo[:n]))
        return 0

    if cmd == "ingest":
        path = Path(argv[1])
        d = json.loads(path.read_text())
        recs = d["results"] if isinstance(d, dict) else d

        # A record that errored carries NO data. Marking it done would silently drop
        # that part from the campaign forever -- which is exactly what a whole-batch
        # failure looks like (the browser losing the te.com origin makes every fetch
        # raise "TypeError: Failed to fetch"). Only error-free records are recorded.
        good = [r for r in recs if not r.get("err")]
        bad = [r for r in recs if r.get("err")]
        if bad and not good:
            print(f"REFUSING TO INGEST {path.name}: all {len(bad)} records errored "
                  f"({bad[0].get('err')!r}).")
            print("Nothing recorded, nothing marked done. Re-navigate the browser to a "
                  "te.com page (the in-page fetch needs that origin) and re-run the batch.")
            return 2

        with RESULTS.open("a", encoding="utf-8") as fh:
            for r in good:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        done.update(r["pn"] for r in good if r.get("pn"))
        DONE.write_text(json.dumps(sorted(done)))
        wl, done2, todo2 = state()
        print(f"ingested {len(good)} records from {path.name}"
              + (f"; {len(bad)} errored and were LEFT PENDING for retry" if bad else "")
              + f"; done {len(done2)} / {len(wl)}, remaining {len(todo2)}")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
