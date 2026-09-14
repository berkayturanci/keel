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

_SECTION_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$", re.MULTILINE)
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
#: Headings whose whole section is a boundary statement **about the change** (#1168).
#: Normalised by :func:`_normalize_heading`, so ``## Non-goals`` and ``## non goals``
#: are the same heading; ``not in this change`` is this repository's own spelling,
#: used by every issue written since #1182.
#:
#: Deliberately **not** here: ``Not planned``, ``Will not do``, ``Decision``,
#: ``Status``, ``Resolution``. Those are close-reason headings — a section saying the
#: issue will not be done — and dropping them would silence the very statement intake
#: exists to read. The round-1 gate seats caught exactly that: with them in the set,
#: `## Not planned` / `This issue is out of scope; closing.` came back `ready` with
#: `can_mutate_code: true`. The test is what the section says about, not how
#: negative it sounds.
_SCOPE_EXCLUSION_HEADINGS = frozenset(
    {
        "out of scope",
        "non goals",
        "non goal",
        "not in scope",
        "not in this change",
    }
)
_OUT_OF_SCOPE_DECLARATION_RE = re.compile(
    r"(?:^(?:out[- ]of[- ]scope|not planned|wontfix|won't fix|not in scope)\b|"
    r"\b(?:this|the)\s+issue\s+(?:is|was|remains)\s+"
    r"(?:out[- ]of[- ]scope|not planned|wontfix|won't fix|not in scope)\b)",
    re.IGNORECASE,
)
#: Markdown a sentence can *start with* that is not part of the sentence: a list
#: marker, a task box, a block quote, an emphasis run. `_sentences` joins stripped
#: lines and keeps these, and the declaration pattern's first alternative is anchored
#: at the start — so `- Out of scope: closing.` slipped through while
#: `Out of scope: closing.` did not. Found by the round-3 gate seat, which also noted
#: `1. ` happened to work only because `_sentences` splits on the `.`.
_LEADING_MARKUP_RE = re.compile(
    r"^(?:\s*(?:[-*+]|\d+[.)])\s+|\s*>\s*|\s*\[[ xX]?\]\s*|\*{1,3}|_{1,3})+"
)


def _declaration_candidate(text: str) -> str:
    """One sentence with any leading markdown markers taken off its front."""
    return _LEADING_MARKUP_RE.sub("", text.strip()).strip()


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
    out_of_scope_reason = _out_of_scope_reason(
        title=context.title,
        body=context.body,
        labels=normalized_labels,
    )
    if out_of_scope_reason:
        status = OUT_OF_SCOPE
        reason = out_of_scope_reason
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


def _structural_lines(text: str) -> list[str]:
    """Lines that stand on their own: list items, markers removed.

    Needed because `_sentences` joins lines that carry no terminator, so a plain bullet
    above a declaration — ``- Discussed with the team`` then ``- Out of scope:
    closing.`` — put the declaration mid-sentence, past the pattern's anchor.

    **List items only.** Splitting on every line re-anchors the pattern at each wrap and
    refuses prose that merely mentions scope — ``The parser rewrite is\nout of scope for
    this change.`` came back `out-of-scope`, the inverse of #1188. Block quotes were
    tried and removed for the same reason: Markdown prefixes *every* continuation line
    of a quote with ``>``, so a wrapped quote is a wrap wearing a marker. A quoted
    declaration on one line is still caught, through `_sentences` and the leading-markup
    strip. Each of these directions was found by a gate seat, one round apart.
    """
    lines: list[str] = []
    for line in text.splitlines():
        if match := _BULLET_RE.match(line):
            if item := match.group("text").strip():
                lines.append(item)
    return lines


def _sentences(text: str) -> list[str]:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if not compact:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", compact) if part.strip()]
    return parts or [compact]


def _is_scope_exclusion_heading(title: str) -> bool:
    """Does this heading open a section that bounds the **change**?

    The test is *starts with*, not equals. ``## Out of scope for v1``,
    ``## Out of scope: mobile UI`` and ``## Non-goals for now`` are the same kind of
    section as ``## Out of scope``, and an equality test refused the issues that wrote
    them — #1168 coming back through the heading, which is now read as prose. A section
    titled "Out of scope…" is a boundary whatever qualifies it.

    A close-reason heading (``Not planned``, ``Decision``, ``Status``) is deliberately
    not in the set: it names a status for the issue, and dropping it would silence the
    statement intake exists to read.
    """
    normalised = _normalize_heading(title)
    return any(
        normalised == heading or normalised.startswith(heading + " ")
        for heading in _SCOPE_EXCLUSION_HEADINGS
    )


def _scannable_chunks(body: str) -> list[str]:
    """``body`` split into the pieces a declaration may hide in, boundaries removed.

    A scope-exclusion section goes whole — heading and content. What remains comes back
    per section, with the heading's own text as a chunk **separate from** its body:
    inline, it glues onto the sentence below (`- works ## Decision Out of scope:
    closing.`) and the declaration pattern is anchored at a sentence start; dropped, a
    declaration written *in* a heading becomes invisible. Each gate round found one of
    those halves.
    """
    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        return [body]
    chunks = [body[: matches[0].start()]]
    #: Depth of the exclusion section currently being skipped, or 0. A section owns its
    #: nested headings: `## Out of scope` followed by `### Mobile` is one boundary, and
    #: skipping only the heading that matched let the sub-section — and its bullets —
    #: back in, which refused the issue again.
    skipping_depth = 0
    for index, match in enumerate(matches):
        depth = len(match.group("hashes"))
        if skipping_depth:
            if depth > skipping_depth:
                continue
            skipping_depth = 0
        if _is_scope_exclusion_heading(match.group("title")):
            skipping_depth = depth
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        chunks.append(match.group("title"))
        chunks.append(body[match.end() : end])
    return [chunk for chunk in chunks if chunk.strip()]


def _out_of_scope_reason(
    *,
    title: str | None,
    body: str | None,
    labels: tuple[str, ...],
) -> str | None:
    """Return the concrete scope declaration, ignoring structural exclusions.

    Two independent filters, and each does one job:

    * **Structurally**, a scope-exclusion section is removed whole. Its heading and its
      bullets describe what the change leaves out; none of it says the issue is closed.
    * **In what remains**, only a sentence shaped like a *declaration about the issue*
      counts — ``Out of scope: …`` opening a sentence, or ``this issue is out of
      scope`` — so ordinary prose saying some detail is out of scope for this change
      still reads as ordinary prose, wherever it sits.

    Everything left is searched, title and body alike. A maintainer writing
    ``## Decision — this issue is out of scope; closing`` is declaring it closed, and
    intake must not hand that to s2.

    ``non-goal`` is deliberately **not** a declaration marker. It names a non-goal of
    the change ("Non-goal: rewrite the parser"), which is a boundary like the section
    it usually appears under, not a statement that the issue will not be done. The
    ``## Non-goals`` heading is handled structurally above.
    """
    for label in labels:
        if label in _OUT_OF_SCOPE_LABELS:
            return f"Issue carries out-of-scope label: {label}."

    candidates: list[str] = []
    if title and title.strip():
        candidates.extend(_sentences(title.strip()))
    if body:
        for chunk in _scannable_chunks(body):
            # Sentences, plus the lines that stand on their own — see
            # `_structural_lines` for why that is list items and quotes rather than
            # every line.
            candidates.extend(_sentences(chunk))
            candidates.extend(_structural_lines(chunk))

    for candidate in candidates:
        # Stripped of leading markers first: the pattern's first alternative is anchored
        # at the start of the sentence, and a bullet or a quote marker is not the
        # sentence. The reason text keeps the sentence as written.
        if _OUT_OF_SCOPE_DECLARATION_RE.search(_declaration_candidate(candidate)):
            return f"Issue declares itself out of scope: {candidate.strip()}"
    return None


def _is_blocked(combined: str, labels: tuple[str, ...]) -> bool:
    if not _BLOCKED_LABELS.isdisjoint(labels):
        return True

    compact = " ".join(line.strip() for line in combined.splitlines() if line.strip())
    # Fast path: bypass expensive sentence iteration if no
    # blocked regex match exists globally in normalized text
    if not _BLOCKED_RE.search(compact):
        return False

    # ⚡ Bolt Optimization: Unroll any() generator to avoid generator overhead
    for sentence in _sentences(compact):
        if _is_actionable_blocker(sentence):
            return True
    return False


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
