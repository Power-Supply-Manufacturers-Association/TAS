"""Shared path resolution for the tas-contribute scripts.

Nothing here knows about any particular machine. The TAS checkout is found, in order:
  1. an explicit --tas-root argument (pulled out of sys.argv before argparse runs),
  2. the TAS_REPO environment variable,
  3. this file's own location (<TAS>/.claude/skills/tas-contribute/scripts/_common.py),
  4. the current directory or one of its parents.
A candidate counts only if it holds schemas/TAS.json and scripts/ingest_gate.py. If none
does, the scripts stop with an error: guessing a root would validate against the wrong tree.

The sibling schema repos (PEAS, SAS, CAS, ...) are resolved from the TAS checkout's PARENT
directory, the same rule the TAS tests and scripts/ingest_gate.py use.
"""
import os
import sys
from pathlib import Path

# Every sibling repo the gate, the tests and the schema registry resolve. A missing one is
# an error, never a skip: scripts/ingest_gate.py silently leaves a missing repo out of its
# $ref registry, which turns into confusing "unresolvable" failures much later.
SIBLINGS = ("PEAS", "CIAS", "SAS", "CAS", "RAS", "MAS", "CTAS", "AAS", "CONAS",
            "TDAS", "EMAS", "TMAS", "COAS")
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"


class SetupError(RuntimeError):
    """The environment cannot run a check. Callers exit 2: a check that cannot run is not a pass."""


def _is_tas(p):
    return (p / "schemas" / "TAS.json").is_file() and (p / "scripts" / "ingest_gate.py").is_file()


def pop_tas_root_arg(argv=None):
    """Remove '--tas-root X' / '--tas-root=X' from argv (default sys.argv) and return X or None."""
    argv = sys.argv if argv is None else argv
    for i, a in enumerate(list(argv)):
        if a == "--tas-root":
            if i + 1 >= len(argv):
                raise SetupError("--tas-root needs a directory")
            val = argv[i + 1]
            del argv[i:i + 2]
            return val
        if a.startswith("--tas-root="):
            del argv[i]
            return a.split("=", 1)[1]
    return None


def find_tas_root(explicit=None):
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("TAS_REPO"):
        cands.append(Path(os.environ["TAS_REPO"]))
    here = Path(__file__).resolve()
    if len(here.parents) > 4:
        cands.append(here.parents[4])
    cwd = Path.cwd().resolve()
    cands += [cwd, *cwd.parents]
    for c in cands:
        c = c.expanduser().resolve()
        if _is_tas(c):
            return c
    if explicit:
        raise SetupError(f"--tas-root {explicit} is not a TAS checkout "
                         "(needs schemas/TAS.json and scripts/ingest_gate.py)")
    raise SetupError("cannot find the TAS checkout: pass --tas-root <dir> or set TAS_REPO")


def setup_imports(root):
    """Put TAS/scripts (the gate) and TAS/validator/build (Blade Runner) on sys.path."""
    for p in (root / "validator" / "build", root / "scripts"):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def missing_siblings(root):
    return [s for s in SIBLINGS if not (root.parent / s / "schemas").is_dir()]


def is_lfs_pointer(path):
    with open(path, "rb") as f:
        return f.read(len(LFS_POINTER)) == LFS_POINTER


def require_data_file(root, name):
    """The live catalogue file must be real data, not an unfetched LFS pointer."""
    p = root / "data" / name
    if not p.is_file():
        raise SetupError(f"data/{name} is missing")
    if is_lfs_pointer(p):
        raise SetupError(f"data/{name} is an unfetched git-LFS pointer, not data: run "
                         f"`git lfs pull --include data/{name}` in the TAS checkout")
    return p


def data_root(root):
    """Data root for the gate. TAS_ROOT overrides it (for tests of the tooling only), as in
    the original pipeline scripts."""
    return Path(os.environ.get("TAS_ROOT", str(root)))
