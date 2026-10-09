"""
Helpers to materialize Claude Code PreToolUse Llama Guard hooks.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_LLAMA_GUARD_HOOK_SCRIPT = "pretool_llama_guard.py"
_LLAMA_GUARD_POST_HOOK_SCRIPT = "posttool_llama_guard.py"
_LLAMA_GUARD_USERPROMPT_HOOK_SCRIPT = "userprompt_llama_guard.py"
_LLAMA_GUARD_STOP_HOOK_SCRIPT = "stop_llama_guard.py"
_LLAMA_GUARD_HOST = "<your-internal-host>"


def _llama_guard_default_base_url() -> str:
    return f"http://{_LLAMA_GUARD_HOST}:10033/v1"


def _llama_guard_default_api_key() -> str:
    return "llama-guard"


def _llama_guard_default_model() -> str:
    return "llama-guard"


def _append_no_proxy_entries() -> None:
    required = [_LLAMA_GUARD_HOST, "127.0.0.1", "localhost", ".svc"]
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    parts = [p.strip() for p in current.split(",") if p.strip()]
    seen = set(parts)
    for item in required:
        if item not in seen:
            parts.append(item)
            seen.add(item)
    merged = ",".join(parts)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


def ensure_llama_guard_claude_hooks(*, workspace: Path, skill_dir: Path) -> None:
    """Install the LlamaGuard hook suite + runtime config when enabled."""
    raw = os.environ.get("PINCHBENCH_LLAMA_GUARD", "").strip().lower()
    if raw not in ("1", "true", "yes"):
        return
    _append_no_proxy_entries()

    src_script = skill_dir / "docker" / _LLAMA_GUARD_HOOK_SCRIPT
    src_post_script = skill_dir / "docker" / _LLAMA_GUARD_POST_HOOK_SCRIPT
    src_userprompt_script = skill_dir / "docker" / _LLAMA_GUARD_USERPROMPT_HOOK_SCRIPT
    src_stop_script = skill_dir / "docker" / _LLAMA_GUARD_STOP_HOOK_SCRIPT
    if not src_script.is_file():
        logger.warning("Llama Guard pre hook script missing: %s", src_script)
        return
    if not src_post_script.is_file():
        logger.warning("Llama Guard post hook script missing: %s", src_post_script)
        return
    if not src_userprompt_script.is_file():
        logger.warning("Llama Guard userprompt hook script missing: %s", src_userprompt_script)
        return
    if not src_stop_script.is_file():
        logger.warning("Llama Guard stop hook script missing: %s", src_stop_script)
        return

    hooks_dir = workspace / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest_script = hooks_dir / _LLAMA_GUARD_HOOK_SCRIPT
    dest_post_script = hooks_dir / _LLAMA_GUARD_POST_HOOK_SCRIPT
    dest_userprompt_script = hooks_dir / _LLAMA_GUARD_USERPROMPT_HOOK_SCRIPT
    dest_stop_script = hooks_dir / _LLAMA_GUARD_STOP_HOOK_SCRIPT
    shutil.copy2(src_script, dest_script)
    shutil.copy2(src_post_script, dest_post_script)
    shutil.copy2(src_userprompt_script, dest_userprompt_script)
    shutil.copy2(src_stop_script, dest_stop_script)
    try:
        for dest in (dest_script, dest_post_script, dest_userprompt_script, dest_stop_script):
            mode = dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            dest.chmod(mode)
    except OSError:
        pass

    base_url = _llama_guard_default_base_url()
    api_key = _llama_guard_default_api_key()
    model = _llama_guard_default_model()
    timeout_raw = os.environ.get("PINCHBENCH_LLAMA_GUARD_TIMEOUT_SEC", "").strip()
    try:
        timeout_sec = float(timeout_raw) if timeout_raw else 8.0
    except ValueError:
        timeout_sec = 8.0
    on_err = os.environ.get("PINCHBENCH_LLAMA_GUARD_ON_ERROR", "ask").strip().lower()
    if on_err not in ("allow", "deny", "ask"):
        on_err = "ask"

    runtime = {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "timeout_sec": timeout_sec,
        "on_error_decision": on_err,
        "state_path": str((hooks_dir / "llama_guard_state.json").resolve()),
    }
    debug_log_path = os.environ.get("PINCHBENCH_LLAMA_GUARD_LOG", "").strip()
    if debug_log_path:
        runtime["debug_log_path"] = debug_log_path
    if os.environ.get("PINCHBENCH_LLAMA_GUARD_DEBUG", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
        "on",
    ):
        runtime["debug"] = True
    (hooks_dir / "llama_guard_runtime.json").write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    settings_path = workspace / ".claude" / "settings.json"
    data: Dict[str, Any] = {}
    if settings_path.is_file():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read %s for Llama Guard merge: %s", settings_path, exc)
            data = {}

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks

    pretool_list = hooks.setdefault("PreToolUse", [])
    if not isinstance(pretool_list, list):
        pretool_list = []
        hooks["PreToolUse"] = pretool_list

    posttool_list = hooks.setdefault("PostToolUse", [])
    if not isinstance(posttool_list, list):
        posttool_list = []
        hooks["PostToolUse"] = posttool_list

    userprompt_list = hooks.setdefault("UserPromptSubmit", [])
    if not isinstance(userprompt_list, list):
        userprompt_list = []
        hooks["UserPromptSubmit"] = userprompt_list

    stop_list = hooks.setdefault("Stop", [])
    if not isinstance(stop_list, list):
        stop_list = []
        hooks["Stop"] = stop_list

    subagent_stop_list = hooks.setdefault("SubagentStop", [])
    if not isinstance(subagent_stop_list, list):
        subagent_stop_list = []
        hooks["SubagentStop"] = subagent_stop_list

    hook_cmd = f'python3 "{dest_script.resolve()}"'
    post_hook_cmd = f'python3 "{dest_post_script.resolve()}"'
    userprompt_hook_cmd = f'python3 "{dest_userprompt_script.resolve()}"'
    stop_hook_cmd = f'python3 "{dest_stop_script.resolve()}"'

    def _is_llama_row(row: Any) -> bool:
        if not isinstance(row, dict):
            return False
        inner = row.get("hooks")
        if not isinstance(inner, list):
            return False
        for h in inner:
            if not isinstance(h, dict):
                continue
            cmd = str(h.get("command", ""))
            if any(
                s in cmd
                for s in (
                    _LLAMA_GUARD_HOOK_SCRIPT,
                    _LLAMA_GUARD_POST_HOOK_SCRIPT,
                    _LLAMA_GUARD_USERPROMPT_HOOK_SCRIPT,
                    _LLAMA_GUARD_STOP_HOOK_SCRIPT,
                )
            ):
                return True
        return False

    for lst in (pretool_list, posttool_list, userprompt_list, stop_list, subagent_stop_list):
        lst[:] = [row for row in lst if not _is_llama_row(row)]

    hook_timeout = int(min(120, max(5, int(timeout_sec) + 2)))
    post_hook_timeout = int(min(120, max(5, int(timeout_sec) + 5)))

    userprompt_list.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": userprompt_hook_cmd,
                    "timeout": hook_timeout,
                }
            ],
        }
    )

    pretool_list.append(
        {
            "matcher": "Bash|Write|Edit|MultiEdit|WebFetch|WebSearch|Skill|Task|NotebookEdit|mcp__.*",
            "hooks": [
                {
                    "type": "command",
                    "command": hook_cmd,
                    "timeout": hook_timeout,
                }
            ],
        }
    )

    posttool_list.append(
        {
            "matcher": "Write|Edit|MultiEdit|Read|Skill",
            "hooks": [
                {
                    "type": "command",
                    "command": post_hook_cmd,
                    "timeout": post_hook_timeout,
                }
            ],
        }
    )

    stop_list.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": stop_hook_cmd,
                    "timeout": post_hook_timeout,
                }
            ],
        }
    )

    subagent_stop_list.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": stop_hook_cmd,
                    "timeout": post_hook_timeout,
                }
            ],
        }
    )

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Llama Guard hook suite installed -> %s", settings_path)
