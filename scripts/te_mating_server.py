#!/usr/bin/env python3
"""ABT #249: local work server for the TE mating pull.

api.te.com is Akamai-gated to raw curl (403) and only answers in-page fetches from a
browser session, so the pull must run inside the page. Rather than inlining thousands
of part numbers into each browser_evaluate, the page pulls work from here and posts
results back. Loopback is a "potentially trustworthy" origin, so an https page may
fetch http://127.0.0.1 without tripping mixed-content blocking.

  GET  /batch?n=250   -> {"batch": [...pns], "remaining": N, "done": bool}
  POST /results       -> appends the posted records to the checkpoint, marks them done
  GET  /status        -> progress counters

State lives in TAS/staging/te/ so the campaign is resumable across sessions.
Bind is 127.0.0.1 only.

Usage: te_mating_server.py [port]      (default 8787)
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

STAGE = Path.home() / "PSMA" / "TAS" / "staging" / "te"
WORKLIST = STAGE / "te_worklist.json"
DONE = STAGE / "te_done.json"
RESULTS = STAGE / "te_mates_raw.jsonl"
LIMIT_FILE = STAGE / "te_limit.json"

_lock = threading.Lock()


def load_state():
    worklist = json.loads(WORKLIST.read_text())
    done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    limit = json.loads(LIMIT_FILE.read_text())["limit"] if LIMIT_FILE.exists() else len(worklist)
    return worklist[:limit], done


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        # Chrome Private Network Access: a public-origin page (te.com) reaching a
        # loopback address must get this on the preflight, or the request hangs
        # rather than failing. Without it the in-page fetch never returns.
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        with _lock:
            worklist, done = load_state()
            todo = [p for p in worklist if p not in done]
            if self.path.startswith("/status"):
                return self._json({"total": len(worklist), "done": len(done),
                                   "remaining": len(todo)})
            n = 250
            if "n=" in self.path:
                try:
                    n = max(1, min(500, int(self.path.split("n=")[1].split("&")[0])))
                except ValueError:
                    pass
            batch = todo[:n]
            return self._json({"batch": batch, "remaining": len(todo) - len(batch),
                               "done": not batch})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        recs = payload.get("results") or []
        with _lock:
            with RESULTS.open("a", encoding="utf-8") as fh:
                for r in recs:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            done = set(json.loads(DONE.read_text())) if DONE.exists() else set()
            done.update(r["pn"] for r in recs if r.get("pn"))
            DONE.write_text(json.dumps(sorted(done)))
            worklist, _ = load_state()
            remaining = len([p for p in worklist if p not in done])
        return self._json({"ok": True, "accepted": len(recs), "remaining": remaining})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    STAGE.mkdir(parents=True, exist_ok=True)
    srv = HTTPServer(("127.0.0.1", port), H)
    worklist, done = load_state()
    print(f"serving {len(worklist)} parts ({len(done)} already done) on 127.0.0.1:{port}",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
