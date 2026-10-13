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
#: Closed without the work landing: a PR closed unmerged, an issue closed as not planned or
#: a duplicate. The blocker is gone but the thing it waited for never arrived, so it is
#: reported and never counted as cleared.
ABANDONED = "abandoned"
UNKNOWN = "unknown"

_ITEM = r"(?:(?:pull request|pr|issue)s?\s*)?#\d+"
#: Between two items of one list: ``,`` ``, and`` ``, or`` ``and`` ``or`` ``&`` ``/`` ``+``,
#: or a markdown bullet on the next line.
_SEP = r"(?:\s*(?:,\s*(?:(?:and|or)\b\s*)?|&|/|\+|(?:and|or)\b)\s*|\s*\n\s*[-*+]\s+)"
_REF_LIST = rf"(?P<refs>{_ITEM}(?:{_SEP}{_ITEM})*)"
#: ``blocked by #N``, ``depends on: PR #N``, ``waiting on #1, #2, and #3``,
#: ``Depends on:`` then a bullet per ref — the intake phrase set followed by a list of
#: same-repository ``#N`` references. ``owner/repo#N`` is not matched (the phrase must be
#: followed by ``#N``, optionally after ``PR``/``issue``), so it is never resolved against
#: the wrong repository; the lookbehind only refuses a phrase glued to a longer word.
_BLOCKED_REF_RE = re.compile(
    rf"(?<![\w/])(?:{BLOCKED_PHRASE_RE.pattern})\s*:?\s*(?:[-*+]\s+)?{_REF_LIST}",
    re.IGNORECASE,
)
#: Phrases that are also ordinary nouns: "Upgrade dependency #123" quotes an upstream issue
#: number, so they only count when written as a label ("Dependency: #123").
_NOUN_PHRASES = frozenset({"dependency", "needs dependency"})
#: Text that looks like more references than the grammar captured: a separator followed by
#: something carrying a ``#N`` (``and owner/repo#9``) or an issue/pull URL.
_MORE_REFS_RE = re.compile(
    r"\s*(?:[,;&/+]|\b(?:and|or)\b|\n\s*[-*+])\s*[^\n]*?(?:#\d+|/(?:issues|pull)/\d+)",
    re.IGNORECASE,
)
_LINE_REF_RE = re.compile(r"[^\n]*?(?:#\d+|/(?:issues|pull)/\d+)", re.IGNORECASE)
_DO_NOT_MERGE = (
    r"(?:do not|don't|dont|must not|should not|shouldn't|cannot|can't)\s+"
    r"merge\s+(?:before|until(?:\s+after)?|prior to)"
)
#: ``do not merge before #N`` / ``... before v1.2.0``. A bare word ("before release") or a
#: time/count ("before 5pm", "before 3 reviewers") is not a reference: only ``#N``, a
#: ``v``-prefixed tag (``v2``, ``v1.2.0``) or a three-part dotted version (``1.2.0``)
#: counts; ``10.30`` or ``3.5 hours`` do not.
_BEFORE_RE = re.compile(
    rf"(?<![\w/]){_DO_NOT_MERGE}\s+(?:the\s+)?(?:"
    rf"(?P<issue>(?:(?:pull request|pr|issue)\s*)?#\d+)"
    r"|(?P<tag>v\d[\w.+\-/]*|\d{1,3}\.\d+\.\d[\w.+\-/]*))",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"(?:\bnot|\bno longer|\bnever|n't|\bnothing|\bpreviously|\bformerly|\bwas|\bwere)\s+"
    r"(?:currently\s+|really\s+|yet\s+)?$",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"(?:```|~~~).*?(?:```|~~~|\Z)", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
#: ``[#12](https://...)`` reads as its link text; a link whose text carries no ``#N`` keeps
#: its URL, so an issue URL behind prose stays visible to the "could not read" check.
_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\n]*)\)")
#: ``_#12_`` / ``*#12*`` (``**`` and ``__`` are dropped outright, so ``**Blocked by:** #5`` reads).
_EMPHASIS_REF_RE = re.compile(r"([*_])(#\d+)\1")


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


def _unwrap_link(match: re.Match[str]) -> str:
    label, url = match.groups()
    return label if "#" in label else f"{label} {url}"


def _prose(body: str) -> str:
    text = _HTML_COMMENT_RE.sub(" ", body)
    text = _FENCE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(_unwrap_inline, text)
    text = _LINK_RE.sub(_unwrap_link, text).replace("**", "").replace("__", "")
    return _EMPHASIS_REF_RE.sub(r"\2", text.replace("\u2019", "'"))


def scan_blockers(body: str | None) -> tuple[tuple[Blocker, ...], bool]:
    """``(blockers, incomplete)`` stated in ``body``.

    ``incomplete`` is True when the text carries a reference the grammar did not capture —
    ``and owner/repo#9``, a URL, a phrase whose ``#N`` it could not read. The caller must
    then never certify that every blocker cleared.
    """
    text = _prose(body or "")
    found: list[Blocker] = []
    incomplete = False
    for m in BLOCKED_PHRASE_RE.finditer(text):
        if _NEGATION_RE.search(text[: m.start()]):
            continue
        ref = _BLOCKED_REF_RE.match(text, m.start())
        if ref is None:
            # A phrase with a reference we could not read: say so rather than drop it.
            incomplete = incomplete or bool(_LINE_REF_RE.match(text, m.end()))
            continue
        if m.group(1).lower() in _NOUN_PHRASES and not text[m.end() :].lstrip(" \t").startswith(
            ":"
        ):
            continue
        for num in re.findall(r"\d+", ref.group("refs")):
            found.append(Blocker(ISSUE, num))
        incomplete = incomplete or bool(_MORE_REFS_RE.match(text, ref.end()))
    for m in _BEFORE_RE.finditer(text):
        if _NEGATION_RE.search(text[: m.start()]):
            continue
        if m.group("issue"):
            found.append(Blocker(ISSUE, re.sub(r"\D", "", m.group("issue"))))
        else:
            found.append(Blocker(TAG, m.group("tag").rstrip(".")))
        incomplete = incomplete or bool(_MORE_REFS_RE.match(text, m.end()))
    seen: set[Blocker] = set()
    ordered: list[Blocker] = []
    for b in found:
        if b not in seen:
            seen.add(b)
            ordered.append(b)
    return tuple(ordered), incomplete


def parse_blockers(body: str | None) -> tuple[Blocker, ...]:
    """Blockers stated in ``body``, de-duplicated: phrase forms first, then ``before`` forms.

    Code fences, inline code spans that quote a blocker phrase, and HTML comments (a PR
    template's placeholder) are not claims. A negated phrase ("not blocked by #5") is
    skipped; "unblocked by #5" never matches because the phrase needs a word boundary.
    """
    return scan_blockers(body)[0]


#: One line: ``<state> <state_reason|-> <issue|unmerged|<merged_at>>``. The last field is
#: how a PR is told from an issue, and whether it merged (``merged_at`` is null if not).
_ISSUE_JQ = (
    '.state + " " + (.state_reason // "-") + " " + '
    '(if .pull_request then (.pull_request.merged_at // "unmerged") else "issue" end)'
)


def _kw(_run):
    return {"_run": _run} if _run is not None else {}


def resolve_blocker(blocker: Blocker, *, cwd: str | None = None, _run=None) -> str:
    """``cleared`` / ``pending`` / ``abandoned`` / ``unknown`` for one blocker. Read-only.

    An open issue or PR is pending. A closed one is cleared only when the work landed: a PR
    that was merged, an issue closed as ``completed`` (or with no reason, as old issues
    are). A PR closed unmerged, or an issue closed ``not_planned`` / as a duplicate, is
    ``abandoned``. A tag is cleared once ``git/ref/tags/<tag>`` exists and pending only on
    an HTTP 404 while the repository itself answers. Anything else — no ``gh``, offline, no
    auth, odd output — is ``unknown``, never cleared: an operator told "the blocker is gone"
    merges on it.
    """
    if blocker.kind == ISSUE:
        argv = ["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{blocker.ref}", "--jq", _ISSUE_JQ]
        result = run_argv(argv, cwd=cwd, **_kw(_run))
        parts = result.stdout.split() if result.ok else []
        if len(parts) != 3:
            return UNKNOWN
        state, reason, merged = parts
        if state == "open":
            return PENDING
        if state != "closed":
            return UNKNOWN
        if merged == "issue":
            return CLEARED if reason in ("completed", "-") else ABANDONED
        return ABANDONED if merged == "unmerged" else CLEARED
    argv = ["gh", "api", f"repos/{{owner}}/{{repo}}/git/ref/tags/{blocker.ref}", "--jq", ".ref"]
    result = run_argv(argv, cwd=cwd, **_kw(_run))
    if result.ok:
        return CLEARED if result.stdout.strip() else UNKNOWN
    if "HTTP 404" not in result.stderr:
        return UNKNOWN
    # A 404 also answers a repository the caller cannot see (private, no auth, wrong
    # directory): only a repository that does answer makes it "the tag is not there yet".
    repo = run_argv(["gh", "api", "repos/{owner}/{repo}", "--jq", ".id"], cwd=cwd, **_kw(_run))
    return PENDING if repo.ok and repo.stdout.strip() else UNKNOWN


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
    """Open PRs with at least one cleared or abandoned stated blocker, plus PRs whose blocker
    references could not be read at all (``blockers`` empty, ``incomplete`` true).

    ``ok`` is False (and ``error`` set) when the PR list itself cannot be read; a blocker
    that cannot be read is listed as ``unknown`` and never counts toward ``all_cleared``.
    A PR is never reported against itself.
    """
    rows = _open_prs(limit, cwd, _run)
    if rows is None:
        return {
            "ok": False,
            "error": "could not list open pull requests",
            "truncated": False,
            "prs": [],
        }
    states: dict[Blocker, str] = {}
    prs: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("number"), int):
            continue
        number = row["number"]
        found, incomplete = scan_blockers(row["body"] if isinstance(row.get("body"), str) else None)
        blockers = [b for b in found if not (b.kind == ISSUE and b.ref == str(number))]
        if not blockers and not incomplete:
            continue
        entries = []
        for b in blockers:
            if b not in states:
                states[b] = resolve_blocker(b, cwd=cwd, _run=_run)
            entries.append({**b.to_dict(), "state": states[b]})
        # A PR whose blocker references could not be read at all is shown, not dropped.
        unreadable = incomplete and not entries
        if not unreadable and not any(e["state"] in (CLEARED, ABANDONED) for e in entries):
            continue
        prs.append(
            {
                "number": number,
                "title": row.get("title", ""),
                "url": row.get("url", ""),
                "draft": bool(row.get("isDraft")),
                "blockers": entries,
                "incomplete": incomplete,
                "all_cleared": not incomplete and all(e["state"] == CLEARED for e in entries),
            }
        )
    prs.sort(key=lambda p: p["number"])
    return {"ok": True, "error": None, "truncated": len(rows) >= limit, "prs": prs}


def render(report: dict[str, Any]) -> str:
    """Human-readable form of a :func:`stale_blockers` report."""
    if not report["ok"]:
        return f"keel stale-blockers: {report['error']}"
    lines = []
    if not report["prs"]:
        lines.append("No open PRs with a cleared or abandoned blocker.")
    for pr in report["prs"]:
        states = {e["state"] for e in pr["blockers"]}
        if pr["all_cleared"]:
            kind = "all blockers cleared"
        elif not pr["blockers"]:
            kind = "blocker reference could not be read"
        elif pr["incomplete"]:
            kind = "blocker list may be incomplete"
        elif CLEARED not in states:
            kind = "blocker abandoned, none cleared"
        else:
            kind = "some blockers cleared or abandoned"
        draft = " (draft)" if pr["draft"] else ""
        lines.append(f"#{pr['number']}{draft} {pr['title']} — {kind}")
        for e in pr["blockers"]:
            label = f"#{e['ref']}" if e["kind"] == ISSUE else e["ref"]
            lines.append(f"  - {label}: {e['state']}")
    if report["truncated"]:
        lines.append("Only the first --limit open PRs were read; raise --limit to read more.")
    return "\n".join(lines)
