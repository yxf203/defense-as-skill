"""Evaluate a deployed skill against an arbitrary instances jsonl.

This is a thin adapter over ``skillGuard/subsets/run_all_instances_mixed.sh``,
which already supports the ``INSTANCES_JSONL`` and ``MIXED_SUMMARY_DIR`` env
vars.  We pass the cheap/full eval jsonls through those hooks so one script
does both roles without any modification.

The adapter returns a structured payload shaped like the legacy
:class:`SubsetEvaluator` output so downstream code (analyzer / refiner) keeps
working unchanged:

    {
      "mode": "cheap" | "full",
      "round_id": "<tag>",
      "artifacts_dir": "<absolute path>",
      "cells": [ {...per-task dict...}, ... ],
      "aggregate": {malicious_asr, guard_effective_rate, ...},
    }
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import EvolutionConfig
from .evaluator import EvalCell, SubsetEvaluator
from .paths import WorkspacePaths
from .logger_utils import utc_iso

logger = logging.getLogger("evolution.instance_evaluator")


class InstanceEvaluator:
    """Run ``run_all_instances_mixed.sh`` against an arbitrary instances jsonl.

    Shares the result-parsing code with :class:`SubsetEvaluator` (``_collect_cells`` +
    ``_aggregate``) via composition so guard/ASR metrics are computed the same
    way in both paths.
    """

    def __init__(self, cfg: EvolutionConfig, paths: WorkspacePaths):
        self.cfg = cfg
        self.paths = paths
        self.skill_guard = cfg.paths.skill_guard_root
        # Reuse the legacy parser by borrowing the instance.
        self._parser = SubsetEvaluator(cfg, paths)

    # ---------------- Public API ----------------

    def run(
        self,
        *,
        instances_jsonl: Path,
        tag: str,
        mode: str,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Run one eval pass over ``instances_jsonl`` and return the payload."""
        if not instances_jsonl.is_file():
            raise FileNotFoundError(f"instances jsonl missing: {instances_jsonl}")
        script = self.skill_guard / "subsets" / "run_all_instances_mixed.sh"
        if not script.is_file():
            raise FileNotFoundError(f"run_all_instances_mixed.sh not found at {script}")

        artifacts_dir = self.paths.artifacts_runs / tag
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        summary_dir = artifacts_dir / f"{mode}_eval"
        summary_dir.mkdir(parents=True, exist_ok=True)
        # Clean the summary dir so we always get a fresh jsonl for this cell pass.
        for p in summary_dir.glob("*"):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)

        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        env["INSTANCES_JSONL"] = str(instances_jsonl)
        env["MIXED_SUMMARY_DIR"] = str(summary_dir)
        env["RESULT_TAG"] = f"{tag}__{mode}"
        env.setdefault("JOBS", str(self.cfg.evaluation.__dict__.get("jobs", 1) or 1))

        log_path = artifacts_dir / f"{mode}_eval.stdout.log"
        logger.info(
            "Launching %s eval: tag=%s instances=%s (n=%d) summary_dir=%s",
            mode, tag, instances_jsonl, _count_lines(instances_jsonl), summary_dir,
        )

        start = time.time()
        with log_path.open("w", encoding="utf-8") as f:
            f.write(f"# {utc_iso()} starting {mode} eval\n")
            f.write(f"# instances_jsonl={instances_jsonl}\n")
            f.write(f"# summary_dir={summary_dir}\n")
            f.flush()
            cmd = ["bash", str(script)]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(self.skill_guard),
                    env=env,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    timeout=self.cfg.evaluation.round_timeout_sec,
                    check=False,
                )
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                exit_code = -1
                logger.warning("%s eval timed out after %ds", mode, self.cfg.evaluation.round_timeout_sec)
        elapsed = time.time() - start

        summary_jsonl = summary_dir / "asr_subset_results.jsonl"
        cells = self._read_cells(summary_jsonl, artifacts_dir / f"{mode}_cells.jsonl")
        aggregate = self._parser._aggregate(cells)  # noqa: SLF001
        aggregate["mode"] = mode
        aggregate["elapsed_seconds"] = round(elapsed, 2)
        aggregate["exit_code"] = exit_code
        aggregate["instances_jsonl"] = str(instances_jsonl)
        aggregate["summary_dir"] = str(summary_dir)

        # Copy run JSONs that each cell references so the artifact is self-contained.
        self._copy_run_jsons(summary_jsonl, artifacts_dir / f"{mode}_run_jsons")

        return {
            "mode": mode,
            "round_id": tag,
            "artifacts_dir": str(artifacts_dir),
            "cells": [c.to_dict() for c in cells],
            "aggregate": aggregate,
            "summary_jsonl": str(summary_jsonl),
        }

    # ---------------- internals ----------------

    def _read_cells(self, summary_jsonl: Path, archive_path: Path) -> List[EvalCell]:
        if not summary_jsonl.is_file():
            logger.warning("summary jsonl missing: %s", summary_jsonl)
            return []
        # Persist the merged jsonl into artifacts for provenance.
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(summary_jsonl, archive_path)
        # SubsetEvaluator._collect_cells reads from the configured subset
        # directory. Plant our jsonl there, call the parser, then clean up so
        # the mixed-subset scripts are not affected.
        staged = self.skill_guard / "subsets" / self._parser.cfg.evaluation.subset / "asr_subset_results.jsonl"
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(summary_jsonl, staged)
        try:
            cells = self._parser._collect_cells(archive_path.parent)  # noqa: SLF001
        finally:
            try:
                staged.unlink()
            except OSError:
                pass
        return cells

    def _copy_run_jsons(self, summary_jsonl: Path, dest_dir: Path) -> None:
        if not summary_jsonl.is_file():
            return
        dest_dir.mkdir(parents=True, exist_ok=True)
        for line in summary_jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rj = row.get("result_json")
            if not rj:
                continue
            src = Path(rj)
            if not src.is_file():
                continue
            safe_name = re.sub(
                r"[^A-Za-z0-9._-]+", "_",
                f"{row.get('task_id','task')}__{row.get('injected_skill_path','benign') or 'benign'}",
            )[:160]
            try:
                shutil.copy2(src, dest_dir / f"{safe_name}.json")
            except OSError:
                continue


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0
