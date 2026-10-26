# Sourced by the shell scripts: resolve the TAS checkout into $TAS.
# Order: --tas-root DIR argument (already parsed into TAS_ROOT_ARG), $TAS_REPO, this
# script's location (<TAS>/.claude/skills/tas-contribute/scripts), then the current dir.
_is_tas() { [ -f "$1/schemas/TAS.json" ] && [ -f "$1/scripts/ingest_gate.py" ]; }
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAS=""
for c in "${TAS_ROOT_ARG:-}" "${TAS_REPO:-}" "$_here/../../../.." "$PWD"; do
  [ -n "$c" ] || continue
  if _is_tas "$c"; then TAS="$(cd "$c" && pwd)"; break; fi
done
if [ -z "$TAS" ]; then
  echo "ERROR: cannot find the TAS checkout; pass --tas-root DIR or set TAS_REPO" >&2
  exit 2
fi
RELEASE_REPO="Power-Supply-Manufacturers-Association/TAS"
ASSET_NAME="tas-blade-runner-linux-x86_64-cp312"
# The release tag names the last commit that changed the validator sources, so every
# checkout whose validator is the same finds the same release.
validator_tag() {
  local sha
  sha="$(git -C "$TAS" log -1 --format=%H -- validator/src validator/metrics validator/tools validator/CMakeLists.txt | cut -c1-12)"
  [ -n "$sha" ] || { echo "ERROR: cannot read the validator's last commit (is $TAS a git clone?)" >&2; return 2; }
  echo "blade-runner-$sha"
}
