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


def parse_report(data: dict | str) -> list[Finding] | None:
    """Map an ai-jury JSON report into Findings, or ``None`` if it is not a report.

    The ``None`` return is the point: it separates *"the panel reviewed the diff and
    found nothing"* from *"this output is not a verdict at all"*, which
    :func:`parse_findings` collapses into the same empty list. Only the caller that
    decides whether a gate passed needs that distinction — see :func:`run_gate`.

    Tolerates trailing non-JSON. :func:`keel.runner.run_argv` hands back
    ``stdout + stderr`` concatenated, and ai-jury logs its progress to stderr, so a
    real report is followed by ``[jury] …`` lines. A strict ``json.loads`` rejects the
    whole thing and silently loses every finding.
    """
    if isinstance(data, str):
        try:
            data, _end = json.JSONDecoder().raw_decode(data.lstrip())
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict) or "findings" not in data:
        return None
    return _findings_from(data)


def parse_findings(data: dict | str) -> list[Finding]:
    """Map an ai-jury JSON report (dict or raw string) into keel Findings.

    Unparseable input yields ``[]``. Use :func:`parse_report` when the difference
    between "no findings" and "no report" matters.
    """
    return parse_report(data) or []


def _findings_from(data: dict) -> list[Finding]:
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


def _unreadable_diff_finding(*, severity: str = "minor") -> Finding:
    """Record that the diff itself could not be read, so no review was possible."""
    return Finding(
        severity=severity,
        message=("jury could not run: the diff could not be read from git (is the base "
                 "branch fetched locally? a shallow or single-branch clone cannot "
                 "resolve base...HEAD). No review was performed."),
        source="jury:unreadable-diff",
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
) -> tuple[bool, list[Finding], bool]:
    """Run ``jury`` on ``diff_text`` and map its findings.

    Returns ``(ok, findings, timed_out)``. ``ok`` is False when a finding blocks
    (critical/major) or when the run produced no verdict at all in gating mode.
    Fail-soft no-op when there is no diff or the ``jury`` CLI is not installed — keel
    does not depend on ai-jury, so an absent CLI is a legitimate no-op, distinct from
    a run that started and did not finish.

    Three ways a run can end without a review, all handled alike — gating fails closed
    with a blocking ``major``, advisory surfaces a ``minor``:

    * the diff is oversize and was never submitted,
    * the CLI was killed by ``timeout``,
    * the CLI returned no parseable verdict, whatever its exit code.

    The last used to report ``(True, [])``: :func:`parse_findings` yields ``[]`` for
    unparseable output, so ``blocked`` came out False and a hung, crashed, or
    unreadable panel read as a clean pass. The test is deliberately *"did we parse a
    verdict"* rather than *"was the exit code zero"* — ai-jury exits nonzero to signal
    "request changes", which is a completed review whose findings must be honoured,
    while an exit of zero carrying unreadable output is not a review at all.
    """
    if diff_text is None:
        # The diff could not be read (git failed). That is not "nothing to review":
        # passing here would silently remove the review gate from the merge decision,
        # which is the same fail-open the verdict check below exists to prevent.
        gating = mode == "gating"
        return (not gating), [_unreadable_diff_finding(
            severity="major" if gating else "minor")], False
    if not diff_text:
        return True, [], False
    size = len(diff_text.encode("utf-8"))
    if size > MAX_DIFF_BYTES:
        if mode == "gating":
            return False, [_oversize_finding(size, severity="major")], False
        return True, [_oversize_finding(size)], False
    if not available(cwd=cwd, _run=_run):
        return True, [], False
    fd, path = tempfile.mkstemp(suffix=".diff")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(diff_text)
        result = run_argv(["jury", "--format", "json", "--diff-file", path],
                          cwd=cwd, timeout=timeout, **_kw(_run))
    finally:
        os.unlink(path)
    # stdout alone: ai-jury logs its progress (`[jury] …`) to stderr, and reading the
    # concatenation is what made every report unparseable (#624). `parse_report` still
    # tolerates trailing non-JSON, for a vendor that also chats on stdout.
    report = parse_report(result.stdout)
    if report is None:
        gating = mode == "gating"
        incomplete = _incomplete_finding(
            result, timeout=timeout, severity="major" if gating else "minor")
        # timed_out rides along so the outcome renders as TIMEOUT rather than FAIL,
        # the distinction #622 established for command gates.
        return (not gating), [incomplete], result.timed_out
    blocked = any(f.severity in ("critical", "major") for f in report)
    return (not blocked), report, False
