#!/usr/bin/env python3
"""Provenance in the live catalogues is a REFERENCE, not an explanation.

    python3 scripts/strip_provenance_narrative.py [FILE ...] [--apply]

A provenance entry answers one question: where did this value come from. Source, a
short source name, a URL, a date. Nothing else. The reasoning behind a repair belongs
in the ABT ticket and the commit message -- both of which already exist and are where
anyone would look for it.

That is not what the corpus holds. Across the live catalogues ~85,000 provenance strings
carry narrative prose, up to 1,462 characters: audit conclusions, counter-arguments,
before/after values, the evidence for a correction, and in places an argument with a
previous pass. sourceName's median is a reasonable 73 characters and its p90 is 548,
which is the shape of a field being used for something it was not meant for.

The pressure is real -- there is nowhere else in the schema to record a verification
state or a retraction -- but a name field is not the answer, and this is the same
defect as the 2,344-character commercialName essays found in MAS, one level down.

WHAT THIS KEEPS. source, sourceUrl, retrievedDate, fields[] -- untouched, always. A
sourceName that is already a plain identifier is left exactly as it is.

WHAT IT DOES.
  * "Real Source (detail) [citation verified 2026-08-01: ...]"  ->  "Real Source (detail)"
    A bracketed CAMPAIGN VERDICT is dropped. Only that: a bracket is not evidence of
    prose. 'TO-247 [B]' is a package code and "('.16 mm [4 in] Centerline'" is a units
    quotation, and cutting at the first " [" turned 101 connector rows into the
    mid-sentence fragment "...value swap ('.16 mm" -- which then read as a name and
    survived the sweep. Nothing is ever emitted with unbalanced brackets or quotes.
  * A sourceName that is pure narrative is replaced by a short reference: the ABT ticket
    it cites plus a few words, or a dated repair label. The narrative is not summarised
    and not moved -- it is dropped, because it exists in the ticket and the commit.
  * derivation loses a repair-history clause ("; replaces the series row's placeholder
    value") and KEEPS everything else. It is not a name: the schema calls it "the
    assumption record", so sentences and verbs are legitimate there. Truncating it at
    the first ";" cost 107,000 connector entries the rule for a second derived field.

WHAT BELONGS SOMEWHERE ELSE, NOT HERE. A verification act goes in the PEAS provenance
keys `verification` / `verificationDate` / `verificationMethod`, and a withdrawal in
`retracted` / `retractionReason`. This script only removes; moving a verdict into its
real field is a per-campaign judgement no regex can make.

THIS IS A ONE-SHOT, AND THAT IS THE STANDING GAP. The 2026-09-05 sweep left connectors
clean at 11:13 and the next campaign wrote 410 fresh bracketed verdicts into the same
field by 11:50. Run `--check` (exit 1 on any hit) from a guard or before a data commit;
a cleanup that runs once cannot hold an invariant.

WHAT IT DOES NOT TOUCH. data/quarantine.ndjson, where a full reason IS the point: a
withdrawn record must carry the case against it, and that file is excluded by name.

Dry run by default. --apply writes, streaming line by line with a concurrent-append
guard, never loading a catalogue whole.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"

ABT = re.compile(r"ABT\s*#(\d+)")


def short_name(s: str) -> str | None:
    """Return the trimmed source identifier, or None to drop the field entirely."""
    if not s:
        return None
    txt = s.strip()

    # 1. drop a bracketed CAMPAIGN VERDICT -- and only that. A bracket by itself is
    #    not prose: 'TO-247 [B]' is a package code, "'.16 mm [4 in]'" is a units
    #    quotation, and cutting at the first " [" is what produced the truncated
    #    fragments this pass exists to clean up.
    cut = _drop_verdict_brackets(txt)
    if cut and not _is_narrative(cut):
        # balance is checked only on text THIS function cut. A name that arrived
        # already clipped ("Murata MLCC parametric export (staging/murata/mlcc") is
        # damaged, not prose, and only its original can repair it -- replacing it with
        # a generic label would destroy the one identifier still there.
        if cut == txt or (_is_balanced(cut) and not _looks_clipped(cut)):
            return cut

    # 2. pure narrative: reduce to a ticket reference, which is where the reasoning lives
    m = ABT.search(txt)
    if m:
        return f"OpenConverters repair (ABT #{m.group(1)})"

    # 3. no ticket named: keep the leading clause ONLY if it stands on its own as an
    #    identifier -- balanced, unclipped, and not a sentence. A fragment that ends
    #    mid-quote is worse than no name at all.
    head = re.split(r"[.;:]", cut or txt, 1)[0].strip()
    if head and not _is_narrative(head) and _is_balanced(head) and not _looks_clipped(head):
        return head
    m = _DATED_LABEL.match(txt)
    if m:                       # a dated repair label: "pitch repair 2026-07-02"
        return f"{m.group(1).strip()} {m.group(2)}"
    return "OpenConverters repair"


_VERDICT = re.compile(
    r"(citation|verified|verification|removed|withdrawn|retracted|disproven|"
    r"not confirmable|ABT\s*#|inferred from|\d{4}-\d{2}-\d{2})", re.I)


def _drop_verdict_brackets(txt: str) -> str:
    """Remove ' [ ... ]' segments that carry a campaign verdict. Leave the rest."""
    out = re.sub(
        r"\s\[([^\]]{30,})\](?=\s|$)",
        lambda m: "" if _VERDICT.search(m.group(1)) else m.group(0),
        txt,
    )
    # an UNCLOSED verdict bracket (the annotation was itself truncated once already)
    m = re.search(r"\s\[([^\]]{30,})$", out)
    if m and _VERDICT.search(m.group(1)):
        out = out[: m.start()]
    return out.strip()


def _is_balanced(s: str) -> bool:
    """A name never ends mid-parenthesis, mid-bracket or mid-quote."""
    return (s.count("(") == s.count(")") and s.count("[") == s.count("]")
            and s.count('"') % 2 == 0 and s.count("'") % 2 == 0)


def _looks_clipped(s: str) -> bool:
    return bool(re.search(r"[,(\[]\s*$", s)) or s.endswith(" ")


_NARRATIVE_WORDS = (
    "because", "however", "rather than", "turned out", "was wrong", "adversarial",
    "audit", "refuted", "restored", "reverted", "corrected", "withdraw", "precedent",
    "evidence", "confirmed", "contradict", "this row", "the reason", "not a measurement",
    "removed", "verified 20", "citation ", "matched as", "inferred from",
    "instead", "therefore", "so that", "nobody", "no replacement", "says nothing",
)


_SENTENCE = re.compile(r"[.!?][)\]\"']?\s+[A-Z(]")     # two or more sentences
# a finite verb WITH A SUBJECT in front of it. A noun phrase has none; "verified
# against the catalog pitch" is a participle and stays.
_FINITE = re.compile(
    r"\b\w+\s+(was|were|is|are|has been|have been|had|will|would|should|could|"
    r"states|stated|shows|showed|means|meant|replaces|replaced|removed|carried|"
    r"cited|matched|confirms|confirmed|contradicts|refutes|says|said|gives)\s+\w", re.I)
# "0.0001 m -> 0.0025 m": a repair's before/after pair, never part of a name
_VALUE_PAIR = re.compile(r"-?\d[\d.eE+-]*\s*[^\s;]{0,8}?\s*(->|→|=>)\s*-?\d")
# "pitch repair 2026-07-02: ..." -- a dated action introducing its own story
_DATED_ACTION = re.compile(r"\b\d{4}-\d{2}-\d{2}\s*:")
# "; replaces the series row's placeholder value" -- the history of a repair
_DATED_LABEL = re.compile(r"^([a-zA-Z][\w -]{2,40}?)\s+(\d{4}-\d{2}-\d{2})\b")
_REASON_CLAUSE = re.compile(
    r"[;,]\s*(replaces?|replaced|supersedes?|superseded|corrects\b|corrected|"
    r"overrides?|restores?|restored|withdraw\w*|retract\w*)\b", re.I)


def _is_narrative(s: str) -> bool:
    """Prose, as opposed to a noun phrase that names a source.

    Length alone is NOT the test -- "Samsung Electro-Mechanics Component Library, |Z| &
    R (ESR) vs frequency chart (graphType |Z|_R, TEMPDC, 25 degC)" is 110 characters and
    is exactly what a source name should say. A first draft of this function used a word
    count and destroyed it. What distinguishes prose is sentences and verbs -- and, as
    the 2026-09-05 residue showed, a before/after value pair and a date-stamped action,
    neither of which needs a single one of the words below.
    """
    low = s.lower()
    if any(w in low for w in _NARRATIVE_WORDS):
        return True
    if _SENTENCE.search(s) or _VALUE_PAIR.search(s) or _DATED_ACTION.search(s):
        return True
    return bool(_FINITE.search(s) or _REASON_CLAUSE.search(s))


def short_derivation(s: str) -> str | None:
    """Keep the rule and its assumptions. Drop the history of a repair.

    `derivation` is NOT a name: PEAS calls it "the assumption record", so a sentence
    and a verb belong in it. The only thing removed is a clause recording what a repair
    replaced -- that lives in the ticket. Truncating at the first ";" (the previous
    behaviour) silently deleted the rule for every field after the first.
    """
    if not s:
        return None
    txt = _drop_verdict_brackets(s.strip())
    while True:
        m = _REASON_CLAUSE.search(txt)
        if not m:
            break
        end = txt.find(";", m.start() + 1)
        txt = (txt[:m.start()] + (txt[end:] if end != -1 else "")).strip()
    txt = txt.strip().rstrip(";,").strip()
    return txt or None


def clean_record(rec, edits: list | None = None) -> int:
    """Walk any nesting, trim every provenance entry. Returns fields changed.

    Every (old, new) pair is appended to `edits` so the caller can patch the line as
    TEXT instead of re-serialising the record -- see process().
    """
    changed = 0
    if edits is None:
        edits = []
    if isinstance(rec, dict):
        prov = rec.get("provenance")
        if isinstance(prov, list):
            for e in prov:
                if not isinstance(e, dict):
                    continue
                for key, fn in (("sourceName", short_name), ("derivation", short_derivation)):
                    if key not in e:
                        continue
                    old = str(e[key])
                    new = fn(old)
                    if new is None:
                        e.pop(key, None); changed += 1; edits.append((old, None))
                    elif new != e[key]:
                        e[key] = new; changed += 1; edits.append((old, new))
        for v in rec.values():
            changed += clean_record(v, edits)
    elif isinstance(rec, list):
        for v in rec:
            changed += clean_record(v, edits)
    return changed


def _patch_text(raw: bytes, edits: list, cleaned) -> bytes | None:
    """Rewrite only the changed STRINGS in the original line.

    A catalogue is not uniformly serialised -- 60% of connectors.ndjson rows use
    ", "/": " separators and the rest are compact -- so re-dumping a touched record
    reformats the whole line and buries the real change in cosmetic churn. Returns
    None when the textual patch cannot be proven equivalent.
    """
    if any(new is None for _, new in edits):
        return None
    text = raw.decode("utf-8")
    for old, new in edits:
        tok = json.dumps(old, ensure_ascii=False)
        if tok not in text:
            tok = json.dumps(old)                       # \uXXXX-escaped variant
            if tok not in text:
                return None
        text = text.replace(tok, json.dumps(new, ensure_ascii=False))
    try:
        if json.loads(text) != cleaned:
            return None
    except Exception:
        return None
    return text.encode("utf-8")


def process(path: Path, apply: bool, samples: list) -> tuple[int, int]:
    size0 = path.stat().st_size
    tmp = path.with_suffix(".ndjson.narrative.tmp")
    rows = touched = read = 0
    with open(path, "rb") as src, open(tmp, "wb") as out:
        for raw in src:
            read += len(raw)
            if read > size0:                     # appended while we ran
                out.write(raw); continue
            line = raw
            if b'"provenance"' in raw:
                try:
                    rec = json.loads(raw)
                    edits: list = []
                    n = clean_record(rec, edits)
                    if n:
                        patched = _patch_text(raw, edits, rec)
                        if patched is None:
                            patched = json.dumps(rec, ensure_ascii=False).encode() + b"\n"
                        for old, new in edits[: max(0, 6 - len(samples))]:
                            samples.append((path.name, old, new))
                        line = patched
                        touched += 1
                except Exception:
                    line = raw
            rows += 1
            out.write(line)
        out.flush(); os.fsync(out.fileno())
    if apply:
        os.replace(tmp, path)
    else:
        tmp.unlink(missing_ok=True)
    return rows, touched


def main(argv):
    apply = "--apply" in argv
    check = "--check" in argv          # exit 1 if any narrative is left: for a guard
    names = [a for a in argv if not a.startswith("--")]
    files = [Path(n) if os.sep in n else DATA / n for n in names] or \
            sorted(p for p in DATA.glob("*.ndjson") if "quarantine" not in p.name)
    total_rows = total_touched = 0
    samples: list = []
    for p in files:
        if not p.exists() or "quarantine" in p.name:
            continue
        rows, touched = process(p, apply, samples)
        if touched:
            print(f"  {p.name:26} {touched:7} rows trimmed of {rows}")
        total_rows += rows; total_touched += touched
    print(f"\n{total_touched} rows trimmed across {total_rows} scanned")
    for fname, old, new in samples:
        print(f"  e.g. {fname}: {old[:90]!r} -> {new!r}")
    if check:
        print("--check: FAIL" if total_touched else "--check: clean")
        return 1 if total_touched else 0
    print("--apply not given: nothing written" if not apply else "written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
