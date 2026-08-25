"""Thin I/O: execute shell-command gates (build / lint / command extensions).

This is the only place keel shells out for gates. It is deliberately thin and
**fail-soft**: a timeout or a missing binary becomes a failed :class:`CommandResult`
rather than an exception. The subprocess call is injectable (``_run``) so the gate
runner is fully unit-testable offline; agentic gates are dispatched elsewhere.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .findings import Finding
from .model import DEFAULT_GATE_TIMEOUT_S

if TYPE_CHECKING:  # pragma: no cover
    from .gates import GateSpec

# Re-exported, not re-declared. #876 asked for the two aliases to agree and #896
# achieved that by copying the definition here byte for byte — which reverting
# left the whole suite green, because nothing compared them (#931). One
# definition cannot drift from itself.
#
# `gates` is the lower-level module: it owns `GateSpec`, and it imports nothing
# from here, so this edge is one-way and creates no cycle.
from .gates import GateRunner  # noqa: E402  (after TYPE_CHECKING by design)

__all__ = ["GateRunner", "CommandResult", "run_argv", "command_gate_runner"]

_ON_FAIL_SEVERITY = {"block": "major", "suggest": "minor", "warn": "nit"}

#: reviewdog-style errorformat: ``path:line[:col]: message`` (first hit wins).
#: A single multiline ``search`` replaces a per-``splitlines`` loop: ``^`` is
#: anchored to each line by ``re.MULTILINE``, the path classes exclude ``\n`` so a
#: match can never span lines, and the trailing ``(?:[:\s]|$)`` accepts the line
#: number at end-of-line.
_LOCATION_RE = re.compile(
    r"^[ \t]*(?P<path>[^\s\n:][^:\n]*?):(?P<line>\d+)(?::\d+)?(?:[:\s]|$)",
    re.MULTILINE,
)


def first_location(text: str) -> tuple[str | None, int | None]:
    """Extract the first ``path:line`` location from tool output (``(None, None)`` if none)."""
    m = _LOCATION_RE.search(text)
    return (m.group("path"), int(m.group("line"))) if m else (None, None)


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    code: int
    #: ``stdout + stderr``, concatenated. Kept for the diagnostic uses that genuinely
    #: want both (a failing gate's message, an output tail). **Do not parse structured
    #: data out of this** — a command that writes progress or warnings to stderr while
    #: exiting 0 (git's ``warning: refname … is ambiguous``, ai-jury's ``[jury] …``
    #: logs) leaves the real payload glued to noise. Parse :attr:`stdout` instead.
    output: str
    #: True when the wall-clock timeout killed the command (exit 124). A timeout is
    #: still a failure — ``ok`` stays False — but it carries no pass/fail verdict, so
    #: callers can label it distinctly instead of reporting it as a broken test.
    timed_out: bool = False
    #: Captured standard output alone. This is what parsers must read: a tool's
    #: machine-readable result goes here, never contaminated by stderr diagnostics.
    stdout: str = ""
    #: Captured standard error alone.
    stderr: str = ""


def _result(proc) -> CommandResult:
    out = proc.stdout or ""
    err = proc.stderr or ""
    return CommandResult(proc.returncode == 0, proc.returncode, out + err, stdout=out, stderr=err)


def run_command(
    cmd: str, *, cwd: str | None = None, timeout: int = DEFAULT_GATE_TIMEOUT_S, _run=subprocess.run
) -> CommandResult:
    """Run ``cmd`` in a shell, capturing output. Fail-soft on timeout/OS error."""
    try:
        # Intentional shell boundary: cmd must come only from operator-controlled
        # project config or extension YAML, never from PR content or agent output.
        proc = _run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )  # nosec B604
    except subprocess.TimeoutExpired:
        return CommandResult(False, 124, f"timed out after {timeout}s", timed_out=True)
    except OSError as exc:
        return CommandResult(False, 127, str(exc), stderr=str(exc))
    return _result(proc)


def run_argv(
    argv: list[str], *, cwd: str | None = None, timeout: int = 120, _run=subprocess.run
) -> CommandResult:
    """Run an argv list (no shell). Fail-soft on timeout/OS error. Used by git/gh wrappers."""
    try:
        proc = _run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(False, 124, f"timed out after {timeout}s", timed_out=True)
    except OSError as exc:
        return CommandResult(False, 127, str(exc), stderr=str(exc))
    return _result(proc)


def _tail(text: str, n: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def command_gate_runner(
    repo_root: str | None = None,
    *,
    timeout: int = DEFAULT_GATE_TIMEOUT_S,
    _run=subprocess.run,
) -> GateRunner:
    """A :data:`keel.gates.GateRunner` that executes ``command`` gates via the shell.

    Non-command gates (agentic / builtin like ``jury``) are not executed here — in
    command-only mode they pass as no-ops; the agent-dispatch layer runs those.

    ``timeout`` is the fallback wall-clock limit for a gate that carries none of its
    own; a :attr:`~keel.gates.GateSpec.timeout` resolved by
    :func:`~keel.gates.plan_gates` always wins. A gate killed by that limit is
    reported as a **timeout** rather than a failure: it still blocks (``ok`` is
    False and the severity is unchanged), but the message says the command never
    produced a verdict instead of implying a test broke.
    """

    def runner(spec: GateSpec) -> tuple[bool, list[Finding], bool, bool]:
        if spec.kind != "command" or not spec.run:
            # Not executed here — the agent-dispatch layer runs agentic gates. Flagged
            # `not_run` so this can never be recorded as "ran and passed"; `ok` stays
            # True so a soft gate does not spuriously fail a command-only run.
            return True, [], False, True
        limit = timeout if spec.timeout is None else spec.timeout
        result = run_command(spec.run, cwd=repo_root, timeout=limit, _run=_run)
        if result.ok:
            return True, [], False, False
        severity = _ON_FAIL_SEVERITY[spec.on_fail]
        if result.timed_out:
            # No pass/fail verdict exists — do not dress the kill up as a test result.
            message = (
                f"{spec.id} timed out after {limit}s (exit {result.code}); "
                "the command produced no pass/fail result. Raise the limit via "
                "knobs.gate_timeout_s (or this gate's timeout:) if it legitimately "
                "needs longer — a genuinely hanging command is still a defect."
            )
            return False, [Finding(severity, message, spec.id)], True, False
        message = f"{spec.id} failed (exit {result.code})"
        tail = _tail(result.output)
        if tail:
            message += f": {tail}"
        path, line = first_location(result.output)
        return (
            False,
            [
                Finding(
                    severity,
                    message,
                    spec.id,
                    path=path,
                    line=line,
                    anchorable=path is not None and line is not None,
                )
            ],
            False,
            False,
        )

    return runner
