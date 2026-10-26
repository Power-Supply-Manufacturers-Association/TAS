#!/usr/bin/env bash
# Build Blade Runner (tas_validator) and the metrics engine (tas_metrics) from validator/.
#
#   build_blade_runner.sh [--tas-root DIR] [--build-dir DIR] [--with-tests]
#
# Needs: a C++20 compiler (g++ >= 11 or clang >= 14), CMake >= 3.18, Ninja, Python 3 dev
# headers (Debian/Ubuntu: sudo apt install build-essential cmake ninja-build python3-dev),
# and network on the first configure (CMake fetches nlohmann/json, pybind11, Catch2).
# Uses 3 parallel jobs. Ends with the freshness check: the modules must be built from the
# validator source on disk, or the gate would silently run an older Blade Runner.
# Extra CMake arguments can be passed in EXTRA_CMAKE_ARGS (e.g. FETCHCONTENT_SOURCE_DIR_*
# to reuse already-downloaded dependency sources).
set -euo pipefail
BUILD_DIR=""; TESTS=OFF; TAS_ROOT_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tas-root) TAS_ROOT_ARG="$2"; shift 2;;
    --build-dir) BUILD_DIR="$2"; shift 2;;
    --with-tests) TESTS=ON; shift;;
    *) echo "unknown argument $1" >&2; exit 2;;
  esac
done
source "$(dirname "${BASH_SOURCE[0]}")/_tas_root.sh"
BUILD_DIR="${BUILD_DIR:-$TAS/validator/build}"
for t in cmake ninja python3; do command -v $t >/dev/null || { echo "ERROR: $t not found" >&2; exit 2; }; done
PY="$(command -v python3)"
python3 -c 'import sysconfig,os,sys; h=sysconfig.get_paths()["include"]; sys.exit(0 if os.path.exists(os.path.join(h,"Python.h")) else 1)' \
  || { echo "ERROR: Python.h not found: install the python3-dev package" >&2; exit 2; }
if [ -f "$BUILD_DIR/CMakeCache.txt" ] && ! grep -q '^CMAKE_GENERATOR:INTERNAL=Ninja$' "$BUILD_DIR/CMakeCache.txt"; then
  echo "ERROR: $BUILD_DIR was configured with another generator; use --build-dir NEWDIR" >&2; exit 2
fi
# shellcheck disable=SC2086
cmake -S "$TAS/validator" -B "$BUILD_DIR" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TAS_VALIDATOR_TESTS="$TESTS" -DPython3_EXECUTABLE="$PY" -DPython_EXECUTABLE="$PY" \
  -DPYTHON_EXECUTABLE="$PY" ${EXTRA_CMAKE_ARGS:-}
TARGETS="tas_validator tas_metrics"
[ "$TESTS" = ON ] && TARGETS="$TARGETS tas_validator_tests tas_metrics_tests"
# shellcheck disable=SC2086
cmake --build "$BUILD_DIR" -j3 --target $TARGETS
if [ "$TESTS" = ON ]; then
  "$BUILD_DIR/tas_validator_tests"      # Catch2, run directly
  "$BUILD_DIR/tas_metrics_tests"
fi
python3 "$TAS/validator/tools/check_freshness.py" --module-dir "$BUILD_DIR" --validator-dir "$TAS/validator"
python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import tas_validator, tas_metrics; print('import ok:', len(tas_validator.check_codes()), 'Blade Runner checks')" "$BUILD_DIR"
echo "Built into $BUILD_DIR"
