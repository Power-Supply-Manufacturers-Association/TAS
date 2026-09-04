#!/usr/bin/env python3
"""Refuse to trust a built tas_validator module without proving it is not
stale (ABT #397).

Importing a compiled tas_validator.*.so ALWAYS succeeds, whether it was built
five seconds ago or from a checkout that predates the last five commits to
validator/src -- there is no load-time error, hasattr() just quietly returns
False for whatever check was added since, and a gate reports "clean" having
never run the new rule. That is exactly how ABT #552's false-rejection report
was chased for hours against a build that was already fixed at HEAD, and how
ABT #397 itself was found (a freshly-added validate_circuit() present in one
build tree and absent from a same-repo sibling tree, 4.5 hours older, with
nothing to tell them apart at import time).

This script is the loud check that should run right after import, in any
consumer (blade_gate.py, changed_records_gate, the librarian, an interactive
REPL) that cares whether the module it just loaded reflects current
validator/src -- NOT by timestamp (mtimes lie: touch, git checkout, rsync,
and a build system's own caching can all leave an old .so with a newer
mtime than the source it was built from), but by re-hashing the source with
the SAME function (gen_build_fingerprint.compute_fingerprint) that produced
the fingerprint baked into the module at build time, and comparing.

Usage:
    python3 check_freshness.py --module-dir <dir with tas_validator*.so> \
                                --validator-dir <path to validator/>

Exit 0, prints "FRESH <hash>", iff the loaded module's build_fingerprint()
equals a fresh recomputation from --validator-dir right now.
Exit 1, prints a diagnosis, otherwise -- including if the module lacks
build_fingerprint() entirely (a build from before ABT #397 landed this
guard: stale by definition, since it predates the very check).
No fallback path: a missing module, a missing validator dir, or an import
error is a hard failure (non-zero exit + exception), never a silent "assume
fresh".
"""
import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_build_fingerprint import compute_fingerprint  # noqa: E402


def check(module_dir: Path, validator_dir: Path) -> str:
    module_dir = Path(module_dir).resolve()
    validator_dir = Path(validator_dir).resolve()
    if not module_dir.is_dir():
        raise FileNotFoundError(f"no such module dir: {module_dir}")

    sys.path.insert(0, str(module_dir))
    tas_validator = importlib.import_module("tas_validator")

    if not hasattr(tas_validator, "build_fingerprint"):
        raise RuntimeError(
            f"{module_dir} contains a tas_validator module built before ABT #397's "
            "fingerprint guard existed -- it cannot even be checked for staleness, "
            "which makes it stale by definition. Rebuild it."
        )

    built = tas_validator.build_fingerprint()
    current = compute_fingerprint(validator_dir)
    if built != current:
        raise RuntimeError(
            f"STALE MODULE at {module_dir}\n"
            f"  embedded build_fingerprint: {built}\n"
            f"  current validator/src hash: {current}\n"
            f"This .so was compiled from different source content than what is on "
            f"disk in {validator_dir} right now. Rebuild {module_dir} before trusting "
            f"anything imported from it (cmake --build {module_dir})."
        )
    return built


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module-dir", required=True,
                     help="directory containing the built tas_validator*.so")
    ap.add_argument("--validator-dir", required=True,
                     help="path to the validator/ directory (containing src/ and include/)")
    args = ap.parse_args()
    fingerprint = check(Path(args.module_dir), Path(args.validator_dir))
    print(f"FRESH {fingerprint}")


if __name__ == "__main__":
    main()
