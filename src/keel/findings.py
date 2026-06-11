"""Structured findings + the severity → gate-decision mapping.

Every quality signal in keel — a reviewer finding, a gate result, a jury
consensus item — is normalised into a :class:`Finding`. The merge decision is a
pure function of the findings, mirroring ``ship``'s gating rules:

* ``critical`` / ``major`` ⇒ **block** the merge,
* ``minor``               ⇒ **suggest** (gated like a 5c suggestion),
* ``nit``                 ⇒ **advisory** (logged, never gates).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Severities, most to least serious.
SEVERITIES: tuple[str, ...] = ("critical", "major", "minor", "nit")
_RANK: dict[str, int] = {s: i for i, s in enumerate(SEVERITIES)}

DECISIONS: tuple[str, ...] = ("block", "suggest", "advisory")
_DECISION: dict[str, str] = {
    "critical": "block",
    "major": "block",
    "minor": "suggest",
    "nit": "advisory",
}


class FindingError(ValueError):
    """Raised on an invalid severity."""


@dataclass(frozen=True)
class Finding:
    """One normalised quality signal."""

    severity: str
    message: str
    source: str  # gate / reviewer / jury id that produced it
    path: str | None = None
    line: int | None = None
    anchorable: bool = False
    provenance: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.severity not in _RANK:
            raise FindingError(
                f"unknown severity {self.severity!r}; valid: {', '.join(SEVERITIES)}"
            )


def decision_for(severity: str) -> str:
    """Map a severity to its merge decision (``block`` / ``suggest`` / ``advisory``)."""
    try:
        return _DECISION[severity]
    except KeyError:
        raise FindingError(
            f"unknown severity {severity!r}; valid: {', '.join(SEVERITIES)}"
        ) from None


def is_anchorable(finding: Finding) -> bool:
    """True if the finding can be posted as an inline diff comment (file + line)."""
    return finding.anchorable and finding.path is not None and finding.line is not None


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Deterministic order: severity, then source, path, line, message."""
    return sorted(
        findings,
        key=lambda f: (_RANK[f.severity], f.source, f.path or "", f.line or 0, f.message),
    )


@dataclass(frozen=True)
class Verdict:
    """Aggregate decision over a set of findings."""

    blocked: bool
    counts: dict[str, int]
    findings: tuple[Finding, ...]


def summarize(findings: list[Finding]) -> Verdict:
    """Aggregate findings into a :class:`Verdict` (blocked + per-severity counts)."""
    counts = {s: 0 for s in SEVERITIES}
    blocked = False
    for f in findings:
        counts[f.severity] += 1
        if decision_for(f.severity) == "block":
            blocked = True
    return Verdict(blocked=blocked, counts=counts, findings=tuple(sort_findings(findings)))
