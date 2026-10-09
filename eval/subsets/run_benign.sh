#!/usr/bin/env bash
# Benign-task smoke subset: runs a handful of benign tasks to verify that the
# active guard skill does NOT over-trigger on clean requests.
#
# Mirrors `run_smoke.sh` in structure so skill-evolution's SubsetEvaluator can
# reuse the exact same collection pipeline (asr_subset_results.jsonl).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# When run/config.sh does not set OUTPUT_BASE, use this host path (compose default).
# Override without editing: OUTPUT_BASE_FALLBACK=pinchbench-benign-docker-glm5 ./subsets/run_benign.sh
OUTPUT_BASE_FALLBACK="${OUTPUT_BASE_FALLBACK:-pinchbench-out-docker}"
if [[ -f ./run/config.sh ]]; then
  # shellcheck source=/dev/null
  source ./run/config.sh
fi
OUTPUT_BASE_DIR="${OUTPUT_BASE:-$OUTPUT_BASE_FALLBACK}"

JOBS="${JOBS:-1}"

# _append_asr_result.py reads benchmark JSON from --output-base (OUTPUT_BASE_DIR).
# The merged asr_subset_results.jsonl used to always prefer ./subsets/benign, which
# in Docker (bind-mount /work writable) never fell back to the volume. When OUTPUT_BASE
# is set (e.g. /pinchbench-out), keep the summary next to cc_* runs.
if [[ -n "${OUTPUT_BASE:-}" ]]; then
  SUMMARY_DIR="${OUTPUT_BASE_DIR%/}/subset-summaries/benign"
else
  SUMMARY_DIR="./subsets/benign"
fi
TMP_DIR="${SUMMARY_DIR}/.tmp_results"
if ! mkdir -p "$TMP_DIR" 2>/dev/null; then
  if [[ -n "${OUTPUT_BASE:-}" ]]; then
    SUMMARY_DIR="./subsets/benign"
    TMP_DIR="${SUMMARY_DIR}/.tmp_results"
    mkdir -p "$TMP_DIR" || {
      echo "run_benign: cannot create $TMP_DIR" >&2
      exit 1
    }
  else
    SUMMARY_DIR="${OUTPUT_BASE_DIR%/}/subset-summaries/benign"
    TMP_DIR="${SUMMARY_DIR}/.tmp_results"
    mkdir -p "$TMP_DIR" || {
      echo "run_benign: cannot create $TMP_DIR" >&2
      exit 1
    }
  fi
fi
SUMMARY_JSONL="${SUMMARY_DIR}/asr_subset_results.jsonl"

# Drop stale per-case *.jsonl from earlier runs (see run_smoke.sh).
find "$TMP_DIR" -maxdepth 1 -type f -name '*.jsonl' -delete 2>/dev/null || true

run_one() {
  local task="$1"
  shift

  echo "=== benign $task ==="
  local since_epoch
  since_epoch="$(python3 -c 'import time; print(time.time())')"

  ./run/run_claude_code_task_benign.sh "$task" --verbose "$@"

  local safe_name
  safe_name="$(echo "${task}__benign" | tr '/ ' '__' | tr -cd 'A-Za-z0-9_.-')"
  local tmp_jsonl="${TMP_DIR}/${safe_name}.$$.$RANDOM.jsonl"

  # Note: for benign rows we pass --label benign explicitly (the helper also
  # auto-detects via eval.label / frontmatter.label, but being explicit keeps
  # the subset output stable even if someone reuses an older task file).
  python3 ./subsets/_append_asr_result.py \
    --task-id "$task" \
    --label benign \
    --output-base "$OUTPUT_BASE_DIR" \
    --since-epoch "$since_epoch" \
    --out-jsonl "$tmp_jsonl"
}

# Keep the benign set small and representative. Pair each malicious task_id
# used in run_smoke.sh with its benign counterpart so downstream evolution
# sees both sides of the same agent persona.
for TASK in \
  'task_00_sanity' \
  'task_01_calendar' \
  'task_02_stock' \
  'task_03_blog' \
; do
  while [[ "$(jobs -pr | wc -l)" -ge "$JOBS" ]]; do
    wait -n
  done
  run_one "$TASK" "$@" &
done

wait

rm -f "$SUMMARY_JSONL"
find "$TMP_DIR" -maxdepth 1 -type f -name '*.jsonl' -print0 | sort -z | xargs -0 cat >> "$SUMMARY_JSONL"
echo "[subset-summary] merged -> $SUMMARY_JSONL"
