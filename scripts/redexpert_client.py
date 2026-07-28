#!/usr/bin/env python3
"""Minimal REDEXPERT MCP client over plain HTTP (no Claude MCP client needed).

The Wurth REDEXPERT MCP at https://redexpert.we-online.com/mcp speaks streamable
HTTP JSON-RPC and needs no session id, so campaigns against it can run UNATTENDED --
unlike the TE pull, which needs a browser to pass Akamai.

Quirk: the server sends a malformed double header "content-encoding: identity, gzip"
that urllib3 cannot decode, so every request asks for identity encoding.

Tools: get_product_family_ids, get_products, get_inductor_losses, get_suitable_inductors

Usage:
  redexpert_client.py families
  redexpert_client.py products <module> [filterBy filterOperation filterValue]
"""
import json
import sys

import requests

URL = "https://redexpert.we-online.com/mcp"
HEADERS = {"Content-Type": "application/json",
           "Accept": "application/json, text/event-stream",
           "Accept-Encoding": "identity"}


def _parse(resp):
    txt = resp.text
    if "data:" in txt:
        for line in txt.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return {"_raw": txt[:500]}


class Redexpert:
    def __init__(self, timeout=180):
        self.s = requests.Session()
        self.timeout = timeout
        self._n = 0
        self._init()

    def _rpc(self, method, params=None, notify=False):
        self._n += 1
        msg = {"jsonrpc": "2.0", "method": method}
        if not notify:
            msg["id"] = self._n
        if params is not None:
            msg["params"] = params
        r = self.s.post(URL, headers=HEADERS, json=msg, timeout=self.timeout)
        if notify:
            return None
        if r.status_code != 200:
            raise RuntimeError(f"REDEXPERT HTTP {r.status_code}: {r.text[:200]}")
        body = _parse(r)
        if "error" in body:
            raise RuntimeError(f"REDEXPERT error: {json.dumps(body['error'])[:300]}")
        return body.get("result")

    def _init(self):
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "psma-tas", "version": "1"}})
        self._rpc("notifications/initialized", notify=True)

    def call(self, name, arguments=None):
        res = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        # unwrap the MCP content envelope -> parsed JSON when the tool returns JSON text
        out = []
        for c in (res or {}).get("content", []):
            if c.get("type") == "text":
                t = c.get("text", "")
                try:
                    out.append(json.loads(t))
                except json.JSONDecodeError:
                    out.append(t)
        if len(out) == 1:
            return out[0]
        return out or res

    def families(self):
        return self.call("get_product_family_ids")

    def products(self, module, filter_by=None, op=None, value=None,
                 sort_by=None, sort_order=None):
        args = {"module": module}
        if filter_by:
            args.update({"filterBy": filter_by, "filterOperation": op,
                         "filterValue": value})
        if sort_by:
            args.update({"sortBy": sort_by, "sortOrder": sort_order or "asc"})
        return self.call("get_products", args)


def main(argv):
    rx = Redexpert()
    if not argv or argv[0] == "families":
        fams = rx.families()
        print(json.dumps(fams, indent=1)[:4000])
        return 0
    if argv[0] == "products":
        mod = argv[1]
        rest = argv[2:]
        res = rx.products(mod, *(rest or [None, None, None]))
        s = json.dumps(res)
        print(f"module {mod}: {len(s)} bytes")
        print(s[:2500])
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
