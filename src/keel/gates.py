"""Plan + run quality gates — built-in gates and project Lego gates, uniformly.

A *gate* is anything that can pass/fail and produce findings: the built-in
``build`` / ``lint`` / ``jury`` gates (from ``project.yaml``'s ``gates:`` list),
plus the project's blocking-capable extension hooks. :func:`plan_gates`
turns a config + loaded extensions into an ordered list of :class:`GateSpec`;
:func:`run_gates` executes them through an injected ``runner`` with fail-soft
semantics, normalising everything into :class:`keel.findings.Finding`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .findings import Finding

if TYPE_CHECKING:  # pragma: no cover
    from .config import ProjectConfig
    from .extensions import Extension

#: Built-in gate names accepted in ``project.yaml``'s ``gates:`` list.
BUILTIN_GATES: tuple[str, ...] = ("build", "lint", "jury")

# A failed gate with no explicit findings is reported at this severity.
_ON_FAIL_SEVERITY: dict[str, str] = {"block": "major", "suggest": "minor", "warn": "nit"}


class GateError(ValueError):
    """Raised when a config references an unknown built-in gate."""


@dataclass(frozen=True)
class GateSpec:
    """A planned gate. ``phase`` is the backbone step it runs at."""

    id: str
    kind: str  # command | agentic | builtin
    phase: str  # backbone step name, e.g. "guard", "test", or "pre-merge"
    on_fail: str  # block | suggest | warn
    run: str | None = None
    prompt: str | None = None
    agent: str = "inherit"
    source: str = "builtin"
    required_capabilities: tuple[str, ...] = ()
    optional_capabilities: tuple[str, ...] = ()
    #: Resolved wall-clock limit for a ``command`` gate, in seconds. ``None`` means
    #: the runner's own fallback applies (a spec built outside :func:`plan_gates`).
    timeout: int | None = None


@dataclass(frozen=True)
class GateOutcome:
    """Result of running one gate."""

    gate: str
    ok: bool
    findings: tuple[Finding, ...] = ()
    error: str | None = None
    skipped: bool = False
    #: True when this gate was killed by its wall-clock limit rather than returning a
    #: verdict. Purely descriptive: a timed-out gate is still ``ok=False`` with an
    #: unchanged severity, so it blocks the merge exactly as a failure does. Only the
    #: label and the operator-facing explanation differ — a hanging command is a real
    #: defect and must stay red.
    timed_out: bool = False


# runner(spec) -> (ok, findings) or (ok, findings, timed_out). May raise; run_gates
# handles it fail-soft. The 2-tuple form stays supported for runners that cannot
# time out (agentic / builtin dispatchers).
GateRunner = Callable[
    [GateSpec], "tuple[bool, list[Finding]] | tuple[bool, list[Finding], bool]"
]


def plan_gates(config: ProjectConfig, loaded: dict[str, list[Extension]]) -> tuple[GateSpec, ...]:
    """Order gates by backbone phase: guard, built-in test gates, test hooks, pre-merge.

    Every gate that shells out gets its wall-clock ``timeout`` resolved here, so the
    planner is the single place budgets are decided:

    * ``command`` gates, most specific first — the extension's own ``timeout:``
      frontmatter → ``knobs.gate_timeout_s`` → :data:`keel.model.DEFAULT_GATE_TIMEOUT_S`;
    * the ``jury`` builtin, which also shells out (via ``run_argv``) —
      ``knobs.jury_timeout_s``, kept separate because a cross-vendor panel and a test
      suite have unrelated runtimes.

    ``agentic`` gates carry ``None``: the agent-dispatch layer runs those, nothing
    shells out for them, and a number there would advertise a limit never applied.
    """
    project_timeout = config.knobs.gate_timeout_s

    def _timeout_for(e: Extension) -> int | None:
        if e.kind != "command":
            return None
        return e.timeout if e.timeout is not None else project_timeout

    specs: list[GateSpec] = []

    for e in loaded.get("guard", []):
        specs.append(GateSpec(e.id, e.kind, "guard", e.on_fail,
                              run=e.run, prompt=e.prompt, agent=e.agent, source=e.source,
                              required_capabilities=e.required_capabilities,
                              optional_capabilities=e.optional_capabilities,
                              timeout=_timeout_for(e)))

    for name in config.gates:
        if name == "build":
            specs.append(GateSpec("build", "command", "test", "block",
                                  run=config.knobs.build_gate_cmd, timeout=project_timeout))
        elif name == "lint":
            if config.knobs.lint_cmd:  # lint is optional
                specs.append(GateSpec("lint", "command", "test", "block",
                                      run=config.knobs.lint_cmd, timeout=project_timeout))
        elif name == "jury":
            specs.append(GateSpec("jury", "builtin", "test", "block",
                                  timeout=config.knobs.jury_timeout_s))
        else:
            raise GateError(
                f"unknown built-in gate {name!r}; valid: {', '.join(BUILTIN_GATES)} "
                "(project gates belong in extension slots, not in gates:)"
            )

    for slot, phase in (("tester", "test"), ("test", "test"), ("pre-merge", "pre-merge")):
        for e in loaded.get(slot, []):
            specs.append(GateSpec(e.id, e.kind, phase, e.on_fail,
                                  run=e.run, prompt=e.prompt, agent=e.agent, source=e.source,
                                  required_capabilities=e.required_capabilities,
                                  optional_capabilities=e.optional_capabilities,
                                  timeout=_timeout_for(e)))
    return tuple(specs)


def run_gates(specs, runner: GateRunner, *, fail_soft: bool = True) -> list[GateOutcome]:
    """Run each gate via ``runner``; normalise to outcomes (fail-soft by default)."""
    outcomes: list[GateOutcome] = []
    for spec in specs:
        try:
            # tuple() first: the runner contract has always been "any 2-iterable",
            # so indexing the raw return would reject a generator that used to work.
            result = tuple(runner(spec))
            # Runners that cannot time out may return the 2-tuple form.
            ok, found = result[0], result[1]
            timed_out = result[2] is True if len(result) > 2 else False
        except Exception as exc:  # noqa: BLE001 - fail-soft is the contract
            if not fail_soft:
                raise
            if spec.on_fail == "block":
                # A hard gate that errors must still block (can't silently pass).
                finding = Finding("major", f"gate {spec.id!r} errored: {exc}", spec.id)
                outcomes.append(GateOutcome(spec.id, False, (finding,), error=str(exc)))
            else:
                # Soft gate broke -> degrade to a no-op (logged), never abort.
                outcomes.append(GateOutcome(spec.id, True, (), error=str(exc), skipped=True))
            continue

        found = tuple(found)
        if ok:
            outcomes.append(GateOutcome(spec.id, True, found))
        else:
            if not found:
                sev = _ON_FAIL_SEVERITY[spec.on_fail]
                found = (Finding(sev, f"gate {spec.id!r} failed", spec.id),)
            # ok stays False for a timeout: the merge gate is unchanged, only the label.
            outcomes.append(GateOutcome(spec.id, False, found, timed_out=timed_out))
    return outcomes


def collect_findings(outcomes: list[GateOutcome]) -> list[Finding]:
    """Flatten all findings across gate outcomes (in outcome order)."""
    out: list[Finding] = []
    for o in outcomes:
        out.extend(o.findings)
    return out
