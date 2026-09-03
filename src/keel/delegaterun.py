"""Thin I/O: execute one planned delegate run, foreground or detached (#1012).

The pure half — which argv, which framing, which effort spelling, which attribution — is
:mod:`keel.delegate`. This module is the only place that *runs* a plan: one subprocess for
the ``cli``/``profile`` transports, one loopback HTTP call for ``ollama``, one
:func:`keel.api_delegate.generate` call for the hosted and OpenAI-compatible ones. Every
edge is injectable (``_run``, ``_opener``, ``_env``, ``_now``, ``_read``) so the whole
surface is unit-testable offline and deterministic.

**Fail-soft, always.** A missing binary, a nonzero exit, a quota refusal, a timeout, an
unparseable answer — each becomes ``ok: false`` with a machine-readable ``error_code`` in
the same JSON document a success produces. Nothing here raises at an operator. The
policy that reads those codes — do not retry a ``rate-limit``, refuse a non-tool provider
on tier 3, fall back to the host agent — stays with the caller (ship s4/s7), because it is
policy and this is a transport.

**The detach primitive.** A delegated implementation runs for tens of minutes; a host
LLM's turn does not. ``--detach`` spawns the same run as a background child in its own
session, and the state file under ``.keel/state/delegate/<run-id>.json`` is authoritative:
the parent may exit, the session may end, the operator may start a new one, and
``keel delegate wait <run-id>`` still returns the result. That is the primitive an
orchestrating agent uses instead of a sleep loop — the loop it would otherwise write burns
its own context window on polling and cannot survive the turn ending, which is how a live
run ended up with three reviewers finished and no verdict on the PR.

A run id is validated before it becomes a path. It reaches this module from a CLI flag and
names a file under the state directory; ``..`` or a separator there is refused rather than
normalized, so ``wait`` on an unknown or hostile id fails closed instead of reading one.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess  # nosec B404
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import api_delegate, delegate, runner, workspace
from .delegate import RunPlan

SCHEMA_VERSION = "keel.delegate-run.v1"

#: Where detached run state lives, relative to the project root. Inside the existing
#: gitignored ``.keel/state/`` tree, so a run record is never committed.
STATE_RELDIR = (".keel", "state", "delegate")

#: Characters a run id may contain. Deliberately tight: the id becomes a file name.
_RUN_ID_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")

#: Cap on a single HTTP response body, matching :mod:`keel.api_delegate`.
_MAX_RESPONSE_BYTES = 50 * 1024 * 1024

#: Exit code :func:`keel.runner.run_argv` reports for an OS-level spawn failure — which,
#: for a delegate, is overwhelmingly "the CLI is not installed".
_SPAWN_FAILURE_CODE = 127

#: Substrings that mark a quota refusal in a CLI's own output. A hosted API answers 429
#: and :mod:`keel.api_delegate` classifies it; a CLI exits nonzero with prose, and the
#: caller's no-retry-on-quota rule needs the same ``rate-limit`` code either way.
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "429",
    "resource_exhausted",
    "quota exceeded",
    "usage limit",
    "too many requests",
)


class RunIdError(ValueError):
    """A run id that may not become a path."""


def check_run_id(run_id: str) -> str:
    """Validate a run id and return it. Raises :class:`RunIdError` on anything unsafe."""
    if not run_id or not _RUN_ID_OK.issuperset(run_id) or run_id.strip(".") == "":
        raise RunIdError(
            f"invalid run id {run_id!r}: use letters, digits, '.', '_' or '-' only "
            "(a run id becomes a file name under .keel/state/delegate/)"
        )
    return run_id


def new_run_id(
    *, _clock: Callable[[], float] = time.time, _token: Callable[[], str] | None = None
) -> str:
    """A fresh, sortable run id: ``<epoch-seconds>-<8 hex>``.

    Time-prefixed so ``keel delegate status`` lists runs in the order they started, and
    random-suffixed so two runs started in the same second cannot collide.
    """
    token = _token() if _token is not None else uuid.uuid4().hex[:8]
    return f"{int(_clock())}-{token}"


def state_dir(root: str | Path = ".") -> Path:
    """The detached-run state directory for ``root`` (not created)."""
    return Path(root).joinpath(*STATE_RELDIR)


def state_path(root: str | Path, run_id: str) -> Path:
    """Path of one run's state document."""
    return state_dir(root) / f"{check_run_id(run_id)}.json"


def out_path(root: str | Path, run_id: str) -> Path:
    """Path of one detached run's captured stdout+stderr."""
    return state_dir(root) / f"{check_run_id(run_id)}.out"


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _now_iso(clock: Callable[[], datetime.datetime]) -> str:
    return clock().isoformat()


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def result_document(
    plan: RunPlan,
    *,
    ok: bool,
    text: str = "",
    exit_code: int | None = None,
    duration_s: float = 0.0,
    timed_out: bool = False,
    error_code: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """The one JSON document ``keel delegate run`` prints, success or failure.

    ``exit_code`` is ``None`` for the HTTP transports: there is no process, and reporting
    a synthetic ``1`` would let a caller mistake a refused API key for a crashed CLI.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "provider": plan.provider,
        "vendor": plan.vendor,
        "model": plan.model,
        "role": plan.role,
        "transport": plan.transport,
        "text": text,
        "exit_code": exit_code,
        "duration_s": duration_s,
        "timed_out": timed_out,
        "error_code": error_code,
        "error": error,
        "attribution": dict(plan.attribution),
        "effort_applied": plan.effort_applied,
        "warnings": list(plan.warnings),
    }


def rate_limited(text: str) -> bool:
    """Does this output read as a quota refusal? (pure, best-effort)"""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def execute(
    plan: RunPlan,
    *,
    _run: Callable[..., runner.CommandResult] | None = None,
    _opener=None,
    _env=os.environ,
    _now: Callable[[], float] = time.monotonic,
    _read: Callable[[str], str] = _read_text,
) -> dict[str, Any]:
    """Run one plan and return the JSON contract. Never raises.

    ``_run`` defaults to :func:`keel.runner.run_argv`, resolved **at call time** rather
    than bound as the default value: a default argument is evaluated once at import, so a
    test patching ``keel.runner.run_argv`` would still reach the real subprocess — which
    for this module means actually launching an agent CLI.
    """
    # Rebound onto the parameter rather than a fresh local: #879's AST sweep reads the
    # keywords of every call spelled `_run(...)` / `_popen(...)`, and renaming the callee
    # would take this spawn site out of the rule's sight.
    _run = runner.run_argv if _run is None else _run
    started = _now()

    def finish(**kwargs) -> dict[str, Any]:
        return result_document(plan, duration_s=round(_now() - started, 3), **kwargs)

    try:
        prompt = _read(plan.prompt_path)
    except OSError as exc:
        return finish(ok=False, error_code="no-prompt", error=str(exc))
    if not prompt.strip():
        return finish(
            ok=False,
            error_code="no-prompt",
            error=f"{plan.prompt_path} is empty; a delegate with no brief produces nothing",
        )
    if plan.transport == "ollama":
        return _run_ollama(plan, prompt, finish, opener=_opener)
    if plan.transport == "api":
        return _run_api(plan, prompt, finish, env=_env, opener=_opener)
    return _run_argv(plan, prompt, finish, run=_run)


def _run_argv(plan: RunPlan, prompt: str, finish, *, run) -> dict[str, Any]:
    """The ``cli`` and ``profile`` transports: one subprocess, prompt on stdin."""
    argv = list(plan.argv)
    if plan.stdin_mode == delegate.STDIN_STREAM_JSON:
        stdin_text: str | None = delegate.stream_json_frame(prompt)
    elif plan.stdin_mode == delegate.STDIN_TEXT:
        stdin_text = prompt
    else:
        # A profile's `prompt_mode: arg`. The planner cannot build this argv — it is
        # handed a path, never the text — so the prompt is appended here, at the one
        # seam that has both.
        argv.append(prompt)
        stdin_text = None
    result = run(argv, cwd=plan.cwd, timeout=plan.timeout, stdin_text=stdin_text)
    raw = result.stdout or result.output
    text = delegate.parse_stream_json(raw) if plan.stdin_mode == delegate.STDIN_STREAM_JSON else raw
    if result.timed_out:
        return finish(
            ok=False,
            exit_code=result.code,
            timed_out=True,
            error_code="timeout",
            error=f"{argv[0]} timed out after {plan.timeout}s",
            text=text,
        )
    if result.code == _SPAWN_FAILURE_CODE and not raw:
        return finish(
            ok=False,
            exit_code=result.code,
            error_code="missing-binary",
            error=f"{argv[0]} could not be executed: {result.stderr or result.output}",
        )
    if not result.ok:
        code = "rate-limit" if rate_limited(result.output) else "nonzero-exit"
        return finish(
            ok=False,
            exit_code=result.code,
            error_code=code,
            error=f"{argv[0]} exited {result.code}: {_tail(result.output)}",
            text=text,
        )
    if not text.strip():
        return finish(
            ok=False,
            exit_code=result.code,
            error_code="empty-output",
            error=f"{argv[0]} exited 0 and produced no output",
        )
    return finish(ok=True, exit_code=result.code, text=text)


def _run_api(plan: RunPlan, prompt: str, finish, *, env, opener) -> dict[str, Any]:
    """Hosted and OpenAI-compatible vendors: exactly one call through ``api_delegate``."""
    request = plan.request or {}
    result = api_delegate.generate(
        request["vendor"],
        request["model"],
        prompt,
        endpoint=request.get("endpoint"),
        api_key_env=request.get("api_key_env"),
        max_tokens=request.get("max_tokens", api_delegate.DEFAULT_MAX_TOKENS),
        timeout=request.get("timeout", api_delegate.DEFAULT_TIMEOUT),
        extra_payload=request.get("extra_payload") or None,
        _env=env,
        _opener=opener,
    )
    if not result.ok:
        return finish(ok=False, error_code=result.error_code, error=result.error)
    return finish(ok=True, text=result.text)


def _run_ollama(plan: RunPlan, prompt: str, finish, *, opener) -> dict[str, Any]:
    """The built-in local vendor: one POST to the hardcoded loopback ``/api/generate``."""
    request = plan.request or {}
    body = json.dumps(delegate.ollama_payload(request["model"], prompt)).encode("utf-8")
    # The URL is delegate.OLLAMA_GENERATE_URL, a hardcoded constant — never config, never
    # the registry, never model or prompt content.
    http_request = urllib.request.Request(  # nosec B310
        delegate.OLLAMA_GENERATE_URL,
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    client = opener if opener is not None else api_delegate.build_http_only_opener()
    try:
        with client.open(http_request, timeout=plan.timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        code = "rate-limit" if exc.code == 429 else "http"
        return finish(ok=False, error_code=code, error=f"HTTP {exc.code}")
    except Exception as exc:
        # Deliberately broad, as in providerprobe: urllib raises URLError, http.client
        # exceptions (not OSError subclasses), socket timeouts and the address guard's
        # own error. An operator sees "the local server did not answer", not a traceback.
        return finish(ok=False, error_code="network", error=str(exc))
    if not 200 <= status < 300:
        return finish(ok=False, error_code="http", error=f"HTTP {status}")
    try:
        data = json.loads(raw)
    except ValueError:
        return finish(ok=False, error_code="bad-response", error="response is not valid JSON")
    text = delegate.parse_ollama_response(data)
    if not text:
        return finish(
            ok=False, error_code="bad-response", error="response carried no completion text"
        )
    return finish(ok=True, text=text)


def _tail(text: str, limit: int = 400) -> str:
    stripped = (text or "").strip()
    return stripped[-limit:]


# --------------------------------------------------------------------------- detach


def load_state(root: str | Path, run_id: str) -> dict[str, Any] | None:
    """One run's state document, or ``None`` when it does not exist / cannot be read."""
    try:
        path = state_path(root, run_id)
    except RunIdError:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_state(root: str | Path, record: dict[str, Any]) -> Path:
    """Persist a run's state atomically, so a reader never sees a torn document."""
    path = state_path(root, record["run_id"])
    workspace.write_text_atomic(path, json.dumps(record, indent=2, sort_keys=True))
    return path


def list_runs(root: str | Path = ".") -> list[dict[str, Any]]:
    """Every readable run record under the state directory, oldest id first."""
    directory = state_dir(root)
    records = []
    try:
        names = sorted(p.stem for p in directory.glob("*.json"))
    except OSError:  # pragma: no cover - glob on a readable parent does not fail
        return []
    for name in names:
        record = load_state(root, name)
        if record is not None:
            records.append(record)
    return records


def start_detached(
    argv: list[str],
    *,
    root: str | Path,
    run_id: str,
    cwd: str | None = None,
    _popen=None,
    _clock: Callable[[], datetime.datetime] = _utc_now,
) -> dict[str, Any]:
    """Spawn ``argv`` as a background child and record it. Never raises.

    The **parent** writes the initial ``running`` record because only the parent knows the
    pid, and a ``wait`` issued immediately afterwards must find a file rather than race
    the child's first write. The child overwrites it with ``status: done`` plus the result
    when it finishes (:func:`finish_detached`).

    The child gets its own session where the platform has one, so killing the caller's
    process group does not take the delegate with it — surviving the caller is the whole
    point of ``--detach``.

    ``_popen`` defaults to :class:`subprocess.Popen` resolved at call time, for the same
    reason :func:`execute`'s ``_run`` does: a default bound at import cannot be patched,
    and here the cost of missing the seam is a real agent CLI launched by a unit test.
    """
    _popen = subprocess.Popen if _popen is None else _popen
    check_run_id(run_id)
    directory = state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "pid": None,
        "started_at": _now_iso(_clock),
        "status": "running",
        "argv": list(argv),
        "out_path": str(out_path(root, run_id)),
        "result": None,
    }
    kwargs: dict[str, Any] = {}
    if hasattr(os, "setsid"):  # pragma: no branch - POSIX everywhere keel runs
        kwargs["start_new_session"] = True
    try:
        handle = out_path(root, run_id).open("w", encoding="utf-8")
    except OSError as exc:
        record["status"] = "done"
        record["result"] = _spawn_failure(run_id, exc)
        write_state(root, record)
        return record
    try:
        child = _popen(  # nosec B603 - argv is keel's own re-invocation, never shell
            list(argv),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
    except OSError as exc:
        record["status"] = "done"
        record["result"] = _spawn_failure(run_id, exc)
    else:
        record["pid"] = child.pid
    finally:
        handle.close()
    write_state(root, record)
    return record


def _spawn_failure(run_id: str, exc: OSError) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "run_id": run_id,
        "error_code": "spawn-failed",
        "error": str(exc),
    }


def finish_detached(
    root: str | Path,
    run_id: str,
    result: dict[str, Any],
    *,
    _clock: Callable[[], datetime.datetime] = _utc_now,
) -> Path:
    """Record a detached run's result, keeping whatever the parent already wrote."""
    record = load_state(root, run_id) or {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "pid": None,
        "started_at": _now_iso(_clock),
        "argv": [],
        "out_path": str(out_path(root, run_id)),
    }
    record["status"] = "done"
    record["finished_at"] = _now_iso(_clock)
    record["result"] = result
    return write_state(root, record)


#: How often :func:`wait` re-reads the state file.
DEFAULT_POLL_S = 0.5


def wait(
    root: str | Path,
    run_id: str,
    *,
    timeout: float | None = None,
    poll: float = DEFAULT_POLL_S,
    _sleep: Callable[[float], None] = time.sleep,
    _now: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any] | None, str | None]:
    """Block until ``run_id`` finishes. Returns ``(result, error_code)``.

    Fails closed on an unknown id: an orchestrator that mistypes a run id, or asks about a
    run from another checkout, must get ``unknown-run`` rather than a wait that can only
    end in a timeout. The state file is the authority — the pid is not consulted, so a
    result survives the process that produced it, a new session, and a reboot.
    """
    deadline = None if timeout is None else _now() + timeout
    while True:
        record = load_state(root, run_id)
        if record is None:
            return None, "unknown-run"
        if record.get("status") == "done":
            return record.get("result"), None
        if deadline is not None and _now() >= deadline:
            return None, "timeout"
        _sleep(poll)


def planning_failure(
    provider: str,
    role: str,
    *,
    code: str,
    message: str,
) -> dict[str, Any]:
    """The JSON contract for a run that never started — a plan that could not be made.

    Same keys as :func:`result_document` with the unresolved halves null, so a caller
    parses one shape whether the provider was unknown, the model token unsafe, or the
    delegate simply exited nonzero.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "provider": provider,
        "vendor": None,
        "model": None,
        "role": role,
        "transport": None,
        "text": "",
        "exit_code": None,
        "duration_s": 0.0,
        "timed_out": False,
        "error_code": code,
        "error": message,
        "attribution": {},
        "effort_applied": False,
        "warnings": [],
    }
