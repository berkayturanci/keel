"""The pure run planner: lay the project's gates/extensions onto the backbone.

``build_plan`` is deterministic and I/O-free — it answers "what would run, in what
order, for this project?" by mapping the planned gates onto their backbone steps.
This is exactly what a dry-run / ``keel plan`` shows, and what config-injection
tests assert against (the right project values appear, no foreign ones leak).

Actually *executing* the plan (git, gh, agent dispatch) is the thin I/O layer; the
ordering and composition live here, so they stay testable and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import gates, model
from .config import ProjectConfig
from .extensions import Extension


@dataclass(frozen=True)
class PlanItem:
    """One backbone step plus the gate ids that execute at it."""

    step_id: str
    step_name: str
    agentic: bool
    gates: tuple[str, ...] = ()


def build_plan(config: ProjectConfig, loaded: dict[str, list[Extension]]) -> tuple[PlanItem, ...]:
    """Map the planned gates onto the fixed backbone (deterministic)."""
    specs = gates.plan_gates(config, loaded)
    test_gates = tuple(s.id for s in specs if s.phase == "test")
    pre_merge_gates = tuple(s.id for s in specs if s.phase == "pre-merge")

    items: list[PlanItem] = []
    for step in model.BACKBONE:
        if step.name == "test":
            step_gates = test_gates
        elif step.name == "merge":
            step_gates = pre_merge_gates
        else:
            step_gates = ()
        items.append(PlanItem(step.id, step.name, step.agentic, step_gates))
    return tuple(items)


def render_plan(config: ProjectConfig, plan: tuple[PlanItem, ...]) -> str:
    """Render a plan as a stable, human-readable tree (used by dry-run / CLI)."""
    repo = config.repo or "(repo)"
    lines = [
        f"keel plan — {repo}",
        f"  base_branch: {config.base_branch}   core_version: {config.core_version}",
        "  backbone:",
    ]
    for item in plan:
        marker = "  [agent]" if item.agentic else ""
        lines.append(f"    {item.step_id:>3}  {item.step_name}{marker}")
        for gate in item.gates:
            lines.append(f"           - gate: {gate}")
    return "\n".join(lines)
