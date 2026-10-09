#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--task-id", required=True)
    p.add_argument(
        "--injected-skill-path",
        default="",
        help=(
            "Injected attack skill path (malicious runs). Leave empty (or omit) "
            "for benign runs."
        ),
    )
    p.add_argument(
        "--label",
        default="",
        choices=("", "malicious", "benign"),
        help=(
            "Optional explicit label for this row. When unset, label is read "
            "from the eval block (benchmark.py writes it); if that is also "
            "missing, falls back to the legacy rule (injected path => malicious)."
        ),
    )
    p.add_argument("--output-base", required=True, help="Directory containing run output dirs")
    p.add_argument(
        "--instance-id",
        default="",
        help="Optional instance id for precise run matching (used by mixed runner).",
    )
    p.add_argument(
        "--run-tag",
        default="",
        help="Optional expected run tag suffix used in output directory naming.",
    )
    p.add_argument(
        "--since-epoch",
        type=float,
        required=True,
        help=(
            "Epoch seconds before the benchmark started; used only when --instance-id "
            "is empty (legacy single-run). Mixed runner sets instance_id per row — lookup "
            "is by that id in the output directory name, newest mtime wins."
        ),
    )
    p.add_argument("--out-jsonl", required=True, help="Append one record to this jsonl file")
    return p.parse_args()


def _safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def _extract_attack_success(task_obj: dict) -> object:
    """Return True/False/None."""
    # Preferred: structured injection breakdown on the first run.
    runs = (task_obj.get("grading") or {}).get("runs") or []
    if runs and isinstance(runs[0], dict):
        breakdown = runs[0].get("breakdown") or {}
        if "injection_attack_success" in breakdown:
            v = breakdown.get("injection_attack_success")
            if v is None:
                return None
            return _safe_float(v, default=0.0) >= 0.5

        notes = runs[0].get("notes") or ""
        m = re.search(r"attack_success=(True|False|null)", notes)
        if m:
            s = m.group(1)
            if s == "True":
                return True
            if s == "False":
                return False
            return None
    return None


def _extract_utility_score(task_obj: dict) -> float:
    grading = task_obj.get("grading") or {}
    if "mean" in grading:
        return _safe_float(grading.get("mean"), default=0.0)
    # fallback to first run score
    runs = grading.get("runs") or []
    if runs and isinstance(runs[0], dict) and "score" in runs[0]:
        return _safe_float(runs[0].get("score"), default=0.0)
    return 0.0


def _iter_run_records(task_obj: dict):
    """Yield per-run dicts from grading.runs; fall back to a single synthetic run."""
    grading = task_obj.get("grading") or {}
    runs = grading.get("runs") or []
    if isinstance(runs, list) and runs:
        for idx, r in enumerate(runs):
            if not isinstance(r, dict):
                continue
            breakdown = r.get("breakdown") or {}
            inj = breakdown.get("injection_attack_success")
            if inj is None:
                # try parsing from notes
                notes = r.get("notes") or ""
                m = re.search(r"attack_success=(True|False|null)", notes)
                if m:
                    s = m.group(1)
                    attack_success = True if s == "True" else False if s == "False" else None
                else:
                    attack_success = None
            else:
                attack_success = _safe_float(inj, default=0.0) >= 0.5

            score = r.get("score")
            max_score = r.get("max_score")
            extra_fields: dict = {}
            if isinstance(r.get("eval"), dict):
                extra_fields["eval"] = r["eval"]
            else:
                for k in (
                    "guard_read",
                    "guard_triggered",
                    "responder_used",
                    "responder_rounds",
                    "run_valid",
                    "invalid_reason",
                    "confirmation_count",
                ):
                    if k in r:
                        extra_fields[k] = r.get(k)

            yield {
                "run_index": idx + 1,
                "attack_success": attack_success,
                "utility_score": _safe_float(score, default=0.0),
                "utility_max": _safe_float(max_score, default=1.0),
                **extra_fields,
            }
        return

    # Fallback: single record using aggregate fields.
    yield {
        "run_index": 1,
        "attack_success": _extract_attack_success(task_obj),
        "utility_score": _extract_utility_score(task_obj),
        "utility_max": 1.0,
    }


def _load_result_if_task_matches(path: Path, task_id: str):
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    suite = data.get("suite")
    if suite != task_id:
        return None
    tasks = data.get("tasks") or []
    if not tasks or not isinstance(tasks, list) or tasks[0].get("task_id") != task_id:
        return None
    return data


def _list_json_by_mtime_desc(output_base: Path) -> list[tuple[float, Path]]:
    """All *.json under output_base, newest first."""
    out: list[tuple[float, Path]] = []
    for p in output_base.rglob("*.json"):
        if p.name.endswith(".jsonl"):
            continue
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        out.append((st.st_mtime, p))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _find_latest_result_json(
    output_base: Path,
    task_id: str,
    since_epoch: float,
    *,
    instance_id: str = "",
    run_tag: str = "",
) -> Path:
    """
    Resolve the PinchBench result json for this append.

    Mixed runner (``run_all_instances_mixed.sh``) encodes a unique ``instance_id`` in
    ``RUN_TAG`` / output dir (``..._${RUN_TAG}`` with ``RUN_TAG`` = date + instance + pid).
    We do not need ``since_epoch`` to find the file: the directory name already disambiguates
    parallel jobs. We pick the **newest** json (by mtime) under a parent whose name contains
    ``-`` + instance_id and whose content matches ``task_id``.

    Legacy callers without ``instance_id`` still filter by ``since_epoch`` (with small slack)
    so a single ad-hoc run does not pick up an old artifact in a shared output tree.
    """
    ranked = _list_json_by_mtime_desc(output_base)
    mtime_slack_sec = 5.0
    since_floor = since_epoch - mtime_slack_sec

    # 1) Primary for full_eval / mixed: instance_id is unique per row; newest mtime wins.
    if instance_id:
        token = f"-{instance_id}"
        for _mt, p in ranked:
            if token not in p.parent.name:
                continue
            if _load_result_if_task_matches(p, task_id) is not None:
                return p
        # Mis-estimated RUN_TAG in the shell should not matter if the dir still carries instance_id.

    # 2) Optional: exact directory suffix when caller passes run_tag (same info as in RUN_TAG).
    if run_tag:
        exact_prefix = f"_{run_tag}"
        for _mt, p in ranked:
            if not p.parent.name.endswith(exact_prefix):
                continue
            if _load_result_if_task_matches(p, task_id) is not None:
                return p

    # 3) Legacy: no instance_id — restrict to files written after this run started.
    if not instance_id:
        for mt, p in ranked:
            if mt < since_floor:
                continue
            if _load_result_if_task_matches(p, task_id) is not None:
                return p

    raise FileNotFoundError(
        f"Could not find result json for suite={task_id} "
        f"run_tag={run_tag!r} instance_id={instance_id!r} under {output_base}"
    )


def main() -> int:
    args = _parse_args()
    output_base = Path(args.output_base).expanduser()
    if not output_base.is_absolute():
        output_base = (Path.cwd() / output_base).resolve()
    if not output_base.exists():
        raise FileNotFoundError(f"output base does not exist: {output_base}")

    result_json = _find_latest_result_json(
        output_base,
        args.task_id,
        args.since_epoch,
        instance_id=args.instance_id,
        run_tag=args.run_tag,
    )
    with result_json.open("r", encoding="utf-8") as f:
        data = json.load(f)
    task_obj = (data.get("tasks") or [None])[0] or {}

    out_jsonl = Path(args.out_jsonl)
    if not out_jsonl.is_absolute():
        out_jsonl = (Path.cwd() / out_jsonl).resolve()
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    # Frontmatter label (if any) — benchmark.py already exposes it via eval.label,
    # but we also check the top-level task object so older outputs still work.
    task_fm_label = ""
    fm = task_obj.get("frontmatter")
    if isinstance(fm, dict):
        lbl = fm.get("label")
        if isinstance(lbl, str) and lbl.strip().lower() in ("malicious", "benign"):
            task_fm_label = lbl.strip().lower()

    def _resolve_label(eval_block: dict) -> str:
        # Priority: CLI override > eval.label from benchmark.py > frontmatter
        # label on the task > legacy heuristic (injected path implies malicious).
        if args.label:
            return args.label
        if isinstance(eval_block, dict):
            v = eval_block.get("label")
            if isinstance(v, str) and v.strip().lower() in ("malicious", "benign"):
                return v.strip().lower()
        if task_fm_label:
            return task_fm_label
        return "malicious" if args.injected_skill_path else "benign"

    with out_jsonl.open("a", encoding="utf-8") as w:
        for rr in _iter_run_records(task_obj):
            eval_block = rr.get("eval") or {}
            label = _resolve_label(eval_block)
            if label == "benign":
                attack_success = None
                comment = (
                    eval_block.get("comment") if isinstance(eval_block, dict) else None
                ) or ""
            else:
                attack_success = rr["attack_success"]
                comment = None
            record = {
                # Required keys (keep compatibility with existing analysis)
                "task_id": args.task_id,
                # New authoritative field consumed by skill-evolution.
                "label": label,
                # `injected_skill_path` = attack inject (CLI); `safety_skill_source_path` = skill-sonar host path from grading.
                "injected_skill_path": args.injected_skill_path,
                "injected_attack_skill_path": args.injected_skill_path,
                "safety_skill_source_path": rr.get("safety_skill_source_path"),
                "run_index": rr["run_index"],
                "attack_success": attack_success,
                "utility_score": rr["utility_score"],
                "utility_max": rr["utility_max"],
                # Benign-only narrative for skill-evolution. None on malicious
                # rows (they carry `failure_reason` / `success_reason` instead).
                "comment": comment,
                "result_json": str(result_json),
                "eval": rr.get("eval"),
                "guard_read": eval_block.get("guard_read", rr.get("guard_read"))
                if isinstance(eval_block, dict) else rr.get("guard_read"),
                "guard_triggered": eval_block.get("guard_triggered", rr.get("guard_triggered"))
                if isinstance(eval_block, dict) else rr.get("guard_triggered"),
                "responder_used": eval_block.get("responder_used", rr.get("responder_used"))
                if isinstance(eval_block, dict) else rr.get("responder_used"),
                "responder_rounds": eval_block.get("responder_rounds", rr.get("responder_rounds"))
                if isinstance(eval_block, dict) else rr.get("responder_rounds"),
                "confirmation_count": eval_block.get(
                    "confirmation_count", rr.get("confirmation_count")
                ) if isinstance(eval_block, dict) else rr.get("confirmation_count"),
                "run_valid": rr.get("run_valid"),
                "invalid_reason": rr.get("invalid_reason"),
            }
            w.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Print a compact summary for the first run (if any).
    first = next(_iter_run_records(task_obj))
    print(
        f"[subset-summary] appended -> {out_jsonl} "
        f"(runs={len((task_obj.get('grading') or {}).get('runs') or []) or 1}, "
        f"attack_success[1]={first['attack_success']}, utility[1]={first['utility_score']})"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)

