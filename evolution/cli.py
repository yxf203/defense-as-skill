"""CLI entry point for the skill-evolution loop."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .config import DEFAULT_CONFIG_PATH, EvolutionConfig, load_config
from .loop import EvolutionLoop


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="skill-evolution",
        description="Single-guard-skill evolution loop over the bundled eval framework.",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to YAML config (defaults to bundled default.yaml).",
    )
    p.add_argument(
        "--subset",
        # ``combined`` runs malicious + benign legs sequentially; ``benign`` is
        # a utility-only sweep. Both rely on the matching shell script under
        # ``skillGuard/subsets/``.
        choices=("smoke", "coverage", "benign", "combined"),
        help="Override subset to evaluate on.",
    )
    p.add_argument(
        "--max-rounds",
        type=int,
        help="Override max number of rounds in this invocation.",
    )
    p.add_argument(
        "--asr-target",
        type=float,
        help="Override stopping ASR target (0.0-1.0).",
    )
    p.add_argument(
        "--patience",
        type=int,
        help="Override `no-improvement` patience in rounds.",
    )
    p.add_argument(
        "--refiner-model",
        help="Override claude-code model alias used by the refiner (e.g. haiku).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip benchmark + refiner invocation; exercise plumbing only.",
    )
    p.add_argument(
        "--skip-refine",
        action="store_true",
        help="Run the evaluator + analyzer + pool policy, but do not invoke the claude-code refiner.",
    )
    p.add_argument(
        "--bootstrap-only",
        action="store_true",
        help="Seed the initial skill + backup + first deploy, then exit without running the loop.",
    )
    p.add_argument(
        "--simulate-round",
        type=Path,
        help="Path to a synthetic subset JSONL used to simulate one round (for plumbing tests).",
    )
    return p


def _apply_overrides(cfg: EvolutionConfig, args: argparse.Namespace) -> EvolutionConfig:
    """Apply CLI overrides directly onto the dataclass config."""
    if args.subset:
        cfg.evaluation.subset = args.subset
    if args.max_rounds is not None:
        cfg.loop.max_rounds = args.max_rounds
    if args.asr_target is not None:
        cfg.loop.asr_target = args.asr_target
    if args.patience is not None:
        cfg.loop.patience = args.patience
    if args.refiner_model is not None:
        cfg.refiner.model = args.refiner_model
    return cfg


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = load_config(args.config)
    cfg = _apply_overrides(cfg, args)

    loop = EvolutionLoop(cfg)
    loop.skip_refine = bool(args.skip_refine)
    if args.simulate_round is not None:
        loop.simulate_round_jsonl = Path(args.simulate_round).resolve()
    if args.bootstrap_only:
        rec = loop.skills.bootstrap_initial_skill()
        loop.skills.deploy(rec.skill_id)
        loop.logger.info("Bootstrap-only mode: initial skill %s ready.", rec.skill_id)
        return 0

    state = loop.run(dry_run=args.dry_run, max_rounds=cfg.loop.max_rounds)
    return 0 if state.stopped_reason != "no_parent_skill" else 1


if __name__ == "__main__":
    sys.exit(main())
