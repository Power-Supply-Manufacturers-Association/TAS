#!/usr/bin/env bash
# Regenerate the full clamp-on / cable ferrite CORE set (MAS subtype `cableCore`)
# from the committed per-vendor importers, and concatenate them into ONE file:
#     data/cable_cores.ndjson
#
# This is the reproducible recipe behind the 792 cableCore records that were
# appended into the shared magnetics catalogue (magnetics.ndjson) and deployed to
# KelvinDB / Heaviside / the Hertz picker. The append into magnetics.ndjson itself
# is a deliberate one-liner (below) and is NOT done here — magnetics.ndjson is the
# accumulated canonical catalogue (git-LFS), so this script only rebuilds the
# vendor set; re-integrating is a manual, reviewed step.
#
#   scripts/build_cable_cores.sh                 # rebuild data/cable_cores.ndjson
#
# To (re-)integrate into the catalogue, first drop any prior cableCore rows so it
# stays idempotent, then append the fresh set:
#   grep -v '"subtype": "cableCore"' data/magnetics.ndjson > /tmp/mag.base
#   cat /tmp/mag.base data/cable_cores.ndjson > data/magnetics.ndjson
# then rebuild + deploy the Kelvin magnetic shard (Kelvin/web/scripts/*).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"          # TAS/
cd "$HERE"

# importer -> its output ndjson (all committed; raw inputs committed alongside)
IMPORTERS=(
  "we_cable_cores_import.py:we_cable_cores.ndjson"
  "fairrite_cable_cores_import.py:fairrite_cable_cores.ndjson"
  "tdk_clamp_filters_import.py:tdk_cable_cores.ndjson"
  "laird_cable_cores_import.py:laird_cable_cores.ndjson"
  "ferroxcube_cable_cores_import.py:ferroxcube_cable_cores.ndjson"
  "kitagawa_cable_cores_import.py:kitagawa_cable_cores.ndjson"
  "kemet_cable_cores_import.py:kemet_cable_cores.ndjson"
  "seiwa_cable_cores_import.py:seiwa_cable_cores.ndjson"
  "murata_cable_cores_import.py:murata_cable_cores.ndjson"
)
OUT=data/cable_cores.ndjson
: > "$OUT"
total=0
for pair in "${IMPORTERS[@]}"; do
  script="${pair%%:*}"; nd="data/${pair##*:}"
  python3 "scripts/$script" --apply >/dev/null
  [ -s "$nd" ] || { echo "ERROR: $script produced no $nd" >&2; exit 1; }
  n=$(wc -l < "$nd")
  cat "$nd" >> "$OUT"
  printf "  %-38s %4d\n" "$script" "$n"
  total=$((total + n))
done
echo "----"
echo "wrote $total cableCore records to $OUT  (expect 792 across 9 makers)"
