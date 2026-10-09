"""Logging helpers for the evolution loop.

Two tiers are produced:
1. **machine readable** - JSON / JSONL / CSV under `logs/`.
2. **human readable** - per-round Markdown summary under `logs/rounds/<round>.md`.
"""
from __future__ import annotations

import csv
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import EvolutionConfig
from .paths import WorkspacePaths


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def setup_logger(cfg: EvolutionConfig, paths: WorkspacePaths) -> logging.Logger:
    logger = logging.getLogger("evolution")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    paths.loop_log.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(cfg.logging.console_level.upper())
    console.setFormatter(formatter)
    logger.addHandler(console)

    filehandler = logging.FileHandler(paths.loop_log, mode="a", encoding="utf-8")
    filehandler.setLevel(cfg.logging.file_level.upper())
    filehandler.setFormatter(formatter)
    logger.addHandler(filehandler)

    return logger


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: Optional[List[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fn = fieldnames or sorted({k for r in rows for k in r.keys()})
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fn)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fn})


# -------------- Markdown round summary --------------

def _fmt_float(x: Any) -> str:
    try:
        return f"{float(x):.3f}"
    except (TypeError, ValueError):
        return "-"


def render_round_summary_md(
    *,
    round_id: str,
    skill_id: str,
    parent_skill_id: Optional[str],
    summary_metrics: Dict[str, Any],
    per_task: List[Dict[str, Any]],
    pool_snapshot: List[Dict[str, Any]],
    retired: List[Dict[str, Any]],
    refine_plan: Optional[str] = None,
    refine_diff_files: Optional[List[str]] = None,
    next_skill_id: Optional[str] = None,
    notes: Optional[List[str]] = None,
) -> str:
    lines: List[str] = []
    lines.append(f"# Round `{round_id}` summary")
    lines.append("")
    lines.append(f"- **Skill evaluated**: `{skill_id}`")
    if parent_skill_id:
        lines.append(f"- **Parent skill**: `{parent_skill_id}`")
    if next_skill_id:
        lines.append(f"- **Refined skill produced**: `{next_skill_id}`")
    lines.append(f"- **Generated at**: {utc_iso()}")
    lines.append("")

    lines.append("## Aggregate metrics")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    for k in (
        "n_cells",
        "malicious_cells",
        "benign_cells",
        "malicious_asr",
        "benign_utility",
        "malicious_utility",
        "mean_confirmation",
        "mean_total_tokens",
        "guard_read_rate",
        "guard_triggered_rate",
        "guard_effective_rate",
        "invalid_rate",
    ):
        if k in summary_metrics:
            lines.append(f"| {k} | {_fmt_float(summary_metrics[k])} |")
    lines.append("")

    lines.append("## Per-task feedback")
    lines.append("")
    lines.append("| task_id | split | ASR | utility | guard_read | guard_triggered | conf | reason |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in per_task:
        tid = row.get("task_id", "")
        split = row.get("split", "")
        asr = row.get("attack_success")
        util = row.get("utility_score")
        gr = row.get("guard_read")
        gt = row.get("guard_triggered")
        conf = row.get("confirmation_count")
        reason = row.get("failure_reason") or row.get("success_reason") or ""
        lines.append(
            f"| {tid} | {split} | {asr} | {_fmt_float(util)} | {gr} | {gt} | {conf} | {reason} |"
        )
    lines.append("")

    if refine_plan:
        lines.append("## Refinement plan")
        lines.append("")
        lines.append("```")
        lines.append(refine_plan.strip())
        lines.append("```")
        lines.append("")
    if refine_diff_files:
        lines.append("## Refined files")
        lines.append("")
        for f in refine_diff_files:
            lines.append(f"- `{f}`")
        lines.append("")

    lines.append("## Skill pool snapshot")
    lines.append("")
    lines.append("| skill_id | role | asr | utility | conf | score | status |")
    lines.append("|---|---|---|---|---|---|---|")
    for p in pool_snapshot:
        lines.append(
            "| {sid} | {role} | {asr} | {util} | {conf} | {sc} | {st} |".format(
                sid=p.get("skill_id", ""),
                role=p.get("role", ""),
                asr=_fmt_float(p.get("asr")),
                util=_fmt_float(p.get("utility")),
                conf=_fmt_float(p.get("confirmation")),
                sc=_fmt_float(p.get("score")),
                st=p.get("status", ""),
            )
        )
    lines.append("")

    if retired:
        lines.append("## Retired this round")
        lines.append("")
        lines.append("| skill_id | moved_to | reason |")
        lines.append("|---|---|---|")
        for r in retired:
            lines.append(
                f"| {r.get('skill_id','')} | {r.get('backup_path','')} | {r.get('reason','')} |"
            )
        lines.append("")

    if notes:
        lines.append("## Notes")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines)
