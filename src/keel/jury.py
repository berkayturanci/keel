"""The ``jury`` built-in gate — run the ai-jury CLI on the diff (optional, fail-soft).

keel does **not** depend on ai-jury. If the ``jury`` CLI is on PATH, this gate runs it on
the change's diff and maps its findings into keel :class:`~keel.findings.Finding`s; if it is
absent, the gate is a fail-soft no-op (the flow runs with or without jury). Parsing is pure
and unit-tested; the subprocess is behind the injectable ``_run`` seam.
"""

from __future__ import annotations

import json
import os
import tempfile

from .findings import Finding
from .model import DEFAULT_JURY_TIMEOUT_S
from .runner import CommandResult, run_argv

#: ai-jury severities → keel severities (unknown ⇒ ``minor``).
_SEVERITY = {
    "critical": "critical", "blocker": "critical",
    "major": "major",
    "minor": "minor",
    "nit": "nit", "info": "nit", "note": "nit",
}

MAX_DIFF_BYTES = 1_000_000


def map_severity(severity: str) -> str:
    """Map an ai-jury severity onto a keel severity (default ``minor``)."""
    return _SEVERITY.get((severity or "").strip().lower(), "minor")


def parse_findings(data: dict | str) -> list[Finding]:
    """Map an ai-jury JSON report (dict or raw string) into keel Findings."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return []
    if not isinstance(data, dict):
        return []
    out: list[Finding] = []
    for f in data.get("findings") or []:
        path = f.get("file")
        line = f.get("line")
        line = line if isinstance(line, int) else None
        out.append(Finding(
            severity=map_severity(f.get("severity", "")),
            message=f.get("claim") or "(jury finding)",
            source=f"jury:{f.get('reviewer') or 'consensus'}",
            path=path,
            line=line,
            anchorable=bool(path) and line is not None,
        ))
    return out


def _kw(_run):
    return {"_run": _run} if _run is not None else {}


def available(*, cwd: str | None = None, _run=None) -> bool:
    """True if the ``jury`` CLI is callable."""
    return run_argv(["jury", "--version"], cwd=cwd, timeout=30, **_kw(_run)).ok


def _incomplete_finding(
    result: CommandResult, *, timeout: int, severity: str = "nit"
) -> Finding:
    """Record that the jury CLI ran but produced no verdict.

    A timeout, or a nonzero exit whose output carries no parseable findings, means the
    panel never reached a conclusion. That is emphatically **not** a clean pass — it is
    the *absence* of a review — so in gating mode it fails closed exactly as an oversize
    diff does. The timeout case is named apart from a crash so the operator can tell a
    slow panel from a broken one.
    """
    if result.timed_out:
        detail = (f"timed out after {timeout}s; no verdict was produced. Raise "
                  "knobs.jury_timeout_s if the panel legitimately needs longer")
    else:
        detail = (f"exited {result.code} without a parseable verdict; the panel did not "
                  "complete")
    return Finding(
        severity=severity,
        message=f"jury run incomplete: the jury CLI {detail}.",
        source="jury:incomplete-run",
        path=None,
        line=None,
        anchorable=False,
    )


def _oversize_finding(size: int, *, severity: str = "nit") -> Finding:
    """Record that the jury gate skipped an oversize diff.

    Advisory jury mode keeps the finding non-blocking (``nit``). Gating jury mode
    escalates it to ``major`` so an oversize diff cannot bypass the blocking
    cross-vendor review gate.
    """
    return Finding(
        severity=severity,
        message=(f"jury skipped: diff is {size} bytes, over the {MAX_DIFF_BYTES}-byte "
                 "limit (ai-jury large-diff chunking not applied)"),
        source="jury:skipped-oversize",
        path=None,
        line=None,
        anchorable=False,
    )


def run_gate(
    diff_text: str,
    *,
    cwd: str | None = None,
    mode: str = "advisory",
    timeout: int = DEFAULT_JURY_TIMEOUT_S,
    _run=None,
) -> tuple[bool, list[Finding]]:
    """Run ``jury`` on ``diff_text`` and map its findings.

    Returns ``(ok, findings)``; ``ok`` is False when a finding blocks (critical/major)
    or when the run produced no verdict at all in gating mode. Fail-soft no-op
    (``(True, [])``) when there is no diff or the ``jury`` CLI is not installed — keel
    does not depend on ai-jury, so an absent CLI is a legitimate no-op.

    Three ways a run can end without a review, all handled the same way — advisory
    emits a non-blocking ``nit``, gating fails closed with a blocking ``major``:

    * the diff is oversize and was never submitted,
    * the CLI was killed by ``timeout``,
    * the CLI exited nonzero and its output carried no parseable findings.

    The last two used to report ``(True, [])``: :func:`parse_findings` returns ``[]``
    for unparseable output, so ``blocked`` came out False and a hung or crashed panel
    read as a clean pass. A gate that produced no verdict must never do that.
    """
    if not diff_text:
        return True, []
    size = len(diff_text.encode("utf-8"))
    if size > MAX_DIFF_BYTES:
        if mode == "gating":
            return False, [_oversize_finding(size, severity="major")]
        return True, [_oversize_finding(size)]
    if not available(cwd=cwd, _run=_run):
        return True, []
    fd, path = tempfile.mkstemp(suffix=".diff")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(diff_text)
        result = run_argv(["jury", "--format", "json", "--diff-file", path],
                          cwd=cwd, timeout=timeout, **_kw(_run))
    finally:
        os.unlink(path)
    findings = parse_findings(result.output)
    if not result.ok and not findings:
        # No verdict exists. A nonzero exit that still parsed findings is fine — the
        # panel reached a conclusion and the exit code is ai-jury's own signalling.
        gating = mode == "gating"
        incomplete = _incomplete_finding(
            result, timeout=timeout, severity="major" if gating else "nit")
        return (not gating), [incomplete]
    blocked = any(f.severity in ("critical", "major") for f in findings)
    return (not blocked), findings
