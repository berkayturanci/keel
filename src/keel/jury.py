"""Optional ai-jury integration — keel's ``jury`` built-in gate.

Runs the external ``jury`` CLI (the separate, stdlib-only multi-agent reviewer) on the PR
diff and maps its findings into keel :class:`~keel.findings.Finding`s. **Fail-soft**: a
missing ``jury`` CLI — or any non-JSON / error output — is a no-op pass. keel never blocks
a merge because a review tool isn't installed. The pure parser is unit-tested; the thin
subprocess call goes through the injectable ``_run`` seam.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile

from .findings import SEVERITIES, Finding, summarize
from .runner import run_argv

_SEV = frozenset(SEVERITIES)


def parse_jury_findings(payload: dict) -> list[Finding]:
    """Map a ``jury --format json`` payload's ``findings[]`` into keel Findings."""
    out: list[Finding] = []
    for f in payload.get("findings") or []:
        sev = str(f.get("severity", "minor")).strip().lower()
        if sev not in _SEV:
            sev = "minor"
        message = str(f.get("claim") or f.get("evidence") or "jury finding")
        path = f.get("file") or None
        raw_line = f.get("line")
        line = int(raw_line) if isinstance(raw_line, int) else (
            int(raw_line) if isinstance(raw_line, str) and raw_line.isdigit() else None
        )
        out.append(Finding(sev, message, "jury", path=path, line=line,
                           anchorable=path is not None and line is not None))
    return out


def _load_payload(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def run_jury(diff_text: str, *, mock: bool = False, timeout: int = 600, _run=subprocess.run):
    """Run the ``jury`` CLI on ``diff_text``. Returns ``(ok, findings)``.

    Fail-soft: if the CLI is absent or emits non-JSON, returns ``(True, [])`` (no-op).
    Otherwise blocks (``ok=False``) when jury reports a ``critical``/``major`` finding.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as fh:
        fh.write(diff_text or "")
        path = fh.name
    try:
        argv = ["jury", "--diff-file", path, "--format", "json"]
        if mock:
            argv.append("--mock")
        result = run_argv(argv, timeout=timeout, _run=_run)
    finally:
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover - best-effort cleanup
            pass

    payload = _load_payload(result.output)
    if payload is None:
        return True, []
    findings = parse_jury_findings(payload)
    return (not summarize(findings).blocked), findings
