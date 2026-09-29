"""Plan + run quality gates — built-in gates and project Lego gates, uniformly.

A *gate* is anything that can pass/fail and produce findings: the built-in
``build`` / ``lint`` / ``jury`` gates (from ``project.yaml``'s ``gates:`` list),
plus the project's blocking-capable extension hooks. :func:`plan_gates`
turns a config + loaded extensions into an ordered list of :class:`GateSpec`;
:func:`run_gates` executes them through an injected ``runner`` with fail-soft
semantics, normalising everything into :class:`keel.findings.Finding`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import revertcheck, tdd
from .findings import Finding

if TYPE_CHECKING:  # pragma: no cover
    from .config import ProjectConfig
    from .extensions import Extension

#: Built-in gate names accepted in ``project.yaml``'s ``gates:`` list.
#:
#: ``tdd-order`` is deliberately not among them: it is not a gate a project *lists*, it
#: is the gate ``implement_mode: tdd`` brings with it. Naming it here would let a project
#: ask for the verification of a commit order it never asked its implementer to produce.
#:
#: ``revert-check`` (#1289) is listed, and **opt-in**: it re-runs the test command once per
#: production change, so a project turns it on knowing the cost (:mod:`keel.revertcheck`).
BUILTIN_GATES: tuple[str, ...] = ("build", "lint", "jury", revertcheck.GATE_ID)

#: Directories a source scanner must not walk, as **prefix-independent globs**.
#:
#: ``bandit -r .`` walks everything under the working directory, including trees
#: version control is told to ignore: an installed ``.venv`` (its dependencies
#: alone produce hundreds of findings) and nested checkouts under a harness's
#: worktree directory. Test code is excluded on purpose — bandit's heuristics
#: target application code, and tests legitimately use temp paths, ``urlopen``,
#: and subprocesses, so their findings are false by construction and crowd out
#: real ones.
#:
#: The patterns are globs rather than fixed paths because a fixed ``./tests``
#: does not match ``./.claude/worktrees/<name>/tests`` — the same prefix-anchoring
#: mistake as the coverage ``omit`` pattern in #820.
_SCAN_EXCLUDE_GLOBS = "*/tests/*,*/.venv/*,*/venv/*,*/node_modules/*,*/site-packages/*"

#: Declarative security & SAST presets supported in ``policy_pack.presets``.
POLICY_PACK_PRESETS: dict[str, tuple[str, str, str, str]] = {
    # preset: (gate_id, phase, on_fail, run_cmd)
    "gitleaks": ("gitleaks", "guard", "block", "gitleaks detect --no-git -v"),
    "semgrep": ("semgrep", "test", "suggest", "semgrep scan"),
    "bandit": ("bandit", "test", "suggest", f"bandit -r . -ll -x '{_SCAN_EXCLUDE_GLOBS}'"),
    "trivy": ("trivy", "test", "warn", "trivy fs ."),
}

#: The finding an unconfigured ``build`` gate blocks with (#1328). One of three texts
#: about the same state, each worded for where it is read: this is the gate's finding;
#: ``keel init`` / ``keel setup`` print ``cli._UNSET_BUILD_NOTE`` on the terminal; the
#: scaffolded file carries ``scaffold._UNSET_BUILD_COMMENT`` above ``knobs:``. All three
#: name ``knobs.build_gate_cmd``.
UNCONFIGURED_BUILD_GATE = (
    "no build gate configured: set knobs.build_gate_cmd in .keel/project.yaml "
    "to the command that runs your tests"
)

#: The id and finding of the outcome a run with **nothing to judge** blocks with (#1364).
#: ``gates: []`` (or no ``gates:`` key, which loads the same), or a list whose only entry
#: plans nothing — ``lint`` with no ``knobs.lint_cmd`` — with no extension or preset
#: adding a gate, used to run zero gates, print nothing, exit 0, and let a dry ``keel
#: ship`` say MERGE, while ``keel merge`` refused the empty record. The id is the config
#: key, so the finding reads ``gates: …`` wherever findings are printed.
NO_GATES_ID = "gates"
#:
#: The remedy names only gates that judge wherever they run. ``jury`` is not one of them:
#: with no ``jury`` binary on the host, or an empty diff, the jury gate judges nothing
#: (#1368 review) — it reports ``SKIPPED``, and a plan with no other gate blocks on it
#: (:func:`lone_jury_cannot_judge`, #1369).
NO_GATES_PLANNED = (
    "no gate configured: gates: in .keel/project.yaml plans nothing to run and no "
    "extension adds a gate — list build (with knobs.build_gate_cmd) or lint (with "
    "knobs.lint_cmd), or add a gate extension"
)

#: The finding an unconfigured built-in ``lint`` gate fails with. :func:`plan_gates` does
#: not plan ``lint`` without a command, so only a spec built elsewhere reaches it; it names
#: the knob all the same, as every knob-backed command gate's finding does.
UNCONFIGURED_LINT_GATE = (
    "no lint command configured: set knobs.lint_cmd in .keel/project.yaml "
    "to the command that lints your code, or remove lint from gates:"
)

#: Built-in command gates whose command is a knob -> the finding naming that knob.
_UNCONFIGURED_BUILTIN: dict[str, str] = {
    "build": UNCONFIGURED_BUILD_GATE,
    "lint": UNCONFIGURED_LINT_GATE,
}

#: ``GateSpec.source`` prefixes keel itself writes; any other source is an extension file.
_KEEL_SOURCES: tuple[str, ...] = ("builtin", "policy_pack:", "implement_mode:")

# A failed gate with no explicit findings is reported at this severity.
_ON_FAIL_SEVERITY: dict[str, str] = {"block": "major", "suggest": "minor", "warn": "nit"}


class GateError(ValueError):
    """Raised when a config references an unknown built-in gate."""


#: The backbone steps a gate can run at. `keel run-gates --phases` validates against
#: this, so a typo is refused rather than scoping the run to nothing (#1172).
BACKBONE_PHASES: tuple[str, ...] = ("guard", "test", "pre-merge")


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
    #: True when the gate **cannot judge**: a ``command`` gate with no command (an unset
    #: or blank ``knobs.build_gate_cmd``), or the :data:`NO_GATES_ID` outcome of a run
    #: that planned nothing. Always ``ok=False``. Distinct from an ordinary failure
    #: because no implementer can turn it green — only the project's config can — so the
    #: s4 loop stops on it at once instead of spending its budget (#1364).
    unconfigured: bool = False


# runner(spec) -> (ok, findings[, timed_out[, not_run[, skipped]]]). May raise; run_gates
# handles it fail-soft. The shorter forms stay supported for runners that cannot time out,
# that execute every gate they are given, or whose gates always judge. ``skipped`` is a
# gate that reached its runner and judged nothing (the jury with no CLI, #1369): reported
# ``SKIPPED``, never ``ok``, and honoured only on a passing result.
GateRunner = Callable[
    [GateSpec],
    "tuple[bool, list[Finding]] | tuple[bool, list[Finding], bool] "
    "| tuple[bool, list[Finding], bool, bool] "
    "| tuple[bool, list[Finding], bool, bool, bool]",
]


def plan_gates(
    config: ProjectConfig,
    loaded: dict[str, list[Extension]],
    *,
    implement_mode: str | None = None,
) -> tuple[GateSpec, ...]:
    """Order gates by backbone phase: guard, built-in test gates, test hooks, pre-merge.

    ``implement_mode`` is the resolved s4 profile for *this run* (:func:`keel.tdd.resolve_mode`);
    ``None`` reads the project's ``knobs.implement_mode``, which is what every caller that
    has no per-run ``--tdd`` flag wants. In ``tdd`` mode the pure :data:`keel.tdd.GATE_ID`
    gate is appended last at the ``test`` phase: it is the only gate whose verdict depends
    on the other gates' (the branch must be test-first **and** green), so it is planned to
    run after them.

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
        tuple(config.policy_pack.get("presets", ())) if isinstance(config.policy_pack, dict) else ()
    )

    for e in loaded.get("guard", []):
        specs.append(
            GateSpec(
                e.id,
                e.kind,
                "guard",
                e.on_fail,
                run=e.run,
                prompt=e.prompt,
                agent=e.agent,
                source=e.source,
                required_capabilities=e.required_capabilities,
                optional_capabilities=e.optional_capabilities,
                timeout=_timeout_for(e),
            )
        )

    if "gitleaks" in presets:
        gid, phase, on_fail, run_cmd = POLICY_PACK_PRESETS["gitleaks"]
        specs.append(
            GateSpec(
                gid,
                "command",
                phase,
                on_fail,
                run=run_cmd,
                source="policy_pack:preset:gitleaks",
                timeout=project_timeout,
            )
        )

    for name in config.gates:
        if name == "build":
            specs.append(
                GateSpec(
                    "build",
                    "command",
                    "test",
                    "block",
                    run=config.knobs.build_gate_cmd,
                    timeout=project_timeout,
                )
            )
        elif name == "lint":
            # lint is optional: absent, empty or blank means off. A blank command is not a
            # command, and planning one only to fail it would block every run for a key
            # that says "no lint" (#1368 review).
            if (config.knobs.lint_cmd or "").strip():
                specs.append(
                    GateSpec(
                        "lint",
                        "command",
                        "test",
                        "block",
                        run=config.knobs.lint_cmd,
                        timeout=project_timeout,
                    )
                )
        elif name == "jury":
            specs.append(
                GateSpec("jury", "builtin", "test", "block", timeout=config.knobs.jury_timeout_s)
            )
        elif name == revertcheck.GATE_ID:
            # `pre-merge`, so the s4 loop (`--phases guard,test`) defers it rather than
            # paying for a revert per change on every iteration; s8 runs every phase.
            # No `timeout`: its bounds are `knobs.revert_check`'s, applied per run.
            specs.append(GateSpec(revertcheck.GATE_ID, "builtin", "pre-merge", "block"))
        else:
            raise GateError(
                f"unknown built-in gate {name!r}; valid: {', '.join(BUILTIN_GATES)} "
                "(project gates belong in extension slots, not in gates:)"
            )

    for preset_name in ("semgrep", "bandit", "trivy"):
        if preset_name in presets:
            gid, phase, on_fail, run_cmd = POLICY_PACK_PRESETS[preset_name]
            specs.append(
                GateSpec(
                    gid,
                    "command",
                    phase,
                    on_fail,
                    run=run_cmd,
                    source=f"policy_pack:preset:{preset_name}",
                    timeout=project_timeout,
                )
            )

    for slot, phase in (("tester", "test"), ("test", "test"), ("pre-merge", "pre-merge")):
        for e in loaded.get(slot, []):
            specs.append(
                GateSpec(
                    e.id,
                    e.kind,
                    phase,
                    e.on_fail,
                    run=e.run,
                    prompt=e.prompt,
                    agent=e.agent,
                    source=e.source,
                    required_capabilities=e.required_capabilities,
                    optional_capabilities=e.optional_capabilities,
                    timeout=_timeout_for(e),
                )
            )
    mode = implement_mode or config.knobs.implement_mode
    if mode == tdd.TDD_MODE:
        specs.append(
            GateSpec(
                tdd.GATE_ID,
                "builtin",
                "test",
                "block",
                source=f"implement_mode:{tdd.TDD_MODE}",
            )
        )
    return tuple(specs)


def command_unset(spec: GateSpec) -> bool:
    """Is ``spec`` a ``command`` gate with nothing to run — no command, or only whitespace?

    A blank command is not a command: ``sh -c ' '`` exits 0, so a ``" "`` build command
    used to report ``ok build`` for a gate that ran nothing (#1364). The schema refuses a
    blank ``knobs.build_gate_cmd`` too; this is the same test for any spec, whoever built it.
    """
    return spec.kind == "command" and not (spec.run or "").strip()


def unconfigured_finding(spec: GateSpec) -> Finding:
    """The finding a ``command`` gate with no command fails with (#1328).

    Returned by the command runner, and re-applied by :func:`run_gates` after any runner
    returns (#1364): a gate outside the run's ``--phases`` scope has to stay ``not_run``
    like any other, so the verdict belongs after the scope test, which only the runner
    sees — but a runner that answers "ok, ran" for such a gate must not make it a pass.

    It names what to set: the knob for a knob-backed built-in (``build`` ->
    ``knobs.build_gate_cmd``, ``lint`` -> ``knobs.lint_cmd``), and ``run:`` in the file for
    an extension gate. A spec keel did not plan from either has nothing to name.
    """
    if spec.source == "builtin" and spec.id in _UNCONFIGURED_BUILTIN:
        message = _UNCONFIGURED_BUILTIN[spec.id]
    elif not spec.source.startswith(_KEEL_SOURCES):
        message = f"gate {spec.id!r} has no command configured: set run: in {spec.source}"
    else:
        message = f"gate {spec.id!r} has no command configured"
    return Finding(_ON_FAIL_SEVERITY[spec.on_fail], message, spec.id)


#: Gates evaluated after the others, because each reads their verdict: ``tdd-order``
#: (the branch must be test-first *and* green) and ``revert-check`` (a revert against a red
#: suite proves nothing, so it does not spend a run when the others are red).
DEFERRED_GATES: tuple[str, ...] = (revertcheck.GATE_ID, tdd.GATE_ID)


def split_deferred(
    specs: Sequence[GateSpec],
) -> tuple[tuple[GateSpec, ...], tuple[GateSpec, ...]]:
    """Split planned gates into "run now" and "run after the rest" (:data:`DEFERRED_GATES`).

    These gates read the others' verdict, and a runner cannot: :func:`run_gates` hands each
    spec to the runner independently, and may run them concurrently. So the caller runs
    the first group, summarises it, and only then evaluates the deferred ones — rather than
    depending on a list order that a future ``concurrency > 1`` would quietly invalidate.
    """
    now = tuple(spec for spec in specs if spec.id not in DEFERRED_GATES)
    later = tuple(spec for spec in specs if spec.id in DEFERRED_GATES)
    return now, later


def nothing_to_judge(specs: Sequence[GateSpec]) -> GateOutcome | None:
    """The blocking outcome for a plan with no gate to judge the change, else ``None``.

    Keyed on the **plan**, not on the ``gates:`` key alone: ``gates: []`` beside a
    ``tester`` extension is a documented way to run project gates, and that run judges.
    Neither deferred gate counts (:data:`DEFERRED_GATES`): ``tdd-order`` reads the *other*
    gates' verdict, and "green" over no gates is vacuous; ``revert-check`` runs at the
    ``pre-merge`` phase, so an s4 loop over it alone would judge nothing, and it needs a
    suite the other gates proved green. Independent of ``--phases``: this is not a gate
    outside the run's scope but the absence of any gate in every scope, which is why it
    is reported apart from the planned gates rather than as a ``not_run`` one (#1364).
    """
    now, _later = split_deferred(specs)
    if now:
        return None
    finding = Finding(_ON_FAIL_SEVERITY["block"], NO_GATES_PLANNED, NO_GATES_ID)
    return GateOutcome(NO_GATES_ID, False, (finding,), unconfigured=True)


#: The id of the built-in jury gate, as ``gates:`` lists it.
JURY_ID = "jury"

#: The finding a jury that could not run blocks with when no other gate is planned (#1369).
#: Its remedy names the same gates :data:`NO_GATES_PLANNED` does, for the same reason.
LONE_JURY_JUDGED_NOTHING = (
    "no gate judged this change: jury is the only gate planned and it did not run — list "
    "build (with knobs.build_gate_cmd) or lint (with knobs.lint_cmd) beside it in gates:"
)


def lone_jury_cannot_judge(
    specs: Sequence[GateSpec], outcomes: Sequence[GateOutcome]
) -> list[GateOutcome]:
    """Fail a jury that could not run when it is the only gate planned (#1369).

    ``specs`` are the gates this run executed (the ``now`` half of :func:`split_deferred`)
    and ``outcomes`` their results, in the same order. The jury builtin with no ``jury``
    CLI on the host, or on an empty diff, judges nothing and comes back ``skipped``.
    Beside another gate that stays the documented s8 no-op: the other gate judged, the
    jury's ``nit`` says it did not, and the run reads ``SKIPPED  jury``. With **no** other
    gate, nothing judged the change — the #1364 case of a plan with nothing to judge,
    reached through a gate that is planned but cannot run — so its outcome becomes a
    ``FAIL`` that cannot judge (``unconfigured``): a ``major`` naming what to list beside
    it, ahead of the runner's ``nit`` saying why the jury did not run.

    Keyed on the plan, like :func:`nothing_to_judge`: any other gate counts, a soft or a
    ``not_run`` one included (a plan of soft gates alone is a separate question), and
    ``tdd-order`` is not in ``specs`` — it reads the other gates' verdict.
    """
    result = list(outcomes)
    if len(specs) != 1:
        return result
    spec, outcome = specs[0], result[0]
    if spec.kind != "builtin" or spec.id != JURY_ID or not outcome.skipped or outcome.error:
        return result
    # The runner's own finding stays beside the new one: it says *why* the jury did not run.
    found = (Finding(_ON_FAIL_SEVERITY["block"], LONE_JURY_JUDGED_NOTHING, JURY_ID),)
    return [
        GateOutcome(
            JURY_ID, False, found + outcome.findings, on_fail=outcome.on_fail, unconfigured=True
        )
    ]


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
            skipped = result[4] is True if len(result) > 4 else False
        except Exception as exc:  # noqa: BLE001 - fail-soft is the contract
            if not fail_soft:
                raise
            if spec.on_fail == "block":
                # A hard gate that errors must still block (can't silently pass).
                finding = Finding("major", f"gate {spec.id!r} errored: {exc}", spec.id)
                return GateOutcome(spec.id, False, (finding,), error=str(exc), on_fail=spec.on_fail)
            # Soft gate broke -> degrade to a no-op (logged), never abort.
            return GateOutcome(
                spec.id, True, (), error=str(exc), skipped=True, on_fail=spec.on_fail
            )

        if not not_run and command_unset(spec):
            # Belt and braces (#1364): the unset-command verdict used to live only in
            # `command_gate_runner`, so any other runner answering `(True, [])` for every
            # spec would pass a gate that ran nothing. Re-checked here, after the runner,
            # so a gate the runner scoped out (`--phases`) is still NOT-RUN.
            return GateOutcome(
                spec.id,
                False,
                (unconfigured_finding(spec),),
                on_fail=spec.on_fail,
                unconfigured=True,
            )
        found = tuple(found)
        if ok:
            return GateOutcome(
                spec.id, True, found, skipped=skipped, not_run=not_run, on_fail=spec.on_fail
            )
        if not found:
            sev = _ON_FAIL_SEVERITY[spec.on_fail]
            found = (Finding(sev, f"gate {spec.id!r} failed", spec.id),)
        # ok stays False for a timeout: the merge gate is unchanged, only the label.
        # not_run rides along on this branch too: dropping it would let a future
        # runner that reports a not-run gate as *failing* certify the merge anyway.
        return GateOutcome(
            spec.id, False, found, timed_out=timed_out, not_run=not_run, on_fail=spec.on_fail
        )

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
            applied.append(
                GateOutcome(
                    outcome.gate,
                    True,
                    outcome.findings,
                    error=outcome.error,
                    on_fail=outcome.on_fail,
                )
            )
            continue
        found = outcome.findings or (
            Finding(
                _ON_FAIL_SEVERITY[outcome.on_fail],
                f"gate {outcome.gate!r} failed (reported by the dispatching agent)",
                outcome.gate,
            ),
        )
        applied.append(
            GateOutcome(outcome.gate, False, found, error=outcome.error, on_fail=outcome.on_fail)
        )
    return applied, rejected


def collect_findings(outcomes: list[GateOutcome]) -> list[Finding]:
    """Flatten all findings across gate outcomes (in outcome order)."""
    out: list[Finding] = []
    for o in outcomes:
        out.extend(o.findings)
    return out
