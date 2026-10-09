#!/usr/bin/env bash
# OpenClaw 后端 + 自定义中转。auto-sync allowlist / auth-profile，
# 在必要时自动重启 openclaw-gateway，无需手动改任何配置。
#
# 用法：
#   ./run/run_openclaw.sh                       # task_00_sanity (单任务冒烟)
#   ./run/run_openclaw.sh task_ad_banking_ut0   # 单个 agentdojo 任务
#   ./run/run_openclaw.sh all                   # 全部 1046 个 agentdojo 任务
# 第一个参数为 task id，其余参数传给 benchmark.py（如 --verbose）
#
# 配置在 run/config.sh（从 config.example.sh 复制并改）

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

# 决定 --suite 和 --tasks-dir
if [[ "$TASK" == "all" ]]; then
  SUITE_ARG=(--suite all --tasks-dir tasks/tasks-agentdojo)
elif [[ "$TASK" == task_ad_* ]]; then
  SUITE_ARG=(--suite "$TASK" --tasks-dir tasks/tasks-agentdojo)
else
  SUITE_ARG=(--suite "$TASK")
fi

# 可选 flag
EXTRA=()
if [[ "${OPENCLAW_NO_STREAM:-false}" == "true" ]]; then
  EXTRA+=(--no-stream)
fi
if [[ -n "${OPENCLAW_TIMEOUT_MULT:-}" && "$OPENCLAW_TIMEOUT_MULT" != "1" ]]; then
  EXTRA+=(--timeout-multiplier "$OPENCLAW_TIMEOUT_MULT")
fi

OUT_DIR="${OUTPUT_BASE}/openclaw_${OPENCLAW_MODEL//[\/.]/-}_${RUN_TAG}"

cd "$SKILL_DIR"
echo "🚀 OpenClaw + ${OPENCLAW_MODEL} → ${TASK}"
echo "   base-url: ${OPENCLAW_BASE_URL}"
echo "   output:   ${OUT_DIR}"
echo

exec python3 scripts/benchmark.py \
  --backend openclaw \
  --model "$OPENCLAW_MODEL" \
  --base-url "$OPENCLAW_BASE_URL" \
  --api-key "$OPENCLAW_API_KEY" \
  "${EXTRA[@]}" \
  "${SUITE_ARG[@]}" \
  --judge "$JUDGE" \
  --no-upload \
  --output-dir "$OUT_DIR" \
  "$@"
