"""Main orchestration: the single-guard-skill evolution loop."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .analyzer import analyse_round
from .config import EvolutionConfig
from .evaluator import SubsetEvaluator
from .logger_utils import (
    append_jsonl,
    render_round_summary_md,
    setup_logger,
    utc_iso,
    write_csv,
    write_json,
)
from .paths import WorkspacePaths, ensure_layout
from .refiner import Refiner
from .skill_manager import SkillManager, SkillRecord


@dataclass
class LoopState:
    round_idx: int = 0
    last_asr: Optional[float] = None
    best_asr: Optional[float] = None
    rounds_since_improvement: int = 0
    total_skills_created: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)
    stopped_reason: Optional[str] = None


def _new_round_id(idx: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"round_{idx:03d}_{stamp}"


def _within_target(asr: Optional[float], target: float) -> bool:
    return asr is not None and asr <= target


class EvolutionLoop:
    def __init__(self, cfg: EvolutionConfig):
        self.cfg = cfg
        self.paths: WorkspacePaths = WorkspacePaths(
            root=cfg.paths.evolution_root,
            skills_active=cfg.paths.evolution_root / "skills" / "active",
            skills_backup=cfg.paths.evolution_root / "skills" / "backup",
            logs=cfg.paths.evolution_root / "logs",
            logs_rounds=cfg.paths.evolution_root / "logs" / "rounds",
            logs_skills=cfg.paths.evolution_root / "logs" / "skills",
            logs_refiner=cfg.paths.evolution_root / "logs" / "refiner",
            artifacts=cfg.paths.evolution_root / "artifacts",
            artifacts_runs=cfg.paths.evolution_root / "artifacts" / "runs",
            pool_state=cfg.paths.evolution_root / "skills" / "pool.json",
            lineage_jsonl=cfg.paths.evolution_root / "logs" / "lineage.jsonl",
            summary_jsonl=cfg.paths.evolution_root / "logs" / "round_summaries.jsonl",
            loop_log=cfg.paths.evolution_root / "logs" / "loop.log",
        )
        ensure_layout(self.paths)
        self.logger = setup_logger(cfg, self.paths)
        self.skills = SkillManager(cfg, self.paths)
        self.evaluator = SubsetEvaluator(cfg, self.paths)
        self.refiner = Refiner(cfg, self.paths, self.skills)
        self.state = LoopState()
        # CLI-injected flags.
        self.skip_refine: bool = False
        self.simulate_round_jsonl: Optional[Path] = None

    # ------------------------------------------------------------------
    def run(self, *, dry_run: bool = False, max_rounds: Optional[int] = None) -> LoopState:
        self.logger.info("=" * 72)
        self.logger.info("Starting skill-evolution loop (subset=%s, max_rounds=%d)",
                         self.cfg.evaluation.subset,
                         max_rounds or self.cfg.loop.max_rounds)
        self.logger.info("Workspace root: %s", self.paths.root)
        # Bootstrap initial skill (or reuse existing).
        initial = self.skills.bootstrap_initial_skill()
        self.logger.info("Initial skill: %s (%s)", initial.skill_id, initial.bundle_path)

        n_rounds = max_rounds or self.cfg.loop.max_rounds

        parent_skill: Optional[SkillRecord] = initial
        for i in range(1, n_rounds + 1):
            self.state.round_idx = i
            round_id = _new_round_id(i)
            self.logger.info("--- Round %d (%s) ---", i, round_id)
            if parent_skill is None:
                self.logger.warning("No parent skill available; aborting.")
                self.state.stopped_reason = "no_parent_skill"
                break
            try:
                round_info = self._run_one_round(
                    parent_skill=parent_skill,
                    round_id=round_id,
                    dry_run=dry_run,
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("Round failed: %s", exc)
                self.state.stopped_reason = f"exception:{exc}"
                break

            self.state.history.append(round_info)
            asr = round_info["aggregate"].get("malicious_asr")
            self.state.last_asr = asr
            if asr is not None:
                if self.state.best_asr is None or asr + 1e-9 < self.state.best_asr:
                    improved = self.state.best_asr is None or (
                        (self.state.best_asr - asr) >= self.cfg.loop.min_improvement
                    )
                    self.state.best_asr = asr
                    if improved:
                        self.state.rounds_since_improvement = 0
                    else:
                        self.state.rounds_since_improvement += 1
                else:
                    self.state.rounds_since_improvement += 1

            parent_skill = self.skills.choose_parent_for_next_round()

            # Check stopping conditions.
            stop_reason = self._check_stop()
            if stop_reason:
                self.logger.info("Stopping: %s", stop_reason)
                self.state.stopped_reason = stop_reason
                break

        self._write_history_dump()
        self.logger.info("Loop finished. Best ASR=%s, rounds=%d, stopped=%s",
                         self.state.best_asr, self.state.round_idx, self.state.stopped_reason)
        return self.state

    # ------------------------------------------------------------------
    def _run_one_round(
        self,
        *,
        parent_skill: SkillRecord,
        round_id: str,
        dry_run: bool,
    ) -> Dict[str, Any]:
        self.logger.info("Deploying parent skill %s", parent_skill.skill_id)
        deployed_path = self.skills.deploy(parent_skill.skill_id)
        self.logger.info("Parent skill deployed at %s", deployed_path)

        if dry_run:
            self.logger.warning("DRY RUN: skipping benchmark invocation. Generating stub cells.")
            eval_out = {
                "round_id": round_id,
                "cells": [],
                "aggregate": {
                    "n_cells": 0,
                    "malicious_cells": 0,
                    "benign_cells": 0,
                    "malicious_asr": None,
                    "benign_utility": None,
                    "malicious_utility": None,
                    "mean_confirmation": None,
                    "mean_total_tokens": None,
                    "guard_read_rate": None,
                    "guard_triggered_rate": None,
                    "guard_effective_rate": None,
                    "invalid_rate": None,
                    "mode": "dry_run",
                },
                "artifacts_dir": str(self.paths.artifacts_runs / round_id),
            }
            (self.paths.artifacts_runs / round_id).mkdir(parents=True, exist_ok=True)
        elif self.simulate_round_jsonl is not None:
            self.logger.warning(
                "SIMULATE: loading synthetic subset jsonl from %s", self.simulate_round_jsonl
            )
            eval_out = self._simulate_round(round_id=round_id, src_jsonl=self.simulate_round_jsonl)
        else:
            t0 = time.time()
            eval_out = self.evaluator.run_round(round_id=round_id)
            self.logger.info(
                "Evaluation done in %.1fs (n_cells=%d)",
                time.time() - t0,
                eval_out["aggregate"].get("n_cells", 0),
            )

        aggregate: Dict[str, Any] = eval_out["aggregate"]
        cells: List[Dict[str, Any]] = eval_out["cells"]
        artifacts_dir = Path(eval_out["artifacts_dir"])

        # Update metrics on the *parent* (it is the one we evaluated).
        self.skills.update_metrics(
            parent_skill.skill_id,
            {
                "malicious_asr": aggregate.get("malicious_asr"),
                "benign_utility": aggregate.get("benign_utility"),
                "malicious_utility": aggregate.get("malicious_utility"),
                "mean_confirmation": aggregate.get("mean_confirmation"),
                "mean_total_tokens": aggregate.get("mean_total_tokens"),
                "guard_read_rate": aggregate.get("guard_read_rate"),
                "guard_triggered_rate": aggregate.get("guard_triggered_rate"),
                "guard_effective_rate": aggregate.get("guard_effective_rate"),
                "n_cells": aggregate.get("n_cells"),
                "round_evaluated": round_id,
            },
            round_id=round_id,
        )

        feedback = analyse_round(aggregate, cells)

        # Persist per-task JSON + CSV into round artifact dir.
        write_json(artifacts_dir / "feedback.json", feedback)
        write_csv(artifacts_dir / "per_task.csv", feedback["per_task"])

        # Refine (create child) — only if we haven't already hit max_total_skills
        refine_summary: Optional[Dict[str, Any]] = None
        child_rec: Optional[SkillRecord] = None
        if self.skip_refine:
            self.logger.warning("--skip-refine set: not invoking claude-code refiner.")
        elif len(self.skills.records()) < self.cfg.loop.max_total_skills and not dry_run:
            try:
                refine_result = self.refiner.refine(
                    parent=parent_skill,
                    round_id=round_id,
                    aggregate=aggregate,
                    per_task=feedback["per_task"],
                    bucket_counts=feedback.get("bucket_counts") or {},
                )
                child_rec = refine_result["child"]
                refine_summary = refine_result["summary"]
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("Refinement failed: %s", exc)
        elif dry_run:
            self.logger.warning("DRY RUN: skipping refinement.")
        else:
            self.logger.warning("Skill cap %d reached; skipping refinement", self.cfg.loop.max_total_skills)

        # Pool policy: assign roles, retire evictions.
        pool_update = self.skills.apply_pool_policy(round_id=round_id)
        pool_snapshot = self.skills.pool_snapshot()

        # Markdown + JSONL round summary.
        retired = pool_update.get("retired") or []
        md = render_round_summary_md(
            round_id=round_id,
            skill_id=parent_skill.skill_id,
            parent_skill_id=parent_skill.parent_skill_id,
            summary_metrics=aggregate,
            per_task=feedback["per_task"],
            pool_snapshot=pool_snapshot,
            retired=retired,
            refine_plan=(refine_summary or {}).get("final_text"),
            refine_diff_files=(refine_summary or {}).get("applied_files"),
            next_skill_id=(child_rec.skill_id if child_rec else None),
            notes=[
                f"mode={aggregate.get('mode')}",
                f"guard_effective_rate={aggregate.get('guard_effective_rate')}",
                f"stopped_reason_so_far={self.state.stopped_reason}",
            ],
        )
        (self.paths.logs_rounds / f"{round_id}.md").write_text(md, encoding="utf-8")

        round_record: Dict[str, Any] = {
            "round_id": round_id,
            "parent_skill_id": parent_skill.skill_id,
            "child_skill_id": child_rec.skill_id if child_rec else None,
            "aggregate": aggregate,
            "bucket_counts": feedback.get("bucket_counts"),
            "retired_this_round": retired,
            "refine_summary": refine_summary,
            "artifacts_dir": str(artifacts_dir),
            "md_summary": str(self.paths.logs_rounds / f"{round_id}.md"),
            "completed_at": utc_iso(),
        }
        append_jsonl(self.paths.summary_jsonl, round_record)
        return round_record

    # ------------------------------------------------------------------
    def _simulate_round(self, *, round_id: str, src_jsonl: Path) -> Dict[str, Any]:
        """Load cells from a synthetic subset jsonl.  Used only for plumbing tests."""
        import shutil

        if not src_jsonl.is_file():
            raise FileNotFoundError(f"simulate jsonl missing: {src_jsonl}")
        # Replay: copy to where the evaluator expects so its collection path can reuse.
        target = (
            self.cfg.paths.skill_guard_root
            / "subsets"
            / self.cfg.evaluation.subset
            / "asr_subset_results.jsonl"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_jsonl, target)
        artifacts_dir = self.paths.artifacts_runs / round_id
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        cells = self.evaluator._collect_cells(artifacts_dir)  # noqa: SLF001 (test helper)
        aggregate = self.evaluator._aggregate(cells)  # noqa: SLF001
        aggregate["mode"] = "simulated"
        return {
            "round_id": round_id,
            "cells": [c.to_dict() for c in cells],
            "aggregate": aggregate,
            "artifacts_dir": str(artifacts_dir),
        }

    # ------------------------------------------------------------------
    def _check_stop(self) -> Optional[str]:
        if self.state.round_idx >= self.cfg.loop.max_rounds:
            return "max_rounds_reached"
        if _within_target(self.state.last_asr, self.cfg.loop.asr_target):
            return f"asr_target_reached ({self.state.last_asr} <= {self.cfg.loop.asr_target})"
        if self.state.rounds_since_improvement >= self.cfg.loop.patience:
            return f"no_improvement_{self.cfg.loop.patience}_rounds"
        if len(self.skills.records()) >= self.cfg.loop.max_total_skills:
            return "max_total_skills_reached"
        return None

    # ------------------------------------------------------------------
    def _write_history_dump(self) -> None:
        write_json(
            self.paths.logs / "loop_state.json",
            {
                "round_idx": self.state.round_idx,
                "last_asr": self.state.last_asr,
                "best_asr": self.state.best_asr,
                "rounds_since_improvement": self.state.rounds_since_improvement,
                "total_skills_created": len(self.skills.records()),
                "stopped_reason": self.state.stopped_reason,
                "history": self.state.history,
                "finished_at": utc_iso(),
            },
        )
