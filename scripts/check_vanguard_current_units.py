#!/usr/bin/env python3
"""Per-series evidence for the Vanguard current-unit question (ABT #351 class B2).

    python3 scripts/check_vanguard_current_units.py vanguard.json out.json

ve1.com labels its current attribute "DC Current Max (A)", but Vanguard's own
C50000 datasheet says "Current Rating (mA): 85 to 630" — the website's unit label
is wrong. That was established for ONE series. The failing corpus rows span about
fifteen, so this fetches each affected series' datasheet and reads the unit off it
rather than generalising from the one that was checked.

For every series it reports the unit actually printed next to the current spec:

    mA   -> the corpus ratedCurrents for that series are 1000x too large
    A    -> the website label is right for that series and nothing should change
    ?    -> the datasheet could not be read; NOTHING is assumed either way

Output is evidence, not a repair: {series: {unit, datasheet, snippet}}. The
rescale is a separate, reviewed step and must only touch series proven "mA".
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def pdf_text(data: bytes) -> str:
    """Text via pdftotext when present, else a raw-stream fallback."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        out = subprocess.run(["pdftotext", path, "-"], capture_output=True, timeout=120)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.decode("utf-8", "replace")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    import zlib
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            chunks.append(zlib.decompress(m.group(1)))
        except Exception:
            pass
    blob = b"\n".join(chunks).decode("latin-1")
    return " ".join(re.findall(r"\((.*?)\)", blob))


def unit_of(text: str) -> tuple[str, str]:
    """The unit printed against the current spec, plus the evidence snippet."""
    for m in re.finditer(r"(?i)current[^\n]{0,40}?\((m?A)\)", text):
        return ("mA" if m.group(1).lower() == "ma" else "A",
                text[max(0, m.start() - 60):m.start() + 80].replace("\n", " "))
    for m in re.finditer(r"(?i)\((m?A)\)[^\n]{0,40}?current", text):
        return ("mA" if m.group(1).lower() == "ma" else "A",
                text[max(0, m.start() - 60):m.start() + 80].replace("\n", " "))
    return "?", ""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.exit(__doc__)
    catalog = json.loads(Path(argv[0]).read_text())
    by_sku = {p["sku"]: p for p in catalog["products"]}

    wanted = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else None
    if not wanted:
        print("pipe in {series: example_sku} on stdin", file=sys.stderr)
        return 2

    results = {}
    for series, sku in wanted.items():
        p = by_sku.get(sku)
        if not p:
            results[series] = {"unit": "?", "why": f"sku {sku} not in catalog"}
            continue
        try:
            page = get(p["permalink"]).decode("utf-8", "replace")
        except Exception as e:
            results[series] = {"unit": "?", "why": f"page fetch failed: {e}"}
            continue
        pdfs = re.findall(r'href="([^"]+\.pdf)"', page)
        if not pdfs:
            results[series] = {"unit": "?", "why": "no datasheet link on the product page"}
            continue
        url = pdfs[0]
        try:
            unit, snippet = unit_of(pdf_text(get(url)))
        except Exception as e:
            results[series] = {"unit": "?", "why": f"pdf fetch/parse failed: {e}", "datasheet": url}
            continue
        results[series] = {"unit": unit, "datasheet": url, "snippet": snippet[:160], "exampleSku": sku}
        print(f"  {series:12} {unit:3}  {url.split('/')[-1][:58]}")
        time.sleep(0.5)

    Path(argv[1]).write_text(json.dumps(results, indent=1))
    ma = sum(1 for r in results.values() if r["unit"] == "mA")
    amps = sum(1 for r in results.values() if r["unit"] == "A")
    unknown = sum(1 for r in results.values() if r["unit"] == "?")
    print(f"\nseries proven mA: {ma} | proven A: {amps} | undetermined: {unknown}")
    print(f"evidence -> {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
