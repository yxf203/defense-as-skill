#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional


def parse_task_id_from_markdown(task_file: Path) -> str:
    """Parse frontmatter id from task markdown; fallback to stem."""
    text = task_file.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return task_file.stem

    parts = text.split("---", 2)
    if len(parts) < 3:
        return task_file.stem

    frontmatter = parts[1]
    for raw_line in frontmatter.splitlines():
        line = raw_line.strip()
        if line.startswith("id:"):
            value = line.split(":", 1)[1].strip()
            if value:
                return value
    return task_file.stem


def to_rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def build_benign_instances(root: Path, start_idx: int) -> List[Dict]:
    task_dir = root / "tasks" / "tasks-benign"
    instances: List[Dict] = []
    idx = start_idx

    for task_file in sorted(task_dir.glob("task_*.md")):
        task_id = parse_task_id_from_markdown(task_file)
        instance = {
            "instance_id": f"inst_benign_{task_id}",
            "task_id": task_id,
            "task_path": to_rel(task_file, root),
            "task_source": "tasks-benign",
            "skill_id": None,
            "skill_path": None,
            "skill_kind": None,
            "metadata_path": None,
            "family": None,
            "subfamily": None,
            "is_benign": True,
        }
        instances.append(instance)
        idx += 1

    return instances


def build_skill_task_map(root: Path) -> Dict[str, str]:
    task_dir = root / "tasks" / "tasks-skill"
    mapping: Dict[str, str] = {}
    for task_file in sorted(task_dir.glob("task_*.md")):
        task_id = parse_task_id_from_markdown(task_file)
        mapping[task_id] = to_rel(task_file, root)
    return mapping


def normalize_family(metadata: Dict, skill_id: Optional[str]) -> Optional[str]:
    if metadata.get("attack_family"):
        return metadata["attack_family"]
    if skill_id:
        parts = skill_id.split("/")
        if len(parts) >= 1:
            return parts[0].replace("-", "_")
    return None


def normalize_subfamily(metadata: Dict, skill_id: Optional[str]) -> Optional[str]:
    if metadata.get("poisoning_type"):
        return metadata["poisoning_type"]
    if skill_id:
        parts = skill_id.split("/")
        if len(parts) >= 2:
            return parts[1]
    return None


def build_injected_instances(root: Path, start_idx: int) -> List[Dict]:
    task_map = build_skill_task_map(root)
    meta_root = root / "attack-metadata"
    instances: List[Dict] = []
    idx = start_idx

    for meta_file in sorted(meta_root.glob("*/*/*/attack_metadata.json")):
        metadata = json.loads(meta_file.read_text(encoding="utf-8"))
        task_id = metadata.get("task_id")
        skill_id = metadata.get("skill_id")
        if not task_id or task_id not in task_map:
            continue

        skill_path_abs = root / "injected-skills" / (skill_id or "") / "SKILL.md"
        skill_path = to_rel(skill_path_abs, root) if skill_id and skill_path_abs.exists() else None

        instance = {
            "instance_id": f"inst_{idx:05d}",
            "task_id": task_id,
            "task_path": task_map[task_id],
            "task_source": "tasks-skill",
            "skill_id": skill_id,
            "skill_path": skill_path,
            "skill_kind": "injected" if skill_id else None,
            "metadata_path": to_rel(meta_file, root),
            "family": normalize_family(metadata, skill_id),
            "subfamily": normalize_subfamily(metadata, skill_id),
            "is_benign": False,
        }
        instances.append(instance)
        idx += 1

    return instances


def write_jsonl(records: List[Dict], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as f:
        for row in records:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build all_instances.jsonl for skillGuard")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="skillGuard root directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output JSONL path (default: <root>/all_instances.jsonl)",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    output_file = args.output.resolve() if args.output else root / "all_instances.jsonl"

    injected = build_injected_instances(root, start_idx=1)
    benign = build_benign_instances(root, start_idx=len(injected) + 1)
    all_rows = injected + benign

    write_jsonl(all_rows, output_file)
    print(f"Wrote {len(all_rows)} instances to {output_file}")
    print(f"Injected: {len(injected)}, Benign: {len(benign)}")


if __name__ == "__main__":
    main()
