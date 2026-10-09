"""Path helpers for the evolution workspace."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import EvolutionConfig


@dataclass
class WorkspacePaths:
    root: Path
    skills_active: Path
    skills_backup: Path
    logs: Path
    logs_rounds: Path
    logs_skills: Path
    logs_refiner: Path
    artifacts: Path
    artifacts_runs: Path
    pool_state: Path
    lineage_jsonl: Path
    summary_jsonl: Path
    loop_log: Path


def make_workspace_paths(cfg: EvolutionConfig) -> WorkspacePaths:
    root = cfg.paths.evolution_root
    return WorkspacePaths(
        root=root,
        skills_active=root / "skills" / "active",
        skills_backup=root / "skills" / "backup",
        logs=root / "logs",
        logs_rounds=root / "logs" / "rounds",
        logs_skills=root / "logs" / "skills",
        logs_refiner=root / "logs" / "refiner",
        artifacts=root / "artifacts",
        artifacts_runs=root / "artifacts" / "runs",
        pool_state=root / "skills" / "pool.json",
        lineage_jsonl=root / "logs" / "lineage.jsonl",
        summary_jsonl=root / "logs" / "round_summaries.jsonl",
        loop_log=root / "logs" / "loop.log",
    )


def ensure_layout(paths: WorkspacePaths) -> None:
    for p in (
        paths.skills_active,
        paths.skills_backup,
        paths.logs_rounds,
        paths.logs_skills,
        paths.logs_refiner,
        paths.artifacts_runs,
    ):
        p.mkdir(parents=True, exist_ok=True)
