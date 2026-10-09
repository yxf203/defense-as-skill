#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# Use OUTPUT_BASE from config when available (docker uses /pinchbench-out).
OUTPUT_BASE_FALLBACK="${OUTPUT_BASE_FALLBACK:-pinchbench-out-docker}"
if [[ -f ./run/config.sh ]]; then
  # shellcheck source=/dev/null
  source ./run/config.sh
fi
OUTPUT_BASE_DIR="${OUTPUT_BASE:-$OUTPUT_BASE_FALLBACK}"

JOBS="${JOBS:-1}"

if [[ -n "${OUTPUT_BASE:-}" ]]; then
  SUMMARY_DIR="${OUTPUT_BASE_DIR%/}/subset-summaries/coverage"
else
  SUMMARY_DIR="./subsets/coverage"
fi
TMP_DIR="${SUMMARY_DIR}/.tmp_results"
if ! mkdir -p "$TMP_DIR" 2>/dev/null; then
  if [[ -n "${OUTPUT_BASE:-}" ]]; then
    SUMMARY_DIR="./subsets/coverage"
    TMP_DIR="${SUMMARY_DIR}/.tmp_results"
    mkdir -p "$TMP_DIR" || {
      echo "run_coverage: cannot create $TMP_DIR" >&2
      exit 1
    }
  else
    SUMMARY_DIR="${OUTPUT_BASE_DIR%/}/subset-summaries/coverage"
    TMP_DIR="${SUMMARY_DIR}/.tmp_results"
    mkdir -p "$TMP_DIR" || {
      echo "run_coverage: cannot create $TMP_DIR" >&2
      exit 1
    }
  fi
fi
SUMMARY_JSONL="${SUMMARY_DIR}/asr_subset_results.jsonl"

# Drop stale per-case *.jsonl from earlier runs (see run_smoke.sh).
find "$TMP_DIR" -maxdepth 1 -type f -name '*.jsonl' -delete 2>/dev/null || true

run_one() {
  local task="$1"
  local pathrel="$2"
  shift 2

  echo "=== $task @ $pathrel ==="
  local since_epoch
  since_epoch="$(python3 -c 'import time; print(time.time())')"

  # task-skill injected benchmark: force --tasks-dir tasks/tasks-skill (inside run_claude_code_task_skill.sh)
  # and override injected path per case.
  ./run/run_claude_code_task_skill.sh "$task" --injected-skill-path "$pathrel" --verbose "$@"

  # Write per-case summary to a temp file (avoid concurrent append races).
  local safe_name
  safe_name="$(echo "${task}__${pathrel}" | tr '/ ' '__' | tr -cd 'A-Za-z0-9_.-')"
  local tmp_jsonl="${TMP_DIR}/${safe_name}.$$.$RANDOM.jsonl"

  python3 ./subsets/_append_asr_result.py \
    --task-id "$task" \
    --injected-skill-path "$pathrel" \
    --output-base "$OUTPUT_BASE_DIR" \
    --since-epoch "$since_epoch" \
    --out-jsonl "$tmp_jsonl"
}

for item in \
  'task_01_calendar|authorization-abuse/approval_carryover/ics-event' \
  'task_01_calendar|state-integrity/audit_suppression/ics-event' \
  'task_03_blog|memory-poisoning/authority_escalation/blog-writer' \
  'task_14_humanizer|external-actuation/calendar_ticket_crm_tampering/text-humanizer' \
  'task_07_email|authorization-abuse/consent_suppression/email-drafter' \
  'task_01_calendar|data-exfiltration/context_leak/ics-event' \
  'task_17_email_search|network-lateral-movement/cross_service_pivot/email-search' \
  'task_17_email_search|memory-poisoning/data_exfiltration/email-search' \
  'task_01_calendar|data-exfiltration/env_secret_access/ics-event' \
  'task_01_calendar|availability/excessive_compute_or_resource_hijack/ics-event' \
  'task_02_stock|authorization-abuse/fake_preapproval/stock-research' \
  'task_02_stock|data-exfiltration/file_to_sink_exfil/stock-research' \
  'task_03_blog|system-execution/guard_or_permission_downgrade/blog-writer' \
  'task_01_calendar|semantic-selection-integrity/human_model_view_divergence/ics-event' \
  'task_18_market_research|availability/infinite_retry_or_blocking/market-research' \
  'task_18_market_research|network-lateral-movement/internal_enumeration/market-research' \
  'task_01_calendar|availability/lock_or_queue_abuse/ics-event' \
  'task_07_email|semantic-selection-integrity/manifest_body_mismatch/email-drafter' \
  'task_02_stock|state-integrity/memory_deletion_overwrite/stock-research' \
  'task_07_email|state-integrity/memory_insertion/email-drafter' \
  'task_01_calendar|semantic-selection-integrity/metadata_baiting/ics-event' \
  'task_02_stock|system-execution/persistence_hook_abuse/stock-research' \
  'task_14_humanizer|external-actuation/purchase_or_billing_simulation/text-humanizer' \
  'task_14_humanizer|authorization-abuse/recipient_endpoint_laundering/text-humanizer' \
  'task_02_stock|network-lateral-movement/repeated_auth_attempts/stock-research' \
  'task_06_events|external-actuation/repo_or_ci_actuation/conference-research' \
  'task_02_stock|state-integrity/retrieval_workspace_poisoning/stock-research' \
  'task_02_stock|semantic-selection-integrity/schema_deception/stock-research' \
  'task_12_skill_search|intent-misalignment/scope_creep_automation/config-updater' \
  'task_05_summary|system-execution/second_stage_fetch/document-summarizer' \
  'task_14_humanizer|intent-misalignment/semantic_mismatch_tool_chain/text-humanizer' \
  'task_07_email|data-exfiltration/staged_or_covert_exfil/email-drafter' \
  'task_03_blog|intent-misalignment/task_drift_wrong_artifact_action/blog-writer' \
  'task_01_calendar|availability/token_or_context_bloat/ics-event' \
  'task_18_market_research|system-execution/unapproved_command_execution/market-research' \
  'task_02_stock|external-actuation/unauthorized_messaging/stock-research' \
  'task_07_email|network-lateral-movement/unauthorized_outbound_access/email-drafter' \
  'task_02_stock|memory-poisoning/workflow_hijacking/stock-research' \
; do
  TASK="${item%%|*}"
  PATHREL="${item#*|}"
  while [[ "$(jobs -pr | wc -l)" -ge "$JOBS" ]]; do
    wait -n
  done
  run_one "$TASK" "$PATHREL" "$@" &
done

wait

rm -f "$SUMMARY_JSONL"
find "$TMP_DIR" -maxdepth 1 -type f -name '*.jsonl' -print0 | sort -z | xargs -0 cat >> "$SUMMARY_JSONL"
echo "[subset-summary] merged -> $SUMMARY_JSONL"
