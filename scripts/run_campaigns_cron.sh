#!/usr/bin/env bash
# ABT #249 + #304 campaign keep-alive, driven by cron.
#
# WHY CRON: background processes started from an agent tool call do NOT survive the
# session — verified twice, once with `nohup &` and once with `setsid nohup &`. Both
# were killed with the whole process tree (no traceback, no STOP file). cron is the
# only launcher here that outlives a session, and it already runs the librarian
# nightlies, so it is a proven path on this box.
#
# flock makes this idempotent: if the previous run is still going, this one exits
# immediately. So a */5 schedule simply restarts a campaign that was killed, and is a
# no-op while one is healthy. Neither campaign loses work when killed — both
# checkpoint after every batch/part and never mark an errored item done.
#
# Stop either campaign by creating its STOP file:
#   touch /home/alf/PSMA/TAS/staging/te/STOP
#   touch /home/alf/PSMA/TAS/staging/murata/STOP
set -uo pipefail

TAS=/home/alf/PSMA/TAS
cd "$TAS" || exit 1
export DISPLAY=:0          # TE needs a headed browser (user-approved exception, ABT #249)
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

case "${1:-}" in
  te)
    [ -f "$TAS/staging/te/STOP" ] && exit 0
    exec flock -n "$TAS/staging/te/.lock" \
      python3 scripts/te_mating_overnight.py --batch 120 --delay 0.3 --write-every 5 \
      >> "$TAS/staging/te/cron_stdout.log" 2>&1
    ;;
  murata)
    [ -f "$TAS/staging/murata/STOP" ] && exit 0
    exec flock -n "$TAS/staging/murata/.lock" \
      python3 scripts/murata_bias_harvest.py fetch --delay 0.35 \
      >> "$TAS/staging/murata/cron_stdout.log" 2>&1
    ;;
  molex)
    [ -f "$TAS/staging/molex/STOP" ] && exit 0
    exec flock -n "$TAS/staging/molex/.lock" \
      python3 scripts/molex_mating_harvest.py fetch --delay 0.25 \
      >> "$TAS/staging/molex/cron_stdout.log" 2>&1
    ;;
  *)
    echo "usage: $0 {te|murata}" >&2; exit 2
    ;;
esac
