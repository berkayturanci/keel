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

from . import api_delegate, providers, runner
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
