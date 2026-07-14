"""Pure issue intake and readiness classification for work-owning commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

READY = "ready"
NEEDS_INPUT = "needs-input"
BLOCKED = "blocked"
OUT_OF_SCOPE = "out-of-scope"
READINESS_STATUSES = (READY, NEEDS_INPUT, BLOCKED, OUT_OF_SCOPE)

_SECTION_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?P<text>.+?)\s*$")
_BLOCKED_RE = re.compile(
    r"\b(blocked by|depends on|dependency|waiting on|needs dependency|blocked until)\b",
    re.IGNORECASE,
)
_NON_BLOCKING_DEPENDENCY_RE = re.compile(
    r"\b(no dependenc(?:y|ies)|dependenc(?:y|ies):\s*(?:none|no|n/a)|"
    r"depends on:\s*(?:none|no|n/a)|blocked by:\s*(?:none|no|n/a)|"
    r"waiting on\s+(?:none|no one|nobody|no-one))\b",
    re.IGNORECASE,
)
_AMBIGUOUS_RE = re.compile(
    r"\b(tbd|todo|unclear|ambiguous|maybe|not sure|needs clarification|decide later)\b",
    re.IGNORECASE,
)
_OUT_OF_SCOPE_LABELS = frozenset({"out-of-scope", "wontfix", "not-planned"})
_OUT_OF_SCOPE_RE = re.compile(
    r"\b(out[- ]of[- ]scope|not planned|wontfix|won't fix|non-goal|not in scope)\b",
    re.IGNORECASE,
)
_DOCS_RE = re.compile(r"\b(doc|docs|documentation|readme|changelog)\b", re.IGNORECASE)
_TESTS_RE = re.compile(r"\b(test|tests|coverage|ci|lint)\b", re.IGNORECASE)
_BLOCKED_LABELS = frozenset({"blocked", "status:blocked", "needs-dependency"})
_RISK_RE = re.compile(
    r"\b(security|release|migration|schema|api|breaking|billing|secret|credential|ci|"
    r"production|compatibility)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IssueContext:
    """Issue text supplied by an adapter before code mutation starts."""

    title: str | None = None
    body: str | None = None
    labels: tuple[str, ...] = ()

    @property
    def provided(self) -> bool:
        return bool((self.title or "").strip() or (self.body or "").strip() or self.labels)


def assess_issue(
    *,
    title: str | None = None,
    body: str | None = None,
    labels: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return a deterministic readiness record for an issue-like work item."""
    context = IssueContext(title=title, body=body, labels=tuple(labels))
    sections = _sections(context.body or "")
    acceptance = _acceptance_criteria(sections)
    objective = _objective(context.title, context.body, sections)
    deliverable = _deliverable(sections, acceptance)
    combined = " ".join(filter(None, (context.title, context.body, " ".join(context.labels))))
    normalized_labels = tuple(label.strip().lower() for label in context.labels)

    missing: list[str] = []
    blockers: list[str] = []
    questions: list[str] = []

    if not objective:
        missing.append("objective")
        questions.append("What objective should this issue accomplish?")
    if not deliverable:
        missing.append("deliverable")
        questions.append("What concrete deliverable should be produced?")
    if not acceptance:
        missing.append("acceptance_criteria")
        questions.append("What acceptance criteria define done for this issue?")

    status = READY
    reason = "Issue has an objective, deliverable, and acceptance criteria."
    if _is_out_of_scope(combined, normalized_labels):
        status = OUT_OF_SCOPE
        reason = "Issue is marked out of scope or not planned."
        questions = []
    elif _is_blocked(combined, normalized_labels):
        status = BLOCKED
        reason = "Issue declares a dependency or waiting condition."
        blockers.append(_blocked_summary(combined))
        questions.append("What dependency must clear before this issue can start?")
    elif missing:
        status = NEEDS_INPUT
        reason = f"Missing required intake field(s): {', '.join(missing)}."
    elif _AMBIGUOUS_RE.search(combined):
        status = NEEDS_INPUT
        reason = "Issue scope contains ambiguous or deferred wording."
        missing.append("scope_clarity")
        questions.append("Which exact scope should be implemented now?")

    return {
        "schema_version": "keel.issue-intake.v1",
        "provided": context.provided,
        "status": status,
        "can_mutate_code": status == READY,
        "work_block_policy": {
            "skip_when_not_ready": status != READY,
            "continue_with_next_ready_issue": status != READY,
            "non_ready_statuses": [NEEDS_INPUT, BLOCKED, OUT_OF_SCOPE],
        },
        "reason": reason,
        "objective": objective,
        "deliverable": deliverable,
        "acceptance_criteria": acceptance,
        "risk_tier_inputs": _risk_inputs(combined, normalized_labels),
        "required_docs_tests": _required_docs_tests(combined, acceptance),
        "missing_info": missing,
        "blockers": blockers,
        "questions": _unique(questions)[:3],
        "ledger_record": {
            "readiness": status,
            "mutation_allowed": status == READY,
            "skip_reason": None if status == READY else reason,
            "question_count": len(_unique(questions)[:3]),
        },
    }


def _sections(body: str) -> dict[str, str]:
    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        return {}
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        title = _normalize_heading(match.group("title"))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[title] = body[start:end].strip()
    return sections


def _normalize_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _objective(title: str | None, body: str | None, sections: dict[str, str]) -> str | None:
    for key in ("objective", "problem", "summary", "context"):
        if value := _first_sentence_or_bullet(sections.get(key, "")):
            return value
    if body and not sections:
        return _first_sentence_or_bullet(body)
    return title.strip() if title and title.strip() else None


def _deliverable(sections: dict[str, str], acceptance: list[str]) -> str | None:
    del acceptance
    for key in ("deliverable", "proposed direction", "proposal", "scope", "implementation"):
        if value := _first_sentence_or_bullet(sections.get(key, "")):
            return value
    return None


def _acceptance_criteria(sections: dict[str, str]) -> list[str]:
    for key in (
        "acceptance criteria",
        "acceptance",
        "definition of done",
        "done when",
        "dod",
    ):
        if key in sections:
            bullets = _bullets(sections[key])
            return bullets if bullets else _sentences(sections[key])
    return []


def _first_sentence_or_bullet(text: str) -> str | None:
    bullets = _bullets(text)
    if bullets:
        return bullets[0]
    sentences = _sentences(text)
    return sentences[0] if sentences else None


def _bullets(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        if match := _BULLET_RE.match(line):
            item = match.group("text").strip()
            if item:
                items.append(item)
    return items


def _sentences(text: str) -> list[str]:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if not compact:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", compact) if part.strip()]
    return parts or [compact]


def _is_out_of_scope(combined: str, labels: tuple[str, ...]) -> bool:
    return not _OUT_OF_SCOPE_LABELS.isdisjoint(labels) or bool(
        _OUT_OF_SCOPE_RE.search(combined)
    )


def _is_blocked(combined: str, labels: tuple[str, ...]) -> bool:
    if not _BLOCKED_LABELS.isdisjoint(labels):
        return True

    compact = " ".join(line.strip() for line in combined.splitlines() if line.strip())
    # Fast path: bypass expensive sentence iteration if no
    # blocked regex match exists globally in normalized text
    if not _BLOCKED_RE.search(compact):
        return False

    return any(_is_actionable_blocker(sentence) for sentence in _sentences(compact))


def _blocked_summary(combined: str) -> str:
    sentences = _sentences(combined)
    for sentence in sentences:
        if _is_actionable_blocker(sentence):
            return sentence
    return "Declared blocked dependency."


def _is_actionable_blocker(text: str) -> bool:
    return bool(_BLOCKED_RE.search(text)) and not bool(_NON_BLOCKING_DEPENDENCY_RE.search(text))


def _risk_inputs(combined: str, labels: tuple[str, ...]) -> dict[str, Any]:
    keywords = _unique(match.group(0).lower() for match in _RISK_RE.finditer(combined))
    risk_labels = [label for label in labels if "risk" in label or label.startswith("tier")]
    return {
        "keywords": keywords,
        "labels": risk_labels,
        "has_high_risk_signal": bool(keywords or risk_labels),
    }


def _required_docs_tests(combined: str, acceptance: list[str]) -> dict[str, Any]:
    text = " ".join([combined, *acceptance])
    return {
        "docs": "required" if _DOCS_RE.search(text) else "unspecified",
        "tests": "required" if _TESTS_RE.search(text) else "unspecified",
    }


def _unique(items) -> list[str]:
    seen = set()
    result = []
    for item in items:
        v = str(item).strip()
        if v and v not in seen:
            seen.add(v)
            result.append(v)
    return result
