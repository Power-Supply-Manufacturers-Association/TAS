"""Print frequency of unique full validation error messages per file."""
from __future__ import annotations
import json, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from port_part_libraries import (
    TARGETS, DATA, build_registry, get_validator, transform,
)

registry = build_registry()
only = sys.argv[1] if len(sys.argv) > 1 else None
for fname, disc, repo, schema_file in TARGETS:
    if only and fname != only:
        continue
    val = get_validator(registry, repo, schema_file)
    src = DATA / fname
    if not src.exists():
        continue
    counter: Counter[str] = Counter()
    samples: dict[str, dict] = {}
    n = 0
    with src.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                rec = json.loads(line)
                wrapped = transform(rec, disc)
            except (ValueError, json.JSONDecodeError) as e:
                counter[f"transform: {e}"] += 1
                continue
            inner = wrapped[disc]
            errs = list(val.iter_errors(inner))
            if not errs:
                continue
            for e in errs[:1]:
                key = f"{'/'.join(map(str, e.absolute_path))}: {e.message[:120]}"
                counter[key] += 1
                if key not in samples:
                    samples[key] = {"path": list(e.absolute_path),
                                    "instance": str(e.instance)[:200]}
    print(f"\n=== {fname}  (n={n}) ===")
    for key, c in counter.most_common(40):
        print(f"  {c:6d}  {key}")
        if c > 10 and key in samples:
            s = samples[key]
            print(f"          @ {s['path']}  instance={s['instance']!r}")
