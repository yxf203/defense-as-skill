#!/usr/bin/env bash
# Smoke 子集 — **自包含版**：不 source ./run/config.sh。
#
# 用法：在下方「自包含环境」块中填写/修改 export（可复制自 run/config.example.sh），
# 然后与本仓库原版 run_smoke.sh 一样执行：
#   ./subsets/run_smoke_standalone.sh
#   ./subsets/run_smoke_standalone.sh --verbose
#
# 可与另一实验并行：各用不同副本或不同终端里先 export 再跑，无需改共享的 run/config.sh。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# =============================================================================
# 自包含环境 — 按实验修改（不读取 run/config.sh）
# =============================================================================
export OUTPUT_BASE="${OUTPUT_BASE:-/tmp/pinchbench-out}"

export CC_BASE_URL="${CC_BASE_URL:-http://35.220.164.252:3888/}"
export CC_AUTH_TOKEN="${CC_AUTH_TOKEN:-<your-api-key>}"
export CC_ALIAS="${CC_ALIAS:-haiku}"
# export CC_REAL_MODEL="${CC_REAL_MODEL:-claude-haiku-4-5-20251001}"
export CC_REAL_MODEL="${CC_REAL_MODEL:-gpt-5.4}"
export CC_HOST="${CC_HOST:-35.220.164.252}"

export JUDGE="${JUDGE:-haiku}"
# export TASKSKILL_JUDGE_MODEL=""   # 可选：与 JUDGE 不同时再设

# 仅用于通过子脚本里的「非空」检查；每个 cell 仍会传 --injected-skill-path 覆盖 benchmark。
export TASKSKILL_INJECTED_PATH="${TASKSKILL_INJECTED_PATH:-semantic-selection-integrity/schema_deception/stock-research}"
# =============================================================================

OUTPUT_BASE_DIR="$OUTPUT_BASE"
JOBS="${JOBS:-1}"

SUMMARY_DIR="./subsets/smoke"
TMP_DIR="${SUMMARY_DIR}/.tmp_results"
if ! mkdir -p "$TMP_DIR" 2>/dev/null; then
  SUMMARY_DIR="${OUTPUT_BASE_DIR%/}/subset-summaries/smoke"
  TMP_DIR="${SUMMARY_DIR}/.tmp_results"
  mkdir -p "$TMP_DIR"
fi
SUMMARY_JSONL="${SUMMARY_DIR}/asr_subset_results.jsonl"

# Drop stale per-case *.jsonl from earlier runs (see run_smoke.sh).
find "$TMP_DIR" -maxdepth 1 -type f -name '*.jsonl' -delete 2>/dev/null || true

run_one() {
  local task="$1"
  local pathrel="$2"
  shift 2

  echo "=== $task @ $pathrel ==="
  local since_epoch
  since_epoch="$(python3 -c 'import time; print(time.time())')"

  ./run/run_claude_code_task_skill_standalone.sh "$task" --injected-skill-path "$pathrel" --verbose "$@"

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
  while [[ "$(jobs -pr | wc -l)" -ge "$JOBS" ]]; do
    wait -n
  done
  run_one "$TASK" "$PATHREL" "$@" &
done

wait

rm -f "$SUMMARY_JSONL"
find "$TMP_DIR" -maxdepth 1 -type f -name '*.jsonl' -print0 | sort -z | xargs -0 cat >> "$SUMMARY_JSONL"
echo "[subset-summary] merged -> $SUMMARY_JSONL"
