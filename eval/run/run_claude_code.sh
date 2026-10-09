#!/usr/bin/env bash
# Claude Code 后端 + 官方 Anthropic 模型（sonnet / opus / haiku）。
# 走 api.anthropic.com，需要 `claude login` 过，且 http_proxy 走梯子。
#
# 用法：
#   ./run/run_claude_code.sh                       # task_00_sanity
#   ./run/run_claude_code.sh task_ad_banking_ut0   # 单个 agentdojo 任务
#   ./run/run_claude_code.sh all                   # 全部 1046 个 agentdojo 任务
# 第一个参数固定为 task id；之后的参数会原样传给 scripts/benchmark.py，例如：
#   ./run/run_claude_code.sh task_ad_banking_ut0 --verbose
#   ./run/run_claude_code.sh task_ad_banking_ut0 -v

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$HERE/.." && pwd)"
CONFIG="${HERE}/config.sh"

if [[ ! -f "$CONFIG" ]]; then
  echo "❌ 找不到 $CONFIG"
  echo "   先运行: cp run/config.example.sh run/config.sh && vim run/config.sh"
  exit 1
fi
# shellcheck source=/dev/null
source "$CONFIG"

if [[ $# -ge 1 ]]; then
  TASK="$1"
  shift
else
  TASK="task_00_sanity"
fi
RUN_TAG="$(date +%Y%m%d-%H%M%S)"

if [[ "$TASK" == "all" ]]; then
  SUITE_ARG=(--suite all --tasks-dir tasks/tasks-agentdojo)
elif [[ "$TASK" == task_ad_* ]]; then
  SUITE_ARG=(--suite "$TASK" --tasks-dir tasks/tasks-agentdojo)
else
  SUITE_ARG=(--suite "$TASK")
fi

OUT_DIR="${OUTPUT_BASE}/cc_official_${CLAUDE_OFFICIAL_MODEL}_${RUN_TAG}"

cd "$SKILL_DIR"
echo "🚀 Claude Code (官方) + ${CLAUDE_OFFICIAL_MODEL} → ${TASK}"
echo "   output: ${OUT_DIR}"
echo

exec python3 scripts/benchmark.py \
  --backend claude-code \
  --model "$CLAUDE_OFFICIAL_MODEL" \
  "${SUITE_ARG[@]}" \
  --judge "$JUDGE" \
  --no-upload \
  --output-dir "$OUT_DIR" \
  "$@"
