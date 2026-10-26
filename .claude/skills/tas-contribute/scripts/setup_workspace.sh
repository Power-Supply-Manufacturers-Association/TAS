#!/usr/bin/env bash
# Set up a contributor workspace in the PSMA layout: TAS plus every sibling schema repo it
# resolves, side by side in one directory, the TAS catalogues pulled from git-LFS, and a
# Python venv with the test dependencies.
#
#   setup_workspace.sh [WORKSPACE_DIR] [--catalogues capacitors,resistors] [--no-venv]
#
# WORKSPACE_DIR defaults to ./PSMA. --catalogues pulls only those live catalogues (plus the
# quarantine and the fabricated-parts denylist the gate always needs) instead of all ~5 GB.
# Re-running it updates every repo with a fast-forward pull. Blade Runner is a separate
# step: fetch_blade_runner.sh (prebuilt, Linux x86_64 + CPython 3.12) or build_blade_runner.sh.
set -euo pipefail
WS="PSMA"; CATS=""; VENV=1
while [ $# -gt 0 ]; do
  case "$1" in
    --catalogues) CATS="$2"; shift 2;;
    --no-venv) VENV=0; shift;;
    -*) echo "unknown option $1" >&2; exit 2;;
    *) WS="$1"; shift;;
  esac
done
ORG="https://github.com/Power-Supply-Manufacturers-Association"
REPOS="TAS PEAS CIAS SAS CAS RAS MAS CTAS AAS CONAS TDAS EMAS TMAS COAS"
for t in git git-lfs python3; do command -v $t >/dev/null || { echo "ERROR: $t not found" >&2; exit 2; }; done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "ERROR: Python >= 3.10 needed" >&2; exit 2; }
mkdir -p "$WS"; WS="$(cd "$WS" && pwd)"
for r in $REPOS; do
  if [ -d "$WS/$r/.git" ]; then
    echo "== update $r"; GIT_LFS_SKIP_SMUDGE=1 git -C "$WS/$r" pull --ff-only
  else
    echo "== clone $r"; GIT_LFS_SKIP_SMUDGE=1 git clone "$ORG/$r.git" "$WS/$r"
  fi
done
cd "$WS/TAS"
git lfs install --local >/dev/null
if [ -n "$CATS" ]; then
  INC="data/quarantine.ndjson,data/fabricated_denylist.ndjson"
  for c in ${CATS//,/ }; do INC="$INC,data/${c%.ndjson}.ndjson"; done
  echo "== git lfs pull --include $INC"
  git lfs pull --include "$INC"
  FILES="${INC//,/ }"
else
  FREE_GB=$(df -Pk . | awk 'NR==2 {print int($4/1048576)}')
  [ "$FREE_GB" -ge 8 ] || { echo "ERROR: ${FREE_GB} GB free; the full pull needs ~5 GB plus headroom. Use --catalogues." >&2; exit 2; }
  echo "== git lfs pull (all catalogues, ~5 GB)"
  git lfs pull
  FILES="$(git ls-files 'data/*.ndjson')"
fi
for f in $FILES; do
  head -c 40 "$f" | grep -q '^version https://git-lfs' && { echo "ERROR: $f is still an LFS pointer" >&2; exit 1; }
done
echo "== LFS data materialised"
if [ "$VENV" = 1 ]; then
  [ -d "$WS/.venv" ] || python3 -m venv "$WS/.venv"
  "$WS/.venv/bin/python" -m pip install -q --upgrade pip
  "$WS/.venv/bin/python" -m pip install -q pytest jsonschema referencing
  PY="$WS/.venv/bin/python"
  echo "== venv ready: source $WS/.venv/bin/activate"
else
  PY=python3
  "$PY" -c 'import jsonschema, referencing, pytest' || { echo "ERROR: pip install pytest jsonschema referencing" >&2; exit 2; }
fi
echo "== smoke test: schemas"
"$PY" -m pytest -q tests/test_schemas.py
echo
echo "Workspace ready at $WS. Next: Blade Runner --"
echo "  bash $WS/TAS/.claude/skills/tas-contribute/scripts/fetch_blade_runner.sh   # or build_blade_runner.sh"
