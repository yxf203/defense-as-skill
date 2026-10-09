"""MCTS-style skill-evolution loop.

Overall shape (see ``README_MCTS.md`` for the full spec)::

    initialization
    --------------
    root = bootstrap(base_skill)
    full_eval(root)
    if terminal(root): stop
    batch = multi_refine(root, k)          # one claude call, k children
    for each child in batch:
        cheap_eval(child)                  # attach score to child
        add child to active pool

    main loop
    ---------
    repeat until budget exhausted or terminal:
        v  = argmax_uct(active pool, c)
        full_eval(v)
        v.status = full_evaluated
        if terminal(v): break
        batch = multi_refine(v, k)
        for each child in batch:
            cheap_eval(child)              # backprop visit counts up the chain
            add child to active pool

Important invariants
~~~~~~~~~~~~~~~~~~~~
1. Cheap scores never mix with full scores.  ``cheap_mean`` and
   ``full_eval_score`` live in separate fields.
2. Each node is fully-evaluated at most once; after that, it exits the active
   pool and cannot be re-selected.
3. Expansion always uses the just-full-evaluated node itself as the parent —
   *not* the parent's parent.
4. ``full_eval_feedback`` (aggregate + per-task + bucket counts) is the refiner's
   input when generating the next batch.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .analyzer import analyse_round
from .config import EvolutionConfig
from .instance_evaluator import InstanceEvaluator
from .logger_utils import append_jsonl, setup_logger, utc_iso, write_csv, write_json
from .multi_refiner import MultiRefiner
from .paths import WorkspacePaths, ensure_layout
from .skill_manager import SkillManager, SkillRecord
from .tree import (
    STATUS_ACTIVE,
    STATUS_FULL_EVALUATED,
    STATUS_TERMINAL,
    Tree,
    TreeNode,
)

logger = logging.getLogger("evolution.mcts")


# ---------------------------------------------------------------------------


def _score_from_aggregate(agg: Dict[str, Any], fn: str) -> float:
    """Map an eval aggregate dict to a scalar score in [0, 1]-ish where higher is better."""
    if fn == "guard_effective_rate":
        v = agg.get("guard_effective_rate")
        return float(v) if v is not None else 0.0
    # Default: inverse ASR.
    asr = agg.get("malicious_asr")
    if asr is None:
        return 0.0
    return round(1.0 - float(asr), 6)


def _tag(kind: str, idx: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{kind}_{idx:03d}_{stamp}"


def _backoff_seconds(attempt: int, base_sec: float, max_sec: float) -> float:
    return min(float(max_sec), float(base_sec) * (2 ** max(0, int(attempt))))


def _health_probe_ok(url: str, *, timeout_sec: float) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout_sec)
        return True
    except urllib.error.HTTPError as exc:
        return int(exc.code) < 500
    except Exception:
        return False


def _wait_for_backend(url: str, *, timeout_sec: float, poll_sec: float, max_wait_sec: float) -> bool:
    deadline = time.monotonic() + max(0.0, float(max_wait_sec))
    while time.monotonic() < deadline:
        if _health_probe_ok(url, timeout_sec=timeout_sec):
            return True
        time.sleep(max(0.5, float(poll_sec)))
    return _health_probe_ok(url, timeout_sec=timeout_sec)


def _runtime_looks_transient(aggregate: Dict[str, Any], *, invalid_rate_threshold: float) -> bool:
    malicious_cells = int(aggregate.get("malicious_cells") or 0)
    malicious_asr = aggregate.get("malicious_asr")
    if malicious_cells > 0 and malicious_asr is None:
        return True
    invalid_rate = aggregate.get("invalid_rate")
    if invalid_rate is not None and float(invalid_rate) >= float(invalid_rate_threshold):
        return True
    return False


# ---------------------------------------------------------------------------


@dataclass
class MctsState:
    """In-memory bookkeeping for a single MCTS run."""

    iteration: int = 0
    full_eval_count: int = 0
    cheap_eval_count: int = 0
    best_full_score: Optional[float] = None
    best_full_node: Optional[str] = None
    stopped_reason: Optional[str] = None
    started_at: str = field(default_factory=utc_iso)


class MctsLoop:
    """Single-tree MCTS evolution loop."""

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
            summary_jsonl=cfg.paths.evolution_root / "logs" / "mcts_iterations.jsonl",
            loop_log=cfg.paths.evolution_root / "logs" / "mcts.log",
        )
        ensure_layout(self.paths)
        self.logger = setup_logger(cfg, self.paths)
        self.skills = SkillManager(cfg, self.paths)
        self.evaluator = InstanceEvaluator(cfg, self.paths)
        self.multi_refiner = MultiRefiner(cfg, self.paths, self.skills)
        tree_state_path = cfg.paths.evolution_root / "skills" / "tree.json"
        self.tree = Tree(tree_state_path)
        self.state = MctsState()
        self._backend_health_url = self._resolve_backend_health_url()

        self._validate_mcts_cfg()

    # ------------------------------------------------------------------
    def _validate_mcts_cfg(self) -> None:
        m = self.cfg.mcts
        missing: List[str] = []
        if not m.cheap_eval_instances_jsonl:
            missing.append("mcts.cheap_eval_instances_jsonl")
        if not m.full_eval_instances_jsonl:
            missing.append("mcts.full_eval_instances_jsonl")
        if missing:
            raise ValueError(
                "MctsLoop requires "
                + ", ".join(missing)
                + " to be set in the YAML config."
            )
        for name, p in (
            ("cheap_eval_instances_jsonl", m.cheap_eval_instances_jsonl),
            ("full_eval_instances_jsonl", m.full_eval_instances_jsonl),
        ):
            if not Path(p).is_file():  # type: ignore[arg-type]
                raise FileNotFoundError(f"{name} missing: {p}")

    def _resolve_backend_health_url(self) -> str:
        if self.cfg.mcts.backend_health_url.strip():
            return self.cfg.mcts.backend_health_url.strip()
        env = self.cfg.refiner.env or {}
        return str(env.get("ANTHROPIC_BASE_URL", "") or "").strip()

    # ---------------- Public entrypoint ----------------
    def run(self) -> MctsState:
        cfg = self.cfg
        self.logger.info("=" * 72)
        self.logger.info(
            "Starting MCTS evolution: k=%d c_uct=%.3f max_iter=%d max_full_eval=%d score_fn=%s",
            cfg.mcts.k_children,
            cfg.mcts.c_uct,
            cfg.mcts.max_iterations,
            cfg.mcts.max_full_evals,
            cfg.mcts.score_fn,
        )
        self.logger.info("cheap jsonl: %s", cfg.mcts.cheap_eval_instances_jsonl)
        self.logger.info("full  jsonl: %s", cfg.mcts.full_eval_instances_jsonl)

        # Initialization.
        root = self._ensure_root()
        if root.status == STATUS_TERMINAL:
            self.logger.info("Root already terminal; stopping.")
            self.state.stopped_reason = "root_terminal"
            return self.state

        # After init, root has full_eval done and children (cheap-evaluated) in active pool.
        # Main loop.
        for i in range(1, cfg.mcts.max_iterations + 1):
            self.state.iteration = i
            if self._check_stop():
                break
            self.logger.info("=" * 60)
            self.logger.info("MCTS iteration %d / %d", i, cfg.mcts.max_iterations)

            # Selection.
            selected = self.tree.select_active(c=cfg.mcts.c_uct)
            if selected is None:
                self.logger.warning("No active nodes remain; stopping.")
                self.state.stopped_reason = "active_pool_empty"
                break

            # Full eval.
            full_score, feedback = self._full_eval(selected, iteration=i)
            self.state.full_eval_count += 1

            # Terminal check.
            if full_score >= cfg.mcts.asr_success_score:
                self.tree.mark_terminal(selected.node_id, reason=f"full_score={full_score:.3f}")
                self.state.stopped_reason = f"terminal_reached (node={selected.node_id}, score={full_score:.3f})"
                self._track_best(selected.node_id, full_score)
                self._write_iteration_summary(
                    iteration=i,
                    selected_node_id=selected.node_id,
                    full_score=full_score,
                    feedback_aggregate=feedback.get("aggregate") or {},
                    children_ids=[],
                    child_scores=[],
                    terminal=True,
                )
                break

            self._track_best(selected.node_id, full_score)

            # Expansion + rollout.
            children, child_scores = self._expand_and_rollout(
                parent_node=selected,
                feedback=feedback,
                iteration=i,
            )
            self._write_iteration_summary(
                iteration=i,
                selected_node_id=selected.node_id,
                full_score=full_score,
                feedback_aggregate=feedback.get("aggregate") or {},
                children_ids=[c.node_id for c in children],
                child_scores=child_scores,
                terminal=False,
            )

        self._finalize()
        return self.state

    # ---------------- Initialization ----------------
    def _ensure_root(self) -> TreeNode:
        """If the tree is empty, seed root from the base skill and do initial full eval + expansion."""
        if self.tree.root_id() is not None:
            root = self.tree.get(self.tree.root_id())  # type: ignore[arg-type]
            assert root is not None
            self.logger.info("Resuming existing tree with root=%s", root.node_id)
            # If root never got its full eval (e.g. crashed mid-init), do it now.
            if root.full_eval_score is None:
                self._initial_full_eval(root)
            # If root has no children yet, expand it.
            if not root.children_ids:
                feedback = root.full_eval_feedback or {}
                self._expand_and_rollout(parent_node=root, feedback=feedback, iteration=0)
            return root

        # Fresh run: bootstrap base skill as root.
        rec = self.skills.bootstrap_initial_skill()
        root = TreeNode(
            node_id=rec.skill_id,
            parent_id=None,
            depth=0,
            created_by="initial",
            created_at=utc_iso(),
            round_id=_tag("init", 0),
            skill_path=rec.skill_path,
            bundle_path=rec.bundle_path,
        )
        self.tree.add_root(root)
        append_jsonl(
            self.paths.lineage_jsonl,
            {"event": "tree_root", "node_id": root.node_id, "ts": utc_iso()},
        )
        self.logger.info("Seeded root node: %s", root.node_id)
        self._initial_full_eval(root)
        self._expand_and_rollout(parent_node=root, feedback=root.full_eval_feedback or {}, iteration=0)
        return root

    def _initial_full_eval(self, root: TreeNode) -> None:
        score, feedback = self._full_eval(root, iteration=0)
        self.state.full_eval_count += 1
        if score >= self.cfg.mcts.asr_success_score:
            self.tree.mark_terminal(root.node_id, reason=f"root_full_score={score:.3f}")
            self.state.stopped_reason = f"terminal_at_root (score={score:.3f})"
        self._track_best(root.node_id, score)

    # ---------------- Full eval ----------------
    def _full_eval(self, node: TreeNode, *, iteration: int) -> (
        'tuple[float, Dict[str, Any]]'
    ):
        """Deploy the node's skill, run the FULL instance jsonl, persist feedback.

        Returns ``(score, feedback_dict)`` where feedback_dict already contains
        ``aggregate``, ``per_task`` and ``bucket_counts`` (from analyser).
        """
        self.logger.info("[full-eval] node=%s iteration=%d", node.node_id, iteration)
        self.skills.deploy(node.node_id)
        tag = _tag("fullE", iteration)
        payload = self._run_eval_with_resilience(
            instances_jsonl=self.cfg.mcts.full_eval_instances_jsonl,  # type: ignore[arg-type]
            tag=tag,
            mode="full",
            extra_env={"JOBS": str(self.cfg.mcts.eval_jobs)},
        )
        aggregate = payload["aggregate"]
        cells = payload["cells"]
        feedback = analyse_round(aggregate, cells)
        score = _score_from_aggregate(aggregate, self.cfg.mcts.score_fn)

        full_round_artifacts = Path(payload["artifacts_dir"])
        write_json(full_round_artifacts / f"full_feedback_{node.node_id}.json", feedback)
        write_csv(full_round_artifacts / f"full_per_task_{node.node_id}.csv", feedback["per_task"])

        self.tree.record_full_eval(
            node.node_id,
            score=score,
            feedback=feedback,
            round_id=tag,
        )
        self.tree.append_history(
            node.node_id,
            {"event": "full_eval", "iteration": iteration, "score": score, "tag": tag},
        )
        # Stash metrics on the SkillManager record too (for legacy consumers).
        self.skills.update_metrics(
            node.node_id,
            {
                "malicious_asr": aggregate.get("malicious_asr"),
                "benign_utility": aggregate.get("benign_utility"),
                "guard_read_rate": aggregate.get("guard_read_rate"),
                "guard_triggered_rate": aggregate.get("guard_triggered_rate"),
                "guard_effective_rate": aggregate.get("guard_effective_rate"),
                "full_eval_score": score,
                "full_eval_tag": tag,
            },
            round_id=tag,
        )
        self.logger.info(
            "[full-eval] node=%s score=%.4f asr=%s util=%s",
            node.node_id, score, aggregate.get("malicious_asr"), aggregate.get("benign_utility"),
        )
        return score, feedback

    # ---------------- Expansion + rollout ----------------
    def _expand_and_rollout(
        self,
        *,
        parent_node: TreeNode,
        feedback: Dict[str, Any],
        iteration: int,
    ) -> 'tuple[List[TreeNode], List[float]]':
        """Generate k children of ``parent_node`` in one refiner call, then
        cheap-eval each one and attach to the tree.

        ``parent_node`` must be a just-full-evaluated node (per spec, expansion
        always anchors at the node that was full-evaluated, not its parent).
        """
        cfg = self.cfg
        k = cfg.mcts.k_children
        round_id = _tag("expand", iteration)
        # Find the SkillRecord for the parent.
        parent_rec = self.skills.get(parent_node.node_id)
        if parent_rec is None:
            self.logger.error("Parent skill record missing for %s", parent_node.node_id)
            return [], []

        per_task = feedback.get("per_task") or []
        aggregate = feedback.get("aggregate") or {}
        bucket_counts = feedback.get("bucket_counts") or {}

        try:
            batch = self.multi_refiner.refine_batch(
                parent=parent_rec,
                round_id=round_id,
                aggregate=aggregate,
                per_task=per_task,
                bucket_counts=bucket_counts,
                k=k,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("multi_refiner failed: %s", exc)
            return [], []

        child_records: List[SkillRecord] = batch["children"]
        # Attach to tree.
        new_nodes: List[TreeNode] = []
        for rec in child_records:
            node = TreeNode(
                node_id=rec.skill_id,
                parent_id=parent_node.node_id,
                depth=parent_node.depth + 1,
                created_by="refine",
                created_at=utc_iso(),
                round_id=round_id,
                skill_path=rec.skill_path,
                bundle_path=rec.bundle_path,
            )
            self.tree.add_child(parent_node.node_id, node)
            append_jsonl(
                self.paths.lineage_jsonl,
                {
                    "event": "expand",
                    "parent_node_id": parent_node.node_id,
                    "child_node_id": rec.skill_id,
                    "iteration": iteration,
                    "ts": utc_iso(),
                },
            )
            new_nodes.append(node)

        # Rollout: cheap eval each child once.
        child_scores: List[float] = []
        for node in new_nodes:
            score = self._cheap_eval(node, iteration=iteration)
            child_scores.append(score)
        return new_nodes, child_scores

    def _cheap_eval(self, node: TreeNode, *, iteration: int) -> float:
        self.logger.info("[cheap-eval] node=%s iteration=%d", node.node_id, iteration)
        self.skills.deploy(node.node_id)
        tag = f"cheapE_{node.node_id}"
        payload = self._run_eval_with_resilience(
            instances_jsonl=self.cfg.mcts.cheap_eval_instances_jsonl,  # type: ignore[arg-type]
            tag=tag,
            mode="cheap",
            extra_env={"JOBS": str(self.cfg.mcts.eval_jobs)},
        )
        aggregate = payload["aggregate"]
        score = _score_from_aggregate(aggregate, self.cfg.mcts.score_fn)
        aux = {
            "malicious_asr": aggregate.get("malicious_asr"),
            "benign_utility": aggregate.get("benign_utility"),
            "guard_effective_rate": aggregate.get("guard_effective_rate"),
            "n_cells": aggregate.get("n_cells"),
            "iteration": iteration,
            "tag": tag,
        }
        self.tree.record_cheap_eval(node.node_id, score=score, aux=aux)
        self.tree.append_history(
            node.node_id,
            {"event": "cheap_eval", "iteration": iteration, "score": score, "aux": aux},
        )
        self.state.cheap_eval_count += 1
        self.logger.info("[cheap-eval] node=%s score=%.4f", node.node_id, score)
        return score

    def _run_eval_with_resilience(
        self,
        *,
        instances_jsonl: Path,
        tag: str,
        mode: str,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        max_attempts = 1 + max(0, int(self.cfg.mcts.runtime_transient_retries))
        payload: Optional[Dict[str, Any]] = None
        for attempt in range(max_attempts):
            run_tag = tag if attempt == 0 else f"{tag}_a{attempt}"
            payload = self.evaluator.run(
                instances_jsonl=instances_jsonl,
                tag=run_tag,
                mode=mode,
                extra_env=extra_env,
            )
            aggregate = payload.get("aggregate") or {}
            transient = _runtime_looks_transient(
                aggregate,
                invalid_rate_threshold=self.cfg.mcts.runtime_transient_invalid_rate,
            )
            if not transient:
                return payload
            if attempt + 1 >= max_attempts:
                self.logger.warning(
                    "[%s-eval] transient pattern persists after %d attempt(s); keep last payload",
                    mode,
                    max_attempts,
                )
                return payload
            delay = _backoff_seconds(
                attempt,
                self.cfg.mcts.runtime_retry_base_sec,
                self.cfg.mcts.runtime_retry_max_sec,
            )
            self.logger.warning(
                "[%s-eval] transient runtime pattern (invalid_rate=%s malicious_asr=%s); "
                "health_url=%s delay=%.1fs attempt=%d/%d",
                mode,
                aggregate.get("invalid_rate"),
                aggregate.get("malicious_asr"),
                self._backend_health_url or "(unset)",
                delay,
                attempt + 1,
                max_attempts,
            )
            if self._backend_health_url:
                _wait_for_backend(
                    self._backend_health_url,
                    timeout_sec=self.cfg.mcts.backend_health_timeout_sec,
                    poll_sec=self.cfg.mcts.backend_health_poll_sec,
                    max_wait_sec=self.cfg.mcts.backend_health_max_wait_sec,
                )
            time.sleep(delay)
        assert payload is not None
        return payload

    # ---------------- Stopping + reporting ----------------
    def _check_stop(self) -> bool:
        s = self.state
        cfg = self.cfg
        if s.full_eval_count >= cfg.mcts.max_full_evals:
            s.stopped_reason = s.stopped_reason or "max_full_evals_reached"
            self.logger.info("Stopping: %s", s.stopped_reason)
            return True
        if s.stopped_reason:
            return True
        if not self.tree.active():
            s.stopped_reason = "active_pool_empty"
            self.logger.info("Stopping: %s", s.stopped_reason)
            return True
        return False

    def _track_best(self, node_id: str, score: float) -> None:
        if self.state.best_full_score is None or score > self.state.best_full_score:
            self.state.best_full_score = score
            self.state.best_full_node = node_id

    def _write_iteration_summary(
        self,
        *,
        iteration: int,
        selected_node_id: str,
        full_score: float,
        feedback_aggregate: Dict[str, Any],
        children_ids: List[str],
        child_scores: List[float],
        terminal: bool,
    ) -> None:
        record = {
            "iteration": iteration,
            "selected_node_id": selected_node_id,
            "full_score": full_score,
            "aggregate": feedback_aggregate,
            "children_ids": children_ids,
            "child_cheap_scores": child_scores,
            "terminal": terminal,
            "best_full_score_so_far": self.state.best_full_score,
            "best_full_node_so_far": self.state.best_full_node,
            "n_active": len(self.tree.active()),
            "ts": utc_iso(),
        }
        append_jsonl(self.paths.summary_jsonl, record)
        self.tree.write_snapshot_md(
            self.paths.logs_rounds / f"iter_{iteration:03d}.md",
            title=f"MCTS iteration {iteration} tree snapshot",
        )

    def _finalize(self) -> None:
        best = self.tree.best_full_evaluated()
        state_dump = {
            "iteration": self.state.iteration,
            "full_eval_count": self.state.full_eval_count,
            "cheap_eval_count": self.state.cheap_eval_count,
            "best_full_node": self.state.best_full_node,
            "best_full_score": self.state.best_full_score,
            "stopped_reason": self.state.stopped_reason or "loop_completed",
            "started_at": self.state.started_at,
            "finished_at": utc_iso(),
            "tree_best_node": best.node_id if best else None,
            "tree_snapshot": self.tree.snapshot(),
        }
        write_json(self.paths.logs / "mcts_state.json", state_dump)
        self.tree.write_snapshot_md(
            self.paths.logs / "mcts_tree_final.md",
            title="MCTS final tree snapshot",
        )
        self.logger.info(
            "MCTS finished: iter=%d full=%d cheap=%d best_score=%s best_node=%s stop=%s",
            self.state.iteration,
            self.state.full_eval_count,
            self.state.cheap_eval_count,
            self.state.best_full_score,
            self.state.best_full_node,
            self.state.stopped_reason,
        )
