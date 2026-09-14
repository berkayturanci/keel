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
#: The short form, anchored at the start, for the **title** and nowhere else.
#:
#: A title is one line, with nothing in front of it and nothing after it to qualify — it
#: names the issue's whole subject. A body sentence is not: `Out of scope for v1: the
#: Android client.` opening an issue is a boundary, `Not in scope for Windows.` is a
#: carve-out, and neither closes anything. Fifteen review rounds went into trying to tell
#: those from a closure by position, and each rule inverted on some real sentence. The
#: body asks the one question that has an answer: is the issue the subject.
_OUT_OF_SCOPE_OPENER_RE = re.compile(
    r"^(?:out[- ]of[- ]scope|not planned|wontfix|won't fix|not in scope)\b",
    re.IGNORECASE,
)
#: A declaration **in the body**: a sentence whose subject is the issue.
#:
#: Anchored at the start of the *sentence* — which is not the same as the start of a
#: line, and that distinction is the whole lesson of this change. Eight rounds tried to
#: anchor a short `Out of scope: …` form at a line start and it broke on every piece of
#: markdown that can precede one. A sentence start is stable, and requiring the issue to
#: be the **subject** is what separates a closure from a mention: "This issue is out of
#: scope" closes it, "A backport of this issue is out of scope" carves out a backport.
#: An unanchored match refused the second, which is #1168 inverted.
_OUT_OF_SCOPE_DECLARATION_RE = re.compile(
    r"^(?:this|the)\s+issue\s+(?:is|was|remains)\s+"
    r"(?:out[- ]of[- ]scope|not planned|wontfix|won't fix|not in scope)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
#: Markdown that can sit in front of a sentence without being part of it. Stripped before
#: the anchor is applied, so a bullet or a quote marker does not hide the subject.
_LEADING_MARKUP_RE = re.compile(
    r"^(?:"
    r"\s*(?:[-*+]|\d+[.)])\s+"  # list marker
    r"|\s*>\s*"  # block quote
    r"|\s*\[[ xX]?\]\s*"  # task box
    r"|\s*(?:-{3,}|\*{3,}|_{3,})\s*"  # thematic break
    # `[\s\S]` rather than `.`: an HTML comment may span lines, and `.` does not cross
    # one, so a multi-line comment in front of a sentence was left where it stood.
    r"|\s*<!--[\s\S]*?-->\s*"  # html comment
    r"|\s*!\[[^\]]*\]\([^)]*\)\s*"  # image
    r"|[*_]{1,3}"  # emphasis run
    r"|\s+"
    r")+"
)
#: A short label opening a heading or a line — ``Decision — …``, ``Status: …``. Removed
#: before the anchor so the subject that follows is seen, while a sentence that merely
#: *mentions* the issue further in ("A backport of this issue is out of scope") is not,
#: because it carries no such separator before the subject. Bounded in length and
#: forbidden sentence-ending punctuation, so it cannot eat a real clause.
_LEADING_LABEL_RE = re.compile(
    # A label is a **short name**, not a clause: at most three words, each a word with
    # optional internal punctuation, then an explicit terminator. `Decision:`,
    # `Follow-up:`, `Update 2026-09-14:`, `Decision (2026-09-14) —` all qualify.
    #
    # The length-capped "anything up to a terminator" form this replaces let the label
    # strip re-anchor the pattern in the middle of a compound sentence: `Users need safer
    # sync — this issue is not in scope for Windows.` became `this issue is not in scope
    # for Windows.` and read as a closure, when it carves out Windows. That is #1168
    # inverted, and the subject test cannot see it because the subject really is there —
    # just not at the start of the sentence, which is the whole point of the anchor.
    r"^\w[\w.()/'’-]*(?:\s+[\w.()/'’-]+){0,2}\s*[:\u2014\u2013]\s*"
    # A spaced hyphen separates the same way; `-` inside a word does not.
    r"|^\w[\w.()/'’]*(?:\s+[\w.()/'’]+){0,2}\s+-\s+"
)


def _declaration_candidates(text: str, *, allow_label: bool = False) -> tuple[str, ...]:
    """Every prefix-stripped form of one sentence the declaration may be anchored in.

    The sentence **as written** comes first, because the label pattern cannot tell a
    `Decision:` prefix from the declaration's own `out of scope: closing.` — stripping
    unconditionally ate the closure down to `closing.`. Then markdown removed; then a
    label removed and markdown removed *again*, because `**Decision:** This issue is…`
    leaves a closing `**` between the label and the subject that the first pass cannot
    see. Each round of this review found one of these.
    """
    forms = [text.strip()]
    stripped = _LEADING_MARKUP_RE.sub("", forms[0])
    if stripped != forms[0]:
        forms.append(stripped)
    if not allow_label:
        return tuple(forms)
    delabelled = _LEADING_LABEL_RE.sub("", stripped, count=1).strip()
    if delabelled != stripped:
        forms.append(_LEADING_MARKUP_RE.sub("", delabelled).strip())
    return tuple(forms)


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


def _bullet_lines(text: str) -> list[str]:
    """List items, markers removed — each a statement in its own right.

    `_sentences` joins lines carrying no terminator, so a plain bullet above a
    declaration (`- Discussed with the team` then `- This issue is out of scope.`) puts
    the declaration mid-sentence, past the anchor. A list item is a statement whether or
    not it ends in a full stop.

    List items **only**. Every line was tried and refused prose the moment a wrap put the
    phrase at a line start; block quotes were tried and refused it too, because Markdown
    prefixes each continuation line of a quote with `>`. Neither shape carries a list
    marker. The subject test is what makes this safe now — a bullet has to name the issue
    to match anything at all.
    """
    return [item for line in text.splitlines() if (item := _bullet_text(line))]


def _bullet_text(line: str) -> str:
    match = _BULLET_RE.match(line)
    return match.group("text").strip() if match else ""


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


def _scannable_chunks(body: str) -> list[tuple[bool, str]]:
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
        return [(False, body)]
    chunks: list[tuple[bool, str]] = [(False, body[: matches[0].start()])]
    for index, match in enumerate(matches):
        # An exclusion section owns its own body and nothing else. Owning nested headings
        # was tried and failed in both directions: `# Out of scope` over a run of `##`
        # sections swallowed the `## Decision` that closed the issue, and every heuristic
        # for telling a title level from a section level inverted on some real body. What
        # makes the simple rule safe is the subject test above — a bullet under a boundary
        # section saying `Out of scope: the mobile client` names no issue and matches
        # nothing, so the section's *contents* no longer need hiding, only its own prose.
        if _is_scope_exclusion_heading(match.group("title")):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        # Flagged as a heading: its text may carry a record label (`Decision —`) in front
        # of the declaration. It does **not** get the short form — `## Not planned` over a
        # list of features is a boundary section, and reading its title as a closure
        # refused the issue while the identical bullets under `## Non-goals` passed.
        chunks.append((True, match.group("title")))
        chunks.append((False, body[match.end() : end]))
    return [(is_heading, chunk) for is_heading, chunk in chunks if chunk.strip()]


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
    * **In what remains**, a declaration is a sentence whose *subject is the issue* —
      ``this issue is out of scope``, opening the sentence. Not ``Out of scope: …``,
      which is the same string as a boundary bullet; not a mention in the middle of a
      sentence, which carves out a part rather than closing the whole. The short form
      is the title's, where one line and no markdown make an anchor safe.

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

    # The title takes the short form: it names the issue's whole subject, with nothing
    # before it and nothing after it to qualify.
    if title and (heading := title.strip()):
        if _OUT_OF_SCOPE_OPENER_RE.search(heading) or _OUT_OF_SCOPE_DECLARATION_RE.search(heading):
            return f"Issue declares itself out of scope: {heading}"

    #: `(text, may a leading label be stripped)`. A heading's `Decision —` prefix is a
    #: record label; the same shape inside prose is a prepositional phrase (`For Windows:
    #: this issue is not in scope.`), and stripping it turned a carve-out into a closure.
    candidates: list[tuple[str, bool]] = []
    if body:
        for is_heading, chunk in _scannable_chunks(body):
            candidates.extend((sentence, is_heading) for sentence in _sentences(chunk))
            candidates.extend((line, False) for line in _bullet_lines(chunk))

    for candidate, allow_label in candidates:
        # Leading markdown removed first, so the anchor sees the sentence rather than the
        # bullet in front of it.
        if any(
            _OUT_OF_SCOPE_DECLARATION_RE.search(form)
            for form in _declaration_candidates(candidate, allow_label=allow_label)
        ):
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
