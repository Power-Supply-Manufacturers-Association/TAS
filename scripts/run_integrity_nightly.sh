#!/usr/bin/env bash
# Nightly integrity watchdog — run by cron at 02:00, before the librarian nightlies.
#
# WHAT IT IS. Runs every standing guard over the live catalogues and reports only
# what is NEW since the last accepted baseline, plus re-checks whether any quarantined
# row's withdrawal reason has stopped being true.
#
# TOKENS: ZERO. This is plain Python over the NDJSON files. The expensive judgement —
# is this rule measuring the parts or measuring my own parser? — was spent once, when
# each guard was written and counter-checked. Running them is arithmetic, and that is
# the whole point of the split: adjudicate rarely and expensively, check constantly
# and for free.
#
# WHY IT EXISTS. A one-shot repair cannot hold an invariant. On 2026-09-04 a cleanup
# swept 14 catalogues and 410 fresh violations were written into connectors within six
# hours by later commits. Every large defect cohort found in the 2026-09-05 audit came
# from an IMPORTER, not from independently bad rows.
#
# QUIET WHEN HEALTHY. A job that prints 1,265 known findings nightly is a job nobody
# reads. Known findings are silent; only NEW ones (and guards that could not run) are
# reported. Nothing here writes to data/ — recovery emits candidates for adjudication,
# because schema validity is not correctness: of 327 cable cores restored on exactly
# that signal, 65 were contradicted by their own vendors while validating cleanly.
#
# flock makes it idempotent: if last night's run is still going, this one exits.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${HOME}/.local/share/psma-librarian"
LOG="${LOG_DIR}/integrity.log"
STATE="${REPO}/staging/integrity"
LOCK="${LOG_DIR}/integrity.lock"
PY="${LIBRARIAN_PYTHON:-python3}"
mkdir -p "$LOG_DIR" "$STATE"

exec 9>"$LOCK"
flock -n 9 || { echo "$(date -u +%FT%TZ) previous integrity run still going; skipping" >> "$LOG"; exit 0; }

cd "$REPO"

# A full disk is not a hypothetical here: on 2026-09-05 a guard died mid-rewrite of a
# 300 MB catalogue with "No space left on device" AND STILL EXITED 0. Refuse to start
# rather than risk a truncated atomic swap.
AVAIL_MB=$(df -Pm . | awk 'NR==2 {print $4}')
if [ "${AVAIL_MB:-0}" -lt 2048 ]; then
  echo "$(date -u +%FT%TZ) ABORT: only ${AVAIL_MB} MB free; guards rewrite 300 MB files" >> "$LOG"
  exit 2
fi

{
  echo "=== integrity $(date -u +%FT%TZ) ==="
  "$PY" scripts/integrity_scan.py --json "$STATE/scan.json"
  SCAN=$?
  echo "--- quarantine recovery candidates ---"
  "$PY" scripts/quarantine_recover.py --out "$STATE/quarantine_candidates.ndjson"
  echo "scan exit=$SCAN (0 nothing new / 1 NEW findings / 2 a guard could not run)"
} >> "$LOG" 2>&1

# Surface the verdict where a human will trip over it, rather than only in a log.
SCAN_EXIT=$(awk '/^scan exit=/{sub(/^scan exit=/,""); print $1}' "$LOG" | tail -1)
if [ "${SCAN_EXIT:-0}" != "0" ]; then
  cp "$LOG" "${LOG_DIR}/integrity.ATTENTION.log" 2>/dev/null || true
fi
exit 0
