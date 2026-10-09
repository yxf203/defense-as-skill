#!/usr/bin/env bash
# Claude Code 后端 + 自部署模型（通过 ANTHROPIC_BASE_URL 等 env vars 重定向）。
# 把内置 --model haiku/sonnet/opus 别名映射到任意 Anthropic API 兼容端点。
#
# NO_PROXY 让 node fetch 对自部署 host 单独绕过 Clash，绝不动全局 http_proxy。
#
# 用法：
#   ./run/run_claude_code_custom.sh                       # task_00_sanity
#   ./run/run_claude_code_custom.sh task_ad_banking_ut0   # 单个 agentdojo 任务
#   ./run/run_claude_code_custom.sh all                   # 全部 1046 个 agentdojo 任务
# 第一个参数为 task id，其余参数传给 benchmark.py（如 --verbose）

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

# 把 alias 转成对应的 ANTHROPIC_DEFAULT_<TIER>_MODEL 环境变量
case "$CC_ALIAS" in
  haiku)  ALIAS_VAR="ANTHROPIC_DEFAULT_HAIKU_MODEL"  ;;
  sonnet) ALIAS_VAR="ANTHROPIC_DEFAULT_SONNET_MODEL" ;;
  opus)   ALIAS_VAR="ANTHROPIC_DEFAULT_OPUS_MODEL"   ;;
  *)
    echo "❌ CC_ALIAS 必须是 haiku / sonnet / opus 之一，当前: $CC_ALIAS"
    exit 1
    ;;
esac

OUT_DIR="${OUTPUT_BASE}/cc_custom_${CC_REAL_MODEL//[\/.]/-}_${RUN_TAG}"

cd "$SKILL_DIR"
echo "🚀 Claude Code (自部署) + ${CC_REAL_MODEL} (via --model ${CC_ALIAS}) → ${TASK}"
echo "   base-url: ${CC_BASE_URL}"
echo "   bypass:   ${CC_HOST}"
echo "   output:   ${OUT_DIR}"
echo

exec env \
  ANTHROPIC_BASE_URL="$CC_BASE_URL" \
  ANTHROPIC_AUTH_TOKEN="$CC_AUTH_TOKEN" \
  "$ALIAS_VAR=$CC_REAL_MODEL" \
  NO_PROXY="${CC_HOST},127.0.0.1,localhost" \
  no_proxy="${CC_HOST},127.0.0.1,localhost" \
  python3 scripts/benchmark.py \
    --backend claude-code \
    --model "$CC_ALIAS" \
    "${SUITE_ARG[@]}" \
    --judge "$JUDGE" \
    --no-upload \
    --output-dir "$OUT_DIR" \
    "$@"
