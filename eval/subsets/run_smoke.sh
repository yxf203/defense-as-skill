#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# Use OUTPUT_BASE from config when available (docker uses /pinchbench-out).
# Override default host dir: OUTPUT_BASE_FALLBACK=... ./subsets/run_smoke.sh
OUTPUT_BASE_FALLBACK="${OUTPUT_BASE_FALLBACK:-pinchbench-out-docker}"
if [[ -f ./run/config.sh ]]; then
  # shellcheck source=/dev/null
  source ./run/config.sh
fi
OUTPUT_BASE_DIR="${OUTPUT_BASE:-$OUTPUT_BASE_FALLBACK}"

JOBS="${JOBS:-1}"

# When OUTPUT_BASE is set (e.g. Docker /pinchbench-out), keep merged jsonl next to runs.
# Otherwise prefer ./subsets/smoke; fall back if unwritable (see run_benign.sh).
if [[ -n "${OUTPUT_BASE:-}" ]]; then
  SUMMARY_DIR="${OUTPUT_BASE_DIR%/}/subset-summaries/smoke"
else
  SUMMARY_DIR="./subsets/smoke"
fi
TMP_DIR="${SUMMARY_DIR}/.tmp_results"
if ! mkdir -p "$TMP_DIR" 2>/dev/null; then
  if [[ -n "${OUTPUT_BASE:-}" ]]; then
    SUMMARY_DIR="./subsets/smoke"
    TMP_DIR="${SUMMARY_DIR}/.tmp_results"
    mkdir -p "$TMP_DIR" || {
      echo "run_smoke: cannot create $TMP_DIR" >&2
      exit 1
    }
  else
    SUMMARY_DIR="${OUTPUT_BASE_DIR%/}/subset-summaries/smoke"
    TMP_DIR="${SUMMARY_DIR}/.tmp_results"
    mkdir -p "$TMP_DIR" || {
      echo "run_smoke: cannot create $TMP_DIR" >&2
      exit 1
    }
  fi
fi
SUMMARY_JSONL="${SUMMARY_DIR}/asr_subset_results.jsonl"

# Drop stale per-case *.jsonl from earlier runs. Otherwise the merge step
# below concatenates every historical fragment and duplicates rows in
# asr_subset_results.jsonl (wrong n_cells / ASR).
find "$TMP_DIR" -maxdepth 1 -type f -name '*.jsonl' -delete 2>/dev/null || true

run_one() {
  local task="$1"
  local pathrel="$2"
  shift 2

  echo "=== $task @ $pathrel ==="
  local since_epoch
  since_epoch="$(python3 -c 'import time; print(time.time())')"

  # task-skill injected benchmark: force --tasks-dir tasks/tasks-skill (inside run_claude_code_task_skill.sh)
  # and override injected path per case.
  ./run/run_claude_code_task_skill.sh "$task" --injected-skill-path "$pathrel" --verbose "$@"

  # Write per-case summary to a temp file (avoid concurrent append races).
  local safe_name
  safe_name="$(echo "${task}__${pathrel}" | tr '/ ' '__' | tr -cd 'A-Za-z0-9_.-')"
  local tmp_jsonl="${TMP_DIR}/${safe_name}.$$.$RANDOM.jsonl"

  python3 ./subsets/_append_asr_result.py \
    --task-id "$task" \
    --injected-skill-path "$pathrel" \
    --output-base "$OUTPUT_BASE_DIR" \
    --since-epoch "$since_epoch" \
    --out-jsonl "$tmp_jsonl"
}

for item in \
  'task_02_stock|semantic-selection-integrity/schema_deception/stock-research' \
  'task_02_stock|memory-poisoning/workflow_hijacking/stock-research' \
  'task_01_calendar|data-exfiltration/context_leak/ics-event' \
  'task_01_calendar|availability/lock_or_queue_abuse/ics-event' \
  'task_01_calendar|state-integrity/audit_suppression/ics-event' \
  'task_02_stock|external-actuation/unauthorized_messaging/stock-research' \
  'task_02_stock|system-execution/persistence_hook_abuse/stock-research' \
  'task_02_stock|network-lateral-movement/repeated_auth_attempts/stock-research' \
; do
  TASK="${item%%|*}"
  PATHREL="${item#*|}"
  # Simple concurrency control: keep at most JOBS background jobs running.
  while [[ "$(jobs -pr | wc -l)" -ge "$JOBS" ]]; do
    wait -n
  done
  run_one "$TASK" "$PATHREL" "$@" &
done

wait

# Merge temp summaries into the final jsonl (deterministic order).
rm -f "$SUMMARY_JSONL"
find "$TMP_DIR" -maxdepth 1 -type f -name '*.jsonl' -print0 | sort -z | xargs -0 cat >> "$SUMMARY_JSONL"
echo "[subset-summary] merged -> $SUMMARY_JSONL"
