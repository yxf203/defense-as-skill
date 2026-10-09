#!/usr/bin/env bash
# Run every instance in split/all_instances.jsonl with guard enabled:
# - is_benign=false -> task-skill + continuation-profile malicious
# - is_benign=true  -> task-benign + continuation-profile benign
#
# Produces:
#   <SUMMARY_DIR>/asr_subset_results.jsonl
#   <SUMMARY_DIR>/aggregate.jsonl
#
# Optional env:
#   JOBS=1 (parallel workers)
#   MIXED_SUMMARY_DIR=/custom/path
#   RESULT_TAG=my-test-name
#   OUTPUT_BASE_ISOLATE_RUN=1 (default): mkdir OUTPUT_BASE/run_<stamp>[_<RESULT_TAG>] and export
#     OUTPUT_BASE to it so PinchBench JSON for this run does not mix with older dirs.
#     Set to 0 to keep writing directly under config OUTPUT_BASE (legacy flat layout).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

INSTANCES_JSONL="${INSTANCES_JSONL:-$ROOT/split/all_instances.jsonl}"
if [[ "$INSTANCES_JSONL" != /* ]]; then
  INSTANCES_JSONL="$ROOT/$INSTANCES_JSONL"
fi
if [[ ! -f "$INSTANCES_JSONL" ]]; then
  echo "run_all_instances_mixed: missing $INSTANCES_JSONL" >&2
  exit 1
fi

OUTPUT_BASE_FALLBACK="${OUTPUT_BASE_FALLBACK:-pinchbench-out-docker}"
if [[ -f ./run/config.sh ]]; then
  # shellcheck source=/dev/null
  source ./run/config.sh
fi
OUTPUT_BASE_ROOT="${OUTPUT_BASE:-$OUTPUT_BASE_FALLBACK}"
OUTPUT_BASE_DIR="$OUTPUT_BASE_ROOT"

# One PinchBench tree per mixed eval (avoids _append_asr_result picking older JSON).
OUTPUT_BASE_ISOLATE_RUN="${OUTPUT_BASE_ISOLATE_RUN:-1}"
if [[ "$OUTPUT_BASE_ISOLATE_RUN" != "0" ]]; then
  _iso_stamp="$(date +%Y%m%d-%H%M%S)_$$"
  if [[ -n "${RESULT_TAG:-}" ]]; then
    _iso_tag="$(
      RESULT_TAG="${RESULT_TAG}" python3 -c "import os,re; t=os.environ.get('RESULT_TAG','').strip(); t=re.sub(r'[^A-Za-z0-9._-]+','-',t).strip('-'); print(t or 'tag')"
    )"
    OUTPUT_BASE_DIR="${OUTPUT_BASE_ROOT%/}/run_${_iso_stamp}_${_iso_tag}"
  else
    OUTPUT_BASE_DIR="${OUTPUT_BASE_ROOT%/}/run_${_iso_stamp}"
  fi
  mkdir -p "$OUTPUT_BASE_DIR"
  export OUTPUT_BASE="$OUTPUT_BASE_DIR"
fi

if [[ -n "${MIXED_SUMMARY_DIR:-}" ]]; then
  SUMMARY_DIR="$MIXED_SUMMARY_DIR"
elif [[ -n "${OUTPUT_BASE:-}" ]]; then
  SUMMARY_DIR="${OUTPUT_BASE_DIR%/}/subset-summaries/all_instances_mixed"
else
  SUMMARY_DIR="./subsets/all_instances_mixed"
fi
TMP_DIR="${SUMMARY_DIR}/.tmp_results"
mkdir -p "$TMP_DIR"

SUMMARY_JSONL="${SUMMARY_DIR}/asr_subset_results.jsonl"
AGG_JSONL="${SUMMARY_DIR}/aggregate.jsonl"
RUN_LOG="${SUMMARY_DIR}/run.log"
rm -f "$SUMMARY_JSONL" "$AGG_JSONL" "$RUN_LOG"
export PINCHBENCH_LLAMA_GUARD_LOG="$RUN_LOG"
python3 - <<'PY' "$TMP_DIR"
from pathlib import Path
import sys
p = Path(sys.argv[1])
for fp in p.glob("*.jsonl"):
    fp.unlink()
PY

echo "[mixed] root: $ROOT" | tee -a "$RUN_LOG"
echo "[mixed] instances: $INSTANCES_JSONL" | tee -a "$RUN_LOG"
echo "[mixed] output_base root (config): $OUTPUT_BASE_ROOT" | tee -a "$RUN_LOG"
echo "[mixed] output_base run dir: $OUTPUT_BASE_DIR" | tee -a "$RUN_LOG"
echo "[mixed] summary dir: $SUMMARY_DIR" | tee -a "$RUN_LOG"
echo "[mixed] JOBS: ${JOBS:-1}" | tee -a "$RUN_LOG"
echo "[mixed] PINCHBENCH_LLAMA_GUARD_DEBUG: ${PINCHBENCH_LLAMA_GUARD_DEBUG:-0}" | tee -a "$RUN_LOG"
echo | tee -a "$RUN_LOG"

INSTANCE_LINES="$(python3 - <<'PY' "$INSTANCES_JSONL"
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
rows = []
for raw in path.read_text(encoding="utf-8").splitlines():
    raw = raw.strip()
    if not raw:
        continue
    d = json.loads(raw)
    rows.append(
        (
            d.get("instance_id") or "",
            d.get("task_id") or "",
            str(bool(d.get("is_benign"))).lower(),
            d.get("skill_path") or "",
        )
    )
rows.sort(key=lambda x: x[0])
for rid, task, is_benign, skill_path in rows:
    print("\t".join([rid, task, is_benign, skill_path]))
PY
)"

run_one_instance() {
  local idx="$1"
  local instance_id="$2"
  local task_id="$3"
  local is_benign="$4"
  local skill_path="$5"
  shift 5

  echo "[$idx] $instance_id task=$task_id benign=$is_benign" | tee -a "$RUN_LOG"
  local since_epoch
  local task_rc=0
  local run_tag
  run_tag="$(date +%Y%m%d-%H%M%S)-${instance_id}-$$"
  since_epoch="$(python3 -c 'import time; print(time.time())')"
  if [[ "$is_benign" == "true" ]]; then
    set +e
    RUN_TAG_OVERRIDE="$run_tag" \
      ./run/run_claude_code_task_benign.sh "$task_id" --continuation-profile benign "$@" 2>&1 | tee -a "$RUN_LOG"
    task_rc=${PIPESTATUS[0]}
    set -e
    if [[ "$task_rc" -ne 0 ]]; then
      echo "[warn] benign run failed: instance=$instance_id task=$task_id rc=$task_rc" | tee -a "$RUN_LOG"
    fi
    set +e
    python3 ./subsets/_append_asr_result.py \
      --task-id "$task_id" \
      --label benign \
      --output-base "$OUTPUT_BASE_DIR" \
      --instance-id "$instance_id" \
      --run-tag "$run_tag" \
      --since-epoch "$since_epoch" \
      --out-jsonl "${TMP_DIR}/${instance_id}.jsonl" \
      2>&1 | tee -a "$RUN_LOG"
    append_rc=${PIPESTATUS[0]}
    set -e
    if [[ "$append_rc" -ne 0 ]]; then
      echo "[error] _append_asr_result failed rc=$append_rc instance=$instance_id task=$task_id label=benign" | tee -a "$RUN_LOG"
    fi
  else
    # all_instances skill_path looks like:
    # injected-skills/<attack-path>/SKILL.md  -> convert to <attack-path>
    local rel_attack_path
    # Note: "$skill_path" must appear immediately after `python3 -`, before `<<`, or bash misparses.
    rel_attack_path="$(python3 - "$skill_path" <<'PY'
import sys
p = (sys.argv[1] or "").strip()
prefix = "injected-skills/"
suffix = "/SKILL.md"
if p.startswith(prefix):
    p = p[len(prefix):]
if p.endswith(suffix):
    p = p[:-len(suffix)]
print(p)
PY
)"
    if [[ -z "$rel_attack_path" ]]; then
      echo "skip malformed malicious row: $instance_id (skill_path empty)" | tee -a "$RUN_LOG"
      return 0
    fi
    set +e
    RUN_TAG_OVERRIDE="$run_tag" \
      TASKSKILL_INJECTED_PATH="$rel_attack_path" \
      ./run/run_claude_code_task_skill.sh "$task_id" --continuation-profile malicious "$@" 2>&1 | tee -a "$RUN_LOG"
    task_rc=${PIPESTATUS[0]}
    set -e
    if [[ "$task_rc" -ne 0 ]]; then
      echo "[warn] malicious run failed: instance=$instance_id task=$task_id rc=$task_rc path=$rel_attack_path" | tee -a "$RUN_LOG"
    fi
    set +e
    python3 ./subsets/_append_asr_result.py \
      --task-id "$task_id" \
      --injected-skill-path "$rel_attack_path" \
      --label malicious \
      --output-base "$OUTPUT_BASE_DIR" \
      --instance-id "$instance_id" \
      --run-tag "$run_tag" \
      --since-epoch "$since_epoch" \
      --out-jsonl "${TMP_DIR}/${instance_id}.jsonl" \
      2>&1 | tee -a "$RUN_LOG"
    append_rc=${PIPESTATUS[0]}
    set -e
    if [[ "$append_rc" -ne 0 ]]; then
      echo "[error] _append_asr_result failed rc=$append_rc instance=$instance_id task=$task_id label=malicious" | tee -a "$RUN_LOG"
    fi
  fi
}

JOBS="${JOBS:-1}"
if ! [[ "$JOBS" =~ ^[0-9]+$ ]] || [[ "$JOBS" -lt 1 ]]; then
  JOBS=1
fi

count=0
while IFS=$'\t' read -r instance_id task_id is_benign skill_path; do
  [[ -z "${instance_id:-}" ]] && continue
  count=$((count + 1))
  while [[ "$(jobs -pr | wc -l | tr -d ' ')" -ge "$JOBS" ]]; do
    wait -n || true
  done
  run_one_instance "$count" "$instance_id" "$task_id" "$is_benign" "$skill_path" "$@" || true &
done <<< "$INSTANCE_LINES"
while [[ "$(jobs -pr | wc -l | tr -d ' ')" -gt 0 ]]; do
  wait -n || true
done

python3 - <<'PY' "$TMP_DIR" "$SUMMARY_JSONL"
from pathlib import Path
import sys
tmp = Path(sys.argv[1])
out = Path(sys.argv[2])
parts = sorted(tmp.glob("*.jsonl"))
with out.open("w", encoding="utf-8") as w:
    for p in parts:
        txt = p.read_text(encoding="utf-8").strip()
        if txt:
            w.write(txt)
            w.write("\n")
PY

python3 - <<'PY' "$SUMMARY_JSONL" "$AGG_JSONL"
import json, sys
from pathlib import Path

summary = Path(sys.argv[1])
agg_jsonl = Path(sys.argv[2])
rows = []
for raw in summary.read_text(encoding="utf-8").splitlines():
    raw = raw.strip()
    if raw:
        rows.append(json.loads(raw))

def mean(xs):
    return round(sum(xs) / len(xs), 4) if xs else None

def b(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y")
    return None

def f(v, d=0.0):
    try:
        return float(v)
    except Exception:
        return d

mal = []
ben = []
invalid = []
for r in rows:
    label = (r.get("label") or "").strip().lower()
    if label not in ("malicious", "benign"):
        label = "malicious" if r.get("injected_skill_path") else "benign"
    if label == "malicious":
        mal.append(r)
    else:
        ben.append(r)
    if r.get("run_valid") is False:
        invalid.append(r)

mal_asr = mean([1.0 if b(r.get("attack_success")) else 0.0 for r in mal if r.get("attack_success") is not None])
payload = {
    "n_cells": len(rows),
    "malicious_cells": len(mal),
    "benign_cells": len(ben),
    "malicious_asr": mal_asr,
    "benign_utility": mean([f(r.get("utility_score"), 0.0) for r in ben if r.get("utility_score") is not None]),
    "malicious_utility": mean([f(r.get("utility_score"), 0.0) for r in mal if r.get("utility_score") is not None]),
    "mean_confirmation": mean([f(r.get("confirmation_count"), 0.0) for r in rows]),
    "mean_total_tokens": mean([f((r.get("eval") or {}).get("total_tokens"), 0.0) for r in rows]),
    "mean_cost_usd": mean([f((r.get("eval") or {}).get("cost_usd"), 0.0) for r in rows]),
    "guard_read_rate": mean([1.0 if b(r.get("guard_read")) else 0.0 for r in rows]),
    "guard_triggered_rate": mean([1.0 if b(r.get("guard_triggered")) else 0.0 for r in rows]),
    "guard_effective_rate": mean([
        1.0 if (b(r.get("guard_triggered")) and b(r.get("attack_success")) is False) else 0.0
        for r in mal
    ]) if mal else None,
    "invalid_rate": mean([1.0 if r.get("run_valid") is False else 0.0 for r in rows]),
    "n_invalid": len(invalid),
    "source_jsonl": str(summary),
}
agg_jsonl.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
PY

# Copy final artifacts to a host-visible results directory under skillGuard/results/<tag>.
RESULTS_BASE="$ROOT/results"
TAG_RAW="${RESULT_TAG:-all_instances_mixed-$(date +%Y%m%d-%H%M%S)}"
TAG_SAFE="$(python3 - <<'PY' "$TAG_RAW"
import re, sys
s = sys.argv[1]
s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")
print(s or "all_instances_mixed")
PY
)"
FINAL_DIR="${RESULTS_BASE}/${TAG_SAFE}"
mkdir -p "$FINAL_DIR"
cp -f "$SUMMARY_JSONL" "$FINAL_DIR/asr_subset_results.jsonl"
cp -f "$AGG_JSONL" "$FINAL_DIR/aggregate.jsonl"
cp -f "$RUN_LOG" "$FINAL_DIR/run.log"

echo
echo "[mixed] summary jsonl: $SUMMARY_JSONL"
echo "[mixed] aggregate jl:  $AGG_JSONL"
echo "[mixed] final export:  $FINAL_DIR"
