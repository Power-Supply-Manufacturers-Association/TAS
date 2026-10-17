#!/usr/bin/env python3
"""Register an ALREADY-CACHED document in the ingest gate's document-evidence store.

The store (staging/doc_evidence/, layout in scripts/ingest_gate.py's docstring)
lets rule 2 accept a citation whose URL does not spell the part, when the cited
document's own text prints the part number. This script only copies text a
campaign has already fetched into that store. It NEVER fetches anything.

    # one PDF on disk (text extracted with pdftotext -layout)
    python3 scripts/register_doc_evidence.py --url URL --file doc.pdf --retrieved 2026-10-10

    # one plain-text extraction already on disk (say how it was made)
    python3 scripts/register_doc_evidence.py --url URL --file doc.txt \\
        --retrieved 2026-10-10 --text-method "pdftotext -raw"

    # one entry of a docfetch cache (gzip JSON {url,status,kind,text,date},
    # file name sha1(url).json.gz; staging/cleanup/tracing/docfetch.py)
    python3 scripts/register_doc_evidence.py --url URL --docfetch DIR

    # many: JSONL lines {"url":..., "file":..., "retrievedDate":..., "textMethod":...}
    #                or {"url":..., "docfetch": DIR}
    python3 scripts/register_doc_evidence.py --batch list.jsonl

    # does the stored copy of URL print PART? (exactly the gate's test)
    python3 scripts/register_doc_evidence.py --check URL PART

Every input problem (missing file, non-200 cache entry, empty text, no
retrievedDate, a URL already on file with different text) is an error, not a
skip: the run stops and says which entry. --keep-going reports and continues.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_gate import (  # noqa: E402
    DOC_EVIDENCE_DIR,
    DocEvidenceError,
    DocEvidenceStore,
    SEARCH_RE,
    text_names_part,
)


def _pdftotext_version():
    r = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True)
    line = (r.stderr or r.stdout).splitlines()
    return line[0].strip() if line else "pdftotext"


def from_file(url, path, retrieved, text_method=None):
    path = Path(path)
    data = path.read_bytes()
    if data[:4] == b"%PDF":
        r = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                           capture_output=True, timeout=300)
        if r.returncode != 0:
            raise DocEvidenceError("pdftotext failed on %s: %s"
                                   % (path, r.stderr.decode("utf-8", "replace")[:200]))
        text = r.stdout.decode("utf-8", "replace")
        method = "pdftotext -layout (%s)" % _pdftotext_version()
        doc_sha = hashlib.sha256(data).hexdigest()
    else:
        if not text_method:
            raise DocEvidenceError(
                "%s is not a PDF: give --text-method (how this text was extracted)"
                % path)
        text = data.decode("utf-8", "replace")
        method = text_method
        doc_sha = None
    if not retrieved:
        raise DocEvidenceError("%s: no retrievedDate given -- the store records when "
                               "the document was fetched, and will not guess" % url)
    return dict(text=text, retrieved_date=retrieved, registered_from=str(path.resolve()),
                document_sha256=doc_sha, text_method=method)


def from_docfetch(url, cache_dir):
    f = Path(cache_dir) / (hashlib.sha1(url.encode()).hexdigest() + ".json.gz")
    if not f.exists():
        raise DocEvidenceError("%s is not in the docfetch cache %s" % (url, cache_dir))
    rec = json.load(gzip.open(f, "rt"))
    if rec.get("url") != url:
        raise DocEvidenceError("%s: cache entry records url %r" % (f, rec.get("url")))
    if rec.get("status") != 200 or not (rec.get("text") or "").strip():
        raise DocEvidenceError("%s: cache entry has status %r and %s text -- nothing "
                               "was retrieved" % (url, rec.get("status"),
                                                  "some" if rec.get("text") else "no"))
    kind = rec.get("kind")
    method = {"pdf": "docfetch: pdftotext -layout",
              "html": "docfetch: HTML tag-stripped"}.get(kind)
    if method is None:
        raise DocEvidenceError("%s: unknown docfetch kind %r" % (url, kind))
    return dict(text=rec["text"], retrieved_date=rec.get("date"),
                registered_from=str(f.resolve()), document_sha256=None,
                text_method=method)


def register_one(store, spec, dry_run=False, replace=False):
    url = (spec.get("url") or "").strip()
    if not url:
        raise DocEvidenceError("entry has no url: %r" % spec)
    if SEARCH_RE.search(url):
        raise DocEvidenceError("%s is a search-shaped URL; the gate never consults the "
                               "store for one (a results page echoes its query)" % url)
    if spec.get("docfetch"):
        got = from_docfetch(url, spec["docfetch"])
    elif spec.get("file"):
        got = from_file(url, spec["file"], spec.get("retrievedDate"),
                        spec.get("textMethod"))
    else:
        raise DocEvidenceError("entry for %s names neither file nor docfetch" % url)
    if dry_run:
        return "would-register"
    return store.register(url, got["text"], got["retrieved_date"],
                          got["registered_from"],
                          document_sha256=got["document_sha256"],
                          text_method=got["text_method"],
                          registered_date=datetime.date.today().isoformat(),
                          replace=replace)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", type=Path, default=DOC_EVIDENCE_DIR)
    ap.add_argument("--url")
    ap.add_argument("--file")
    ap.add_argument("--docfetch")
    ap.add_argument("--retrieved", help="YYYY-MM-DD the document was fetched")
    ap.add_argument("--text-method")
    ap.add_argument("--batch", type=Path)
    ap.add_argument("--check", nargs=2, metavar=("URL", "PART"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--replace", action="store_true",
                    help="overwrite an entry whose stored text differs")
    ap.add_argument("--keep-going", action="store_true",
                    help="report a bad entry and continue instead of stopping")
    a = ap.parse_args()
    store = DocEvidenceStore(a.store)

    if a.check:
        url, part = a.check
        text = store.text_for(url)
        if text is None:
            print("NOT ON FILE: %s" % url)
            return 1
        ok = text_names_part(text, part)
        print("%s: stored text %s %s" % (url, "PRINTS" if ok else "does NOT print", part))
        return 0 if ok else 1

    if a.batch:
        specs = [json.loads(l) for l in a.batch.read_text().splitlines() if l.strip()]
    elif a.url:
        specs = [{"url": a.url, "file": a.file, "docfetch": a.docfetch,
                  "retrievedDate": a.retrieved, "textMethod": a.text_method}]
    else:
        ap.error("give --url (with --file or --docfetch), --batch or --check")

    counts, errors = {}, []
    for spec in specs:
        try:
            st = register_one(store, spec, dry_run=a.dry_run, replace=a.replace)
        except (DocEvidenceError, OSError) as e:
            if not a.keep_going:
                raise
            errors.append("%s: %s" % (spec.get("url"), e))
            st = "error"
        counts[st] = counts.get(st, 0) + 1
    for e in errors:
        print("ERROR " + e, file=sys.stderr)
    print(json.dumps({"store": str(a.store), "entries": len(specs), **counts}))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
