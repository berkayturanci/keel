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
from .runner import run_argv

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


def run_gate(diff_text: str, *, cwd: str | None = None, _run=None) -> tuple[bool, list[Finding]]:
    """Run ``jury`` on ``diff_text`` and map its findings.

    Returns ``(ok, findings)``; ``ok`` is False only when a finding blocks
    (critical/major). Fail-soft no-op (``(True, [])``) when there is no diff or the
    ``jury`` CLI is not installed.
    """
    if not diff_text or len(diff_text.encode("utf-8")) > MAX_DIFF_BYTES:
        return True, []
    if not available(cwd=cwd, _run=_run):
        return True, []
    fd, path = tempfile.mkstemp(suffix=".diff")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(diff_text)
        result = run_argv(["jury", "--format", "json", "--diff-file", path],
                          cwd=cwd, timeout=600, **_kw(_run))
    finally:
        os.unlink(path)
    findings = parse_findings(result.output)
    blocked = any(f.severity in ("critical", "major") for f in findings)
    return (not blocked), findings
