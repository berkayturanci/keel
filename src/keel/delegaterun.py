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

import contextlib
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


def pid_path(root: str | Path, run_id: str) -> Path:
    """Path of one detached run's pid, kept **beside** the record rather than in it.

    Two writers touch a detached run: the parent, which knows the pid, and the child,
    which knows the result. If both wrote the same document the parent's write would be a
    read-check-write over a file the child can replace at any instant, and the child's
    terminal record is precisely what must never be lost. Separate files means neither
    writer needs a lock, a guard, or a retry: the record is the child's alone, the pid
    file is the parent's alone, and a reader that wants both reads both.
    """
    return state_dir(root) / f"{check_run_id(run_id)}.pid"


def write_pid(root: str | Path, run_id: str, pid: int) -> None:
    """Record a detached child's pid. Best-effort: a run without one is bounded by its
    ``deadline_at`` instead, so failing to write it must not fail the spawn."""
    with contextlib.suppress(OSError):
        workspace.write_text_atomic(pid_path(root, run_id), f"{pid}\n")


def read_pid(root: str | Path, run_id: str) -> int | None:
    """The recorded pid of a detached run, or ``None`` when there is none to read."""
    try:
        return int(pid_path(root, run_id).read_text(encoding="utf-8").strip())
    except (OSError, ValueError, RunIdError):
        return None


def run_record(root: str | Path, run_id: str) -> dict[str, Any] | None:
    """A run's record as a **reader** wants it: the child's document plus the pid.

    ``pid`` is assembled here rather than stored, so nothing that writes the record has
    to carry it and no writer can clobber the other's half.
    """
    record = load_state(root, run_id)
    if record is None:
        return None
    record["pid"] = read_pid(root, run_id)
    return record


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
        "read_only": plan.read_only,
        # Reported beside `read_only` so a caller can refuse rather than discover
        # afterwards that its "reviewer" held the implementer's write flags.
        "read_only_backed": plan.read_only_backed,
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
    """The ``cli`` and ``profile`` transports: one subprocess, prompt on stdin.

    The delegate's answer is **stdout alone**. ``CommandResult.output`` glues stderr onto
    it, and every agent CLI writes progress, warnings and login notices there — so reading
    the concatenation lets a run that produced no answer come back ``ok: true`` with the
    noise as its "output", which downstream is a diff to apply or a review to post. The
    concatenation stays for the diagnostic tail of a *failure*, where both halves help.
    """
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
    raw = result.stdout
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
    if getattr(result, "spawn_failed", False):
        # The runner's own OSError signal, not exit 127: a command that really ran can
        # exit 127 too, and reading the code alone made this branch unreachable.
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
        record = run_record(root, name)
        if record is not None:
            records.append(record)
    return records


#: Extra wall-clock seconds a detached run gets beyond its own ``--timeout`` before
#: ``wait`` calls it abandoned. The child enforces the timeout itself and then has to
#: write its result; the grace is the room for that last write.
DEADLINE_GRACE_S = 60


def start_detached(
    argv: list[str],
    *,
    root: str | Path,
    run_id: str,
    cwd: str | None = None,
    timeout: int | None = None,
    provider: str | None = None,
    role: str | None = None,
    _popen=None,
    _clock: Callable[[], datetime.datetime] = _utc_now,
) -> dict[str, Any]:
    """Spawn ``argv`` as a background child and record it. Never raises.

    The record is written **before** the spawn, so a ``wait`` issued immediately
    afterwards always finds a file, and the parent never writes it again: the pid goes to
    its own ``<run-id>.pid`` file (:func:`pid_path`). That is the fix for a lost update.
    A guarded read-check-write from the parent was still a race — the child's terminal
    record can land between the read and the write, and the parent would put ``running``
    back over the result the caller is waiting for. Two files, one writer each, and no
    window at all.

    ``deadline_at`` is stamped from the run's own ``--timeout`` plus
    :data:`DEADLINE_GRACE_S`. It is what stops a ``wait`` with no ``--timeout`` of its own
    from blocking forever when the child dies without recording anything — a
    ``SIGKILL``, an OOM kill, a reboot.

    The child gets its own session where the platform has one, so killing the caller's
    process group does not take the delegate with it — surviving the caller is the whole
    point of ``--detach``.

    ``_popen`` defaults to :class:`subprocess.Popen` resolved at call time, for the same
    reason :func:`execute`'s ``_run`` does: a default bound at import cannot be patched,
    and here the cost of missing the seam is a real agent CLI launched by a unit test.
    """
    _popen = subprocess.Popen if _popen is None else _popen
    check_run_id(run_id)
    started = _clock()
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "provider": provider,
        "role": role,
        "started_at": started.isoformat(),
        "timeout": timeout,
        "deadline_at": (
            None
            if timeout is None
            else (started + datetime.timedelta(seconds=timeout + DEADLINE_GRACE_S)).isoformat()
        ),
        "status": "running",
        "argv": list(argv),
        "out_path": str(out_path(root, run_id)),
        "result": None,
    }
    kwargs: dict[str, Any] = {}
    if hasattr(os, "setsid"):  # pragma: no branch - POSIX everywhere keel runs
        kwargs["start_new_session"] = True
    try:
        # Creating the directory and opening the log are I/O like any other: an
        # unwritable root (a read-only checkout, a root owned by another user) must come
        # back as the same fail-soft contract every other failure does, not as a
        # PermissionError traceback out of `keel delegate run --detach`.
        state_dir(root).mkdir(parents=True, exist_ok=True)
        write_state(root, record)
        handle = out_path(root, run_id).open("w", encoding="utf-8")
    except OSError as exc:
        return _failed_spawn_record(root, record, exc)
    try:
        # argv is keel's own re-invocation (sys.executable -m keel), never a shell.
        child = _popen(
            list(argv),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
    except OSError as exc:
        return _failed_spawn_record(root, record, exc)
    finally:
        handle.close()
    write_pid(root, run_id, child.pid)
    record["pid"] = child.pid
    return record


def _failed_spawn_record(root: str | Path, record: dict[str, Any], exc: OSError) -> dict[str, Any]:
    """Mark a run that never started, persisting the record when the disk allows it.

    Best-effort on purpose: the failure being reported may itself be "this directory
    cannot be written", and a second write would raise the very traceback the caller is
    being spared. The returned document is the contract either way.
    """
    record["status"] = "done"
    record["pid"] = None
    record["result"] = _spawn_failure(record, exc)
    with contextlib.suppress(OSError):
        write_state(root, record)
    return record


def _spawn_failure(record: dict[str, Any], exc: OSError) -> dict[str, Any]:
    return _detached_failure(record, code="spawn-failed", message=str(exc))


def _detached_failure(record: dict[str, Any], *, code: str, message: str) -> dict[str, Any]:
    """A full return contract for a detached run that never produced one itself.

    Same keys as :func:`result_document`, so ``keel delegate wait`` hands back one shape
    whether the delegate answered, failed, or vanished.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "provider": record.get("provider"),
        "vendor": None,
        "model": None,
        "role": record.get("role"),
        "transport": None,
        "text": "",
        "exit_code": None,
        "duration_s": 0.0,
        "timed_out": code == "lost",
        "error_code": code,
        "error": message,
        "attribution": {},
        "read_only": None,
        "read_only_backed": False,
        "effort_applied": False,
        "warnings": [],
        "run_id": record.get("run_id"),
        "out_path": record.get("out_path"),
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


def process_is_alive(pid: int) -> bool:
    """Is ``pid`` still a process we can see? Best-effort, never raises.

    ``os.kill(pid, 0)`` sends no signal; it asks the kernel whether the pid exists and
    whether we may signal it. ``PermissionError`` therefore means **alive** (it exists and
    belongs to someone else), which is the opposite of what a bare ``except`` would say.

    Two honest limits, both bounded by the run's own ``deadline_at`` rather than papered
    over: a child not yet reaped by its parent is a zombie and still answers "alive", and
    a recycled pid can answer "alive" for an unrelated process. Neither can make ``wait``
    return a wrong *result* — only make it wait longer before giving up.
    """
    if not hasattr(os, "kill"):  # pragma: no cover - POSIX everywhere keel runs
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _abandoned(
    record: dict[str, Any],
    *,
    alive: Callable[[int], bool],
    clock: Callable[[], datetime.datetime],
) -> str | None:
    """Why this still-``running`` record can never finish, or ``None`` while it might."""
    pid = record.get("pid")
    if isinstance(pid, int) and not alive(pid):
        return (
            f"the delegate process (pid {pid}) is gone and recorded no result; "
            f"its output is in {record.get('out_path')}"
        )
    deadline = record.get("deadline_at")
    if isinstance(deadline, str) and deadline:
        try:
            when = datetime.datetime.fromisoformat(deadline)
        except ValueError:
            return None
        if clock() >= when:
            return (
                f"the run passed its own deadline ({deadline}) without recording a "
                f"result; its output is in {record.get('out_path')}"
            )
    return None


def _mark_crashed(
    root: str | Path,
    run_id: str,
    reason: str,
    *,
    clock: Callable[[], datetime.datetime],
) -> dict[str, Any] | None:
    """Record that a run vanished — re-read immediately before writing, so a child's own
    result always wins. The window is a load and a comparison rather than the whole
    liveness probe that decided the run was gone."""
    record = load_state(root, run_id)
    if record is None or record.get("status") != "running":
        return record if record is None else run_record(root, run_id)
    record["status"] = "crashed"
    record["finished_at"] = _now_iso(clock)
    record["result"] = _detached_failure(record, code="lost", message=reason)
    write_state(root, record)
    return record


def reap_abandoned(
    root: str | Path = ".",
    *,
    _alive: Callable[[int], bool] | None = None,
    _clock: Callable[[], datetime.datetime] = _utc_now,
) -> list[str]:
    """Mark every run that can no longer finish, and return their ids.

    The same liveness and deadline test :func:`wait` applies, run across the whole state
    directory. Without it a run only stops claiming to be ``running`` when somebody
    happens to ``wait`` on it — so ``keel delegate status``, the command an operator uses
    precisely because they are *not* waiting, would be the one view that never told the
    truth about a killed child.
    """
    alive = process_is_alive if _alive is None else _alive
    reaped = []
    for record in list_runs(root):
        if record.get("status") != "running":
            continue
        reason = _abandoned(record, alive=alive, clock=_clock)
        if reason is not None:
            _mark_crashed(root, record["run_id"], reason, clock=_clock)
            reaped.append(record["run_id"])
    return reaped


def wait(
    root: str | Path,
    run_id: str,
    *,
    timeout: float | None = None,
    poll: float = DEFAULT_POLL_S,
    _sleep: Callable[[float], None] = time.sleep,
    _now: Callable[[], float] = time.monotonic,
    _clock: Callable[[], datetime.datetime] = _utc_now,
    _alive: Callable[[int], bool] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Block until ``run_id`` finishes. Returns ``(result, error_code)``.

    The state file is the authority — a *result* is only ever read from it, so it survives
    the process that produced it, a new session, and a reboot.

    Three ways this returns without a result, and all three are bounded:

    * ``unknown-run`` — fails closed and immediately. An orchestrator that mistypes a run
      id, or asks about a run from another checkout, must not get a wait that can only end
      in a timeout.
    * ``lost`` — the child is gone, or the run passed the deadline stamped at spawn, and
      nothing was recorded. Without this a ``SIGKILL``ed child left ``running`` forever
      and a ``wait`` with no ``--timeout`` blocked indefinitely. The record is marked
      ``crashed`` so ``keel delegate status`` stops claiming it is running.
    * ``timeout`` — the caller's own ``--timeout`` elapsed first. The run may still be
      alive; nothing is marked.

    A dead pid is re-checked against the file before being called lost, because the child
    exits *after* writing its result and the two are observed in the other order often
    enough to matter.
    """
    deadline = None if timeout is None else _now() + timeout
    alive = process_is_alive if _alive is None else _alive
    while True:
        record = run_record(root, run_id)
        if record is None:
            return None, "unknown-run"
        status = record.get("status")
        if status == "done":
            return record.get("result"), None
        if status == "crashed":
            return record.get("result"), "lost"
        reason = _abandoned(record, alive=alive, clock=_clock)
        if reason is not None:
            record = _mark_crashed(root, run_id, reason, clock=_clock)
            if record is None:
                return None, "unknown-run"
            if record.get("status") == "done":
                return record.get("result"), None
            return record.get("result"), "lost"
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
        "read_only": None,
        "read_only_backed": False,
        "effort_applied": False,
        "warnings": [],
    }
