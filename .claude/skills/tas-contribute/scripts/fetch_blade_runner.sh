#!/usr/bin/env bash
# Download prebuilt Blade Runner + metrics modules (Linux x86_64, CPython 3.12) from a GitHub
# release of the TAS repo, verify the sha256, install them into validator/build/, and check
# they were built from the validator source in this checkout.
#
#   fetch_blade_runner.sh [--tas-root DIR] [--tag blade-runner-<sha>] [--sha256 HEX] [--force]
#   fetch_blade_runner.sh --file TARBALL --sha256 HEX      # offline: a tarball you already have
#
# --tag     defaults to blade-runner-<last commit that changed validator/ sources>.
# --sha256  pins the expected tarball hash (from a maintainer); otherwise the .sha256 file
#           published next to the tarball is used. Either way a mismatch aborts.
# --force   overwrite modules already in the install dir.
# --out     install dir (default validator/build/, where the gate imports from).
# Any other platform or Python: build instead (build_blade_runner.sh).
set -euo pipefail
TAG=""; PIN=""; FORCE=0; TAS_ROOT_ARG=""; LOCAL=""; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tas-root) TAS_ROOT_ARG="$2"; shift 2;;
    --tag) TAG="$2"; shift 2;;
    --sha256) PIN="$2"; shift 2;;
    --force) FORCE=1; shift;;
    --file) LOCAL="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    *) echo "unknown argument $1" >&2; exit 2;;
  esac
done
source "$(dirname "${BASH_SOURCE[0]}")/_tas_root.sh"
[ "$(uname -s)-$(uname -m)" = "Linux-x86_64" ] || { echo "ERROR: prebuilt modules are Linux x86_64 only; run build_blade_runner.sh" >&2; exit 2; }
PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[ "$PYV" = "3.12" ] || { echo "ERROR: prebuilt modules are for CPython 3.12 (this python3 is $PYV); run build_blade_runner.sh" >&2; exit 2; }
TAG="${TAG:-$(validator_tag)}"
BASE="https://github.com/$RELEASE_REPO/releases/download/$TAG"
DL="$(mktemp -d)"; trap 'rm -rf "$DL"' EXIT
mkdir -p "$DL/download" "$DL/extract"
if [ -n "$LOCAL" ]; then
  [ -n "$PIN" ] || { echo "ERROR: --file needs --sha256 (the hash published with the release)" >&2; exit 2; }
  cp "$LOCAL" "$DL/download/$ASSET_NAME.tar.gz"
else
  echo "Fetching $BASE/$ASSET_NAME.tar.gz"
  curl -fL --retry 3 --proto '=https' -o "$DL/download/$ASSET_NAME.tar.gz" "$BASE/$ASSET_NAME.tar.gz"
fi
if [ -n "$PIN" ]; then
  EXPECTED="$PIN"
else
  curl -fL --retry 3 --proto '=https' -o "$DL/download/$ASSET_NAME.tar.gz.sha256" "$BASE/$ASSET_NAME.tar.gz.sha256"
  EXPECTED="$(awk '{print $1; exit}' "$DL/download/$ASSET_NAME.tar.gz.sha256")"
fi
[[ "$EXPECTED" =~ ^[0-9a-f]{64}$ ]] || { echo "ERROR: no valid expected sha256" >&2; exit 2; }
ACTUAL="$(sha256sum "$DL/download/$ASSET_NAME.tar.gz" | awk '{print $1}')"
if [ "$ACTUAL" != "$EXPECTED" ]; then
  echo "ERROR: sha256 mismatch: expected $EXPECTED, got $ACTUAL -- not installing" >&2; exit 1
fi
echo "sha256 OK $ACTUAL"
# only plain member names, nothing with a path
if tar -tzf "$DL/download/$ASSET_NAME.tar.gz" | grep -qvE '^(\./)?(tas_validator|tas_metrics)\.cpython-312-x86_64-linux-gnu\.so$|^(\./)?MANIFEST\.json$|^\./$'; then
  echo "ERROR: unexpected members in the tarball -- not installing" >&2; exit 1
fi
tar -xzf "$DL/download/$ASSET_NAME.tar.gz" -C "$DL/extract" --no-same-owner
OUT="${OUT:-$TAS/validator/build}"; mkdir -p "$OUT"
for so in "$DL"/extract/*.so; do
  dst="$OUT/$(basename "$so")"
  if [ -e "$dst" ] && [ "$FORCE" != 1 ] && ! cmp -s "$so" "$dst"; then
    echo "ERROR: $dst exists and differs; pass --force to replace it" >&2; exit 2
  fi
done
# rename into place: never rewrite a .so another process may have mapped
for so in "$DL"/extract/*.so; do
  dst="$OUT/$(basename "$so")"
  cmp -s "$so" "$dst" 2>/dev/null && continue
  cp "$so" "$dst.tmp.$$" && mv -f "$dst.tmp.$$" "$dst"
done
[ -f "$DL/extract/MANIFEST.json" ] && cp "$DL/extract/MANIFEST.json" "$OUT/RELEASE_MANIFEST.json"
python3 "$TAS/validator/tools/check_freshness.py" --module-dir "$OUT" --validator-dir "$TAS/validator" || {
  echo "ERROR: the release was built from a different validator source than this checkout; run build_blade_runner.sh" >&2; exit 1; }
python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import tas_validator, tas_metrics; print('import ok:', len(tas_validator.check_codes()), 'Blade Runner checks')" "$OUT"
echo "Installed $TAG into $OUT"
