#!/usr/bin/env bash
# Combined subset: smoke malicious cells + benign tasks, merged into a single
# ``asr_subset_results.jsonl`` that the skill-evolution framework already knows
# how to read. Use this when you want to co-optimize for ASR *and* benign
# behaviour in the same evolution loop.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

OUTPUT_BASE_FALLBACK="${OUTPUT_BASE_FALLBACK:-pinchbench-out-docker}"
if [[ -f ./run/config.sh ]]; then
  # shellcheck source=/dev/null
  source ./run/config.sh
fi
OUTPUT_BASE_DIR="${OUTPUT_BASE:-$OUTPUT_BASE_FALLBACK}"

if [[ -n "${OUTPUT_BASE:-}" ]]; then
  SUMMARY_DIR="${OUTPUT_BASE_DIR%/}/subset-summaries/combined"
else
  SUMMARY_DIR="./subsets/combined"
fi
if ! mkdir -p "$SUMMARY_DIR" 2>/dev/null; then
  if [[ -n "${OUTPUT_BASE:-}" ]]; then
    SUMMARY_DIR="./subsets/combined"
  else
    SUMMARY_DIR="${OUTPUT_BASE_DIR%/}/subset-summaries/combined"
  fi
  mkdir -p "$SUMMARY_DIR" || {
    echo "run_combined: cannot create $SUMMARY_DIR" >&2
    exit 1
  }
fi
SUMMARY_JSONL="${SUMMARY_DIR}/asr_subset_results.jsonl"

rm -f "$SUMMARY_JSONL"

echo "=== running malicious smoke leg ==="
bash ./subsets/run_smoke.sh "$@"
SMOKE_JSONL=""
if [[ -n "${OUTPUT_BASE:-}" && -f "${OUTPUT_BASE_DIR%/}/subset-summaries/smoke/asr_subset_results.jsonl" ]]; then
  SMOKE_JSONL="${OUTPUT_BASE_DIR%/}/subset-summaries/smoke/asr_subset_results.jsonl"
elif [[ -f ./subsets/smoke/asr_subset_results.jsonl ]]; then
  SMOKE_JSONL="./subsets/smoke/asr_subset_results.jsonl"
fi
if [[ -n "$SMOKE_JSONL" ]]; then
  cat "$SMOKE_JSONL" >> "$SUMMARY_JSONL"
fi

echo "=== running benign leg ==="
# Benign runs have no attack; force the benign continuation profile regardless
# of what the caller passed. We append the default after user args so the user
# argument wins when explicitly supplied.
bash ./subsets/run_benign.sh "$@" --continuation-profile benign
BENIGN_JSONL=""
if [[ -n "${OUTPUT_BASE:-}" && -f "${OUTPUT_BASE_DIR%/}/subset-summaries/benign/asr_subset_results.jsonl" ]]; then
  BENIGN_JSONL="${OUTPUT_BASE_DIR%/}/subset-summaries/benign/asr_subset_results.jsonl"
elif [[ -f ./subsets/benign/asr_subset_results.jsonl ]]; then
  BENIGN_JSONL="./subsets/benign/asr_subset_results.jsonl"
fi
if [[ -n "$BENIGN_JSONL" ]]; then
  cat "$BENIGN_JSONL" >> "$SUMMARY_JSONL"
fi

echo "[subset-summary] combined merged -> $SUMMARY_JSONL"
wc -l "$SUMMARY_JSONL"
