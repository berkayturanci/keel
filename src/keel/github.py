"""Thin, fail-soft ``gh`` (GitHub CLI) wrappers (argv, no shell).

Like :mod:`keel.git`, these build the exact ``gh`` command for each backbone
operation and run it via the injectable ``_run`` seam. Command construction is
unit-tested offline; live behaviour is opt-in.
"""

from __future__ import annotations

import random
import time
from collections.abc import Sequence

from .runner import CommandResult, run_argv

_TRANSIENT_PATTERNS = (
    "rate limit",
    "secondary rate limit",
    "too many requests",
    "connection reset",
    "connection refused",
    "could not resolve host",
    "network is unreachable",
    "tls handshake",
    "ssl error",
    "timed out",
    "timeout",
    "500 internal server error",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)


def is_transient_error(result: CommandResult) -> bool:
    """Return whether ``result`` failed due to a transient network or rate limit error."""
    if result.ok:
        return False
    if result.timed_out:
        return True
    combined = f"{result.stderr} {result.stdout}".lower()
    for pattern in _TRANSIENT_PATTERNS:
        if pattern in combined:
            return True
    return False


def run_argv_retry(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    max_attempts: int = 3,
    backoff_factor: float = 1.0,
    jitter: bool = True,
    _run=None,
    _sleep=None,
) -> CommandResult:
    """Execute a ``gh`` command with jittered exponential backoff on transient errors.

    Retries only transient network, 5xx server errors, or secondary rate limit errors.
    Deterministic and dependency-free, with injectable ``_run`` and ``_sleep`` seams
    for offline testing at 100% line + branch coverage.
    """
    sleep_fn = _sleep or time.sleep
    attempt = 1
    while True:
        result = run_argv(argv, cwd=cwd, **_kw(_run))
        if result.ok or attempt >= max_attempts or not is_transient_error(result):
            return result
        delay = backoff_factor * (2 ** (attempt - 1))
        if jitter:
            delay += random.uniform(0.0, 0.5) if _sleep is None else 0.1  # nosec B311
        sleep_fn(delay)
        attempt += 1


def open_pr(
    title: str, body: str, base: str, head: str, *, cwd: str | None = None, _run=None
) -> CommandResult:
    return run_argv(
        ["gh", "pr", "create", "--title", title, "--body", body, "--base", base, "--head", head],
        cwd=cwd, **_kw(_run),
    )


def ci_conclusion(pr: int | str, *, cwd: str | None = None, _run=None) -> str | None:
    """Return the PR's check-rollup state (e.g. SUCCESS/FAILURE/PENDING).

    Three distinct answers, because collapsing them is what let a PR with **no
    checks at all** read as clear to merge (issue #675):

    * a conclusion string — checks reported, here is what they said
    * ``""`` — ``gh`` answered and the rollup is **empty**: nothing ran for this
      head. A fact about the *PR*.
    * ``None`` — ``gh`` could not be asked. A fact about the *runner*.

    Only the caller can weigh those, so this returns the empty string rather than
    folding it into ``None``. :func:`keel.ship.ci_ran` reads the distinction.

    ``statusCheckRollup`` retains every historical run of a check, not just the
    latest — a check that failed once and was later rerun to green still carries
    its old FAILURE conclusion in the raw list, and a freshly requeued rerun may
    carry no timestamp at all yet. The ``--jq`` filter dedupes by check identity
    (``context`` for legacy commit statuses, ``name`` for check runs — an empty
    string is treated the same as absent, matching the Python-side dedupe used
    by the merge gate) down to each check's most recent entry before collecting
    conclusions. "Most recent" prefers an entry genuinely still in flight (no
    ``conclusion`` yet *and* a recognized pending ``status``) over any
    concluded one for the same check — a new run cannot be queued before the
    previous one concluded — and otherwise compares ``completedAt``, falling
    back to ``startedAt``. Requiring a recognized pending ``status`` (not
    merely an absent ``conclusion``) means a malformed or unexpected payload
    shape can never mask a genuine stale failure. This mirrors
    :func:`keel.cli._rollup_recency`/``_PENDING_CHECK_STATES``, manually
    verified against a real ``jq`` binary; jq output can't be exercised by
    this module's offline unit tests (see the module docstring).
    """
    pending_states = (
        "\"EXPECTED\",\"PENDING\",\"QUEUED\",\"REQUESTED\",\"WAITING\",\"IN_PROGRESS\""
    )
    jq = (
        "[.statusCheckRollup[]] "
        "| group_by("
        "(.context | select(. != null and . != \"\")) "
        "// (.name | select(. != null and . != \"\")) "
        "// \"\""
        ") "
        "| map(max_by(["
        "((.conclusion == null) and "
        "((.status | if type == \"string\" then ascii_upcase else \"\" end) "
        "| IN(" + pending_states + "))), "
        "(.completedAt // .startedAt // \"\")"
        "])) "
        "| map(.conclusion // empty) "
        "| unique | join(\",\")"
    )
    result = run_argv(
        ["gh", "pr", "view", str(pr), "--json", "statusCheckRollup", "--jq", jq],
        cwd=cwd, **_kw(_run),
    )
    if not result.ok:
        return None
    return result.stdout.strip()


def ci_check_names(pr: int | str, *, cwd: str | None = None, _run=None) -> list[str] | None:
    """The distinct check identities reported for ``pr``, or ``None`` when ``gh`` failed.

    Used for the **count** an operator sees, so "0 checks" is a visible fact rather
    than something inferred from a blank word. Identity is ``context`` for legacy
    commit statuses and ``name`` for check runs — the same identity
    :func:`ci_conclusion` dedupes on, so the two views agree about what "one check"
    is. ``[]`` means the rollup is genuinely empty; ``None`` means ``gh`` could not
    be asked.
    """
    jq = (
        "[.statusCheckRollup[] "
        "| (.context | select(. != null and . != \"\")) "
        "// (.name | select(. != null and . != \"\")) "
        "// empty] "
        "| unique | .[]"
    )
    return _rollup_strings(pr, jq, cwd=cwd, _run=_run)


def ci_workflow_names(pr: int | str, *, cwd: str | None = None, _run=None) -> list[str] | None:
    """The distinct **workflow** names that reported for ``pr``, or ``None`` on failure.

    Deliberately not :func:`ci_check_names`. ``knobs.ci_workflows`` is keyed by the
    *workflow* name (``CI``, ``CodeQL``), but the rollup reports *job* names — a
    matrix job appears as ``test (py3.13 / ubuntu-latest)``, never as ``CI``. Asking
    the presence question against job names would report every declared workflow
    missing on a repo that uses a matrix, which is most of them.

    ``workflowName`` is what a check run carries for this; legacy commit statuses have
    none, so they fall back to ``context``/``name`` — a project that declares a bare
    status-check name still matches.
    """
    jq = (
        "[.statusCheckRollup[] "
        "| (.workflowName | select(. != null and . != \"\")) "
        "// (.context | select(. != null and . != \"\")) "
        "// (.name | select(. != null and . != \"\")) "
        "// empty] "
        "| unique | .[]"
    )
    return _rollup_strings(pr, jq, cwd=cwd, _run=_run)


def _rollup_strings(pr, jq: str, *, cwd: str | None, _run) -> list[str] | None:
    result = run_argv(
        ["gh", "pr", "view", str(pr), "--json", "statusCheckRollup", "--jq", jq],
        cwd=cwd, **_kw(_run),
    )
    if not result.ok:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def merged_prs(
    *, search: str | None = None, limit: int = 100, cwd: str | None = None, _run=None
) -> CommandResult:
    """List recently-merged PR numbers as a JSON array (``[{"number": N}, ...]``).

    Thin I/O for ``capture-verify`` transport derivation: the authoritative
    merged-PR set is read from the host instead of trusting the agent's args.
    ``search`` narrows the set (e.g. ``"merged:>=2026-06-01"``). Fail-soft —
    the caller inspects ``result.ok`` and degrades gracefully when offline.
    """
    argv = ["gh", "pr", "list", "--state", "merged", "--limit", str(limit), "--json", "number"]
    if search:
        argv += ["--search", search]
    return run_argv(argv, cwd=cwd, **_kw(_run))


def list_prs(
    *, head: str | None = None, limit: int = 100, cwd: str | None = None, _run=None
) -> CommandResult:
    """List PRs (any state) as a JSON array (``[{"number": N, "headRefName": ...}, ...]``).

    Thin I/O for dry-run integrity verification: the PRs that exist around a
    rehearsed run are read from the host. ``head`` narrows to a specific head
    branch. Fail-soft — the caller inspects ``result.ok`` and degrades to "no
    PRs observed" when offline.
    """
    argv = [
        "gh", "pr", "list", "--state", "all", "--limit", str(limit),
        "--json", "number,headRefName",
    ]
    if head:
        argv += ["--head", head]
    return run_argv(argv, cwd=cwd, **_kw(_run))


def pr_state(pr: int | str, *, cwd: str | None = None, _run=None) -> str | None:
    """Live PR state as ``open`` / ``merged`` / ``closed``, or ``None`` when unreadable.

    ``None`` is a fact about the **runner** (``gh`` missing, offline, no auth) and must
    not be read as a fact about the PR — the caller maps it to ``unknown``, never to
    ``missing``. A ``gh`` call that succeeds and reports no such PR is the only thing
    that means the PR is gone.
    """
    result = run_argv(
        ["gh", "pr", "view", str(pr), "--json", "state", "--jq", ".state"],
        cwd=cwd, **_kw(_run),
    )
    if not result.ok:
        return None
    raw = result.stdout.strip().lower()
    return raw if raw in ("open", "merged", "closed") else None


def pr_files(
    pr: int | str, *, cwd: str | None = None, _run=None, _sleep=None
) -> list[str] | None:
    """Paths the pull request changed — what it *meant* to change (#561).

    Read from GitHub rather than from a local diff on purpose: after a squash-merge
    the head branch is usually deleted, so the branch tip may not exist locally at
    the moment this check runs. ``None`` when ``gh`` could not be asked.
    """
    return _lines(
        ["gh", "pr", "view", str(pr), "--json", "files", "--jq", ".files[].path"],
        cwd=cwd, _run=_run, _sleep=_sleep,
    )


def commit_files(
    sha: str, *, cwd: str | None = None, _run=None, _sleep=None
) -> list[str] | None:
    """Paths a commit changed against its first parent — what actually *landed*.

    For a squash-merge the commit has one parent, so this is precisely the set of
    files the merge wrote onto the base branch. ``None`` when ``gh`` could not be
    asked; an empty list means the commit changed nothing, which is itself a fact.
    """
    return _lines(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}",
         "--jq", ".files[].filename"],
        cwd=cwd, _run=_run, _sleep=_sleep,
    )


def _lines(argv: list[str], *, cwd: str | None, _run, _sleep=None) -> list[str] | None:
    """Read newline-separated ``gh`` output, retrying transient failures.

    Routed through :func:`run_argv_retry` rather than :func:`run_argv`: the retry
    was written, tested, and called by nothing (#938). These are the reads it was
    written for — ``pr_files``, ``commit_files`` and, via :func:`_ints`,
    ``prs_merged_between`` — and one ``keel verify-merge`` run makes ``4 + N`` of
    them, N being the pull requests merged in the window (5–25 in this repo). Since
    #936 made an unreadable input exit 2 instead of quietly passing, a single blip
    on the busiest day produces a loud wrong answer, and a gate that cries wolf is
    a gate that gets bypassed.

    Retries only transient errors, so a persistent failure still returns ``None``
    and still becomes ``unknown``. The retry must not become a slower way to
    fail open.
    """
    result = run_argv_retry(argv, cwd=cwd, _run=_run, _sleep=_sleep)
    if not result.ok:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def prs_merged_between(
    base: str, since: str, until: str, *, cwd: str | None = None, _run=None,
    _sleep=None,
) -> list[int] | None:
    """Pull request numbers merged into ``base`` in the half-open window (#561).

    ``since``/``until`` are ISO-8601 timestamps. This is the window in which another
    merge can land work that a branch created before ``since`` will not contain — the
    precondition for an update-branch squash reverting it.
    """
    return _ints(
        ["gh", "pr", "list", "--base", base, "--state", "merged", "--limit", "100",
         "--json", "number,mergedAt",
         "--jq", f'.[] | select(.mergedAt > "{since}" and .mergedAt < "{until}") | .number'],
        cwd=cwd, _run=_run, _sleep=_sleep,
    )


def _ints(argv: list[str], *, cwd: str | None, _run, _sleep=None) -> list[int] | None:
    lines = _lines(argv, cwd=cwd, _run=_run, _sleep=_sleep)
    if lines is None:
        return None
    out = []
    for line in lines:
        try:
            out.append(int(line))
        except ValueError:
            continue
    return out


def _merge_window_fields(result: CommandResult) -> list[str]:
    """The four TSV fields of a ``pr_merge_window`` read, trailing blank preserved.

    Trims newlines only. ``str.strip()`` also removes the trailing tab, so a merged
    PR whose ``mergeCommit.oid`` has not appeared yet arrives as three fields and
    reads as malformed rather than as "not settled yet" — which is precisely the
    state the poll above exists to recognise (#938).
    """
    return result.stdout.strip("\r\n").split("\t")


#: Reads of a just-merged PR before GitHub has populated ``mergeCommit.oid``.
#: Three attempts a second apart: the field settles in well under a second in
#: practice, and the cap matters more than the ceiling — this runs immediately
#: after an irreversible merge, so it must give up rather than hang (#938).
MERGE_COMMIT_POLL_ATTEMPTS = 3
MERGE_COMMIT_POLL_DELAY_S = 1.0


def pr_merge_window(
    pr: int | str, *, cwd: str | None = None, _run=None, _sleep=None
) -> dict | None:
    """When a PR branched and merged, its base, and the SHA it merged as (#561).

    ``createdAt`` stands in for the branch point. It is the conservative choice: a
    branch is cut at or before its PR is opened, so the window can only be too wide,
    never too narrow — a wider window over-reports rather than missing a revert.

    ``None`` when ``gh`` cannot be asked or the PR is not merged.

    A merged PR whose ``mergeCommit.oid`` has not appeared yet is **polled** for,
    briefly, rather than read as absent. ``ship.md`` tells the operator to run the
    drift check "immediately after a successful merge" — the one moment the field
    is least likely to be populated — and since #936 an unreadable input exits 2.
    Without the poll the runbook's own timing is the most frequent trigger of the
    loud path, and the shipped remedy is a sentence of prose asking an agent to
    retry (#938). Bounded, and giving up still yields ``None``: the poll must not
    become a way to eventually pass.
    """
    # Named rather than written as two adjacent literals inside the argv list: an
    # implicit concatenation there reads as a possible missing comma (CodeQL flags it),
    # and an argv list is exactly where that ambiguity is expensive.
    jq = '[.createdAt, .mergedAt, .baseRefName, (.mergeCommit.oid // "")] | @tsv'
    argv = ["gh", "pr", "view", str(pr), "--json",
            "createdAt,mergedAt,baseRefName,mergeCommit", "--jq", jq]
    sleep_fn = _sleep or time.sleep
    for attempt in range(1, MERGE_COMMIT_POLL_ATTEMPTS + 1):
        result = run_argv_retry(argv, cwd=cwd, _run=_run, _sleep=_sleep)
        # Only newlines are trimmed. `.strip()` also eats the trailing tab, which is
        # the very field being waited on — an empty SHA would arrive as three fields
        # and read as malformed rather than as "not settled yet".
        parts = _merge_window_fields(result) if result.ok else []
        # Poll only the settling case: merged (first three fields present) with the
        # SHA still empty. An unmerged PR or an unreadable `gh` is a real answer, and
        # waiting on either would just make every such call three seconds slower.
        settling = len(parts) == 4 and all(parts[:3]) and not parts[3]
        if not settling:
            break
        if attempt < MERGE_COMMIT_POLL_ATTEMPTS:
            sleep_fn(MERGE_COMMIT_POLL_DELAY_S)
    if not result.ok:
        return None
    parts = _merge_window_fields(result)
    if len(parts) != 4 or not all(parts[:3]) or not parts[3]:
        return None
    return {
        "branched_at": parts[0],
        "merged_at": parts[1],
        "base": parts[2],
        "merge_commit": parts[3],
    }


def pr_merge_snapshot(pr: int | str, *, cwd: str | None = None, _run=None) -> CommandResult:
    return run_argv(
        [
            "gh", "pr", "view", str(pr),
            "--json", "headRefOid,mergeStateStatus,statusCheckRollup",
        ],
        cwd=cwd, **_kw(_run),
    )


def merge_pr(
    pr: int | str, *, method: str = "squash", cwd: str | None = None, _run=None
) -> CommandResult:
    return run_argv(["gh", "pr", "merge", str(pr), f"--{method}"], cwd=cwd, **_kw(_run))


def comment(pr: int | str, body: str, *, cwd: str | None = None, _run=None) -> CommandResult:
    return run_argv(["gh", "pr", "comment", str(pr), "--body", body], cwd=cwd, **_kw(_run))


def post_issue_comment(
    owner_repo: str,
    issue_or_pr: int | str,
    body: str,
    *,
    cwd: str | None = None,
    _run=None,
) -> CommandResult:
    return run_argv(
        [
            "gh",
            "api",
            f"repos/{owner_repo}/issues/{issue_or_pr}/comments",
            "-X",
            "POST",
            "-F",
            f"body={body}",
        ],
        cwd=cwd,
        **_kw(_run),
    )


def edit_issue_comment(
    owner_repo: str,
    comment_id: int | str,
    body: str,
    *,
    cwd: str | None = None,
    _run=None,
) -> CommandResult:
    return run_argv(
        [
            "gh",
            "api",
            f"repos/{owner_repo}/issues/comments/{comment_id}",
            "-X",
            "PATCH",
            "-F",
            f"body={body}",
        ],
        cwd=cwd,
        **_kw(_run),
    )


def close_issue(issue: int | str, *, cwd: str | None = None, _run=None) -> CommandResult:
    return run_argv(["gh", "issue", "close", str(issue)], cwd=cwd, **_kw(_run))


def issue_facts(issue: int | str, *, cwd: str | None = None, _run=None) -> CommandResult:
    """Fetch an issue's ``title`` and ``labels`` as JSON for ``keel guard``.

    Thin I/O for blocker evaluation: the issue facts are read from the host
    rather than trusting agent-supplied args. Fail-soft — the caller inspects
    ``result.ok`` and falls back to offline args when offline.
    """
    return run_argv(
        ["gh", "issue", "view", str(issue), "--json", "title,labels"],
        cwd=cwd, **_kw(_run),
    )


def _kw(_run):
    return {"_run": _run} if _run is not None else {}
