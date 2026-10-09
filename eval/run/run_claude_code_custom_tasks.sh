#!/usr/bin/env bash
# Claude Code 后端 + 自部署模型（通过 ANTHROPIC_BASE_URL 等 env vars 重定向），跑 skillGuard/tasks/tasks-benign 任务集。
# 复用 run/config.sh 里的 CC_* / JUDGE / OUTPUT_BASE 配置。
#
# 用法：
#   ./run/run_claude_code_custom_tasks.sh                    # 跑 tasks 下全部（--suite all）
#   ./run/run_claude_code_custom_tasks.sh automated-only     # 仅跑 automated-only
#   ./run/run_claude_code_custom_tasks.sh task_00_sanity     # 跑单个 task id（或逗号分隔多个）
# 其余参数会透传给 scripts/benchmark.py（如 --verbose / --runs 3）
#
# 注意：
# - 与 ./run/run_claude_code_custom.sh 不同：本脚本固定使用 --tasks-dir tasks/tasks-benign（不会切到 tasks-agentdojo）
#

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
  SUITE="$1"
  shift
else
  SUITE="all"
fi

RUN_TAG="$(date +%Y%m%d-%H%M%S)-$$"

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

OUT_DIR="${OUTPUT_BASE}/cc_custom_${CC_REAL_MODEL//[\/.]/-}_tasks_${RUN_TAG}"

cd "$SKILL_DIR"
echo "🚀 Claude Code (自部署) + ${CC_REAL_MODEL} (via --model ${CC_ALIAS}) → tasks (${SUITE})"
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
    --suite "$SUITE" \
    --tasks-dir tasks/tasks-benign \
    --judge "$JUDGE" \
    --no-upload \
    --output-dir "$OUT_DIR" \
    "$@"

