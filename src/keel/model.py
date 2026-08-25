"""The keel backbone: the fixed, ordered step machine and its extension slots.

This module is the single source of truth for the step IDs, the named slots an
extension may register into, and the invariants the backbone always preserves.
It is pure data — no I/O, no config — so consumers (config, extensions,
orchestrator) and tests can all agree on one definition.

It also holds the two gate wall-clock defaults, :data:`DEFAULT_GATE_TIMEOUT_S` and
:data:`DEFAULT_JURY_TIMEOUT_S`. Neither is part of the step machine; they live here
because this is the only module with no intra-package imports, so ``config``, ``gates``,
``runner``, and ``jury`` can share one value without any of them importing each other.
(Defining them in ``gates`` or ``jury`` instead closes a real cycle — ``gates`` names
``config`` in its ``TYPE_CHECKING`` imports, which CodeQL counts as an edge.)

Two related constants is the ceiling for this arrangement: a **third** shared constant,
or a first one unrelated to gate timeouts, should become a dedicated leaf module
(``limits.py``) that this one imports, rather than letting ``model`` accrete values that
have nothing to do with the backbone.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Wall-clock seconds a command gate may run, when neither the gate's own ``timeout:``
#: nor ``knobs.gate_timeout_s`` overrides it. (Why it lives here: module docstring.)
DEFAULT_GATE_TIMEOUT_S: int = 600

#: Wall-clock seconds the ``jury`` built-in may run. Separate from the above on purpose:
#: the jury is a cross-vendor agent CLI, not a project test command, so its budget should
#: be raisable without loosening every test gate — and vice versa.
DEFAULT_JURY_TIMEOUT_S: int = 600


@dataclass(frozen=True)
class Step:
    """One backbone step.

    ``slot`` is the historical primary extension point, kept for compatibility.
    New hook-aware code should use :func:`slots_for_step`.
    """

    id: str
    name: str
    slot: str | None = None
    agentic: bool = False  # True if the step dispatches to an agent (implement/review/classify)


@dataclass(frozen=True)
class Slot:
    """One named extension hook exposed by a backbone step."""

    name: str
    step_id: str
    execution_mode: str = "deterministic"
    may_block: bool = False
    adapter_required: bool = False


#: The fixed backbone, in execution order. Changing this is a keel-core change.
BACKBONE: tuple[Step, ...] = (
    Step("s0", "config"),
    Step("s1", "select"),
    Step("s2", "branch"),
    Step("s3", "guard"),
    Step("s4", "implement", slot="after-implement", agentic=True),
    Step("s5", "classify", agentic=True),
    Step("s6", "ci"),
    Step("s7", "review", slot="reviewers", agentic=True),
    Step("s8", "test", slot="tester"),
    Step("s9", "fixloop"),
    Step("s10", "merge", slot="pre-merge"),
    Step("s11", "capture", slot="post-merge"),
    Step("s12", "close"),
)

#: The named hooks, in backbone order. Extensions are add-only into these.
SLOT_DEFINITIONS: tuple[Slot, ...] = (
    Slot("after:config", "s0"),
    Slot("before:select", "s1"),
    Slot("select", "s1", adapter_required=True),
    Slot("after:select", "s1"),
    Slot("before:branch", "s2"),
    Slot("after:branch", "s2"),
    Slot("guard", "s3", may_block=True),
    Slot("before:implement", "s4", adapter_required=True),
    Slot("after-implement", "s4", adapter_required=True),
    Slot("classify", "s5", adapter_required=True),
    Slot("after:classify", "s5"),
    Slot("before:ci", "s6"),
    Slot("after:ci", "s6"),
    Slot("reviewers", "s7", execution_mode="agentic", adapter_required=True),
    Slot("after:review", "s7", adapter_required=True),
    Slot("tester", "s8", execution_mode="hybrid", may_block=True, adapter_required=True),
    Slot("test", "s8", execution_mode="hybrid", may_block=True, adapter_required=True),
    Slot("after:test", "s8"),
    Slot("before:fixloop", "s9", adapter_required=True),
    Slot("fixloop", "s9", execution_mode="hybrid", adapter_required=True),
    Slot("after:fixloop", "s9"),
    Slot("pre-merge", "s10", may_block=True),
    Slot("after:merge", "s10"),
    Slot("capture", "s11", adapter_required=True),
    Slot("post-merge", "s11", adapter_required=True),
    Slot("before:close", "s12"),
    Slot("on-close", "s12", adapter_required=True),
    Slot("after:close", "s12"),
)

#: The named slots, in backbone order. Extensions are add-only into these.
SLOTS: tuple[str, ...] = tuple(slot.name for slot in SLOT_DEFINITIONS)

#: Invariants the backbone always preserves — no config or extension can override.
INVARIANTS: tuple[str, ...] = (
    "merge_lock",  # every merge goes through the mkdir-based lock
    "window_gate",  # the night no-merge window is enforced
    "fail_soft",  # a soft failure degrades to a no-op, never aborts
    "orchestrator_only_writes",  # only the orchestrator writes to the PR
    "attribution",  # implementer/reviewer vendor+model is recorded
)

_BY_ID: dict[str, Step] = {s.id: s for s in BACKBONE}
_SLOT_META: dict[str, Slot] = {slot.name: slot for slot in SLOT_DEFINITIONS}
_BY_SLOT: dict[str, Step] = {slot: _BY_ID[_SLOT_META[slot].step_id] for slot in SLOTS}


def step_ids() -> tuple[str, ...]:
    """All backbone step IDs in order."""
    return tuple(s.id for s in BACKBONE)


def get_step(step_id: str) -> Step:
    """Return the step with ``step_id`` (raises ``KeyError`` if unknown)."""
    return _BY_ID[step_id]


def step_for_slot(slot: str) -> Step:
    """Return the backbone step that exposes ``slot`` (raises ``KeyError``)."""
    return _BY_SLOT[slot]


def slot_meta(slot: str) -> Slot:
    """Return metadata for a named extension hook (raises ``KeyError``)."""
    return _SLOT_META[slot]


def slots_for_step(step_id: str) -> tuple[Slot, ...]:
    """Return extension hooks exposed by ``step_id`` in declared order."""
    return tuple(slot for slot in SLOT_DEFINITIONS if slot.step_id == step_id)
