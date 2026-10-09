#!/usr/bin/env bash
# Claude Code backend for benign-task evaluation (no attack injection).
#
# Mirror of run_claude_code_task_skill.sh, but:
#   * reads tasks from tasks/tasks-benign
#   * does NOT pass --injected-skill-path
#   * judge is used to emit the benign-feedback `comment` (see
#     lib_grading._maybe_merge_benign_feedback)
#
# Usage:
#   ./run/run_claude_code_task_benign.sh                     # default task_00_sanity
#   ./run/run_claude_code_task_benign.sh task_01_calendar
#   ./run/run_claude_code_task_benign.sh all
#   ./run/run_claude_code_task_benign.sh task_00_sanity --verbose

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$HERE/.." && pwd)"
_DEFAULT_CFG="$HERE/config.sh"
CONFIG_TO_SOURCE="${SKILLGUARD_CONFIG:-$_DEFAULT_CFG}"
if [[ "$CONFIG_TO_SOURCE" != /* ]]; then
  CONFIG_TO_SOURCE="$HERE/$CONFIG_TO_SOURCE"
fi
PRESET_OUTPUT_BASE="${OUTPUT_BASE:-}"

if [[ ! -f "$CONFIG_TO_SOURCE" ]]; then
  echo "❌ cannot find $CONFIG_TO_SOURCE"
  echo "   first run: cp run/config.example.sh run/config.sh && vim run/config.sh"
  exit 1
fi
# shellcheck source=/dev/null
source "$CONFIG_TO_SOURCE"
if [[ -n "${PRESET_OUTPUT_BASE}" ]]; then
  OUTPUT_BASE="$PRESET_OUTPUT_BASE"
fi

if [[ $# -ge 1 ]]; then
  TASK="$1"
  shift
else
  TASK="task_00_sanity"
fi
RUN_TAG="${RUN_TAG_OVERRIDE:-$(date +%Y%m%d-%H%M%S)-$$}"

if [[ "$TASK" == "all" ]]; then
  SUITE_ARG=(
    --suite all
    --tasks-dir tasks/tasks-benign
  )
else
  SUITE_ARG=(
    --suite "$TASK"
    --tasks-dir tasks/tasks-benign
  )
fi

case "$CC_ALIAS" in
  haiku)  ALIAS_VAR="ANTHROPIC_DEFAULT_HAIKU_MODEL"  ;;
  sonnet) ALIAS_VAR="ANTHROPIC_DEFAULT_SONNET_MODEL" ;;
  opus)   ALIAS_VAR="ANTHROPIC_DEFAULT_OPUS_MODEL"   ;;
  *)
    echo "❌ CC_ALIAS must be one of haiku / sonnet / opus, got: $CC_ALIAS"
    exit 1
    ;;
esac

OUT_DIR="${OUTPUT_BASE}/cc_taskbenign_${CC_REAL_MODEL//[\/.]/-}_${RUN_TAG}"

# Benign-feedback judge lives on the same channel as the ASR judge; reuse
# JUDGE (or TASKSKILL_JUDGE_MODEL when the user wants to override for both
# branches consistently).
JUDGE_MODEL="${TASKSKILL_JUDGE_MODEL:-${JUDGE:-}}"
JUDGE_BASE="${JUDGE_BASE_URL:-}"
JUDGE_TOKEN="${JUDGE_AUTH_TOKEN:-}"
JUDGE_HOST_CFG="${JUDGE_HOST:-}"
NO_PROXY_HOSTS="${CC_HOST}"
if [[ -n "$JUDGE_HOST_CFG" && "$JUDGE_HOST_CFG" != "$CC_HOST" ]]; then
  NO_PROXY_HOSTS="${NO_PROXY_HOSTS},${JUDGE_HOST_CFG}"
fi

cd "$SKILL_DIR"
echo "🚀 Claude Code (self-hosted) + benign-task evaluation"
echo "   agent:    ${CC_REAL_MODEL} (benchmark --model ${CC_ALIAS})"
echo "   suite:    ${TASK}"
echo "   tasks:    tasks/tasks-benign"
if [[ -z "$JUDGE_MODEL" ]]; then
  echo "   judge:    (no JUDGE configured, benign 'comment' will be empty)"
else
  src="JUDGE"
  [[ -n "${TASKSKILL_JUDGE_MODEL:-}" ]] && src="TASKSKILL_JUDGE_MODEL"
  echo "   judge:    ${JUDGE_MODEL} (benign feedback comment; from config ${src})"
  if [[ -n "$JUDGE_BASE" || -n "$JUDGE_TOKEN" ]]; then
    echo "   judge gw: dedicated (JUDGE_BASE_URL/JUDGE_AUTH_TOKEN)"
  fi
fi
echo "   output:   ${OUT_DIR}"
echo

BENCH=(python3 scripts/benchmark.py
  --backend claude-code
  --model "$CC_ALIAS"
  "${SUITE_ARG[@]}"
  --no-upload
  --output-dir "$OUT_DIR"
)

if [[ -n "$JUDGE_MODEL" ]]; then
  BENCH+=(--judge "$JUDGE_MODEL")
fi

exec env \
  ANTHROPIC_BASE_URL="$CC_BASE_URL" \
  ANTHROPIC_AUTH_TOKEN="$CC_AUTH_TOKEN" \
  PINCHBENCH_JUDGE_ANTHROPIC_BASE_URL="$JUDGE_BASE" \
  PINCHBENCH_JUDGE_ANTHROPIC_AUTH_TOKEN="$JUDGE_TOKEN" \
  "$ALIAS_VAR=$CC_REAL_MODEL" \
  NO_PROXY="${NO_PROXY_HOSTS},127.0.0.1,localhost" \
  no_proxy="${NO_PROXY_HOSTS},127.0.0.1,localhost" \
  "${BENCH[@]}" \
  "$@"
