"""Stated-blocker detection for open pull requests (#1512).

A draft that says "do not merge before #3177" stays open long after #3177 closed, and
nothing surfaced it. This module reads the *stated* blockers out of PR bodies and asks
GitHub whether each has cleared.

* :func:`parse_blockers` is pure: PR body text in, blocker references out. It reuses
  :data:`keel.intake.BLOCKED_PHRASE_RE`, the phrase set issue intake already uses.
* :func:`resolve_blocker` and :func:`stale_blockers` are the thin, fail-soft ``gh`` I/O.
  A read that fails is ``unknown`` and is **never** reported as ``cleared``: an operator
  told "the blocker is gone" merges on it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .intake import BLOCKED_PHRASE_RE
from .runner import run_argv

ISSUE = "issue"
TAG = "tag"

CLEARED = "cleared"
PENDING = "pending"
UNKNOWN = "unknown"

_REF = r"#\d+"
_REF_LIST = rf"(?P<refs>{_REF}(?:\s*(?:,|&|and)\s*{_REF})*)"
#: ``blocked by #N``, ``depends on: PR #N``, ``waiting on #1, #2 and #3`` — the intake
#: phrase set followed by one or more same-repository references. ``owner/repo#N`` is
#: refused (the lookbehind), since it would be resolved against the wrong repository.
_BLOCKED_REF_RE = re.compile(
    rf"(?<![\w/])(?:{BLOCKED_PHRASE_RE.pattern})\s*:?\s*"
    rf"(?:(?:pr|pull request|issue)\s+)?(?<![\w/]){_REF_LIST}",
    re.IGNORECASE,
)
_DO_NOT_MERGE = (
    r"(?:do not|don't|dont|must not|should not|shouldn't|cannot|can't)\s+"
    r"merge\s+(?:before|until|prior to)"
)
#: ``do not merge before #N`` / ``... before v1.2.0``. A bare word ("before release")
#: is not a reference: only ``#N`` or a version-like tag (leading ``v`` or digit, with a
#: digit in it) counts.
_BEFORE_RE = re.compile(
    rf"(?<![\w/]){_DO_NOT_MERGE}\s+(?:the\s+)?(?:(?P<issue>#\d+)|(?P<tag>v?\d[\w.+\-/]*))",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"(?:\bnot|\bno longer|\bnever|n't|\bnothing)\s+(?:currently\s+|really\s+|yet\s+)?$",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"(?:```|~~~).*?(?:```|~~~|\Z)", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")


@dataclass(frozen=True)
class Blocker:
    """One stated blocker: an issue/PR number (``kind='issue'``) or a tag name."""

    kind: str
    ref: str  # "3177" for an issue/PR, "v1.2.0" for a tag

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "ref": self.ref}


def _unwrap_inline(match: re.Match[str]) -> str:
    """Keep backticked content (a tag is routinely written ``v1.2.0``) unless the span
    is itself a blocker phrase, which is documentation about the phrase, not a claim."""
    inner = match.group(1)
    if _BLOCKED_REF_RE.search(inner) or _BEFORE_RE.search(inner):
        return ""
    return inner


def _prose(body: str) -> str:
    text = _HTML_COMMENT_RE.sub(" ", body)
    text = _FENCE_RE.sub(" ", text)
    return _INLINE_CODE_RE.sub(_unwrap_inline, text)


def parse_blockers(body: str | None) -> tuple[Blocker, ...]:
    """Blockers stated in ``body``, in first-seen order, de-duplicated.

    Code fences, inline code spans that quote a blocker phrase, and HTML comments (a PR
    template's placeholder) are not claims. A negated phrase ("not blocked by #5") is
    skipped; "unblocked by #5" never matches because the phrase needs a word boundary.
    """
    text = _prose(body or "")
    found: list[Blocker] = []
    for m in _BLOCKED_REF_RE.finditer(text):
        if _NEGATION_RE.search(text[: m.start()]):
            continue
        for num in re.findall(r"\d+", m.group("refs")):
            found.append(Blocker(ISSUE, num))
    for m in _BEFORE_RE.finditer(text):
        if m.group("issue"):
            found.append(Blocker(ISSUE, m.group("issue")[1:]))
        else:
            found.append(Blocker(TAG, m.group("tag").rstrip(".")))
    seen: set[Blocker] = set()
    ordered: list[Blocker] = []
    for b in found:
        if b not in seen:
            seen.add(b)
            ordered.append(b)
    return tuple(ordered)


def _kw(_run):
    return {"_run": _run} if _run is not None else {}


def resolve_blocker(blocker: Blocker, *, cwd: str | None = None, _run=None) -> str:
    """``cleared`` / ``pending`` / ``unknown`` for one blocker. Fail-soft, read-only.

    An issue or PR is cleared once it is closed (a merged PR reads as closed) and pending
    while open. A tag is cleared once ``git/ref/tags/<tag>`` exists and pending only on a
    definite 404. Anything else — no ``gh``, offline, no auth, odd output — is ``unknown``.
    """
    if blocker.kind == ISSUE:
        argv = ["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{blocker.ref}", "--jq", ".state"]
        result = run_argv(argv, cwd=cwd, **_kw(_run))
        if not result.ok:
            return UNKNOWN
        state = result.stdout.strip().lower()
        if state in ("closed", "merged"):
            return CLEARED
        return PENDING if state == "open" else UNKNOWN
    argv = ["gh", "api", f"repos/{{owner}}/{{repo}}/git/ref/tags/{blocker.ref}", "--jq", ".ref"]
    result = run_argv(argv, cwd=cwd, **_kw(_run))
    if result.ok:
        return CLEARED if result.stdout.strip() else UNKNOWN
    if "404" in result.stderr or "not found" in result.stderr.lower():
        return PENDING
    return UNKNOWN


def _open_prs(limit: int, cwd: str | None, _run) -> list[dict[str, Any]] | None:
    argv = [
        "gh",
        "pr",
        "list",
        "--state",
        "open",
        "--limit",
        str(limit),
        "--json",
        "number,title,url,isDraft,body",
    ]
    result = run_argv(argv, cwd=cwd, **_kw(_run))
    if not result.ok:
        return None
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError:
        return None
    return rows if isinstance(rows, list) else None


def stale_blockers(*, limit: int = 200, cwd: str | None = None, _run=None) -> dict[str, Any]:
    """Open PRs with at least one cleared stated blocker.

    ``ok`` is False (and ``error`` set) when the PR list itself cannot be read; a blocker
    that cannot be read is listed as ``unknown`` and never counts toward ``all_cleared``.
    A PR is never reported against itself.
    """
    rows = _open_prs(limit, cwd, _run)
    if rows is None:
        return {"ok": False, "error": "could not list open pull requests", "prs": []}
    states: dict[Blocker, str] = {}
    prs: list[dict[str, Any]] = []
    for row in rows:
        number = row.get("number")
        blockers = [
            b
            for b in parse_blockers(row.get("body"))
            if not (b.kind == ISSUE and b.ref == str(number))
        ]
        if not blockers:
            continue
        entries = []
        for b in blockers:
            if b not in states:
                states[b] = resolve_blocker(b, cwd=cwd, _run=_run)
            entries.append({**b.to_dict(), "state": states[b]})
        if not any(e["state"] == CLEARED for e in entries):
            continue
        prs.append(
            {
                "number": number,
                "title": row.get("title", ""),
                "url": row.get("url", ""),
                "draft": bool(row.get("isDraft")),
                "blockers": entries,
                "all_cleared": all(e["state"] == CLEARED for e in entries),
            }
        )
    prs.sort(key=lambda p: p["number"])
    return {"ok": True, "error": None, "prs": prs}


def render(report: dict[str, Any]) -> str:
    """Human-readable form of a :func:`stale_blockers` report."""
    if not report["ok"]:
        return f"keel stale-blockers: {report['error']}"
    if not report["prs"]:
        return "No open PRs with a cleared blocker."
    lines = []
    for pr in report["prs"]:
        kind = "all blockers cleared" if pr["all_cleared"] else "some blockers cleared"
        draft = " (draft)" if pr["draft"] else ""
        lines.append(f"#{pr['number']}{draft} {pr['title']} — {kind}")
        for e in pr["blockers"]:
            label = f"#{e['ref']}" if e["kind"] == ISSUE else e["ref"]
            lines.append(f"  - {label}: {e['state']}")
    return "\n".join(lines)
