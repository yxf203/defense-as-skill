"""Typed wrapper around the YAML configuration file."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "PyYAML is required for the evolution loop. Install via `pip install pyyaml`."
    ) from exc


DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "default.yaml"
)


@dataclass
class PathsCfg:
    skill_guard_root: Path
    original_skill_root: Path
    skill_bundle_name: str
    active_deploy_root: Path
    evolution_root: Path


@dataclass
class EvaluationCfg:
    subset: str = "smoke"
    bench_extra_args: List[str] = field(default_factory=list)
    force_python_fallback: bool = False
    per_cell_timeout_sec: int = 1800
    round_timeout_sec: int = 7200


@dataclass
class LoopCfg:
    max_rounds: int = 3
    asr_target: float = 0.1
    patience: int = 3
    min_improvement: float = 0.05
    max_total_skills: int = 12


@dataclass
class PoolBalanceCfg:
    asr_weight: float = 1.0
    utility_weight: float = 1.0
    confirmation_penalty: float = 0.1


@dataclass
class PoolSelectionCfg:
    strategy: str = "ucb"
    exploration_weight: float = 0.35
    cold_start_first: bool = True
    require_complete_metrics: bool = True


@dataclass
class PoolCfg:
    max_size: int = 5
    balance: PoolBalanceCfg = field(default_factory=PoolBalanceCfg)
    selection: PoolSelectionCfg = field(default_factory=PoolSelectionCfg)
    roles: List[str] = field(
        default_factory=lambda: [
            "best_safe",
            "best_balanced",
            "best_low_friction",
            "baseline_anchor",
        ]
    )


@dataclass
class RefinerCfg:
    model: str = ""
    permission_mode: str = "bypassPermissions"
    timeout_sec: int = 600
    env: Dict[str, str] = field(default_factory=dict)
    isolate_workspace: bool = True


@dataclass
class LoggingCfg:
    console_level: str = "INFO"
    file_level: str = "DEBUG"
    keep_transcript: bool = True


@dataclass
class MctsCfg:
    """Configuration for the MCTS-style evolution loop (``mcts_loop.py``)."""

    # Instance jsonls that drive cheap/full eval passes.
    cheap_eval_instances_jsonl: Optional[Path] = None
    full_eval_instances_jsonl: Optional[Path] = None
    # Number of children produced per expansion (spec says 3-5; default 3).
    k_children: int = 3
    # UCT exploration constant c in Q + c * sqrt(log N_parent / n).
    c_uct: float = 0.7
    # Hard cap on the outer MCTS iterations (each iter = 1 full eval + 1 expansion).
    max_iterations: int = 10
    # Cap on total full evals across the whole run (budget guard).
    max_full_evals: int = 10
    # Terminal condition: node wins if its full_eval_score >= asr_success_score.
    # Score is ``1 - malicious_asr`` by default (see score_fn below).
    asr_success_score: float = 0.9
    # Which function maps an eval aggregate -> scalar score for UCT/terminal.
    # Supported: "asr_inv" (1 - malicious_asr), "guard_effective_rate".
    score_fn: str = "asr_inv"
    # Jobs for the underlying subset shell script (per-instance concurrency).
    eval_jobs: int = 1
    # Runtime resilience knobs: retry whole eval pass when aggregate suggests
    # transient backend failure.
    runtime_transient_retries: int = 3
    runtime_retry_base_sec: float = 15.0
    runtime_retry_max_sec: float = 300.0
    runtime_transient_invalid_rate: float = 0.85
    # Backend health probe before retrying a transient failure.
    backend_health_url: str = ""
    backend_health_poll_sec: float = 10.0
    backend_health_timeout_sec: float = 5.0
    backend_health_max_wait_sec: float = 900.0


@dataclass
class EvolutionConfig:
    paths: PathsCfg
    evaluation: EvaluationCfg
    loop: LoopCfg
    pool: PoolCfg
    refiner: RefinerCfg
    logging: LoggingCfg
    mcts: MctsCfg = field(default_factory=MctsCfg)
    raw: Dict[str, Any] = field(default_factory=dict)


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge two dicts (override wins on scalar conflicts)."""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _as_path(v: Any) -> Path:
    return Path(os.path.expanduser(str(v))).resolve()


def load_config(path: Optional[Path] = None, overrides: Optional[Dict[str, Any]] = None) -> EvolutionConfig:
    """Load YAML config, applying optional override dict on top."""
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        data: Dict[str, Any] = yaml.safe_load(f) or {}
    if path and Path(path) != DEFAULT_CONFIG_PATH:
        with open(path, "r", encoding="utf-8") as f:
            user_data = yaml.safe_load(f) or {}
        data = _merge(data, user_data)
    if overrides:
        data = _merge(data, overrides)

    p = data.get("paths", {}) or {}
    paths_cfg = PathsCfg(
        skill_guard_root=_as_path(p["skill_guard_root"]),
        original_skill_root=_as_path(p["original_skill_root"]),
        skill_bundle_name=str(p.get("skill_bundle_name", "skill-sonar")),
        active_deploy_root=_as_path(p["active_deploy_root"]),
        evolution_root=_as_path(p["evolution_root"]),
    )
    e = data.get("evaluation", {}) or {}
    eval_cfg = EvaluationCfg(
        subset=str(e.get("subset", "smoke")),
        bench_extra_args=list(e.get("bench_extra_args", []) or []),
        force_python_fallback=bool(e.get("force_python_fallback", False)),
        per_cell_timeout_sec=int(e.get("per_cell_timeout_sec", 1800)),
        round_timeout_sec=int(e.get("round_timeout_sec", 7200)),
    )
    l = data.get("loop", {}) or {}
    loop_cfg = LoopCfg(
        max_rounds=int(l.get("max_rounds", 3)),
        asr_target=float(l.get("asr_target", 0.1)),
        patience=int(l.get("patience", 3)),
        min_improvement=float(l.get("min_improvement", 0.05)),
        max_total_skills=int(l.get("max_total_skills", 12)),
    )
    pool = data.get("pool", {}) or {}
    balance = pool.get("balance", {}) or {}
    pool_cfg = PoolCfg(
        max_size=int(pool.get("max_size", 5)),
        balance=PoolBalanceCfg(
            asr_weight=float(balance.get("asr_weight", 1.0)),
            utility_weight=float(balance.get("utility_weight", 1.0)),
            confirmation_penalty=float(balance.get("confirmation_penalty", 0.1)),
        ),
        selection=PoolSelectionCfg(
            strategy=str((pool.get("selection", {}) or {}).get("strategy", "ucb")),
            exploration_weight=float(
                (pool.get("selection", {}) or {}).get("exploration_weight", 0.35)
            ),
            cold_start_first=bool(
                (pool.get("selection", {}) or {}).get("cold_start_first", True)
            ),
            require_complete_metrics=bool(
                (pool.get("selection", {}) or {}).get("require_complete_metrics", True)
            ),
        ),
        roles=list(pool.get("roles", []) or []),
    )
    r = data.get("refiner", {}) or {}
    refiner_cfg = RefinerCfg(
        model=str(r.get("model", "") or ""),
        permission_mode=str(r.get("permission_mode", "bypassPermissions")),
        timeout_sec=int(r.get("timeout_sec", 600)),
        env={k: str(v) for k, v in (r.get("env", {}) or {}).items()},
        isolate_workspace=bool(r.get("isolate_workspace", True)),
    )
    log = data.get("logging", {}) or {}
    log_cfg = LoggingCfg(
        console_level=str(log.get("console_level", "INFO")),
        file_level=str(log.get("file_level", "DEBUG")),
        keep_transcript=bool(log.get("keep_transcript", True)),
    )
    m = data.get("mcts", {}) or {}

    def _opt_path(v: Any) -> Optional[Path]:
        return _as_path(v) if v else None

    mcts_cfg = MctsCfg(
        cheap_eval_instances_jsonl=_opt_path(m.get("cheap_eval_instances_jsonl")),
        full_eval_instances_jsonl=_opt_path(m.get("full_eval_instances_jsonl")),
        k_children=int(m.get("k_children", 3)),
        c_uct=float(m.get("c_uct", 0.7)),
        max_iterations=int(m.get("max_iterations", 10)),
        max_full_evals=int(m.get("max_full_evals", 10)),
        asr_success_score=float(m.get("asr_success_score", 0.9)),
        score_fn=str(m.get("score_fn", "asr_inv")),
        eval_jobs=int(m.get("eval_jobs", 1)),
        runtime_transient_retries=max(0, int(m.get("runtime_transient_retries", 3))),
        runtime_retry_base_sec=float(m.get("runtime_retry_base_sec", 15.0)),
        runtime_retry_max_sec=float(m.get("runtime_retry_max_sec", 300.0)),
        runtime_transient_invalid_rate=float(m.get("runtime_transient_invalid_rate", 0.85)),
        backend_health_url=str(m.get("backend_health_url", "") or ""),
        backend_health_poll_sec=float(m.get("backend_health_poll_sec", 10.0)),
        backend_health_timeout_sec=float(m.get("backend_health_timeout_sec", 5.0)),
        backend_health_max_wait_sec=float(m.get("backend_health_max_wait_sec", 900.0)),
    )
    return EvolutionConfig(
        paths=paths_cfg,
        evaluation=eval_cfg,
        loop=loop_cfg,
        pool=pool_cfg,
        refiner=refiner_cfg,
        logging=log_cfg,
        mcts=mcts_cfg,
        raw=data,
    )
