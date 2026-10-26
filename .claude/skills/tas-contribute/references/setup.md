# Setup

## The workspace layout

TAS schemas and its gate `$ref` the sibling schema repos by absolute `$id`
(`https://psma.com/<repo>/...`) and resolve them from the TAS checkout's **parent directory**.
So every repo must sit side by side:

```
PSMA/
  TAS/   PEAS/  CIAS/  SAS/  CAS/  RAS/  MAS/  CTAS/  AAS/  CONAS/  TDAS/  EMAS/  TMAS/  COAS/
```

All are public under `https://github.com/Power-Supply-Manufacturers-Association/`. A missing
sibling is an error (the tests and `validate_submission.py` refuse to run), never a skip.
Always work on the latest `main` of every repo; never pin an older commit to make something pass.

## One command

```bash
# needs git, git-lfs, python3 >= 3.10
bash setup_workspace.sh ~/PSMA --catalogues capacitors          # only the catalogue you work on
bash setup_workspace.sh ~/PSMA                                  # or all ~5 GB of catalogues
source ~/PSMA/.venv/bin/activate
```

(Before TAS is cloned, fetch the script from the TAS repo on GitHub:
`.claude/skills/tas-contribute/scripts/setup_workspace.sh`. Afterwards run it from your clone.)

It clones (or fast-forwards) the 14 repos, pulls the requested catalogues from git-LFS plus
`data/quarantine.ndjson` and `data/fabricated_denylist.ndjson` (the gate always needs both),
checks none is left as an LFS pointer, makes a venv with `pytest jsonschema referencing`, and
runs `pytest tests/test_schemas.py` (all must pass; if not, something is actually broken --
report it, do not work around it).

### git-LFS facts

- `data/*.ndjson` are git-LFS files. A ~130-byte file starting with
  `version https://git-lfs.github.com/spec/v1` is an unfetched **pointer**, not data. A gate
  that reads a pointer sees zero rows; the scripts refuse pointers, and so should you: zero
  rows from a data file is a symptom, not a result.
- More catalogues later: `git lfs pull --include data/resistors.ndjson`.
- If `GIT_LFS_SKIP_SMUDGE=1` is set in your shell, `git checkout -- data/x.ndjson` writes a
  pointer over the data. Restore with `env -u GIT_LFS_SKIP_SMUDGE git lfs checkout data/x.ndjson`.

## Blade Runner (`tas_validator`) and the metrics engine (`tas_metrics`)

Blade Runner is the C++ physics validator: it flags parts that are physically IMPOSSIBLE or
SUSPICIOUS. `tas_metrics` is the database quality scorer. Both are Python extension modules
the gate imports from `TAS/validator/build/`.

**Prebuilt (Linux x86_64, CPython 3.12):**

```bash
bash TAS/.claude/skills/tas-contribute/scripts/fetch_blade_runner.sh
```

Downloads the release `blade-runner-<sha>` (the last commit that changed `validator/`
sources) from the TAS GitHub releases, verifies its sha256 (`--sha256 HEX` pins a hash a
maintainer gave you), installs into `validator/build/`, and runs
`validator/tools/check_freshness.py`. If the release was built from different validator
source than your checkout, it refuses: build instead.

**Build from source (any Linux/macOS with a C++20 compiler):**

```bash
# Debian/Ubuntu: sudo apt install build-essential cmake ninja-build python3-dev
bash TAS/.claude/skills/tas-contribute/scripts/build_blade_runner.sh [--with-tests]
```

CMake + Ninja, 3 parallel jobs, fetches nlohmann/json, pybind11 (and Catch2 with
`--with-tests`, whose Catch2 binaries it then runs directly). It ends with the freshness
check and an import test.

**Never trust a module by name or file date.** A stale build does not fail; it silently
behaves like an older validator, and a missing check reads as "this part passed".
`validate_submission.py` runs the freshness check every time.

## Resource limits

- Keep your work and cache directories under 2 GB; gzip PDFs and text, delete raw HTML once
  parsed. Stop fetching if the disk has under 8 GB free.
- Stream catalogues line by line; never load a whole catalogue into memory.
- If a process is killed for low memory, wait a random 10-60 minutes and retry that step.
