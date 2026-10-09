#!/usr/bin/env bash
# Claude Code + task-skill 注入实验 — **自包含版**：不读取 run/config.sh。
#
# 环境变量须已由父进程设置（推荐：subsets/run_smoke_standalone.sh 顶部统一 export），
# 或你在运行本脚本前自行 export。缺少变量时会报错退出。
#
# 用法与原 run_claude_code_task_skill.sh 相同：
#   ./run/run_claude_code_task_skill_standalone.sh task_01_calendar
#   ./run/run_claude_code_task_skill_standalone.sh task_02_stock --injected-skill-path foo/bar --verbose

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$HERE/.." && pwd)"

_require() {
  local n="$1"
  if [[ -z "${!n:-}" ]]; then
    echo "❌ 缺少环境变量: $n"
    echo "   请在 subsets/run_smoke_standalone.sh 顶部填写并 export，或在本终端先 export 再运行。"
    exit 1
  fi
}

_require CC_BASE_URL
_require CC_AUTH_TOKEN
_require CC_ALIAS
_require CC_REAL_MODEL
_require CC_HOST
_require OUTPUT_BASE
_require TASKSKILL_INJECTED_PATH

if [[ $# -ge 1 ]]; then
  TASK="$1"
  shift
else
  TASK="task_02_stock"
fi
RUN_TAG="$(date +%Y%m%d-%H%M%S)-$$"

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

JUDGE_MODEL="${TASKSKILL_JUDGE_MODEL:-${JUDGE:-}}"

cd "$SKILL_DIR"
echo "🚀 Claude Code (自部署) + task-skill 注入实验 [standalone，无 config.sh]"
echo "   agent:    ${CC_REAL_MODEL} (benchmark --model ${CC_ALIAS})"
echo "   suite:    ${TASK}"
echo "   inject:   ${TASKSKILL_INJECTED_PATH}"
if [[ -z "$JUDGE_MODEL" ]]; then
  echo "   judge:    (未设置 JUDGE，跳过 ASR)"
else
  src="JUDGE"
  [[ -n "${TASKSKILL_JUDGE_MODEL:-}" ]] && src="TASKSKILL_JUDGE_MODEL"
  echo "   judge:    ${JUDGE_MODEL} (claude -p，来自环境 ${src} → --judge)"
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
  "$ALIAS_VAR=$CC_REAL_MODEL" \
  NO_PROXY="${CC_HOST},127.0.0.1,localhost" \
  no_proxy="${CC_HOST},127.0.0.1,localhost" \
  "${BENCH[@]}" \
  "$@"
