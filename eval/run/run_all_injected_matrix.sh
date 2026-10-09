#!/usr/bin/env bash
# 遍历 injected-skills 下每个 poison bundle × summary.json 对应 task，逐个调用 benchmark.py；
# 每跑完一次往 JSONL 追加一行（由 scripts/asr_matrix_runner.py 解析结果 JSON，不改 benchmark 源码）。
#
# 用法：
#   ./run/run_all_injected_matrix.sh
#   ./run/run_all_injected_matrix.sh --verbose          # 传给 benchmark
#   ./run/run_all_injected_matrix.sh -- --verbose       # 同上（会先去掉一层多余的 --）
#
# 默认输出目录：仓库内 matrix-output/（可用环境变量覆盖路径）
#   统一根目录：$ASR_MATRIX_ROOT（若设置，则下面三项默认都落在其下；Docker  compose 里设为 /pinchbench-out/matrix → 宿主机 pinchbench-out-docker/matrix/）
#   汇总 JSONL：$ASR_MATRIX_JSONL（默认 $ASR_MATRIX_ROOT 或 <skillGuard>/matrix-output/asr_matrix_results.jsonl）
#   单次 JSON：$ASR_MATRIX_BATCHES（默认 …/batches/）
#   终端镜像：$ASR_MATRIX_LOG（默认 …/asr_matrix_bench.log）
#   不要写 log：ASR_MATRIX_NO_LOG=1 ./run/run_all_injected_matrix.sh
#   断点续跑（跳过 JSONL 已成功格子，batch 序号接续）：ASR_MATRIX_RESUME=1 ./run/run_all_injected_matrix.sh
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$HERE/.." && pwd)"
CONFIG="${HERE}/config.sh"

if [[ ! -f "$CONFIG" ]]; then
  echo "❌ 找不到 $CONFIG"
  exit 1
fi
# shellcheck source=/dev/null
source "$CONFIG"

case "${CC_ALIAS:-haiku}" in
  haiku)  ALIAS_VAR="ANTHROPIC_DEFAULT_HAIKU_MODEL"  ;;
  sonnet) ALIAS_VAR="ANTHROPIC_DEFAULT_SONNET_MODEL" ;;
  opus)   ALIAS_VAR="ANTHROPIC_DEFAULT_OPUS_MODEL"   ;;
  *)
    echo "❌ CC_ALIAS 必须是 haiku / sonnet / opus 之一"
    exit 1
    ;;
esac

cd "$SKILL_DIR"
MATRIX_BASE="${ASR_MATRIX_ROOT:-${SKILL_DIR}/matrix-output}"
mkdir -p "$MATRIX_BASE"

JSONL="${ASR_MATRIX_JSONL:-$MATRIX_BASE/asr_matrix_results.jsonl}"
BATCHES="${ASR_MATRIX_BATCHES:-$MATRIX_BASE/batches}"
LOGF="${ASR_MATRIX_LOG:-$MATRIX_BASE/asr_matrix_bench.log}"
J="${JUDGE:-}"

if [[ "${ASR_MATRIX_NO_LOG:-}" != "1" ]]; then
  mkdir -p "$(dirname "$LOGF")"
  touch "$LOGF"
  exec > >(tee -a "$LOGF")
  exec 2>&1
fi

echo "📂 matrix-out: $MATRIX_BASE"
echo "📄 JSONL:      $JSONL"
echo "📁 batches:   $BATCHES"
if [[ "${ASR_MATRIX_NO_LOG:-}" != "1" ]]; then
  echo "📝 bench log: $LOGF"
fi
echo "🤖 agent:     $CC_ALIAS -> $CC_REAL_MODEL"
echo "⚖️  judge:     ${J:-"(未设 JUDGE，无 ASR)"}"
echo

CMD=(python3 scripts/asr_matrix_runner.py
  --jsonl "$JSONL"
  --batch-output "$BATCHES"
  --tasks-dir tasks/tasks-skill
  --backend claude-code
  --model "$CC_ALIAS"
)
[[ -n "$J" ]] && CMD+=(--judge "$J")
[[ "${ASR_MATRIX_RESUME:-}" == "1" ]] && CMD+=(--resume)
# 避免 ``./script.sh -- --verbose`` 变成 runner 收到 ``-- -- --verbose`` 进而传给 benchmark
BENCH_EXTRA=( "$@" )
if [[ ${#BENCH_EXTRA[@]} -ge 1 && "${BENCH_EXTRA[0]}" == "--" ]]; then
  BENCH_EXTRA=( "${BENCH_EXTRA[@]:1}" )
fi
CMD+=(-- "${BENCH_EXTRA[@]}")

exec env \
  ANTHROPIC_BASE_URL="$CC_BASE_URL" \
  ANTHROPIC_AUTH_TOKEN="$CC_AUTH_TOKEN" \
  "$ALIAS_VAR=$CC_REAL_MODEL" \
  NO_PROXY="${CC_HOST},127.0.0.1,localhost" \
  no_proxy="${CC_HOST},127.0.0.1,localhost" \
  "${CMD[@]}"
