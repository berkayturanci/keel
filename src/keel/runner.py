"""Thin I/O: execute shell-command gates (build / lint / command extensions).

This is the only place keel shells out for gates. It is deliberately thin and
**fail-soft**: a timeout or a missing binary becomes a failed :class:`CommandResult`
rather than an exception. The subprocess call is injectable (``_run``) so the gate
runner is fully unit-testable offline; agentic gates are dispatched elsewhere.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .findings import Finding

if TYPE_CHECKING:  # pragma: no cover
    from .gates import GateSpec

GateRunner = Callable[["GateSpec"], tuple[bool, list[Finding]]]

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
    output: str


def run_command(
    cmd: str, *, cwd: str | None = None, timeout: int = 600, _run=subprocess.run
) -> CommandResult:
    """Run ``cmd`` in a shell, capturing output. Fail-soft on timeout/OS error."""
    try:
        # Intentional shell boundary: cmd must come only from operator-controlled
        # project config or extension YAML, never from PR content or agent output.
        proc = _run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)  # nosec B604
    except subprocess.TimeoutExpired:
        return CommandResult(False, 124, f"timed out after {timeout}s")
    except OSError as exc:
        return CommandResult(False, 127, str(exc))
    output = (proc.stdout or "") + (proc.stderr or "")
    return CommandResult(proc.returncode == 0, proc.returncode, output)


def run_argv(
    argv: list[str], *, cwd: str | None = None, timeout: int = 120, _run=subprocess.run
) -> CommandResult:
    """Run an argv list (no shell). Fail-soft on timeout/OS error. Used by git/gh wrappers."""
    try:
        proc = _run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return CommandResult(False, 124, f"timed out after {timeout}s")
    except OSError as exc:
        return CommandResult(False, 127, str(exc))
    output = (proc.stdout or "") + (proc.stderr or "")
    return CommandResult(proc.returncode == 0, proc.returncode, output)


def _tail(text: str, n: int = 20) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def command_gate_runner(
    repo_root: str | None = None, *, timeout: int = 600, _run=subprocess.run
) -> GateRunner:
    """A :data:`keel.gates.GateRunner` that executes ``command`` gates via the shell.

    Non-command gates (agentic / builtin like ``jury``) are not executed here — in
    command-only mode they pass as no-ops; the agent-dispatch layer runs those.
    """

    def runner(spec: GateSpec) -> tuple[bool, list[Finding]]:
        if spec.kind != "command" or not spec.run:
            return True, []
        result = run_command(spec.run, cwd=repo_root, timeout=timeout, _run=_run)
        if result.ok:
            return True, []
        severity = _ON_FAIL_SEVERITY[spec.on_fail]
        message = f"{spec.id} failed (exit {result.code})"
        tail = _tail(result.output)
        if tail:
            message += f": {tail}"
        path, line = first_location(result.output)
        return False, [Finding(
            severity, message, spec.id,
            path=path, line=line, anchorable=path is not None and line is not None,
        )]

    return runner
