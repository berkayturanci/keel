"""Thin I/O: probe every provider keel can dispatch to (#1011).

The pure half — what a provider *is*, where the registry lives, what may clash with
what — is :mod:`keel.providers`. This module answers the machine-dependent question:
**is it usable here, right now?** Every edge is injectable (``_which``, ``_run``,
``_env``, ``_opener``) so the whole surface is unit-testable offline.

Three rules hold for every probe:

* **Time-boxed.** A subprocess gets :data:`PROBE_TIMEOUT_S`, the Ollama HTTP call
  :data:`HTTP_TIMEOUT_S`. ``keel doctor --providers`` must answer in seconds, not
  hang behind a CLI waiting on a login prompt.
* **Fail-soft.** Nothing here raises. A missing binary, a non-zero exit, a timeout,
  an unreachable server, a malformed response — each becomes ``available: False``
  with a reason an operator can act on.
* **Names, never values.** An API-key probe reports that ``ANTHROPIC_API_KEY`` is
  set or not. The key itself is never read into a result, printed, or logged.

The only URL dialed is :data:`keel.providers.OLLAMA_TAGS_URL`, a hardcoded loopback
constant, through the shared non-redirecting opener. A registry- or config-supplied
endpoint is checked for *key presence* only: keel does not make outbound requests to
an address a file names just because ``doctor`` was run.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from . import api_delegate, juryavail, providers, runner, team
from .providers import Provider, Registry

#: Wall-clock seconds a single provider subprocess may take.
PROBE_TIMEOUT_S = 5
#: Wall-clock seconds the Ollama tag listing may take.
HTTP_TIMEOUT_S = 3

SCHEMA_VERSION = "keel.providers.v1"


@dataclass(frozen=True)
class ProbeResult:
    """One provider's probe outcome (JSON-stable via :meth:`as_dict`)."""

    provider: Provider
    available: bool
    reason: str
    #: Models the provider itself reported (``agy models``, Ollama ``/api/tags``).
    #: Empty when the provider exposes no listing or the listing failed.
    models: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        record = self.provider.as_dict()
        record.update(
            {
                "available": self.available,
                "reason": self.reason,
                "models": list(self.models),
            }
        )
        return record


def probe_providers(
    plan: Iterable[Provider],
    *,
    _which: Callable[[str], str | None] = shutil.which,
    _run: Callable[..., runner.CommandResult] = runner.run_argv,
    _env: Mapping[str, str] | None = None,
    _opener=None,
) -> tuple[ProbeResult, ...]:
    """Probe each planned provider in order. Never raises; order is the plan's."""
    env = os.environ if _env is None else _env
    results = []
    for provider in plan:
        try:
            available, reason, models = _probe(
                provider, which=_which, run=_run, env=env, opener=_opener
            )
        except Exception as exc:
            # One provider must never take the report down. Everything below is
            # already fail-soft, so reaching here means an injected seam or a stdlib
            # call surprised us — which is a row that says so, not a traceback out of
            # `keel doctor`.
            available, reason, models = False, f"probe failed: {exc}", ()
        results.append(ProbeResult(provider, available, reason, models))
    return tuple(results)


def _probe(
    provider: Provider,
    *,
    which: Callable[[str], str | None],
    run: Callable[..., runner.CommandResult],
    env: Mapping[str, str],
    opener,
) -> tuple[bool, str, tuple[str, ...]]:
    if provider.transport == "api":
        return _probe_api(provider, env=env)
    if provider.transport == "local":
        return _probe_local(provider, which=which, opener=opener)
    return _probe_cli(provider, which=which, run=run)


def _probe_cli(
    provider: Provider,
    *,
    which: Callable[[str], str | None],
    run: Callable[..., runner.CommandResult],
) -> tuple[bool, str, tuple[str, ...]]:
    """A coding-agent CLI: on ``PATH``, and answering ``--version``.

    Both halves matter. A binary that is present but broken (a half-finished install,
    a wrapper whose runtime is gone) would otherwise be reported as a usable
    implementer and fail at s4, which is the expensive place to find out.
    """
    command = provider.command
    found, reason, _ = _probe_command_only(command, which=which)
    if not found:
        return False, reason, ()
    result = run([command, "--version"], timeout=PROBE_TIMEOUT_S)
    if getattr(result, "timed_out", False):
        return False, f"{command} --version timed out after {PROBE_TIMEOUT_S}s", ()
    if not result.ok:
        return False, f"{command} --version failed (exit {result.code})", ()
    version = _first_line(result.stdout or result.output)
    if version:
        reason = f"{reason} ({version})"
    return True, reason, _cli_models(provider, command, run)


def _cli_models(
    provider: Provider,
    command: str,
    run: Callable[..., runner.CommandResult],
) -> tuple[str, ...]:
    """Models a CLI lists for itself. Only ``agy`` exposes one keel can read."""
    if not (provider.source == "builtin" and provider.name == "agy"):
        return ()
    result = run([command, "models"], timeout=PROBE_TIMEOUT_S)
    if not result.ok:
        return ()
    return providers.parse_model_lines(result.stdout or result.output)


def _probe_local(
    provider: Provider,
    *,
    which: Callable[[str], str | None],
    opener,
) -> tuple[bool, str, tuple[str, ...]]:
    """A model served on this machine.

    The built-in ``ollama`` vendor is both a CLI and a server, and keel needs both:
    the binary to dispatch through, the server to hold the model. A registry ``local``
    entry is probed by its command alone — keel does not dial an address a file names.
    """
    if provider.source != "builtin":
        return _probe_command_only(provider.command, which=which)
    ok, models, error = _ollama_tags(opener)
    if not ok:
        return False, f"{providers.OLLAMA_TAGS_URL} unreachable: {error}", ()
    found, reason, _ = _probe_command_only(provider.command, which=which)
    if not found:
        return False, f"{reason} (the server at {providers.OLLAMA_TAGS_URL} answers)", ()
    return True, f"{reason}; {len(models)} model(s) served locally", models


def _probe_command_only(
    command: str | None,
    *,
    which: Callable[[str], str | None],
) -> tuple[bool, str, tuple[str, ...]]:
    """Is ``command`` on ``PATH``? The half every transport that runs a binary shares."""
    if not command:
        return False, "no command configured", ()
    path = which(command)
    if not path:
        return False, f"{command} not found on PATH", ()
    return True, path, ()


def _probe_api(
    provider: Provider,
    *,
    env: Mapping[str, str],
) -> tuple[bool, str, tuple[str, ...]]:
    """A hosted endpoint: is its key present? Names only — the value is never read.

    No request is made. Whether the key *works* is a question only the vendor can
    answer, and asking costs a billable call plus a round trip on every ``doctor``
    run; whether one is configured is the fact an operator is missing.
    """
    key_env = provider.api_key_env
    if not key_env:
        return False, "no api_key_env configured", ()
    if provider.source == "builtin":
        present = api_delegate.has_api_token(provider.vendor, _env=env)
    else:
        present = bool(env.get(key_env, "").strip())
    where = f" for {provider.endpoint}" if provider.endpoint else ""
    if not present:
        return False, f"{key_env} is not set in the environment{where}", ()
    return True, f"{key_env} is set{where}", ()


def _ollama_tags(opener) -> tuple[bool, tuple[str, ...], str]:
    """GET the local tag listing. Returns ``(ok, models, error)``; never raises."""
    client = opener if opener is not None else api_delegate.build_http_only_opener()
    request = urllib.request.Request(providers.OLLAMA_TAGS_URL, method="GET")  # nosec B310
    try:
        with client.open(request, timeout=HTTP_TIMEOUT_S) as response:
            raw = response.read(5 * 1024 * 1024).decode("utf-8", errors="replace")
    except Exception as exc:
        # Deliberately broad: urllib raises URLError, http.client exceptions (not
        # OSError subclasses), socket timeouts and the address guard's own error.
        # A doctor run reports them all the same way — as "not reachable".
        return False, (), str(exc)
    try:
        data = json.loads(raw)
    except ValueError:
        return False, (), "response is not valid JSON"
    return True, providers.parse_tag_payload(data), ""


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def build_report(
    results: Iterable[ProbeResult],
    *,
    registry: Registry,
    errors: Iterable[str] = (),
) -> dict[str, object]:
    """Assemble the JSON-stable provider document ``keel doctor --providers`` prints."""
    rows = [result.as_dict() for result in results]
    return {
        "schema_version": SCHEMA_VERSION,
        "providers": rows,
        "registry_path": registry.path,
        "registry_present": registry.present,
        "warnings": list(registry.warnings),
        "errors": list(errors),
        "available": sum(1 for row in rows if row["available"]),
        "total": len(rows),
    }


#: Wall-clock seconds ``jury --doctor`` may take. Longer than :data:`PROBE_TIMEOUT_S`
#: because the runner probes its *own* panel behind that one call — one ``--version`` per
#: configured agent — where keel's per-provider probes each get their own budget.
JURY_DOCTOR_TIMEOUT_S = 30

#: ai-jury's readiness document announces itself with this. Checked rather than assumed, so
#: some other ``jury`` on ``PATH`` printing JSON cannot be read as a panel report.
JURY_DOCTOR_SCHEMA_PREFIX = "ai-jury.doctor."


def probe_jury_runner(
    *,
    _which: Callable[[str], str | None] = shutil.which,
    _run: Callable[..., runner.CommandResult] = runner.run_argv,
) -> juryavail.Runner:
    """Is the ``jury`` binary s7 dispatches usable here, and what panel does it hold?

    s7 does not convene a panel out of keel's delegate inventory — it runs ``jury``
    (``src/keel/adapters/commands/ship.md``), which carries its own configured panel. A
    probe that only counted keel's providers answered a different question than the one it
    was asked: on a host with ``claude`` and ``codex`` installed and no ``jury``, it
    reported the panel available, published the panel bench, and left s7 to fail at the
    invocation instead of taking the project's configured fallback or block path.

    So ask the runner. ``jury --doctor --json`` is ai-jury's own readiness document
    (``schema_version: ai-jury.doctor.v1``): it establishes that the binary is present and
    runnable, and reports which of *its* agents are usable — the panel that would actually
    sit. Fail-soft in the same way every other probe here is: a missing binary, a timeout,
    a crash and unreadable output are each a :class:`keel.juryavail.Runner` saying so, never
    an exception out of a resolver.

    A runner that ran but printed no readable report is still **usable** — keel does not
    require a particular ai-jury version, and an older one that has no ``--doctor --json``
    still convenes panels. Its inventory then falls back to :func:`collect`, which is what
    this probe read before it read anything.
    """
    command = juryavail.JURY_RUNNER_COMMAND
    found, reason, _ = _probe_command_only(command, which=_which)
    if not found:
        return juryavail.Runner(False, reason)
    return _probe_jury_doctor(command, reason, run=_run)


def _probe_jury_doctor(
    command: str,
    path: str,
    *,
    run: Callable[..., runner.CommandResult],
) -> juryavail.Runner:
    """Run ``jury --doctor --json`` and read what came back. Never raises.

    Through :func:`keel.runner.run_argv` like every other probe in this module, which
    closes the child's standard input: a readiness check that stopped on a login prompt
    would hang the resolver it is called from.
    """
    result = run([command, "--doctor", "--json"], timeout=JURY_DOCTOR_TIMEOUT_S)
    if getattr(result, "timed_out", False):
        return juryavail.Runner(
            False, f"{command} --doctor timed out after {JURY_DOCTOR_TIMEOUT_S}s"
        )
    doctor = _doctor_report(result.stdout or result.output)
    if doctor is not None:
        version = doctor.get("tool_version")
        version = version if isinstance(version, str) and version.strip() else "unknown version"
        return juryavail.Runner(True, f"{path} (ai-jury {version})", doctor)
    if not result.ok:
        return juryavail.Runner(False, f"{command} --doctor --json failed (exit {result.code})")
    return juryavail.Runner(True, f"{path} (no readable --doctor report)")


def _doctor_report(text: str) -> dict[str, object] | None:
    """ai-jury's ``--doctor --json`` document, or ``None`` when the output is not one.

    ``raw_decode`` rather than ``json.loads`` for the same reason
    :func:`keel.jury.parse_report` uses it: :func:`keel.runner.run_argv` hands back stdout
    and stderr concatenated, so a real report can be followed by log lines.
    """
    try:
        data, _end = json.JSONDecoder().raw_decode((text or "").lstrip())
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    schema = data.get("schema_version")
    if isinstance(schema, str) and schema.startswith(JURY_DOCTOR_SCHEMA_PREFIX):
        return data
    return None


def jury_availability(
    config,
    *,
    tier: int | None,
    difficulty: str | None = None,
    profile: str | None = None,
    any_difficulty: bool = False,
    _probe=None,
    _runner_probe=None,
) -> dict[str, object] | None:
    """Can this machine convene the panel this run's review policy names? (#1066)

    ``None`` when the question does not arise — this run's review is a host bench, so
    nothing about the panel can change the answer and nothing is spent asking. That keeps
    the whole feature inert for every project that has not made the panel its review,
    keel's own ``projects/keel.yaml`` included.

    *This run's* review, not the tier's: ``team.by_difficulty.<band>.review`` and
    ``team.profiles.<name>.review`` may each name the panel, and the resolver applies them
    over the tier's policy. So the predicate is :func:`keel.team.panel_review_source` — the
    same overlay :func:`keel.team._review_seats` resolves the bench with, asked once and in
    one place. Read from ``review.by_tier`` alone it was narrower than the resolver it
    guards: ``keel ship --team-profile strict`` on an unstaffable host published
    ``review_panel: jury`` with ``availability: null`` and was stuck exactly as the issue
    describes, and the call-site sweep could not see it because that site *was* handed a
    measurement — a ``None`` one.

    ``difficulty`` and ``profile`` are the run's own coordinates, the ones handed to
    :func:`keel.team.resolve_assignment`. ``any_difficulty`` is for a caller that cannot
    name the band yet; see :func:`jury_availability_for_any_tier`.

    On a panel tier it asks the panel runner first (:func:`probe_jury_runner`), because the
    runner is what s7 dispatches and it holds the configured panel. Its own report is the
    inventory when it produced one; otherwise keel falls back to :func:`collect` — the
    machinery ``keel doctor --providers`` already prints, reused rather than
    re-implemented — and :func:`keel.juryavail.assess` reads whichever answered. Both probes
    are local: ``PATH`` lookups and ``--version``-shaped calls, an env-var *name* check per
    hosted API, and one loopback request for Ollama. No key value is read, and no address a
    config names is dialled.

    **This is the one machine-dependent input to the reviewer bench, and it is
    deliberate.** Every other input is config; this one is a fact about the world, which is
    why it is allowed to move the outcome — #1014 round 3 closed the *flag* route, not this
    one — and why :meth:`keel.juryavail.Availability.as_dict` travels with it into the
    assignment, the review contract, the run ledger and the closure comment. Two machines
    can resolve the same tier differently: a runner with no ``jury`` installed falls back
    where a workstation convenes the panel, and each says which it did rather than either
    quietly claiming the other's provenance. What a *verification* surface does with that
    is :func:`keel.cli._shipped_jury_availability`'s question, not this one's: it pins to
    what the ship measured rather than re-measuring on a different machine.

    **This function measures; it does not refuse** (#1068). Under ``on_unavailable:
    block`` the ``block`` decision travels in the record, and
    :func:`keel.team._review_seats` raises :class:`keel.juryavail.JuryUnavailableError` on
    the cluster or command whose review really *is* the panel. Refusing here was narrower
    than it looked: one measurement staffs many benches, and
    :func:`jury_availability_for_any_tier` takes it before any of them is known — so a
    ``block`` project could not plan a swarm of entirely non-panel work on an unstaffable
    host, the panel never entering into it. Nothing is lost by deferring the refusal:
    ``_review_seats`` is reached from exactly one function,
    :func:`keel.team.resolve_assignment`, which is every place a bench is resolved.
    """
    if (
        team.panel_review_source(
            config.knobs.team,
            tier=tier,
            difficulty=difficulty,
            profile=profile,
            any_difficulty=any_difficulty,
        )
        is None
    ):
        return None
    jury_runner = (probe_jury_runner if _runner_probe is None else _runner_probe)()
    probe = collect if _probe is None else _probe
    # Only when the runner could not name its own panel: two sweeps of the same agent CLIs
    # is twice the subprocess cost for a second opinion keel would then have to reconcile.
    report = None if jury_runner.panel_rows is not None else probe(config)
    return juryavail.assess(
        report,
        runner=jury_runner,
        min_vendors=config.knobs.team.jury_min_vendors or team.DEFAULT_MIN_VENDORS,
        policy=config.knobs.team.jury_on_unavailable,
    ).as_dict()


def jury_availability_for_any_tier(
    config, *, profile: str | None = None, **kwargs
) -> dict[str, object] | None:
    """The panel probe for a surface that resolves *several* tiers in one call (#1066).

    :func:`keel.swarm.build_swarm_plan` scores each cluster's risk tier while it partitions,
    so its caller cannot name the tier before the plan exists — but every cluster in that
    plan resolves a bench, and a swarm that skipped the probe published ``review_panel:
    jury`` for a tier-3 cluster while the child ``keel ship`` it launched on the same machine
    seated three host reviewers. That is the in-process disagreement #1066 exists to close,
    one layer up.

    Availability is a fact about the *machine*, not about a tier: the tier only decides
    whether the question arises. So this asks it once, for the first tier whose review policy
    names the panel, and hands the one record to every cluster. A project with no panel at
    any tier probes nothing, exactly as before — :func:`jury_availability` returns ``None``
    without measuring when the run's review is a host bench, so the loop below costs a
    config lookup per tier and no subprocess at all.

    The difficulty band is unknowable here for the same reason the tier is — the scorer runs
    inside the partition this record is being measured *for* — so the sweep widens the same
    way: ``any_difficulty`` asks whether any band this policy configures could make the panel
    the review. ``profile`` is the operator's ``--team``, which *is* known, and is passed so
    the profile's own precedence over the band holds here exactly as it does in the resolver.

    Being a superset is exactly why the ``block`` refusal does not live on this path
    (#1068). "Some tier or band of this project could name the panel" is not "this swarm's
    work does": a project with ``by_tier.3: jury`` and ``on_unavailable: block`` may
    legitimately plan a wave of tier-1 docs clusters on a host with no panel, and a refusal
    taken here — before the partition has scored a single cluster — refused it. The record
    carries ``decision: block`` to every cluster instead, and
    :func:`keel.team._review_seats` refuses on the ones whose review really is the panel.
    """
    for tier in (None, *(int(name) for name in team.TIERS)):
        record = jury_availability(
            config, tier=tier, profile=profile, any_difficulty=True, **kwargs
        )
        if record is not None:
            return record
    return None


def collect(
    config,
    *,
    registry_path: str | None = None,
    _which: Callable[[str], str | None] = shutil.which,
    _run: Callable[..., runner.CommandResult] = runner.run_argv,
    _env: Mapping[str, str] | None = None,
    _opener=None,
    _read=None,
) -> dict[str, object]:
    """Load the registry, plan, probe, and return the report. The one call the CLI makes."""
    env = os.environ if _env is None else _env
    kwargs = {} if _read is None else {"_read": _read}
    registry = providers.load_registry(registry_path, env=env, **kwargs)
    plan = providers.plan_probes(config, registry)
    results = probe_providers(plan, _which=_which, _run=_run, _env=env, _opener=_opener)
    return build_report(
        results,
        registry=registry,
        errors=providers.registry_clashes(registry, config),
    )
