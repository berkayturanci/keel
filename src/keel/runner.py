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
# `command_unset` and `unconfigured_finding` ride the same import and are used here,
# not re-exported.
from .gates import GateRunner, command_unset, unconfigured_finding  # noqa: E402

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
    #: True when the command could **not be started** — the ``OSError`` path, which for a
    #: delegate almost always means "that binary is not installed". Distinct from exit
    #: 127, which a command that *did* run can also return: `run_argv` reports both as
    #: code 127, so classifying on the code alone cannot tell "no such binary" from "the
    #: tool ran and said 127". Every caller that needs the difference reads this flag.
    spawn_failed: bool = False


def _result(proc) -> CommandResult:
    out = _decoded(proc.stdout)
    err = _decoded(proc.stderr)
    return CommandResult(proc.returncode == 0, proc.returncode, out + err, stdout=out, stderr=err)


def run_command(
    cmd: str,
    *,
    cwd: str | None = None,
    timeout: int = DEFAULT_GATE_TIMEOUT_S,
    env: dict[str, str] | None = None,
    _run=subprocess.run,
) -> CommandResult:
    """Run ``cmd`` in a shell, capturing output. Fail-soft on timeout/OS error.

    ``env`` replaces the child's environment when given (``None`` inherits it).
    """
    try:
        # Intentional shell boundary: cmd must come only from operator-controlled
        # project config or extension YAML, never from PR content or agent output.
        proc = _run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            # **UTF-8, not the platform default.** `text=True` alone decodes with
            # `locale.getencoding()`, which on Windows is the ANSI code page: cp1252
            # leaves 0x81/8D/8F/90/9D undefined, so `git ls-tree -z` on a repository
            # holding a Cyrillic filename (`Ё` is D0 81) raised UnicodeDecodeError out
            # of the subprocess call — past `run_argv`'s own `TimeoutExpired`/`OSError`
            # guards, turning every fail-soft reader into a traceback. `surrogateescape`
            # also round-trips undecodable bytes back out unchanged, which the landing
            # needs: the names it reads from `ls-tree` are written straight back to
            # `mktree`.
            encoding="utf-8",
            errors="surrogateescape",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=env,
        )  # nosec B604
    except subprocess.TimeoutExpired:
        return CommandResult(False, 124, f"timed out after {timeout}s", timed_out=True)
    except OSError as exc:
        return CommandResult(False, 127, str(exc), stderr=str(exc), spawn_failed=True)
    return _result(proc)


def run_argv(
    argv: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 120,
    stdin_text: str | None = None,
    env: dict[str, str] | None = None,
    keep_line_endings: bool = False,
    _run=subprocess.run,
) -> CommandResult:
    """Run an argv list (no shell). Fail-soft on timeout/OS error. Used by git/gh wrappers.

    ``keep_line_endings`` reads the output as bytes and decodes it here, the same way
    (UTF-8, ``surrogateescape``), because text mode's universal newlines turn every
    ``\\r\\n`` into ``\\n``: a diff of a CRLF file then no longer matches its own lines.

    ``env`` replaces the child's environment when given (``None`` inherits it) — for a
    caller that must keep a variable such as ``GIT_DIFF_OPTS`` away from the child.

    ``stdin_text`` feeds the child on standard input instead of closing it. Every delegate
    CLI keel dispatches to takes its prompt that way (:mod:`keel.delegate`): a prompt
    carries the diff and the brief, and an argv is world-readable in ``ps`` for the life of
    the process. The default stays ``DEVNULL`` — a gate left waiting for input in an
    unattended run is a hang, not a prompt.
    """
    try:
        proc = _run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=not keep_line_endings,
            # **UTF-8, not the platform default.** `text=True` alone decodes with
            # `locale.getencoding()`, which on Windows is the ANSI code page: cp1252
            # leaves 0x81/8D/8F/90/9D undefined, so `git ls-tree -z` on a repository
            # holding a Cyrillic filename (`Ё` is D0 81) raised UnicodeDecodeError out
            # of the subprocess call — past `run_argv`'s own `TimeoutExpired`/`OSError`
            # guards, turning every fail-soft reader into a traceback. `surrogateescape`
            # also round-trips undecodable bytes back out unchanged, which the landing
            # needs: the names it reads from `ls-tree` are written straight back to
            # `mktree`.
            encoding=None if keep_line_endings else "utf-8",
            errors=None if keep_line_endings else "surrogateescape",
            timeout=timeout,
            env=env,
            input=(
                stdin_text.encode("utf-8")
                if keep_line_endings and stdin_text is not None
                else stdin_text
            ),
            # Written out rather than assembled into a **kwargs dict: #879's sweep in
            # tests/test_missing_pins.py reads every spawn site's keywords out of the
            # AST, and a site that hides `stdin` behind a splat is a site the rule
            # cannot see. `subprocess.run` accepts `stdin=None` beside `input` and
            # substitutes a PIPE itself.
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(False, 124, f"timed out after {timeout}s", timed_out=True)
    except OSError as exc:
        return CommandResult(False, 127, str(exc), stderr=str(exc), spawn_failed=True)
    return _result(proc)


def _decoded(data: bytes | str | None) -> str:
    """Captured output as text: bytes (``keep_line_endings``) are decoded as text mode
    would, minus its newline translation."""
    if isinstance(data, bytes):
        return data.decode("utf-8", "surrogateescape")
    return data or ""


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
        if spec.kind != "command":
            # Not executed here — the agent-dispatch layer runs agentic gates. Flagged
            # `not_run` so this can never be recorded as "ran and passed"; `ok` stays
            # True so a soft gate does not spuriously fail a command-only run.
            return True, [], False, True
        if command_unset(spec):
            # A command gate with nothing to run (an unset `knobs.build_gate_cmd`, #1328,
            # or a blank one, #1364 — `sh -c ' '` exits 0) is ours and it fails, naming
            # the knob. "not_run" would read as "record a result for this gate" rather
            # than "configure it".
            return False, [unconfigured_finding(spec)], False, False
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
