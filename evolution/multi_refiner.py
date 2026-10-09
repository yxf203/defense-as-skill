"""One-shot multi-variant refiner.

The MCTS expansion step asks the refiner for **k=3–5 distinct, local edits** of
the same parent skill in a single ``claude -p`` invocation.  Doing it in one
invocation (rather than k separate calls) lets the model compare variants
against each other, keep them mutually distinct, and share the diagnosis cost
once across the batch.

Mechanics
---------
1. :meth:`SkillManager.make_child_from` is called ``k`` times up front so there
   are ``k`` empty child bundles on disk pre-seeded with the parent's files.
2. A single ``claude -p`` command is spawned with ``--add-dir`` pointing at all
   ``k`` child bundle directories.  The prompt is fed on **stdin** (not argv) so
   large eval JSON does not hit ``ARG_MAX``.  The prompt enumerates variants and asks the
   model to apply a **different small local edit** to each one, each targeting
   a different failure pattern from the full-eval feedback.
3. The model also writes a per-variant ``REFINE_NOTES.md`` into each child's
   skill directory with the diagnosis + edit summary.
4. We capture the transcript once per round, then per-variant we snapshot
   which files changed so downstream logging can attribute edits correctly.

The refiner does **not** evaluate the children — that is the MCTS loop's job
(cheap eval → UCT → later full eval).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import EvolutionConfig
from .logger_utils import utc_iso, write_json
from .paths import WorkspacePaths
from .refiner import (
    REFINE_SYSTEM_BRIEF,
    PROMPT_JSON_SEPARATORS,
    _apply_cc_to_anthropic,
    _claude_permission_mode_cli_args,
    _compact_per_task_for_prompt,
    _detect_nested_skill_bundle,
    _extract_final_text,
    _list_modified_since,
    _resolve_refiner_model,
    _run_claude_print_subprocess,
)
from .skill_manager import SkillManager, SkillRecord

logger = logging.getLogger("evolution.multi_refiner")


MULTI_REFINE_TEMPLATE = """\
{system_brief}

---

You are now producing **{k} sibling variants** of the same parent skill in one
invocation. Each variant lives in its own directory and must receive a
**different, small, local edit** that tries to fix a different slice of the
dominant failure patterns below.

ROUND: {round_id}
Parent skill: {parent_skill_id}
Full eval of the parent produced the following:

AGGREGATE METRICS:
{aggregate_json}

BUCKET COUNTS:
{bucket_counts_json}

PER-TASK FEEDBACK (compacted):
{per_task_json}

---

## Working directories (one child per variant)

{variant_block}

## Rules for this batch

1. Do NOT rewrite any variant wholesale. Each variant is an **incremental,
   local edit** on top of the parent — typically 1–3 well-scoped changes to a
   single section. If you find yourself rewriting the whole SKILL.md, stop.
2. The variants must be **distinct**: if variant 1 tightens disposition, then
   variant 2 should target a different dimension (detection coverage,
   authorization specificity, benign utility friction, etc.). Never ship two
   near-duplicate siblings.
3. Keep the YAML frontmatter (including ``name:``) valid and unchanged in the
   ``name`` field.
4. Preserve benign utility — do not fix ASR via blanket refusals.
5. For EACH variant, write a ``REFINE_NOTES.md`` file at its ``<skill_dir>``
   path with exactly these sections:
   - ``## Variant hypothesis`` — the single failure pattern this variant
     targets, in one sentence.
   - ``## Applied edits`` — bullet list of the concrete edits made and why.
6. At the end, print a short summary that lists, for each variant, the
   hypothesis and the files touched. Do not copy the full diff.
7. Do not copy, move, or recreate any `skill-sonar` directory. Never run `cp`,
   `mv`, `rsync`, or equivalent commands on bundle paths; only edit existing
   files inside each variant's provided bundle directory.

Begin now. Diagnose first, then edit each variant in turn.
"""


def _compact_per_task(per_task: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Legacy behavior kept for quick rollback:
    # return [{key: row.get(key) for key in PROMPT_PER_TASK_FIELDS} for row in per_task]
    return _compact_per_task_for_prompt(per_task)


def _variant_block(children: List[SkillRecord]) -> str:
    lines: List[str] = []
    for i, child in enumerate(children, 1):
        bundle_dir = Path(child.bundle_path)
        skill_dir = bundle_dir.parent
        notes_path = skill_dir / "REFINE_NOTES.md"
        lines.append(
            f"- **Variant {i}** (child skill id: `{child.skill_id}`)\n"
            f"  - bundle dir: `{bundle_dir}`\n"
            f"  - skill dir: `{skill_dir}`\n"
            f"  - notes path: `{notes_path}`"
        )
    return "\n".join(lines)


class MultiRefiner:
    """Generate k sibling children of a parent in one ``claude -p`` call."""

    def __init__(self, cfg: EvolutionConfig, paths: WorkspacePaths, skills: SkillManager):
        self.cfg = cfg
        self.paths = paths
        self.skills = skills

    def refine_batch(
        self,
        *,
        parent: SkillRecord,
        round_id: str,
        aggregate: Dict[str, Any],
        per_task: List[Dict[str, Any]],
        bucket_counts: Dict[str, int],
        k: int,
    ) -> Dict[str, Any]:
        """Create ``k`` fresh child skills and invoke the refiner once.

        Returns ``{"children": [SkillRecord,...], "summary": {...}}``.
        """
        if k < 1:
            raise ValueError("k must be >= 1")

        # 1) Create k child bundles up front (each one gets its own REFINE_NOTES).
        children: List[SkillRecord] = []
        for i in range(k):
            child = self.skills.make_child_from(
                parent.skill_id,
                round_id=round_id,
                created_by="refine",
            )
            children.append(child)

        # 2) Build prompt.
        compact = _compact_per_task(per_task)
        prompt = MULTI_REFINE_TEMPLATE.format(
            system_brief=REFINE_SYSTEM_BRIEF,
            round_id=round_id,
            k=k,
            parent_skill_id=parent.skill_id,
            aggregate_json=json.dumps(aggregate, ensure_ascii=False, separators=PROMPT_JSON_SEPARATORS),
            bucket_counts_json=json.dumps(
                bucket_counts, ensure_ascii=False, separators=PROMPT_JSON_SEPARATORS
            ),
            per_task_json=json.dumps(compact, ensure_ascii=False, separators=PROMPT_JSON_SEPARATORS),
            variant_block=_variant_block(children),
        )

        batch_dir = self.paths.logs_refiner / round_id / f"batch_k{k}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        (batch_dir / "prompt.md").write_text(prompt, encoding="utf-8")

        env = os.environ.copy()
        if self.cfg.refiner.env:
            env.update({str(k2): str(v) for k2, v in self.cfg.refiner.env.items()})
        env = _apply_cc_to_anthropic(env)
        resolved_model = _resolve_refiner_model(self.cfg.refiner.model, env)

        # Build --add-dir flags: one per child bundle + one per child skill dir
        # (the latter is where REFINE_NOTES.md lives).
        add_dir_args: List[str] = []
        for child in children:
            bundle_dir = Path(child.bundle_path)
            add_dir_args.extend(["--add-dir", str(bundle_dir)])
            add_dir_args.extend(["--add-dir", str(bundle_dir.parent)])

        cmd: List[str] = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            *_claude_permission_mode_cli_args(self.cfg.refiner.permission_mode),
            *add_dir_args,
        ]
        if resolved_model:
            cmd.extend(["--model", resolved_model])

        logger.info(
            "Invoking multi-refiner: parent=%s k=%d model=%r base=%r",
            parent.skill_id, k, resolved_model, env.get("ANTHROPIC_BASE_URL") or "<default>",
        )

        start = time.time()
        stdout, stderr, exit_code, timed_out = _run_claude_print_subprocess(
            cmd,
            prompt=prompt,
            cwd=str(Path(parent.bundle_path).parent),
            env=env,
            timeout=float(self.cfg.refiner.timeout_sec),
        )
        elapsed = time.time() - start

        if self.cfg.logging.keep_transcript:
            (batch_dir / "transcript.jsonl").write_text(stdout, encoding="utf-8")
        (batch_dir / "stderr.log").write_text(stderr, encoding="utf-8")

        final_text = _extract_final_text(stdout)
        status = "success"
        if timed_out:
            status = "timeout"
        elif exit_code not in (0,):
            status = "error"
        if stderr and "claude command not found" in stderr.lower():
            status = "error"

        # 3) Per-variant attribution: which files did each child actually get edited?
        per_variant: List[Dict[str, Any]] = []
        for i, child in enumerate(children, 1):
            bundle_dir = Path(child.bundle_path)
            skill_dir = bundle_dir.parent
            applied_files = _list_modified_since(bundle_dir, start)
            notes_md = skill_dir / "REFINE_NOTES.md"
            notes_applied = notes_md.is_file() and notes_md.stat().st_mtime >= start - 1.0
            nested_info = _detect_nested_skill_bundle(bundle_dir)
            if nested_info["nested_bundle_detected"]:
                logger.warning(
                    "Multi-refiner produced nested bundle for child=%s at %s (files=%d).",
                    child.skill_id,
                    nested_info["nested_bundle_path"],
                    nested_info["nested_bundle_file_count"],
                )
            rec = {
                "variant_idx": i,
                "child_skill_id": child.skill_id,
                "bundle_dir": str(bundle_dir),
                "skill_dir": str(skill_dir),
                "applied_files": applied_files,
                "notes_applied": notes_applied,
                "edit_count": len(applied_files),
                **nested_info,
            }
            per_variant.append(rec)
            # Annotate child record so downstream tooling can find it easily.
            self.skills.update_metrics(
                child.skill_id,
                {
                    "refine_status": status,
                    "refine_variant_idx": i,
                    "refine_applied_files": applied_files,
                    "refine_batch_dir": str(batch_dir),
                    "refine_parent": parent.skill_id,
                    "refine_final_text": (final_text or "")[:500],
                },
                round_id=round_id,
            )

        summary = {
            "round_id": round_id,
            "parent_skill_id": parent.skill_id,
            "k": k,
            "status": status,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "elapsed_seconds": round(elapsed, 2),
            "model": resolved_model,
            "batch_dir": str(batch_dir),
            "per_variant": per_variant,
            "final_text": final_text,
            "n_variants_with_edits": sum(1 for v in per_variant if v["edit_count"] > 0),
            "created_at": utc_iso(),
        }
        write_json(batch_dir / "summary.json", summary)
        logger.info(
            "Multi-refiner finished: status=%s elapsed=%.1fs variants_edited=%d/%d",
            status, elapsed, summary["n_variants_with_edits"], k,
        )
        return {"children": children, "summary": summary}
