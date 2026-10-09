#!/usr/bin/env bash
# Run the skill-evolution loop on the smoke subset.
#
# Usage:
#   ./run_smoke_loop.sh                # defaults from config/default.yaml
#   ./run_smoke_loop.sh --max-rounds 2
#   ./run_smoke_loop.sh --dry-run      # no benchmark/refiner calls
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Source skillGuard config so all upstream env (CC_*, JUDGE, ANTHROPIC_*) is set exactly
# like the original subset scripts expect. This does NOT modify the framework.
SKILL_GUARD_ROOT="$(python3 - <<'PY'
import yaml, pathlib
cfg = yaml.safe_load(open(pathlib.Path(__file__).resolve().parent / 'config' / 'default.yaml'))
print(cfg['paths']['skill_guard_root'])
PY
)"

if [[ -f "$SKILL_GUARD_ROOT/run/config.sh" ]]; then
  # shellcheck source=/dev/null
  source "$SKILL_GUARD_ROOT/run/config.sh"

  # Re-export the env that run_claude_code_task_skill.sh would otherwise set only for its own
  # `exec env ...` call. We must do this because the Python evaluator launches the script as a
  # subprocess and inherits *our* environment.
  if [[ -n "${CC_ALIAS:-}" ]]; then
    case "$CC_ALIAS" in
      haiku)  ALIAS_VAR="ANTHROPIC_DEFAULT_HAIKU_MODEL"  ;;
      sonnet) ALIAS_VAR="ANTHROPIC_DEFAULT_SONNET_MODEL" ;;
      opus)   ALIAS_VAR="ANTHROPIC_DEFAULT_OPUS_MODEL"   ;;
      *) ALIAS_VAR="" ;;
    esac
    if [[ -n "$ALIAS_VAR" && -n "${CC_REAL_MODEL:-}" ]]; then
      export "$ALIAS_VAR=$CC_REAL_MODEL"
    fi
    [[ -n "${CC_BASE_URL:-}" ]]   && export ANTHROPIC_BASE_URL="$CC_BASE_URL"
    [[ -n "${CC_AUTH_TOKEN:-}" ]] && export ANTHROPIC_AUTH_TOKEN="<your-token>"
    if [[ -n "${CC_HOST:-}" ]]; then
      export NO_PROXY="${CC_HOST},127.0.0.1,localhost"
      export no_proxy="${CC_HOST},127.0.0.1,localhost"
    fi
  fi
fi

export PYTHONPATH="$HERE:${PYTHONPATH:-}"
exec python3 -m evolution.cli --subset smoke "$@"
