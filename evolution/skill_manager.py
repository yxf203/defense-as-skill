"""Skill versioning, pool management, deployment + backup.

Skill storage on disk::

    skill-evolution/
        skills/
            active/
                <skill_id>/
                    skill-sonar/        # the full bundle used by the eval framework
                    metadata.json       # skill id, parent, round, created_by, metrics ...
            backup/
                <skill_id>/
                    skill-sonar/
                    metadata.json

The *deployed* copy — i.e. what ``lib_agent`` actually picks up at runtime — lives at::

    <skillGuard>/safetySkill/skill-sonar/

which is the **first** location ``lib_agent`` probes.  That slot is empty in the repo, so
dropping our active skill there is a drop-in override that leaves the sibling
``safetySkill/skill-sonar`` under ``safety-benchmark/`` untouched.
"""
from __future__ import annotations

import json
import logging
import math
import shutil
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import EvolutionConfig
from .logger_utils import append_jsonl, write_json, utc_iso
from .paths import WorkspacePaths

logger = logging.getLogger("evolution.skill_manager")


# -------------------- Data classes --------------------

@dataclass
class SkillRecord:
    skill_id: str
    parent_skill_id: Optional[str]
    created_at: str
    created_by: str               # "initial" | "refine" | "repair" | "manual"
    round_id: Optional[str]
    skill_path: str               # directory under skills/active or skills/backup
    bundle_path: str              # .../<skill_id>/skill-sonar
    status: str = "active"        # "active" | "retired" | "baseline"
    role: Optional[str] = None    # best_safe | best_balanced | best_low_friction | baseline_anchor
    metrics: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SkillRecord":
        return cls(
            skill_id=d["skill_id"],
            parent_skill_id=d.get("parent_skill_id"),
            created_at=d.get("created_at") or utc_iso(),
            created_by=d.get("created_by") or "manual",
            round_id=d.get("round_id"),
            skill_path=d.get("skill_path") or "",
            bundle_path=d.get("bundle_path") or "",
            status=d.get("status") or "active",
            role=d.get("role"),
            metrics=d.get("metrics") or {},
            notes=d.get("notes") or [],
        )


# -------------------- Pool state --------------------

def _new_skill_id(kind: str = "skill") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{kind}_{stamp}_{suffix}"


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


class SkillManager:
    """Owns on-disk skill storage + active deployment slot."""

    def __init__(self, cfg: EvolutionConfig, paths: WorkspacePaths):
        self.cfg = cfg
        self.paths = paths
        self._pool_path = paths.pool_state
        self._records: Dict[str, SkillRecord] = {}
        self._load_pool()

    # ---------------- Pool IO ----------------

    def _load_pool(self) -> None:
        if not self._pool_path.is_file():
            self._records = {}
            return
        try:
            data = json.loads(self._pool_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._records = {}
            return
        self._records = {
            sid: SkillRecord.from_dict(r) for sid, r in (data.get("records") or {}).items()
        }
        # Paths stored by a previous run may be anchored at a different evolution_root
        # (e.g. host vs docker container). Re-anchor any that don't exist here.
        changed = self._reanchor_paths()
        if changed:
            self._save_pool()

    def _reanchor_paths(self) -> bool:
        """Rewrite stored skill_path/bundle_path when the on-disk layout has moved.

        Keeps backwards-compat when the pool is first created on the host and then
        reopened inside a container where evolution_root differs (e.g. /mnt/... -> /evolution).
        Returns True if any record was rewritten.
        """
        def _tail_after(segments: Path, anchor: str) -> Optional[Path]:
            parts = segments.parts
            if anchor not in parts:
                return None
            idx = parts.index(anchor)
            return Path(*parts[idx + 1 :]) if idx + 1 < len(parts) else None

        changed = False
        for rec in self._records.values():
            for attr, base in (
                ("skill_path", None),
                ("bundle_path", None),
            ):
                stored = getattr(rec, attr) or ""
                if not stored:
                    continue
                p = Path(stored)
                if p.exists():
                    continue
                # Try to re-anchor under paths.skills_active or paths.skills_backup.
                new_path: Optional[Path] = None
                for anchor_name, anchor_dir in (
                    ("active", self.paths.skills_active),
                    ("backup", self.paths.skills_backup),
                ):
                    tail = _tail_after(p, anchor_name)
                    if tail is not None:
                        new_path = anchor_dir / tail
                        break
                if new_path is not None and new_path.exists():
                    setattr(rec, attr, str(new_path))
                    changed = True
        return changed

    def _save_pool(self) -> None:
        payload = {
            "updated_at": utc_iso(),
            "records": {sid: rec.to_dict() for sid, rec in self._records.items()},
        }
        write_json(self._pool_path, payload)

    # ---------------- Public helpers ----------------

    def records(self) -> Dict[str, SkillRecord]:
        return dict(self._records)

    def active(self) -> List[SkillRecord]:
        return [r for r in self._records.values() if r.status == "active"]

    def retired(self) -> List[SkillRecord]:
        return [r for r in self._records.values() if r.status == "retired"]

    def get(self, skill_id: str) -> Optional[SkillRecord]:
        return self._records.get(skill_id)

    # ---------------- Bootstrap ----------------

    def bootstrap_initial_skill(self) -> SkillRecord:
        """Copy the pristine skill into the active pool (once)."""
        for rec in self._records.values():
            if rec.created_by == "initial":
                logger.info("Initial skill already bootstrapped: %s", rec.skill_id)
                return rec

        src_bundle = self.cfg.paths.original_skill_root / self.cfg.paths.skill_bundle_name
        if not src_bundle.is_dir():
            raise FileNotFoundError(
                f"Cannot bootstrap: original skill bundle not found at {src_bundle}"
            )

        # 1) Always preserve pristine copy under backup/initial-<timestamp>
        pristine_copy = (
            self.paths.skills_backup
            / f"initial-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
            / self.cfg.paths.skill_bundle_name
        )
        _copy_tree(src_bundle, pristine_copy)
        logger.info("Pristine skill preserved at %s", pristine_copy)

        # 2) Create the first active skill entry.
        skill_id = _new_skill_id("initial")
        skill_dir = self.paths.skills_active / skill_id
        bundle_dir = skill_dir / self.cfg.paths.skill_bundle_name
        _copy_tree(src_bundle, bundle_dir)

        rec = SkillRecord(
            skill_id=skill_id,
            parent_skill_id=None,
            created_at=utc_iso(),
            created_by="initial",
            round_id=None,
            skill_path=str(skill_dir),
            bundle_path=str(bundle_dir),
            status="active",
            role="baseline_anchor",
            metrics={},
            notes=[f"seeded from {src_bundle}"],
        )
        self._records[skill_id] = rec
        self._save_pool()
        self._write_skill_metadata(rec)
        append_jsonl(
            self.paths.lineage_jsonl,
            {
                "event": "bootstrap",
                "skill_id": skill_id,
                "parent_skill_id": None,
                "created_at": rec.created_at,
                "bundle_path": str(bundle_dir),
                "pristine_backup": str(pristine_copy),
            },
        )
        return rec

    # ---------------- Deploy to framework ----------------

    def deploy(self, skill_id: str) -> Path:
        """Plant *skill_id*'s bundle into the framework's expected slot and return it."""
        rec = self._records.get(skill_id)
        if not rec:
            raise KeyError(f"Unknown skill_id {skill_id}")
        src = Path(rec.bundle_path)
        if not src.is_dir():
            raise FileNotFoundError(f"Bundle missing for {skill_id}: {src}")
        dest = self.cfg.paths.active_deploy_root / self.cfg.paths.skill_bundle_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() or dest.is_symlink():
            if dest.is_symlink():
                dest.unlink()
            else:
                shutil.rmtree(dest)
        shutil.copytree(src, dest)
        # Marker so humans know what's currently deployed.
        marker = dest.parent / ".active-skill.json"
        marker.write_text(
            json.dumps(
                {
                    "skill_id": skill_id,
                    "deployed_at": utc_iso(),
                    "source_bundle": str(src),
                    "deployed_path": str(dest),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        logger.info("Deployed skill %s -> %s", skill_id, dest)
        return dest

    # ---------------- Child creation ----------------

    def make_child_from(self, parent_id: str, *, round_id: str, created_by: str = "refine") -> SkillRecord:
        """Create a fresh skill dir that starts as an exact copy of *parent_id*."""
        parent = self._records.get(parent_id)
        if not parent:
            raise KeyError(f"Unknown parent skill {parent_id}")
        src = Path(parent.bundle_path)
        if not src.is_dir():
            raise FileNotFoundError(f"Parent bundle missing: {src}")
        child_id = _new_skill_id(created_by)
        child_dir = self.paths.skills_active / child_id
        bundle_dir = child_dir / self.cfg.paths.skill_bundle_name
        _copy_tree(src, bundle_dir)
        parent_skill_dir = Path(parent.skill_path)
        parent_notes = parent_skill_dir / "REFINE_NOTES.md"
        child_notes = child_dir / "REFINE_NOTES.md"
        if parent_notes.is_file():
            shutil.copy2(parent_notes, child_notes)
        rec = SkillRecord(
            skill_id=child_id,
            parent_skill_id=parent_id,
            created_at=utc_iso(),
            created_by=created_by,
            round_id=round_id,
            skill_path=str(child_dir),
            bundle_path=str(bundle_dir),
            status="active",
            role=None,
            metrics={},
            notes=[f"forked from {parent_id}"],
        )
        self._records[child_id] = rec
        self._save_pool()
        self._write_skill_metadata(rec)
        append_jsonl(
            self.paths.lineage_jsonl,
            {
                "event": "fork",
                "skill_id": child_id,
                "parent_skill_id": parent_id,
                "created_at": rec.created_at,
                "round_id": round_id,
                "bundle_path": str(bundle_dir),
            },
        )
        return rec

    # ---------------- Metrics + retirement ----------------

    def update_metrics(self, skill_id: str, metrics: Dict[str, Any], *, round_id: Optional[str] = None) -> None:
        rec = self._records.get(skill_id)
        if not rec:
            return
        merged = {**rec.metrics, **metrics, "last_round": round_id, "evaluated_at": utc_iso()}
        if metrics.get("malicious_asr") is not None:
            merged["n_evaluations"] = int(rec.metrics.get("n_evaluations") or 0) + 1
            merged["last_eval_kind"] = "benchmark_round"
        rec.metrics = merged
        self._records[skill_id] = rec
        self._save_pool()
        self._write_skill_metadata(rec)

    def retire(self, skill_id: str, *, reason: str, round_id: Optional[str] = None) -> Optional[Path]:
        rec = self._records.get(skill_id)
        if not rec or rec.status == "retired":
            return None
        src = Path(rec.skill_path)
        backup_dir = self.paths.skills_backup / rec.skill_id
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        if src.is_dir():
            shutil.copytree(src, backup_dir)
            shutil.rmtree(src)
        rec.status = "retired"
        rec.skill_path = str(backup_dir)
        rec.bundle_path = str(backup_dir / self.cfg.paths.skill_bundle_name)
        rec.notes.append(f"retired: {reason} (round={round_id})")
        self._records[skill_id] = rec
        self._save_pool()
        self._write_skill_metadata(rec)
        append_jsonl(
            self.paths.lineage_jsonl,
            {
                "event": "retire",
                "skill_id": skill_id,
                "reason": reason,
                "moved_to": str(backup_dir),
                "round_id": round_id,
                "retired_at": utc_iso(),
            },
        )
        logger.info("Retired skill %s -> %s (%s)", skill_id, backup_dir, reason)
        return backup_dir

    def set_role(self, skill_id: str, role: Optional[str]) -> None:
        rec = self._records.get(skill_id)
        if not rec:
            return
        rec.role = role
        self._records[skill_id] = rec
        self._save_pool()
        self._write_skill_metadata(rec)

    def _write_skill_metadata(self, rec: SkillRecord) -> None:
        # Per-skill metadata file (in the skill dir if active, in backup dir if retired).
        target_dir = Path(rec.skill_path)
        target_dir.mkdir(parents=True, exist_ok=True)
        write_json(target_dir / "metadata.json", rec.to_dict())
        # Mirror into logs/skills for easy browsing.
        write_json(self.paths.logs_skills / f"{rec.skill_id}.json", rec.to_dict())

    # ---------------- Pool selection ----------------

    def score_for_balance(self, rec: SkillRecord) -> float:
        """Higher = better. Uses self.cfg.pool.balance weights on rec.metrics."""
        m = rec.metrics or {}
        asr = float(m.get("malicious_asr") or 0.0)
        util = float(m.get("benign_utility") or 0.0)
        conf = float(m.get("mean_confirmation") or 0.0)
        w = self.cfg.pool.balance
        return (
            -w.asr_weight * asr
            + w.utility_weight * util
            - w.confirmation_penalty * conf
        )

    def apply_pool_policy(self, *, round_id: str) -> Dict[str, Any]:
        """Re-assign role labels + retire anything beyond ``max_size`` active skills."""
        active = [r for r in self._records.values() if r.status == "active" and r.metrics]
        if not active:
            return {"roles": {}, "retired": []}

        # Candidate picks.
        best_safe = min(active, key=lambda r: float(r.metrics.get("malicious_asr") or 1.0))
        best_balanced = max(active, key=self.score_for_balance)
        low_friction_pool = [r for r in active if float(r.metrics.get("malicious_asr") or 1.0) <= 0.5]
        if not low_friction_pool:
            low_friction_pool = active
        best_low_friction = min(
            low_friction_pool,
            key=lambda r: float(r.metrics.get("mean_confirmation") or 999.0),
        )
        baseline = next((r for r in active if r.created_by == "initial"), active[-1])

        # Reset roles so we don't end with stale tags.
        for r in self._records.values():
            if r.status == "active":
                r.role = None
        # Assign one skill per role; ties collapse onto the same record.
        best_safe.role = "best_safe"
        best_balanced.role = best_balanced.role or "best_balanced"
        best_low_friction.role = best_low_friction.role or "best_low_friction"
        baseline.role = baseline.role or "baseline_anchor"

        keep_ids = {best_safe.skill_id, best_balanced.skill_id, best_low_friction.skill_id, baseline.skill_id}
        # Keep the newest evaluated skill too (so fresh refinements are not immediately dropped).
        newest = max(active, key=lambda r: r.created_at)
        keep_ids.add(newest.skill_id)

        max_size = max(self.cfg.pool.max_size, len(keep_ids))
        extras = [r for r in active if r.skill_id not in keep_ids]
        extras.sort(key=lambda r: self.score_for_balance(r))  # ascending; worst first
        retired: List[Dict[str, Any]] = []
        # How many slots are left beyond the protected set.
        spare = max(0, max_size - len(keep_ids))
        dropped = extras[: max(0, len(extras) - spare)]
        for rec in dropped:
            backup = self.retire(
                rec.skill_id,
                reason="pool eviction (pool size cap)",
                round_id=round_id,
            )
            retired.append(
                {
                    "skill_id": rec.skill_id,
                    "backup_path": str(backup) if backup else "",
                    "reason": "pool eviction (size cap)",
                }
            )

        self._save_pool()
        roles = {
            rec.skill_id: rec.role for rec in self._records.values() if rec.status == "active" and rec.role
        }
        return {"roles": roles, "retired": retired}

    def pool_snapshot(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for rec in self._records.values():
            m = rec.metrics or {}
            out.append(
                {
                    "skill_id": rec.skill_id,
                    "role": rec.role,
                    "status": rec.status,
                    "asr": m.get("malicious_asr"),
                    "utility": m.get("benign_utility"),
                    "confirmation": m.get("mean_confirmation"),
                    "score": self.score_for_balance(rec) if m else None,
                    "parent": rec.parent_skill_id,
                    "created_by": rec.created_by,
                    "created_at": rec.created_at,
                }
            )
        return out

    def choose_parent_for_next_round(self) -> Optional[SkillRecord]:
        """Pick the parent skill used by the next refinement round."""
        active = [r for r in self._records.values() if r.status == "active"]
        if not active:
            return None

        sel = self.cfg.pool.selection
        # Cold start: force unseen active skills to be selected at least once.
        if sel.cold_start_first:
            cold = [r for r in active if int((r.metrics or {}).get("n_evaluations") or 0) == 0]
            if cold:
                return min(cold, key=lambda r: r.created_at)

        metric_ready = [r for r in active if self._has_selection_metrics(r, require_complete=sel.require_complete_metrics)]
        if not metric_ready:
            # Fall back to any active even if unmetriced (initial or recovery scenario).
            return max(active, key=lambda r: r.created_at)

        if sel.strategy.lower() != "ucb":
            return max(metric_ready, key=self.score_for_balance)

        total_evals = sum(max(1, int((r.metrics or {}).get("n_evaluations") or 0)) for r in metric_ready)
        return max(metric_ready, key=lambda r: self._score_for_ucb(r, total_evals=total_evals))

    def _has_selection_metrics(self, rec: SkillRecord, *, require_complete: bool) -> bool:
        m = rec.metrics or {}
        if m.get("malicious_asr") is None:
            return False
        if not require_complete:
            return True
        return m.get("benign_utility") is not None and m.get("mean_confirmation") is not None

    def _score_for_ucb(self, rec: SkillRecord, *, total_evals: int) -> float:
        base = self.score_for_balance(rec)
        n_i = max(1, int((rec.metrics or {}).get("n_evaluations") or 1))
        explore = float(self.cfg.pool.selection.exploration_weight)
        bonus = explore * math.sqrt(max(0.0, math.log(max(2, total_evals)) / n_i))
        return base + bonus
