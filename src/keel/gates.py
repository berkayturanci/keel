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

#: Declarative security & SAST presets supported in ``policy_pack.presets``.
POLICY_PACK_PRESETS: dict[str, tuple[str, str, str, str]] = {
    # preset: (gate_id, phase, on_fail, run_cmd)
    "gitleaks": ("gitleaks", "guard", "block", "gitleaks detect --no-git -v"),
    "semgrep": ("semgrep", "test", "suggest", "semgrep scan"),
    "bandit": ("bandit", "test", "suggest", "bandit -r . -ll"),
    "trivy": ("trivy", "test", "warn", "trivy fs ."),
}

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
    #: True when *this runner did not execute the gate at all* — an ``agentic`` gate
    #: reached the command-only runner, which the agent-dispatch layer runs instead.
    #: Distinct from ``ok`` on purpose: "not my job" must never be recorded as "ran and
    #: passed", or a blocking review gate nobody executed would authorize the merge.
    #: ``ok`` stays True so a soft gate does not spuriously fail the run; consumers that
    #: certify (see :func:`keel.ledger.record_gates_passed`) must refuse a *blocking*
    #: gate that was never run.
    not_run: bool = False
    #: The gate's declared severity (``block`` / ``suggest`` / ``warn``), carried from
    #: its :class:`GateSpec` so a consumer reading only outcomes can tell whether a
    #: ``not_run`` gate was one the project required.
    on_fail: str = "block"


# runner(spec) -> (ok, findings[, timed_out[, not_run]]). May raise; run_gates handles
# it fail-soft. The shorter forms stay supported for runners that cannot time out or
# that execute every gate they are given.
GateRunner = Callable[
    [GateSpec],
    "tuple[bool, list[Finding]] | tuple[bool, list[Finding], bool] "
    "| tuple[bool, list[Finding], bool, bool]",
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
    presets = (
        tuple(config.policy_pack.get("presets", ()))
        if isinstance(config.policy_pack, dict)
        else ()
    )

    for e in loaded.get("guard", []):
        specs.append(GateSpec(e.id, e.kind, "guard", e.on_fail,
                              run=e.run, prompt=e.prompt, agent=e.agent, source=e.source,
                              required_capabilities=e.required_capabilities,
                              optional_capabilities=e.optional_capabilities,
                              timeout=_timeout_for(e)))

    if "gitleaks" in presets:
        gid, phase, on_fail, run_cmd = POLICY_PACK_PRESETS["gitleaks"]
        specs.append(GateSpec(gid, "command", phase, on_fail, run=run_cmd,
                              source="policy_pack:preset:gitleaks", timeout=project_timeout))

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

    for preset_name in ("semgrep", "bandit", "trivy"):
        if preset_name in presets:
            gid, phase, on_fail, run_cmd = POLICY_PACK_PRESETS[preset_name]
            specs.append(GateSpec(gid, "command", phase, on_fail, run=run_cmd,
                                  source=f"policy_pack:preset:{preset_name}",
                                  timeout=project_timeout))

    for slot, phase in (("tester", "test"), ("test", "test"), ("pre-merge", "pre-merge")):
        for e in loaded.get(slot, []):
            specs.append(GateSpec(e.id, e.kind, phase, e.on_fail,
                                  run=e.run, prompt=e.prompt, agent=e.agent, source=e.source,
                                  required_capabilities=e.required_capabilities,
                                  optional_capabilities=e.optional_capabilities,
                                  timeout=_timeout_for(e)))
    return tuple(specs)


def run_gates(
    specs,
    runner: GateRunner,
    *,
    fail_soft: bool = True,
    concurrency: int = 1,
) -> list[GateOutcome]:
    """Run each gate via ``runner``; normalise to outcomes (fail-soft by default).

    When ``concurrency > 1``, independent gates are executed concurrently using
    standard library ``concurrent.futures.ThreadPoolExecutor``, while preserving
    exact deterministic outcome ordering.
    """
    def _run_single(spec: GateSpec) -> GateOutcome:
        try:
            # tuple() first: the runner contract has always been "any 2-iterable",
            # so indexing the raw return would reject a generator that used to work.
            result = tuple(runner(spec))
            # Runners that cannot time out may return the 2-tuple form; runners that
            # execute every gate they are given may omit the not-run flag.
            ok, found = result[0], result[1]
            timed_out = result[2] is True if len(result) > 2 else False
            not_run = result[3] is True if len(result) > 3 else False
        except Exception as exc:  # noqa: BLE001 - fail-soft is the contract
            if not fail_soft:
                raise
            if spec.on_fail == "block":
                # A hard gate that errors must still block (can't silently pass).
                finding = Finding("major", f"gate {spec.id!r} errored: {exc}", spec.id)
                return GateOutcome(spec.id, False, (finding,), error=str(exc),
                                   on_fail=spec.on_fail)
            # Soft gate broke -> degrade to a no-op (logged), never abort.
            return GateOutcome(spec.id, True, (), error=str(exc), skipped=True,
                               on_fail=spec.on_fail)

        found = tuple(found)
        if ok:
            return GateOutcome(spec.id, True, found, not_run=not_run,
                               on_fail=spec.on_fail)
        if not found:
            sev = _ON_FAIL_SEVERITY[spec.on_fail]
            found = (Finding(sev, f"gate {spec.id!r} failed", spec.id),)
        # ok stays False for a timeout: the merge gate is unchanged, only the label.
        # not_run rides along on this branch too: dropping it would let a future
        # runner that reports a not-run gate as *failing* certify the merge anyway.
        return GateOutcome(spec.id, False, found, timed_out=timed_out,
                           not_run=not_run, on_fail=spec.on_fail)

    spec_list = list(specs)
    if concurrency <= 1 or len(spec_list) <= 1:
        return [_run_single(s) for s in spec_list]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        return list(executor.map(_run_single, spec_list))


def unrun_blocking(outcomes: list[GateOutcome]) -> tuple[str, ...]:
    """Names of ``on_fail: block`` gates this run did not execute, in outcome order."""
    return tuple(o.gate for o in outcomes if o.not_run and o.on_fail == "block")


def apply_recorded_results(
    outcomes: list[GateOutcome], results: dict[str, str]
) -> tuple[list[GateOutcome], list[str]]:
    """Fold externally-executed gate verdicts into ``outcomes``.

    ``results`` maps a gate id to ``"pass"`` or ``"fail"``. It exists because the
    command-only runner cannot execute ``agentic`` gates — the agent-dispatch layer
    does — and without a way to report back, such a gate stays ``not_run`` forever and
    :func:`keel.ledger.record_gates_passed` can never certify the run. That would make
    a blocking agentic gate a permanent merge block rather than a gate.

    **Only a ``not_run`` outcome is replaced.** A gate keel executed has a measured
    verdict, and letting a recorded one override it would turn this channel into a way
    to certify a run whose gates were observed failing — the same fail-open this whole
    series exists to close, arriving from the other direction. Results naming an
    executed gate are returned in ``rejected`` so the caller can refuse loudly rather
    than silently discard them.

    A recorded result clears ``not_run``, because the gate *was* run; a ``fail``
    additionally produces a finding at the gate's declared severity, exactly as an
    in-process failure would. A not-run gate can be neither timed out nor skipped, so
    the rebuilt outcome carries neither.

    Returns ``(outcomes, rejected)``. Ids matching no outcome at all are left to the
    CLI, which validates them against the plan.
    """
    applied: list[GateOutcome] = []
    rejected: list[str] = []
    for outcome in outcomes:
        verdict = results.get(outcome.gate)
        if verdict is None:
            applied.append(outcome)
            continue
        if not outcome.not_run:
            rejected.append(outcome.gate)
            applied.append(outcome)
            continue
        if verdict == "pass":
            applied.append(GateOutcome(outcome.gate, True, outcome.findings,
                                       error=outcome.error, on_fail=outcome.on_fail))
            continue
        found = outcome.findings or (
            Finding(_ON_FAIL_SEVERITY[outcome.on_fail],
                    f"gate {outcome.gate!r} failed (reported by the dispatching agent)",
                    outcome.gate),
        )
        applied.append(GateOutcome(outcome.gate, False, found, error=outcome.error,
                                   on_fail=outcome.on_fail))
    return applied, rejected


def collect_findings(outcomes: list[GateOutcome]) -> list[Finding]:
    """Flatten all findings across gate outcomes (in outcome order)."""
    out: list[Finding] = []
    for o in outcomes:
        out.extend(o.findings)
    return out
