"""CLI entrypoint for the MCTS-style skill-evolution loop."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from .config import DEFAULT_CONFIG_PATH, EvolutionConfig, load_config
from .mcts_loop import MctsLoop


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="skill-evolution-mcts",
        description=(
            "MCTS-style skill-evolution loop: cheap-eval rollouts + UCT selection + "
            "full-eval promotion + one-shot multi-variant refinement."
        ),
    )
    p.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to YAML config (default: config/default.yaml). "
        "For MCTS the `mcts` section must point at the cheap/full jsonl files.",
    )
    p.add_argument("--cheap-jsonl", type=Path, help="Override mcts.cheap_eval_instances_jsonl.")
    p.add_argument("--full-jsonl", type=Path, help="Override mcts.full_eval_instances_jsonl.")
    p.add_argument("--k", type=int, help="Override mcts.k_children (children per expansion).")
    p.add_argument("--c-uct", type=float, help="Override mcts.c_uct (exploration constant).")
    p.add_argument("--max-iterations", type=int, help="Override mcts.max_iterations.")
    p.add_argument("--max-full-evals", type=int, help="Override mcts.max_full_evals.")
    p.add_argument(
        "--asr-success-score",
        type=float,
        help="Override mcts.asr_success_score (terminal threshold, default 0.9 == asr<=0.1).",
    )
    p.add_argument("--score-fn", choices=("asr_inv", "guard_effective_rate"))
    p.add_argument("--refiner-model", help="Override refiner.model.")
    p.add_argument("--eval-jobs", type=int, help="Override mcts.eval_jobs (subset concurrency).")
    return p


def _apply_overrides(cfg: EvolutionConfig, args: argparse.Namespace) -> EvolutionConfig:
    if args.cheap_jsonl is not None:
        cfg.mcts.cheap_eval_instances_jsonl = args.cheap_jsonl.resolve()
    if args.full_jsonl is not None:
        cfg.mcts.full_eval_instances_jsonl = args.full_jsonl.resolve()
    if args.k is not None:
        cfg.mcts.k_children = args.k
    if args.c_uct is not None:
        cfg.mcts.c_uct = args.c_uct
    if args.max_iterations is not None:
        cfg.mcts.max_iterations = args.max_iterations
    if args.max_full_evals is not None:
        cfg.mcts.max_full_evals = args.max_full_evals
    if args.asr_success_score is not None:
        cfg.mcts.asr_success_score = args.asr_success_score
    if args.score_fn is not None:
        cfg.mcts.score_fn = args.score_fn
    if args.refiner_model is not None:
        cfg.refiner.model = args.refiner_model
    if args.eval_jobs is not None:
        cfg.mcts.eval_jobs = args.eval_jobs
    return cfg


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = load_config(args.config)
    cfg = _apply_overrides(cfg, args)
    loop = MctsLoop(cfg)
    state = loop.run()
    return 0 if state.stopped_reason != "active_pool_empty" or state.full_eval_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
