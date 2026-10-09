"""
ASR judge helpers kept out of lib_agent.py (that module is already large).

Runs Claude Code (`claude -p`) in the victim workspace with tools enabled so the
judge can verify paths under the workspace and (on Unix) ``/tmp`` side effects
mentioned in attack metadata signals — without per-attack ``asr_extra_observation_paths``.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib_injection_skill import claude_permission_mode_cli_args

logger = logging.getLogger(__name__)

USE_SHELL = platform.system() == "Windows"


def _parse_jsonl_stream(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"_raw": line[:500], "_unparsable": True})
    return out


def _coerce_subprocess_output(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def final_text_from_claude_code_transcript(transcript: List[Dict[str, Any]]) -> str:
    """Prefer the stream-json ``result`` event; else last assistant text (tool_use aware)."""
    for event in reversed(transcript or []):
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            r = event.get("result")
            if isinstance(r, str) and r.strip():
                return r.strip()
    try:
        from agentdojo_grader import extract_model_output

        return extract_model_output(transcript)
    except ImportError:
        return ""


def _default_extra_allow_dirs() -> List[Path]:
    """Let the ASR judge read common host temp side effects (signals often cite /tmp/...)."""
    if USE_SHELL:
        import os

        t = os.environ.get("TEMP") or os.environ.get("TMP") or ""
        return [Path(t)] if t and Path(t).is_dir() else []
    p = Path("/tmp")
    return [p] if p.is_dir() else []


def run_claude_code_asr_judge(
    *,
    prompt: str,
    workspace: Path,
    model_id: str,
    timeout_seconds: float,
    verbose: bool = False,
    extra_allow_dirs: Optional[List[Path]] = None,
) -> Dict[str, Any]:
    """Headless Claude Code session with tools; cwd and primary --add-dir = *workspace*.

    Returns keys: status, text (final model string for JSON parse), transcript, stderr,
    exit_code, timed_out, error (optional).
    """
    safe_prompt = (prompt or "").replace("\x00", "")
    if safe_prompt != (prompt or "") and verbose:
        logger.info("   [VERBOSE] ASR prompt contained NUL bytes; sanitized before subprocess call")

    ws = workspace.resolve()
    if not ws.is_dir():
        return {
            "status": "error",
            "text": "",
            "transcript": [],
            "stderr": "",
            "exit_code": -1,
            "timed_out": False,
            "error": f"ASR workspace is not a directory: {ws}",
        }

    extras = extra_allow_dirs if extra_allow_dirs is not None else _default_extra_allow_dirs()
    seen_dirs = {str(ws)}
    cmd: List[str] = [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        *claude_permission_mode_cli_args(),
        "--add-dir",
        str(ws),
    ]
    for d in extras:
        try:
            r = d.resolve()
            if not r.is_dir():
                continue
            key = str(r)
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            cmd.extend(["--add-dir", key])
        except OSError:
            continue
    if model_id:
        cmd.extend(["--model", model_id])
    cmd.append(safe_prompt)

    start = time.time()
    stdout = ""
    stderr = ""
    exit_code = -1
    timed_out = False
    if verbose:
        logger.info(
            "   [VERBOSE] ASR claude-code judge: cwd=%s timeout=%ss",
            ws,
            timeout_seconds,
        )
    judge_env: Optional[Dict[str, str]] = None
    judge_base = os.environ.get("PINCHBENCH_JUDGE_ANTHROPIC_BASE_URL", "").strip()
    judge_token = os.environ.get("PINCHBENCH_JUDGE_ANTHROPIC_AUTH_TOKEN", "").strip()
    if judge_base or judge_token:
        judge_env = os.environ.copy()
        if judge_base:
            judge_env["ANTHROPIC_BASE_URL"] = judge_base
        if judge_token:
            judge_env["ANTHROPIC_AUTH_TOKEN"] = judge_token
        if verbose:
            logger.info(
                "   [VERBOSE] ASR judge using dedicated judge gateway overrides: base=%s token_set=%s",
                bool(judge_base),
                bool(judge_token),
            )
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(ws),
            timeout=timeout_seconds,
            check=False,
            shell=USE_SHELL,
            stdin=subprocess.DEVNULL,
            env=judge_env,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = _coerce_subprocess_output(exc.stdout)
        stderr = _coerce_subprocess_output(exc.stderr)
    except FileNotFoundError as exc:
        stderr = f"claude command not found: {exc}"

    transcript = _parse_jsonl_stream(stdout)
    text = final_text_from_claude_code_transcript(transcript)
    elapsed = time.time() - start

    status = "success"
    if timed_out:
        status = "timeout"
    if not transcript and not text:
        status = "error"
    if exit_code not in (0, -1) and not timed_out:
        status = "error"
    if stderr and "claude command not found" in str(stderr):
        status = "error"

    err: Optional[str] = None
    if status != "success":
        if stderr and stderr.strip():
            err = stderr.strip()[:1500]
        else:
            err = (
                f"exit={exit_code} timed_out={timed_out} "
                f"events={len(transcript)} text_chars={len(text)}"
            )

    if verbose:
        logger.info(
            "   [VERBOSE] ASR claude-code judge done: status=%s events=%d time=%.2fs",
            status,
            len(transcript),
            elapsed,
        )

    return {
        "status": status,
        "text": text,
        "transcript": transcript,
        "stderr": stderr,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "error": err,
    }
