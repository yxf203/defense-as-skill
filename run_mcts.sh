#!/usr/bin/env bash
# Launch the MCTS-style skill-evolution loop.
#
# Usage:
#   ./run_mcts.sh                            # use config/mcts.yaml as-is
#   ./run_mcts.sh --k 5 --c-uct 0.5
#   ./run_mcts.sh --cheap-jsonl /path/a.jsonl --full-jsonl /path/b.jsonl
#   ./run_mcts.sh --max-iterations 20
#
# Any extra flags are forwarded to `python -m evolution.mcts_cli`.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Inside a container (/.dockerenv present) and docker-mcts.yaml available ->
# auto-pick the container-paths config. Override with SKILL_EVOLUTION_CONFIG.
if [[ -n "${SKILL_EVOLUTION_CONFIG:-}" ]]; then
  CONFIG_PATH="$SKILL_EVOLUTION_CONFIG"
elif [[ -f /.dockerenv && -f "$HERE/config/docker-mcts.yaml" ]]; then
  CONFIG_PATH="$HERE/config/docker-mcts.yaml"
else
  CONFIG_PATH="$HERE/config/mcts.yaml"
fi

# Source skillGuard's config.sh so claude -p and the subset shell pick up the
# same CC_* credentials as the rest of the benchmark.
export _EVO_LOAD_CFG="$CONFIG_PATH"
SKILL_GUARD_ROOT="$(python3 -c "
import os, yaml
with open(os.environ['_EVO_LOAD_CFG'], encoding='utf-8') as f:
    cfg = yaml.safe_load(f)
print(cfg['paths']['skill_guard_root'])
")"
unset _EVO_LOAD_CFG

if [[ -f "$SKILL_GUARD_ROOT/run/config.sh" ]]; then
  # shellcheck source=/dev/null
  source "$SKILL_GUARD_ROOT/run/config.sh"
  if [[ -n "${CC_ALIAS:-}" ]]; then
    case "$CC_ALIAS" in
      haiku)  ALIAS_VAR="ANTHROPIC_DEFAULT_HAIKU_MODEL"  ;;
      sonnet) ALIAS_VAR="ANTHROPIC_DEFAULT_SONNET_MODEL" ;;
      opus)   ALIAS_VAR="ANTHROPIC_DEFAULT_OPUS_MODEL"   ;;
      *)      ALIAS_VAR="" ;;
    esac
    if [[ -n "$ALIAS_VAR" && -n "${CC_REAL_MODEL:-}" ]]; then
      export "$ALIAS_VAR=$CC_REAL_MODEL"
    fi
    [[ -n "${CC_BASE_URL:-}"   ]] && export ANTHROPIC_BASE_URL="$CC_BASE_URL"
    [[ -n "${CC_AUTH_TOKEN:-}" ]] && export ANTHROPIC_AUTH_TOKEN="<your-token>"
    if [[ -n "${CC_HOST:-}" ]]; then
      export NO_PROXY="${CC_HOST},127.0.0.1,localhost"
      export no_proxy="${CC_HOST},127.0.0.1,localhost"
    fi
  fi
fi

export PYTHONPATH="$HERE:${PYTHONPATH:-}"
exec python3 -m evolution.mcts_cli --config "$CONFIG_PATH" "$@"
