#!/usr/bin/env python3
"""Derive the set of check codes a Blade Runner scope can emit, straight from
the source files that produce Findings -- so the inventory (PartValidator::
check_codes() / circuit_check_codes()) is generated from the emitting sites
instead of being a hand-typed literal that drifts (ABT #549).

Three code shapes reach a Finding/CorpusFinding's `code`:
  1. Direct:      emit(out, ctx, "SOME_CODE", Severity::..., ...)
  2. Forwarded:   a file-local helper takes a `code` parameter and itself
                  calls emit(..., code, ...); the literal lives at each of
                  the helper's OWN call sites, e.g. analog.cpp's
                  check_db(elec, ctx, out, key, "ANA_CMRR"). Detected
                  structurally: a function whose body calls emit() with a
                  bare identifier in the code slot that is also one of the
                  function's own parameters -- the literal is then lifted
                  from every call site found in the SAME file (these helpers
                  live in an anonymous namespace, so they are file-local by
                  construction in this codebase).
  3. Aggregate:   validate_corpus's CorpusFinding is built by brace-init
                  push_back, e.g. out.push_back({p.first, "GEN_COHORT_CEILING",
                  ...}) -- CorpusFinding's 2nd field (index 1) is `code`.

Deliberately excludes `skipped.push_back("...")` -- those are Verdict.skipped
codes, a different vocabulary from Finding codes, and check_codes() is
documented as "every check id the validator can EMIT".

Argument lists are split with a paren/quote-aware (not a full C++ parser)
tokenizer so ternaries in other argument slots (`cond ? Severity::A :
Severity::B`) can't desynchronise which text is the code argument -- a
regex anchored on ", Severity::" was tried first and mis-split exactly this
shape (diodes.cpp's DIO_TVS_ORDERING calls).

Usage:
    gen_check_codes.py --src-dir DIR --file a.cpp [--file b.cpp ...] \
        [--call-name emit] [--aggregate-call push_back:1]
Prints one code per line, sorted, to stdout.
"""
import argparse
import re
import sys
from pathlib import Path

CODE_RE = r'[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+'
STRING_LIT_RE = re.compile(r'^"(' + CODE_RE + r')"$')
FUNC_SIG_RE = re.compile(r'\b(?:void|std::vector<[^>]+>)\s+(\w+)\s*\(([^)]*)\)\s*\{')


def _skip_string(s: str, i: int):
    """i points at an opening '\"'; return the index just past the matching
    closing quote."""
    i += 1
    while i < len(s):
        if s[i] == '\\':
            i += 2
            continue
        if s[i] == '"':
            return i + 1
        i += 1
    return i


def matching_close(s: str, open_idx: int, open_ch='(', close_ch=')'):
    """`s[open_idx]` is `open_ch`; return the index of its matching
    `close_ch`, skipping over string literals and nested braces/brackets."""
    depth, i = 0, open_idx
    while i < len(s):
        c = s[i]
        if c == '"':
            i = _skip_string(s, i)
            continue
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
            if depth == 0 and c == close_ch:
                return i
        i += 1
    raise ValueError(f"unbalanced '{open_ch}' at offset {open_idx}")


def split_top_level_args(s: str):
    """Split call-argument text on top-level commas, skipping over nested
    (), [], {}, <> and "..." -- enough for this codebase's call sites, not a
    general C++ parser."""
    args, depth, cur, i = [], 0, [], 0
    while i < len(s):
        c = s[i]
        if c == '"':
            j = _skip_string(s, i)
            cur.append(s[i:j]); i = j
            continue
        if c in '([{<':
            depth += 1
        elif c in ')]}>':
            depth -= 1
        if c == ',' and depth == 0:
            args.append(''.join(cur)); cur = []
        else:
            cur.append(c)
        i += 1
    if cur:
        args.append(''.join(cur))
    return [a.strip() for a in args]


def find_calls(name: str, text: str):
    """Yield the split argument list for every top-level call `name(...)` in
    `text` (word-boundary before `name`, so `out.push_back(` matches
    `push_back` but `some_push_back(` does not)."""
    for m in re.finditer(r'(?<!\w)' + re.escape(name) + r'\(', text):
        open_idx = m.end() - 1
        close_idx = matching_close(text, open_idx, '(', ')')
        yield split_top_level_args(text[open_idx + 1:close_idx])


def function_bodies(text: str):
    """Yield (name, param_list, body) for every `void NAME(...) {` /
    `std::vector<...> NAME(...) {` definition."""
    for m in FUNC_SIG_RE.finditer(text):
        name, params = m.group(1), m.group(2)
        start = m.end() - 1  # the '{'
        end = matching_close(text, start, '{', '}')
        yield name, params, text[start + 1:end]


def param_names(param_list: str):
    names = []
    for p in split_top_level_args(param_list):
        p = p.strip()
        if not p:
            continue
        m = re.search(r'(\w+)\s*$', p)
        names.append(m.group(1) if m else None)
    return names


def literal_code(arg: str):
    m = STRING_LIT_RE.match(arg.strip())
    return m.group(1) if m else None


def direct_codes(text: str, code_index: int):
    out = set()
    for args in find_calls('emit', text):
        if len(args) > code_index:
            c = literal_code(args[code_index])
            if c:
                out.add(c)
    return out


def forwarding_functions(text: str, code_index: int):
    """-> dict[func_name] = code_param_index (0-based, IN THE FUNCTION'S OWN
    PARAMETER LIST) for every function that forwards a bare parameter into
    emit()'s code slot."""
    fwd = {}
    for name, params, body in function_bodies(text):
        names = param_names(params)
        for args in find_calls('emit', body):
            if len(args) <= code_index:
                continue
            code_arg = args[code_index].strip()
            if literal_code(code_arg) is not None:
                continue  # direct literal, not a forwarder
            if code_arg in names:
                fwd[name] = names.index(code_arg)
    return fwd


def forwarded_codes(text: str, fwd: dict):
    out = set()
    for fname, idx in fwd.items():
        for args in find_calls(fname, text):
            if idx < len(args):
                c = literal_code(args[idx])
                if c:
                    out.add(c)
    return out


def aggregate_codes(text: str, call_name: str, field_index: int):
    """`call_name({field0, field1, ...})` -- a single brace-init argument;
    unwrap the braces and index into ITS fields (e.g. CorpusFinding's `code`
    is field 1 of `out.push_back({index, code, reference, message, ...})`)."""
    out = set()
    for args in find_calls(call_name, text):
        if len(args) != 1:
            continue
        inner = args[0].strip()
        if not (inner.startswith('{') and inner.endswith('}')):
            continue
        fields = split_top_level_args(inner[1:-1])
        if len(fields) > field_index:
            c = literal_code(fields[field_index])
            if c:
                out.add(c)
    return out


def derive(paths, aggregates):
    codes = set()
    for p in paths:
        text = Path(p).read_text()
        codes |= direct_codes(text, code_index=2)
        codes |= forwarded_codes(text, forwarding_functions(text, code_index=2))
        for call_name, field_index in aggregates:
            codes |= aggregate_codes(text, call_name, field_index)
    return codes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src-dir', required=True)
    ap.add_argument('--file', action='append',
                     help='source file (relative to --src-dir) to scan; repeatable. '
                          'If omitted, scans every *.cpp in --src-dir minus --exclude '
                          '(so a new check-family file is picked up with no CMake/test '
                          'edit -- the whole point is that nothing here is a list to '
                          'remember to update).')
    ap.add_argument('--exclude', action='append', default=[],
                     help='file (relative to --src-dir) to skip when --file is omitted; '
                          'repeatable, e.g. circuits.cpp (its own separate inventory)')
    ap.add_argument('--aggregate', action='append', default=[],
                     help='NAME:FIELD_INDEX -- also scan NAME({...}) brace-inits, '
                          'e.g. push_back:1 for CorpusFinding{index, code, ...}')
    ap.add_argument('--format', choices=['lines', 'cpp-list'], default='lines',
                     help="'lines': one code per line (default). 'cpp-list': a "
                          'braced, comma-separated, quoted C++ initializer-list body '
                          '(what check_codes()/circuit_check_codes() return).')
    ap.add_argument('--out', help='write to this file instead of stdout')
    args = ap.parse_args()
    src_dir = Path(args.src_dir)
    if args.file:
        paths = [src_dir / f for f in args.file]
    else:
        excluded = set(args.exclude)
        paths = sorted(p for p in src_dir.glob('*.cpp') if p.name not in excluded)
    missing = [p for p in paths if not p.is_file()]
    if missing:
        sys.exit(f"gen_check_codes.py: missing source file(s): {missing}")
    if not paths:
        sys.exit("gen_check_codes.py: no source files selected")
    aggregates = []
    for a in args.aggregate:
        name, _, idx = a.partition(':')
        aggregates.append((name, int(idx)))
    codes = sorted(derive(paths, aggregates))
    if not codes:
        sys.exit("gen_check_codes.py: derived ZERO codes -- almost certainly a scraper "
                  "bug (an emit()-call shape it doesn't recognise), not an empty "
                  "validator. Refusing to emit a silently-empty inventory.")
    if args.format == 'cpp-list':
        text = '{\n' + ''.join(f'    "{c}",\n' for c in codes) + '}'
    else:
        text = '\n'.join(codes) + '\n'
    if args.out:
        Path(args.out).write_text(text)
    else:
        print(text)


if __name__ == '__main__':
    main()
