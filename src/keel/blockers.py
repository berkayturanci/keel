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

from .intake import BLOCKED_PHRASE_RE, NON_BLOCKING_DEPENDENCY_RE
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
#: or a markdown bullet on the very next line (a blank line ends the list).
_SEP = r"(?:\s*(?:,\s*(?:(?:and|or)\b\s*)?|&|/|\+|(?:and|or)\b)\s*|[ \t]*\n[ \t]*[-*+][ \t]+)"
_REF_LIST = rf"(?P<refs>{_ITEM}(?:{_SEP}{_ITEM})*)"
# --- The one parsing invariant (#1512) ------------------------------------------------
# Every blocker-phrase occurrence is found first with a phrase-only regex, then the strict
# target grammar is tried right after it. The occurrence ends in exactly one of:
#   (a) references captured (and nothing readable-looking left over), or
#   (b) ``incomplete = True``.
# There is no third outcome in which a phrase is silently dropped.
#: The strict grammar for what may follow a blocked phrase: ``blocked by #N``,
#: ``depends on: PR #N``, ``waiting on #1, #2, and #3``, or ``Depends on:`` and a bullet per
#: ref. ``owner/repo#N`` does not match (the target must start with ``#N``, optionally after
#: ``PR``/``issue``), so it is never resolved against the wrong repository.
_AFTER_BLOCKED_RE = re.compile(rf"\s*:?\s*(?:[-*+]\s+)?{_REF_LIST}", re.IGNORECASE)
#: Phrases that are also ordinary nouns: "Upgrade dependency #123" quotes an upstream issue
#: number, so they only count when written as a label ("Dependency: #123").
_NOUN_PHRASES = frozenset({"dependency", "needs dependency"})
#: Text that looks like more references than the grammar captured: a separator followed by
#: something carrying a ``#N`` (``and owner/repo#9``) or an issue/pull URL.
_MORE_REFS_RE = re.compile(
    r"[ \t]*(?:[,;&/+]|\b(?:and|or)\b|\n[ \t]*[-*+])\s*[^\n]*?(?:#\d+|/(?:issues|pull)/\d+)",
    re.IGNORECASE,
)
#: A second *tag* target after a separator (``before v1.0 and v2.0``). Adjacent only, so a
#: later word that merely starts with ``v1`` does not count.
_MORE_TAGS_RE = re.compile(
    r"\s*(?:[,;&/+]|\b(?:and|or)\b)\s*(?:the\s+)?(?:v\d|\d{1,3}\.\d+\.\d)", re.IGNORECASE
)
#: Something that looks like a reference to a thing we could not read: ``#N``, an issue/pull
#: path (``owner/repo#9`` and URLs both contain one of those) or any URL.
_REFLIKE_RE = re.compile(r"#\d+|/(?:issues|pull)/\d+|https?://\S+", re.IGNORECASE)
_DO_NOT_MERGE = (
    r"(?:do not|don't|dont|must not|should not|shouldn't|cannot|can't)\s+"
    r"merge\s+(?:(?:this|the)(?:\s+(?:pr|pull request))?\s+|it\s+)?"
    r"(?:before|until(?:\s+after)?|prior to)"
)
_BEFORE_PHRASE_RE = re.compile(rf"(?<![\w/]){_DO_NOT_MERGE}", re.IGNORECASE)
#: What may follow ``do not merge before``: ``#N`` (or ``PR #N``), a ``v``-prefixed tag
#: (``v2``, ``v1.2.0``) or a three-part dotted version (``1.2.0``). A bare word ("before
#: release") or a time/count ("before 5pm", "before 3 reviewers") is not a reference.
_AFTER_BEFORE_RE = re.compile(
    r"\s+(?:the\s+)?(?:"
    r"(?P<issue>(?:(?:pull request|pr|issue)\s*)?#\d+)"
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
    if BLOCKED_PHRASE_RE.search(inner) or _BEFORE_PHRASE_RE.search(inner):
        return ""
    return inner


def _own_link(url: str, repo: str | None) -> bool:
    """True when ``url`` points at this repository's own issues/pulls.

    A root-relative ``/issues/N`` or ``/pull/N`` (or a bare ``#N``) is this repository's. A
    protocol-relative ``//host/...`` or ``../other/...`` path is not, and an absolute URL
    counts only when ``repo`` (``owner/name``) is known and matches.
    """
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"[a-z][a-z0-9+.-]*:", url, re.IGNORECASE):
        return bool(re.match(r"(?:/?(?:issues|pull)/\d+|#\d+)", url))
    if repo is None:
        return False
    return bool(
        re.match(
            rf"https?://(?:www\.)?github\.com/{re.escape(repo)}/(?:issues|pull)/\d+(?:[/?#]|$)",
            url,
            re.IGNORECASE,
        )
    )


def _unwrap_link(match: re.Match[str], repo: str | None) -> str:
    """``[#12](own-repo url)`` reads as ``#12``. A ``#N`` label over any other URL is a
    reference into another repository: it is renamed ``external#N`` (so it is never read as
    this repository's ``#N``, yet still counts as an unread reference). Prose keeps its URL."""
    label, url = match.groups()
    if "#" not in label:
        return f"{label} {url}"
    return label if _own_link(url, repo) else label.replace("#", "external#")


def _prose(body: str, repo: str | None) -> str:
    text = _HTML_COMMENT_RE.sub(" ", body)
    text = _FENCE_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(_unwrap_inline, text)
    text = _LINK_RE.sub(lambda m: _unwrap_link(m, repo), text)
    text = text.replace("**", "").replace("__", "")
    return _EMPHASIS_REF_RE.sub(r"\2", text.replace("\u2019", "'"))


def _has_more(text: str, pos: int) -> bool:
    return bool(_MORE_REFS_RE.match(text, pos) or _MORE_TAGS_RE.match(text, pos))


def _unread_target(text: str, pos: int) -> bool:
    """True when the target a phrase should have had (the rest of its line, or the bullet
    block under it when the line ends at the phrase) contains something reference-like."""
    head = text[pos : text.find("\n", pos) if "\n" in text[pos:] else len(text)]
    zone = head if head.strip(" \t:") else re.split(r"\n[ \t]*\n", text[pos:], maxsplit=1)[0]
    return bool(_REFLIKE_RE.search(zone))


def scan_blockers(body: str | None, repo: str | None = None) -> tuple[tuple[Blocker, ...], bool]:
    """``(blockers, incomplete)`` stated in ``body``.

    Every blocker phrase ends in captured references or ``incomplete`` (see the invariant
    above): ``incomplete`` is True when a phrase is followed by something reference-like the
    strict grammar did not read (``and owner/repo#9``, a URL, ``owner/repo#9`` as the first
    bullet) or by a second target. The caller must then never certify that every blocker
    cleared. ``repo`` (``owner/name``) lets a link to this repository's own issue read as a
    bare ``#N``; without it every absolute link is treated as foreign.
    """
    text = _prose(body or "", repo)
    found: list[Blocker] = []
    incomplete = False
    for m in BLOCKED_PHRASE_RE.finditer(text):
        if _NEGATION_RE.search(text[: m.start()]):
            continue
        if m.group(1).lower() in _NOUN_PHRASES and not text[m.end() :].lstrip(" \t").startswith(
            ":"
        ):
            continue
        if NON_BLOCKING_DEPENDENCY_RE.match(text, m.start()):
            continue
        ref = _AFTER_BLOCKED_RE.match(text, m.end())
        if ref is None:
            incomplete = incomplete or _unread_target(text, m.end())
            continue
        found.extend(Blocker(ISSUE, n) for n in re.findall(r"\d+", ref.group("refs")))
        incomplete = incomplete or _has_more(text, ref.end())
    for m in _BEFORE_PHRASE_RE.finditer(text):
        if _NEGATION_RE.search(text[: m.start()]):
            continue
        target = _AFTER_BEFORE_RE.match(text, m.end())
        if target is None:
            incomplete = incomplete or _unread_target(text, m.end())
            continue
        if target.group("issue"):
            found.append(Blocker(ISSUE, re.sub(r"\D", "", target.group("issue"))))
        elif ".." in target.group("tag") or target.group("tag").endswith("/"):
            incomplete = True  # not a tag name git can hold; never put it in an API path
        else:
            found.append(Blocker(TAG, target.group("tag").rstrip(".")))
        incomplete = incomplete or _has_more(text, target.end())
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
# Yes, the issues payload's ``pull_request`` object carries ``merged_at`` (checked live:
# `gh api repos/berkayturanci/keel/issues/1520 --jq '.pull_request.merged_at'` returns a timestamp).
_ISSUE_JQ = (
    '.state + " " + (.state_reason // "-") + " " + '
    '(if .pull_request then (.pull_request.merged_at // "unmerged") else "issue" end)'
)


def _kw(_run):
    return {"_run": _run} if _run is not None else {}


def _why(result) -> str:
    """Why a ``gh`` call produced nothing usable: its first line of complaint, else its exit."""
    for line in (result.stderr or result.output or "").splitlines():
        if line.strip():
            return line.strip()[:120]
    return f"gh exited {result.code}"


def resolve_detail(
    blocker: Blocker, *, cwd: str | None = None, _run=None
) -> tuple[str, str | None]:
    """``(state, reason)`` for one blocker; ``reason`` is set only when the state is unknown.

    An open issue or PR is pending. A closed one is cleared only when the work landed: a PR
    that was merged, an issue closed as ``completed`` (or with no reason, as old issues
    are). A PR closed unmerged, or an issue closed ``not_planned`` / as a duplicate, is
    ``abandoned``. A tag is cleared once ``git/ref/tags/<tag>`` exists and pending only on
    an HTTP 404 while the repository itself answers. Anything else — no ``gh``, offline, no
    auth, rate limit, odd output — is ``unknown``, never cleared: an operator told "the
    blocker is gone" merges on it.
    """
    if blocker.kind == ISSUE:
        argv = ["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{blocker.ref}", "--jq", _ISSUE_JQ]
        result = run_argv(argv, cwd=cwd, **_kw(_run))
        if not result.ok:
            return UNKNOWN, _why(result)
        parts = result.stdout.split()
        if len(parts) != 3 or parts[0] not in ("open", "closed"):
            return UNKNOWN, f"unexpected answer: {result.stdout.strip()[:60]!r}"
        state, reason, merged = parts
        if state == "open":
            return PENDING, None
        if merged == "issue":
            return (CLEARED if reason in ("completed", "-") else ABANDONED), None
        return (ABANDONED if merged == "unmerged" else CLEARED), None
    argv = ["gh", "api", f"repos/{{owner}}/{{repo}}/git/ref/tags/{blocker.ref}", "--jq", ".ref"]
    result = run_argv(argv, cwd=cwd, **_kw(_run))
    if result.ok:
        if result.stdout.strip():
            return CLEARED, None
        return UNKNOWN, "empty answer for the tag lookup"
    if "HTTP 404" not in result.stderr:
        return UNKNOWN, _why(result)
    # A 404 also answers a repository the caller cannot see (private, no auth, wrong
    # directory): only a repository that does answer makes it "the tag is not there yet".
    repo = run_argv(["gh", "api", "repos/{owner}/{repo}", "--jq", ".id"], cwd=cwd, **_kw(_run))
    if repo.ok and repo.stdout.strip():
        return PENDING, None
    return UNKNOWN, "tag lookup was a 404 and the repository could not be read"


def resolve_blocker(blocker: Blocker, *, cwd: str | None = None, _run=None) -> str:
    """``cleared`` / ``pending`` / ``abandoned`` / ``unknown`` for one blocker. Read-only."""
    return resolve_detail(blocker, cwd=cwd, _run=_run)[0]


def _open_prs(limit: int, cwd: str | None, _run) -> list[dict[str, Any]] | None:
    argv = [
        "gh",
        "pr",
        "list",
        "--state",
        "open",
        "--limit",
        str(limit + 1),  # one more than asked, so "truncated" is exact
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


_SLUG_RE = re.compile(r"[\w.-]+/[\w.-]+")


def _repo_slug(cwd: str | None, _run) -> str | None:
    """``owner/name`` of the checkout, or ``None`` when ``gh`` cannot say."""
    result = run_argv(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        cwd=cwd,
        **_kw(_run),
    )
    slug = result.stdout.strip() if result.ok else ""
    return slug if _SLUG_RE.fullmatch(slug) else None


def stale_blockers(*, limit: int = 200, cwd: str | None = None, _run=None) -> dict[str, Any]:
    """Open PRs worth an operator's attention about their stated blockers.

    Listed: a PR with a blocker that cleared or was abandoned, one whose blocker could not be
    *checked* (``unknown``, with the reason), and one whose references could not be read
    (``blockers`` empty, ``incomplete`` true). ``ok`` is False (and ``error`` set) when the PR
    list itself cannot be read. ``unknown_blockers`` counts the distinct blockers whose state
    could not be read; a read failure is never reported as "nothing found". A PR is never
    reported against itself.
    """
    rows = _open_prs(limit, cwd, _run)
    if rows is None:
        return {
            "ok": False,
            "error": "could not list open pull requests",
            "truncated": False,
            "unknown_blockers": 0,
            "prs": [],
        }
    truncated = len(rows) > limit
    rows = rows[:limit]
    # Only a body with an absolute link needs to know which repository is "this" one.
    repo = (
        _repo_slug(cwd, _run)
        if any(isinstance(r, dict) and "](http" in str(r.get("body")) for r in rows)
        else None
    )
    resolved: dict[Blocker, tuple[str, str | None]] = {}
    prs: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("number"), int):
            continue
        number = row["number"]
        body = row["body"] if isinstance(row.get("body"), str) else None
        found, incomplete = scan_blockers(body, repo)
        stated = [b for b in found if not (b.kind == ISSUE and b.ref == str(number))]
        if not stated and not incomplete:
            continue
        entries = []
        for b in stated:
            if b not in resolved:
                resolved[b] = resolve_detail(b, cwd=cwd, _run=_run)
            state, reason = resolved[b]
            entry = {**b.to_dict(), "state": state}
            if reason is not None:
                entry["reason"] = reason
            entries.append(entry)
        # Shown, not dropped: a resolved or abandoned blocker, an unchecked one, or a PR
        # whose references could not be read at all.
        noteworthy = {CLEARED, ABANDONED, UNKNOWN}
        if not (incomplete and not entries) and not any(e["state"] in noteworthy for e in entries):
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
    unknown = sum(1 for state, _ in resolved.values() if state == UNKNOWN)
    return {
        "ok": True,
        "error": None,
        "truncated": truncated,
        "unknown_blockers": unknown,
        "prs": prs,
    }


def render(report: dict[str, Any]) -> str:
    """Human-readable form of a :func:`stale_blockers` report."""
    if not report["ok"]:
        return f"keel stale-blockers: {report['error']}"
    lines = []
    if not report["prs"]:
        lines.append("No open PRs with a cleared, abandoned or unchecked blocker.")
    for pr in report["prs"]:
        states = {e["state"] for e in pr["blockers"]}
        if pr["all_cleared"]:
            kind = "all blockers cleared"
        elif not pr["blockers"]:
            kind = "blocker reference could not be read"
        elif pr["incomplete"]:
            kind = "blocker list may be incomplete"
        elif states == {UNKNOWN}:
            kind = "blocker state could not be checked"
        elif CLEARED not in states and ABANDONED in states:
            kind = "blocker abandoned, none cleared"
        else:
            kind = "some blockers cleared, abandoned or unchecked"
        draft = " (draft)" if pr["draft"] else ""
        lines.append(f"#{pr['number']}{draft} {pr['title']} — {kind}")
        for e in pr["blockers"]:
            label = f"#{e['ref']}" if e["kind"] == ISSUE else e["ref"]
            why = f" ({e['reason']})" if e["state"] == UNKNOWN else ""
            lines.append(f"  - {label}: {e['state']}{why}")
    if report["unknown_blockers"]:
        lines.append(
            f"{report['unknown_blockers']} blocker(s) could not be checked "
            "(rate limit, token scope, network or a 5xx): the list above is incomplete, "
            "so silence about a PR does not mean its blockers are open."
        )
    if report["truncated"]:
        lines.append("Only the first --limit open PRs were read; raise --limit to read more.")
    return "\n".join(lines)
