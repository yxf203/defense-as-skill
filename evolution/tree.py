"""Search tree used by the MCTS-style skill-evolution loop.

Each ``TreeNode`` represents one concrete skill version on disk and carries the
statistics needed by UCT selection plus the (optional) full-eval feedback the
refiner uses when expanding it.

Persistence
-----------
The full tree is serialised to ``skill-evolution/skills/tree.json``; each node
also references a ``skill_id`` that the existing :class:`SkillManager` owns on
disk (``skills/active/<skill_id>/skill-sonar``).
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .logger_utils import utc_iso, write_json

logger = logging.getLogger("evolution.tree")


# Node status values kept deliberately small and explicit so the loop can
# filter the active pool with a single equality check.
STATUS_ACTIVE = "active"
STATUS_FULL_EVALUATED = "full_evaluated"
STATUS_TERMINAL = "terminal"


@dataclass
class TreeNode:
    node_id: str                        # 1:1 with skill_id on disk
    parent_id: Optional[str]
    depth: int
    created_by: str                     # "initial" | "refine"
    created_at: str
    round_id: Optional[str]
    skill_path: str
    bundle_path: str

    children_ids: List[str] = field(default_factory=list)

    # ---- Cheap eval stats (used by UCT) ----
    cheap_eval_scores: List[float] = field(default_factory=list)
    cheap_count: int = 0
    cheap_mean: Optional[float] = None
    cheap_aux: List[Dict[str, Any]] = field(default_factory=list)  # per-rollout metric dump

    # ---- Subtree visit count (rolled up during backprop) ----
    # Equals cheap_count(v) + sum(subtree_visits(child) for child in children).
    # This is the "N(parent(v))" term the UCT selector uses.
    subtree_visits: int = 0

    # ---- Full eval (at most one per node) ----
    full_eval_score: Optional[float] = None
    full_eval_feedback: Optional[Dict[str, Any]] = None   # {aggregate, per_task, bucket_counts}
    full_eval_round_id: Optional[str] = None
    full_eval_at: Optional[str] = None

    status: str = STATUS_ACTIVE          # active | full_evaluated | terminal
    notes: List[str] = field(default_factory=list)

    # Free-form log of events (selection, expansion, etc.) for debugging.
    history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TreeNode":
        return cls(
            node_id=d["node_id"],
            parent_id=d.get("parent_id"),
            depth=int(d.get("depth") or 0),
            created_by=d.get("created_by") or "manual",
            created_at=d.get("created_at") or utc_iso(),
            round_id=d.get("round_id"),
            skill_path=d.get("skill_path") or "",
            bundle_path=d.get("bundle_path") or "",
            children_ids=list(d.get("children_ids") or []),
            cheap_eval_scores=list(d.get("cheap_eval_scores") or []),
            cheap_count=int(d.get("cheap_count") or 0),
            cheap_mean=d.get("cheap_mean"),
            cheap_aux=list(d.get("cheap_aux") or []),
            subtree_visits=int(d.get("subtree_visits") or 0),
            full_eval_score=d.get("full_eval_score"),
            full_eval_feedback=d.get("full_eval_feedback"),
            full_eval_round_id=d.get("full_eval_round_id"),
            full_eval_at=d.get("full_eval_at"),
            status=d.get("status") or STATUS_ACTIVE,
            notes=list(d.get("notes") or []),
            history=list(d.get("history") or []),
        )


class Tree:
    """Tree of skill versions plus UCT math.

    The tree owns only search bookkeeping; the raw skill files (``SKILL.md``
    etc.) live under :class:`SkillManager`.  ``node_id`` is the shared key
    between the two.
    """

    def __init__(self, state_path: Path):
        self._state_path: Path = state_path
        self._nodes: Dict[str, TreeNode] = {}
        self._root_id: Optional[str] = None
        self.load()

    # ----------------------- IO -----------------------
    def load(self) -> None:
        if not self._state_path.is_file():
            self._nodes = {}
            self._root_id = None
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load tree state %s: %s", self._state_path, exc)
            self._nodes = {}
            self._root_id = None
            return
        self._root_id = data.get("root_id")
        self._nodes = {
            nid: TreeNode.from_dict(raw) for nid, raw in (data.get("nodes") or {}).items()
        }

    def save(self) -> None:
        payload = {
            "root_id": self._root_id,
            "updated_at": utc_iso(),
            "nodes": {nid: n.to_dict() for nid, n in self._nodes.items()},
        }
        write_json(self._state_path, payload)

    # ----------------------- Accessors -----------------------
    def root_id(self) -> Optional[str]:
        return self._root_id

    def get(self, node_id: str) -> Optional[TreeNode]:
        return self._nodes.get(node_id)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._nodes

    def nodes(self) -> Dict[str, TreeNode]:
        return dict(self._nodes)

    def active(self) -> List[TreeNode]:
        return [n for n in self._nodes.values() if n.status == STATUS_ACTIVE]

    def full_evaluated(self) -> List[TreeNode]:
        return [n for n in self._nodes.values() if n.status == STATUS_FULL_EVALUATED]

    def children_of(self, node_id: str) -> List[TreeNode]:
        node = self._nodes.get(node_id)
        if not node:
            return []
        return [self._nodes[c] for c in node.children_ids if c in self._nodes]

    def ancestors_of(self, node_id: str, *, include_self: bool = False) -> List[TreeNode]:
        """Return [self?, parent, grandparent, ..., root]."""
        out: List[TreeNode] = []
        cur = self._nodes.get(node_id)
        if cur is None:
            return out
        if include_self:
            out.append(cur)
        while cur is not None and cur.parent_id is not None:
            par = self._nodes.get(cur.parent_id)
            if par is None:
                break
            out.append(par)
            cur = par
        return out

    # ----------------------- Mutations -----------------------
    def add_root(self, node: TreeNode) -> None:
        if self._root_id is not None:
            raise RuntimeError(f"Tree already has root {self._root_id}")
        node.parent_id = None
        node.depth = 0
        self._nodes[node.node_id] = node
        self._root_id = node.node_id
        self.save()

    def add_child(self, parent_id: str, node: TreeNode) -> None:
        parent = self._nodes.get(parent_id)
        if parent is None:
            raise KeyError(f"Unknown parent {parent_id}")
        node.parent_id = parent_id
        node.depth = parent.depth + 1
        self._nodes[node.node_id] = node
        if node.node_id not in parent.children_ids:
            parent.children_ids.append(node.node_id)
        self.save()

    def record_cheap_eval(
        self,
        node_id: str,
        *,
        score: float,
        aux: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Store a cheap-eval score on ``node_id`` and rollup visit counts."""
        node = self._nodes.get(node_id)
        if node is None:
            raise KeyError(f"Unknown node {node_id}")
        node.cheap_eval_scores.append(float(score))
        node.cheap_count = len(node.cheap_eval_scores)
        node.cheap_mean = (
            round(sum(node.cheap_eval_scores) / node.cheap_count, 6)
            if node.cheap_count
            else None
        )
        if aux is not None:
            node.cheap_aux.append(aux)
        # Backprop: every ancestor (including the node itself) gains one visit.
        for n in self.ancestors_of(node_id, include_self=True):
            n.subtree_visits += 1
        self.save()

    def record_full_eval(
        self,
        node_id: str,
        *,
        score: float,
        feedback: Dict[str, Any],
        round_id: Optional[str],
    ) -> None:
        node = self._nodes.get(node_id)
        if node is None:
            raise KeyError(f"Unknown node {node_id}")
        if node.status != STATUS_ACTIVE and node.full_eval_score is not None:
            logger.warning(
                "Node %s already has a full_eval_score; overwriting not allowed — "
                "one node is only fully evaluated once.",
                node_id,
            )
            return
        node.full_eval_score = float(score)
        node.full_eval_feedback = feedback
        node.full_eval_round_id = round_id
        node.full_eval_at = utc_iso()
        node.status = STATUS_FULL_EVALUATED
        self.save()

    def mark_terminal(self, node_id: str, reason: str = "") -> None:
        node = self._nodes.get(node_id)
        if node is None:
            return
        node.status = STATUS_TERMINAL
        if reason:
            node.notes.append(f"terminal: {reason}")
        self.save()

    def append_history(self, node_id: str, event: Dict[str, Any]) -> None:
        node = self._nodes.get(node_id)
        if node is None:
            return
        event = dict(event)
        event.setdefault("ts", utc_iso())
        node.history.append(event)
        self.save()

    # ----------------------- Selection (UCT) -----------------------
    def uct_score(self, node: TreeNode, *, c: float) -> float:
        """UCT for a node.

        UCT(v) = Q(v) + c * sqrt(ln(N_parent) / n(v))

        * ``Q(v)`` = cheap_mean(v); if the node has no cheap evals yet treat it
          as infinitely attractive (we always want to try cold rollouts first).
        * ``n(v)`` = cheap_count(v).
        * ``N_parent`` = ``subtree_visits`` of the parent; for the root we use
          the node's own subtree_visits instead.
        """
        if node.cheap_count == 0:
            return math.inf
        parent = self._nodes.get(node.parent_id) if node.parent_id else None
        if parent is None:
            n_parent = max(1, node.subtree_visits)
        else:
            n_parent = max(1, parent.subtree_visits)
        q = float(node.cheap_mean or 0.0)
        explore = c * math.sqrt(math.log(max(2, n_parent)) / max(1, node.cheap_count))
        return q + explore

    def select_active(self, *, c: float) -> Optional[TreeNode]:
        """Pick the active node with the best UCT score.

        ``full_evaluated`` and ``terminal`` nodes are excluded from selection,
        matching the spec ("一个节点 full eval 只做一次").
        """
        active = self.active()
        if not active:
            return None
        best: Optional[TreeNode] = None
        best_score = -math.inf
        for n in active:
            s = self.uct_score(n, c=c)
            if s > best_score:
                best_score = s
                best = n
        if best is not None:
            logger.debug(
                "UCT selection: node=%s uct=%.4f cheap_mean=%s cheap_count=%d",
                best.node_id, best_score, best.cheap_mean, best.cheap_count,
            )
        return best

    # ----------------------- Reporting -----------------------
    def best_full_evaluated(self) -> Optional[TreeNode]:
        """Return the full-evaluated node with the highest ``full_eval_score``."""
        fe = [n for n in self._nodes.values() if n.full_eval_score is not None]
        if not fe:
            return None
        return max(fe, key=lambda n: float(n.full_eval_score or 0.0))

    def snapshot(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for n in self._nodes.values():
            out.append(
                {
                    "node_id": n.node_id,
                    "parent_id": n.parent_id,
                    "depth": n.depth,
                    "status": n.status,
                    "cheap_count": n.cheap_count,
                    "cheap_mean": n.cheap_mean,
                    "subtree_visits": n.subtree_visits,
                    "full_eval_score": n.full_eval_score,
                    "n_children": len(n.children_ids),
                    "created_by": n.created_by,
                    "created_at": n.created_at,
                    "round_id": n.round_id,
                }
            )
        out.sort(key=lambda r: (r["depth"], r["created_at"]))
        return out

    def write_snapshot_md(self, md_path: Path, *, title: str = "") -> None:
        lines: List[str] = []
        if title:
            lines.append(f"# {title}")
            lines.append("")
        lines.append("| node_id | status | depth | cheap_count | cheap_mean | subtree_visits | full_eval_score | parent |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in self.snapshot():
            cm = "-" if r["cheap_mean"] is None else f"{r['cheap_mean']:.3f}"
            fs = "-" if r["full_eval_score"] is None else f"{r['full_eval_score']:.3f}"
            lines.append(
                f"| `{r['node_id']}` | {r['status']} | {r['depth']} | {r['cheap_count']} | {cm} | {r['subtree_visits']} | {fs} | `{r['parent_id'] or '(root)'}` |"
            )
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
