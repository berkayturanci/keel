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
class PlanHook:
    """One resolved extension hook rendered in step order."""

    slot: str
    id: str
    kind: str
    on_fail: str
    execution_mode: str
    adapter_required: bool
    required_capabilities: tuple[str, ...] = ()
    optional_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanItem:
    """One backbone step plus the gate ids that execute at it."""

    step_id: str
    step_name: str
    agentic: bool
    gates: tuple[str, ...] = ()
    hooks: tuple[PlanHook, ...] = ()


def build_plan(config: ProjectConfig, loaded: dict[str, list[Extension]]) -> tuple[PlanItem, ...]:
    """Map the planned gates onto the fixed backbone (deterministic)."""
    specs = gates.plan_gates(config, loaded)
    test_gates = tuple(s.id for s in specs if s.phase == "test")
    pre_merge_gates = tuple(s.id for s in specs if s.phase == "pre-merge")

    items: list[PlanItem] = []
    for step in model.BACKBONE:
        if step.name == "guard":
            step_gates = tuple(s.id for s in specs if s.phase == "guard")
        elif step.name == "test":
            step_gates = test_gates
        elif step.name == "merge":
            step_gates = pre_merge_gates
        else:
            step_gates = ()
        items.append(PlanItem(step.id, step.name, step.agentic, step_gates,
                              _hooks_for_step(step.id, loaded)))
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
        for hook in item.hooks:
            caps = _capability_summary(hook)
            adapter = " adapter-required" if hook.adapter_required else ""
            lines.append(
                f"           - hook: {hook.id} [{hook.slot}; {hook.kind}; "
                f"on_fail={hook.on_fail}; mode={hook.execution_mode}{adapter}{caps}]"
            )
    return "\n".join(lines)


def plan_as_dict(plan: tuple[PlanItem, ...]) -> list[dict]:
    """Render a plan as plain data for JSON output."""
    return [
        {
            "step_id": item.step_id,
            "step_name": item.step_name,
            "agentic": item.agentic,
            "gates": list(item.gates),
            "hooks": [
                {
                    "slot": hook.slot,
                    "id": hook.id,
                    "kind": hook.kind,
                    "on_fail": hook.on_fail,
                    "execution_mode": hook.execution_mode,
                    "adapter_required": hook.adapter_required,
                    "required_capabilities": list(hook.required_capabilities),
                    "optional_capabilities": list(hook.optional_capabilities),
                }
                for hook in item.hooks
            ],
        }
        for item in plan
    ]


def _hooks_for_step(step_id: str, loaded: dict[str, list[Extension]]) -> tuple[PlanHook, ...]:
    hooks: list[PlanHook] = []
    for slot in model.slots_for_step(step_id):
        for ext in loaded.get(slot.name, []):
            hooks.append(PlanHook(
                slot=slot.name,
                id=ext.id,
                kind=ext.kind,
                on_fail=ext.on_fail,
                execution_mode=ext.mode,
                adapter_required=slot.adapter_required or ext.kind == "agentic",
                required_capabilities=ext.required_capabilities,
                optional_capabilities=ext.optional_capabilities,
            ))
    return tuple(hooks)


def _capability_summary(hook: PlanHook) -> str:
    parts = []
    if hook.required_capabilities:
        parts.append("required=" + ",".join(hook.required_capabilities))
    if hook.optional_capabilities:
        parts.append("optional=" + ",".join(hook.optional_capabilities))
    return "; " + "; ".join(parts) if parts else ""
