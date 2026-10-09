#!/usr/bin/env bash
# Claude Code 后端 + task-skill 注入实验（injected-skills / attack-metadata / ASR judge）。
# 与 run_claude_code_custom.sh 一样走 CC_* 自部署网关；注入路径用 TASKSKILL_INJECTED_PATH；--judge 用 JUDGE（可选 TASKSKILL_JUDGE_MODEL 覆盖）。
#
# 用法：
#   ./run/run_claude_code_task_skill.sh                     # 默认 task_02_stock
#   ./run/run_claude_code_task_skill.sh task_01_calendar
#   ./run/run_claude_code_task_skill.sh all                 # summary.json 里列出的全部 task
#   ./run/run_claude_code_task_skill.sh task_02_stock --verbose
# 第一个参数为 task id 或 all；其余参数原样传给 benchmark.py

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$HERE/.." && pwd)"
# Same as subsets/run_all_instances_mixed.sh: honor SKILLGUARD_CONFIG for test/alternate profiles.
_DEFAULT_CFG="$HERE/config.sh"
CONFIG_TO_SOURCE="${SKILLGUARD_CONFIG:-$_DEFAULT_CFG}"
if [[ "$CONFIG_TO_SOURCE" != /* ]]; then
  CONFIG_TO_SOURCE="$HERE/$CONFIG_TO_SOURCE"
fi
# Preserve externally injected path (e.g. batch runners) so config defaults
# do not overwrite per-instance attack paths after sourcing.
PRESET_TASKSKILL_INJECTED_PATH="${TASKSKILL_INJECTED_PATH:-}"
# Mixed runner may export OUTPUT_BASE to a per-run subdir; sourced config must not override it.
PRESET_OUTPUT_BASE="${OUTPUT_BASE:-}"

if [[ ! -f "$CONFIG_TO_SOURCE" ]]; then
  echo "❌ 找不到 $CONFIG_TO_SOURCE"
  echo "   先运行: cp run/config.example.sh run/config.sh && vim run/config.sh"
  exit 1
fi
# shellcheck source=/dev/null
source "$CONFIG_TO_SOURCE"
if [[ -n "${PRESET_TASKSKILL_INJECTED_PATH}" ]]; then
  TASKSKILL_INJECTED_PATH="$PRESET_TASKSKILL_INJECTED_PATH"
fi
if [[ -n "${PRESET_OUTPUT_BASE}" ]]; then
  OUTPUT_BASE="$PRESET_OUTPUT_BASE"
fi

if [[ -z "${TASKSKILL_INJECTED_PATH:-}" ]]; then
  echo "❌ 请在 run/config.sh 里设置 TASKSKILL_INJECTED_PATH（对应 injected-skills/ 下目录）"
  echo "   示例: TASKSKILL_INJECTED_PATH=\"system-execution/unapproved_command_execution/stock-research\""
  exit 1
fi

if [[ $# -ge 1 ]]; then
  TASK="$1"
  shift
else
  TASK="task_02_stock"
fi
RUN_TAG="${RUN_TAG_OVERRIDE:-$(date +%Y%m%d-%H%M%S)-$$}"

if [[ "$TASK" == "all" ]]; then
  SUITE_ARG=(
    --suite all
    --tasks-dir tasks/tasks-skill
    --only-tasks-in-injected-summary
    --injected-skill-path "$TASKSKILL_INJECTED_PATH"
  )
else
  SUITE_ARG=(
    --suite "$TASK"
    --tasks-dir tasks/tasks-skill
    --injected-skill-path "$TASKSKILL_INJECTED_PATH"
  )
fi

case "$CC_ALIAS" in
  haiku)  ALIAS_VAR="ANTHROPIC_DEFAULT_HAIKU_MODEL"  ;;
  sonnet) ALIAS_VAR="ANTHROPIC_DEFAULT_SONNET_MODEL" ;;
  opus)   ALIAS_VAR="ANTHROPIC_DEFAULT_OPUS_MODEL"   ;;
  *)
    echo "❌ CC_ALIAS 必须是 haiku / sonnet / opus 之一，当前: $CC_ALIAS"
    exit 1
    ;;
esac

OUT_DIR="${OUTPUT_BASE}/cc_taskskill_${CC_REAL_MODEL//[\/.]/-}_${RUN_TAG}"

# --judge：与 PinchBench 其它 run_*.sh 一样用 config 里的 JUDGE；可选 TASKSKILL_JUDGE_MODEL 非空时仅本脚本覆盖。
JUDGE_MODEL="${TASKSKILL_JUDGE_MODEL:-${JUDGE:-}}"
JUDGE_BASE="${JUDGE_BASE_URL:-}"
JUDGE_TOKEN="${JUDGE_AUTH_TOKEN:-}"
JUDGE_HOST_CFG="${JUDGE_HOST:-}"
NO_PROXY_HOSTS="${CC_HOST}"
if [[ -n "$JUDGE_HOST_CFG" && "$JUDGE_HOST_CFG" != "$CC_HOST" ]]; then
  NO_PROXY_HOSTS="${NO_PROXY_HOSTS},${JUDGE_HOST_CFG}"
fi

cd "$SKILL_DIR"
echo "🚀 Claude Code (自部署) + task-skill 注入实验"
echo "   agent:    ${CC_REAL_MODEL} (benchmark --model ${CC_ALIAS})"
echo "   suite:    ${TASK}"
echo "   inject:   ${TASKSKILL_INJECTED_PATH}"
if [[ -z "$JUDGE_MODEL" ]]; then
  echo "   judge:    (未设置 JUDGE，跳过 ASR；与 agent 无关，需单独配置)"
else
  src="JUDGE"
  [[ -n "${TASKSKILL_JUDGE_MODEL:-}" ]] && src="TASKSKILL_JUDGE_MODEL"
  echo "   judge:    ${JUDGE_MODEL} (claude -p，来自 config ${src} → --judge)"
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
