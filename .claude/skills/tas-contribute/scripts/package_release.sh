#!/usr/bin/env bash
# MAINTAINERS: package the built modules for a GitHub release that fetch_blade_runner.sh reads.
#
#   package_release.sh [--tas-root DIR] [--build-dir DIR] [--out DIR]
#
# Refuses unless: the validator sources are committed (no local changes), the modules in the
# build dir pass the freshness check against them, and they are Linux x86_64 / CPython 3.12.
# Writes <out>/tas-blade-runner-linux-x86_64-cp312.tar.gz and .sha256 and prints the
# `gh release create` command to publish them. It does not publish anything itself.
set -euo pipefail
BUILD_DIR=""; OUTDIR=""; TAS_ROOT_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tas-root) TAS_ROOT_ARG="$2"; shift 2;;
    --build-dir) BUILD_DIR="$2"; shift 2;;
    --out) OUTDIR="$2"; shift 2;;
    *) echo "unknown argument $1" >&2; exit 2;;
  esac
done
source "$(dirname "${BASH_SOURCE[0]}")/_tas_root.sh"
BUILD_DIR="${BUILD_DIR:-$TAS/validator/build}"
OUTDIR="${OUTDIR:-$PWD/blade-runner-release}"
if [ -n "$(git -C "$TAS" status --porcelain -- validator/src validator/metrics validator/tools validator/CMakeLists.txt)" ]; then
  echo "ERROR: validator sources have uncommitted changes; a release must match a commit" >&2; exit 1
fi
[ "$(uname -s)-$(uname -m)" = "Linux-x86_64" ] || { echo "ERROR: package on Linux x86_64" >&2; exit 2; }
SOS=("$BUILD_DIR"/tas_validator.cpython-312-x86_64-linux-gnu.so "$BUILD_DIR"/tas_metrics.cpython-312-x86_64-linux-gnu.so)
for so in "${SOS[@]}"; do [ -f "$so" ] || { echo "ERROR: $so missing (build with CPython 3.12)" >&2; exit 2; }; done
FRESH="$(python3 "$TAS/validator/tools/check_freshness.py" --module-dir "$BUILD_DIR" --validator-dir "$TAS/validator")" \
  || { echo "ERROR: modules are not built from the committed validator source: rebuild first" >&2; exit 1; }
TAG="$(validator_tag)"
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
cp "${SOS[@]}" "$STAGE/"
python3 - "$STAGE/MANIFEST.json" "$TAG" "$FRESH" <<'PY'
import json, platform, sys
out, tag, fresh = sys.argv[1:4]
json.dump({"tag": tag, "freshness": fresh, "python": platform.python_version(),
           "platform": f"{platform.system()}-{platform.machine()}",
           "libc": "-".join(platform.libc_ver())}, open(out, "w"), indent=1)
PY
mkdir -p "$OUTDIR"
tar -czf "$OUTDIR/$ASSET_NAME.tar.gz" -C "$STAGE" --owner=0 --group=0 --numeric-owner \
  tas_validator.cpython-312-x86_64-linux-gnu.so tas_metrics.cpython-312-x86_64-linux-gnu.so MANIFEST.json
(cd "$OUTDIR" && sha256sum "$ASSET_NAME.tar.gz" > "$ASSET_NAME.tar.gz.sha256")
cat "$OUTDIR/$ASSET_NAME.tar.gz.sha256"
echo
echo "Publish with:"
echo "  gh release create $TAG '$OUTDIR/$ASSET_NAME.tar.gz' '$OUTDIR/$ASSET_NAME.tar.gz.sha256' \\"
echo "     --repo $RELEASE_REPO --title 'Blade Runner $TAG' --notes 'Prebuilt tas_validator + tas_metrics, Linux x86_64, CPython 3.12. $FRESH'"
