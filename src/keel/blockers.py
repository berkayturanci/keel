"""Stated-blocker detection for open pull requests (#1512).

A draft that says "do not merge before #3177" stays open long after #3177 closed, and
nothing surfaced it. This module reads the *stated* blockers out of PR bodies and asks
GitHub whether each has cleared.

* :func:`parse_blockers` and :func:`scan_blockers` are pure: PR body text in, blocker
  references and completeness out. It reuses :data:`keel.intake.BLOCKED_PHRASE_RE`,
  the phrase set issue intake already uses.
* :func:`resolve_blocker` and :func:`stale_blockers` are the thin, fail-soft ``gh`` I/O.
  A read that fails is ``unknown``. ``all_cleared`` is a best-effort hint for an operator
  to inspect, never a certificate: an operator must read the PR before acting.
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
# --- Recognised phrases and reference zones (#1512) -----------------------------------
# The parser reads the recognised phrases and marks a PR incomplete when it sees a
# reference it could not read in a phrase's zone.
#: The strict grammar for what may follow a blocked phrase: ``blocked by #N``,
#: ``depends on: PR #N``, ``waiting on #1, #2, and #3``, or ``Depends on:`` and a bullet per
#: ref. ``owner/repo#N`` does not match (the target must start with ``#N``, optionally after
#: ``PR``/``issue``), so it is not resolved against this repository.
_AFTER_BLOCKED_RE = re.compile(
    rf"[ \t]*:?[ \t]*(?:\n[ \t]*)?(?:[-*+][ \t]+)?{_REF_LIST}", re.IGNORECASE
)
#: Phrases that are also ordinary nouns: "Upgrade dependency #123" quotes an upstream issue
#: number, so they only count when written as a label ("Dependency: #123").
_NOUN_PHRASES = frozenset({"dependency", "needs dependency"})
#: Reference-like tokens in a blocker zone: this-repo `#N`, foreign `owner/repo#N`,
#: URLs, issue/pull paths, and tag-like targets (`v1`, `v1.2.0`, `1.2.0`).
_REFLIKE_RE = re.compile(
    r"https?://\S+"
    r"|(?<![\w.-])[\w.-]++/[\w.-]++#\d+"
    r"|/(?:issues|pull)/\d+"
    r"|#\d+"
    r"|\bv\d[\w.+\-/]*|\b\d{1,3}\.\d+\.\d[\w.+\-/]*",
    re.IGNORECASE,
)
_DO_NOT_MERGE = (
    r"(?:do not|don't|dont|must not|should not|shouldn't|cannot|can't)\s+"
    r"merge\s+(?:(?:this|the)(?:\s+(?:pr|pull request))?\s+|it\s+)?"
    r"(?:before|until(?:\s+after)?|prior to)"
)
_BEFORE_PHRASE_RE = re.compile(rf"(?<![\w/]){_DO_NOT_MERGE}", re.IGNORECASE)
#: What may follow ``do not merge before``: ``#N`` (or ``PR #N``), a ``v``-prefixed tag
#: (``v2``, ``v1.2.0``) or a three-part dotted version (``1.2.0``). A bare word ("before
#: release") or a time/count ("before 5pm", "before 3 reviewers") is not a reference.
#: A tag target must not be followed by ``#\d`` (or an ``/owner/repo#N`` continuation),
#: which is a foreign repository reference.
_AFTER_BEFORE_RE = re.compile(
    r"\s+(?:the\s+)?(?:"
    r"(?P<issue>(?:(?:pull request|pr|issue)\s*)?#\d+)"
    r"|(?P<tag>(?:v\d|\d{1,3}\.\d+\.\d)[\w.+\-/]*+)(?!#\d))",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"(?:\bnot|\bno longer|\bnever|n't|\bnothing|\bpreviously|\bformerly|\bwas|\bwere)[ \t]+"
    r"(?:(?:currently|really|yet)[ \t]+)?\Z",
    re.IGNORECASE,
)
#: A terminated fence only: a stray unterminated opener must not swallow (hide) the rest of
#: the body or mispair with a later fenced block's opener, so what follows it is still read.
_FENCE_RE = re.compile(
    r"^[ \t]*+([`~])(\1{2,}+)[^\n]*\n(?:(?!^[ \t]*\1\2)[^\n]*\n)*?^[ \t]*\1\2\1*[ \t]*$",
    re.MULTILINE,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_DETAILS_TAG_RE = re.compile(r"<\s*(/)?\s*details(?=[\s>])[^>]*>", re.IGNORECASE)
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)([^\n]*?)(?<!`)\1(?!`)")
#: ``[#12](https://...)`` reads as its link text; a link whose text carries no ``#N`` keeps
#: its URL, so an issue URL behind prose stays visible to the "could not read" check.
_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\n]*)\)")
#: Paired emphasis around a phrase or target (``**Blocked by:**``, ``_#12_``, ``__v1__rc__``)
#: is unwrapped without stripping internal ``__`` inside a tag name like ``v1__rc``.
_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")
_ITALIC_REF_RE = re.compile(r"([*_])(#\d+)\1")


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
    inner = match.groups()[-1]
    if BLOCKED_PHRASE_RE.search(inner) or _BEFORE_PHRASE_RE.search(inner):
        return ""
    return inner


def _own_link(label: str, url: str, repo: str | None) -> bool:
    """True when ``url`` points at this repository's own issue/PR *and* is the one ``label`` names.

    A root-relative ``/issues/N`` or ``/pull/N`` (or a bare ``#N``) is this repository's. A
    protocol-relative ``//host/...`` or ``../other/...`` path is not, and an absolute URL
    counts only when ``repo`` (``owner/name``) is known and matches. A link whose URL names a
    different number than its label (``[#12](.../issues/99)``) is not read as ``#12``.
    """
    if url.startswith("//"):
        return False
    named = re.search(r"(?:(?:issues|pull)/|^#)(\d+)", url)
    labelled = re.search(r"#(\d+)", label)
    if named and labelled and named.group(1) != labelled.group(1):
        return False
    if not re.match(r"[a-z][a-z0-9+.-]*:", url, re.IGNORECASE):
        return bool(re.match(r"(?:/(?:issues|pull)/\d+|#\d+)$", url))
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
    reference into another repository: it is renamed ``external#N`` (so it is not read as
    this repository's ``#N``, yet still counts as an unread reference). Prose keeps its URL."""
    label, url = match.groups()
    if "#" not in label:
        return f"{label} {url}"
    return label if _own_link(label, url, repo) else label.replace("#", "external#")


_ALERT_RE = re.compile(
    r"^[ \t]*>[ \t]*\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]",
    re.IGNORECASE,
)


def _strip_blockquotes(text: str) -> str:
    """Drop blockquote lines (first non-space character is '>'), except GitHub alert blocks
    (> [!NOTE], > [!WARNING], > [!IMPORTANT], > [!CAUTION], > [!TIP] and lines quoted under
    them) which are the author's own text."""
    out: list[str] = []
    in_alert = False
    for line in text.split("\n"):
        stripped = line.lstrip(" \t")
        if stripped.startswith(">"):
            if _ALERT_RE.match(line):
                in_alert = True
            if in_alert:
                out.append(re.sub(r"^[ \t]*>[ \t]?", "", line))
        else:
            in_alert = False
            out.append(line)
    return "\n".join(out)


def _strip_details(text: str) -> str:
    """Drop everything between <details> and </details> (case-insensitive, may span lines,
    may be nested; an unterminated <details> drops to the end of the body)."""
    out: list[str] = []
    depth = 0
    last_end = 0
    for m in _DETAILS_TAG_RE.finditer(text):
        if not m.group(1):
            if depth == 0:
                out.append(text[last_end : m.start()])
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0:
                last_end = m.end()
    if depth == 0:
        out.append(text[last_end:])
    return "".join(out)


def _prose(body: str, repo: str | None) -> str:
    text = body.replace("\r\n", "\n").replace("\r", "\n").replace("\u2019", "'")
    text = _HTML_COMMENT_RE.sub(" ", text)
    text = _FENCE_RE.sub(" ", text)
    text = _strip_blockquotes(text)
    inlines: list[str] = []

    def _stash_inline(m: re.Match[str]) -> str:
        inlines.append(_unwrap_inline(m))
        return f"\x00{len(inlines) - 1}\x00"

    text = _INLINE_CODE_RE.sub(_stash_inline, text)
    text = _strip_details(text)
    text = _LINK_RE.sub(lambda m: _unwrap_link(m, repo), text)
    text = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _ITALIC_REF_RE.sub(r"\2", text)
    if inlines:
        text = re.sub(r"\x00(\d+)\x00", lambda m: inlines[int(m.group(1))], text)
    return text


def _zone(text: str, pos: int) -> str:
    """The span a blocker phrase governs: the rest of its line (or the next line when the
    phrase ends its line), plus any bullet lines directly under that."""
    rest = text[pos:]
    nl = rest.find("\n")
    if nl == -1:
        return rest
    head = rest[:nl]
    lines = [head]
    after = rest[nl + 1 :].split("\n")
    if not head.strip(" \t:"):
        if not after[0].strip(" \t"):
            return head
        lines.append(after.pop(0))
    for line in after:
        if re.match(r"^[ \t]*[-*+]", line):
            lines.append(line)
        else:
            break
    return "\n".join(lines)


def _negated(text: str, start: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    return bool(_NEGATION_RE.search(text[line_start:start]))


def scan_blockers(body: str | None, repo: str | None = None) -> tuple[tuple[Blocker, ...], bool]:
    """``(blockers, incomplete)`` stated in ``body``.

    The parser reads the recognised phrases and marks a PR incomplete when it sees a
    reference it could not read in a phrase's zone. ``all_cleared`` is a best-effort hint
    for an operator to look at, never a certificate: an operator must read the PR before
    acting. ``repo`` (``owner/name``) lets a link to this repository's own issue read as
    a bare ``#N``; without it every absolute link is treated as foreign.
    """
    text = _prose(body or "", repo)
    matches: list[tuple[re.Match[str], str]] = []
    for m in BLOCKED_PHRASE_RE.finditer(text):
        if _negated(text, m.start()):
            continue
        label = m.group(1).lower() in _NOUN_PHRASES
        if label and not text[m.end() :].lstrip(" \t").startswith(":"):
            continue
        if NON_BLOCKING_DEPENDENCY_RE.match(text, m.start()):
            continue
        matches.append((m, "blocked"))
    for m in _BEFORE_PHRASE_RE.finditer(text):
        if _negated(text, m.start()):
            continue
        matches.append((m, "before"))
    matches.sort(key=lambda item: item[0].start())

    found: list[Blocker] = []
    incomplete = False
    for i, (m, kind) in enumerate(matches):
        next_start = matches[i + 1][0].start() if i + 1 < len(matches) else len(text)
        zone = _zone(text[:next_start], m.end())
        zone_ref_spans = [
            (rm.start(), rm.start() + len(rm.group(0).rstrip(".")))
            for rm in _REFLIKE_RE.finditer(zone)
        ]
        this_found: list[Blocker] = []
        captured_spans: list[tuple[int, int]] = []
        if kind == "blocked":
            ref = _AFTER_BLOCKED_RE.match(zone)
            if ref:
                base = ref.start("refs")
                for rm in re.finditer(r"#(\d+)", ref.group("refs")):
                    this_found.append(Blocker(ISSUE, rm.group(1)))
                    captured_spans.append((base + rm.start(), base + rm.end()))
        else:
            target = _AFTER_BEFORE_RE.match(zone)
            if target is not None:
                if target.group("issue"):
                    num = re.sub(r"\D", "", target.group("issue"))
                    end = target.end("issue")
                    this_found.append(Blocker(ISSUE, num))
                    captured_spans.append((end - len(num) - 1, end))
                else:
                    tag = target.group("tag").rstrip(".")
                    if not (".." in tag or tag.endswith("/")):
                        start = target.start("tag")
                        this_found.append(Blocker(TAG, tag))
                        captured_spans.append((start, start + len(tag)))
        found.extend(this_found)
        if captured_spans != zone_ref_spans:
            incomplete = True
    seen: set[Blocker] = set()
    ordered: list[Blocker] = []
    for b in found:
        if b not in seen:
            seen.add(b)
            ordered.append(b)
    return tuple(ordered), incomplete


def parse_blockers(body: str | None) -> tuple[Blocker, ...]:
    """Blockers stated in ``body``, de-duplicated: phrase forms first, then ``before`` forms.

    Code fences, inline code spans that quote a blocker phrase, HTML comments (a PR
    template's placeholder), standard blockquotes (author alerts like ``> [!NOTE]`` are
    scanned), and HTML ``<details>`` blocks (bot-pasted release notes) are ignored as
    non-claims. Negated phrases ("not blocked by #5") are skipped; "unblocked by #5"
    does not match because the phrase needs a word boundary.
    """
    return scan_blockers(body)[0]


#: One line: ``<state> <state_reason|-> <issue|unmerged|<merged_at>>``. The last field is
#: how a PR is told from an issue, and whether it merged (``merged_at`` is null if not).
# GitHub's issues endpoint includes ``pull_request.merged_at`` on PR items (timestamp when
# merged, null when open or closed unmerged).
_ISSUE_JQ = (
    '.state + " " + (.state_reason // "-") + " " + '
    '(if .pull_request then (.pull_request.merged_at // "unmerged") else "issue" end)'
)
_MERGED_AT_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


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
    auth, rate limit, odd output — is ``unknown``: an operator must inspect before acting.
    """
    if blocker.kind == ISSUE:
        argv = ["gh", "api", f"repos/{{owner}}/{{repo}}/issues/{blocker.ref}", "--jq", _ISSUE_JQ]
        result = run_argv(argv, cwd=cwd, **_kw(_run))
        if not result.ok:
            return UNKNOWN, _why(result)
        parts = result.stdout.split()
        if (
            len(parts) != 3
            or parts[0] not in ("open", "closed")
            or (parts[2] not in ("issue", "unmerged") and not _MERGED_AT_RE.fullmatch(parts[2]))
        ):
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
        ans = result.stdout.strip()
        if ans == f"refs/tags/{blocker.ref}":
            return CLEARED, None
        if not ans:
            return UNKNOWN, "empty answer for the tag lookup"
        return UNKNOWN, f"unexpected answer: {ans[:60]!r}"
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
    *checked* (``unknown``, with the reason), an incomplete PR (an unread reference was seen
    in a blocker zone), and one whose references could not be read (``blockers`` empty,
    ``incomplete`` true). ``ok`` is False (and ``error`` set) when the PR list itself cannot be
    read. ``unknown_blockers`` counts the distinct blockers whose state could not be read.
    ``all_cleared`` is a best-effort hint, never a certificate: an operator must read the PR
    before acting. A PR is not reported against itself.
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
        if any(isinstance(r, dict) and "](http" in str(r.get("body")).lower() for r in rows)
        else None
    )
    resolved: dict[Blocker, tuple[str, str | None]] = {}
    prs: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("number")) is not int:
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
        # whose references could not be read or whose blocker list may be incomplete.
        noteworthy = {CLEARED, ABANDONED, UNKNOWN}
        if not incomplete and not any(e["state"] in noteworthy for e in entries):
            continue
        prs.append(
            {
                "number": number,
                "title": row.get("title") or "",
                "url": row.get("url") or "",
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
        elif states == {ABANDONED}:
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
