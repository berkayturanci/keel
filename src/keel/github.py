"""Thin, fail-soft ``gh`` (GitHub CLI) wrappers (argv, no shell).

Like :mod:`keel.git`, these build the exact ``gh`` command for each backbone
operation and run it via the injectable ``_run`` seam. Command construction is
unit-tested offline; live behaviour is opt-in.
"""

from __future__ import annotations

import json
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
        cwd=cwd,
        **_kw(_run),
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
    pending_states = '"EXPECTED","PENDING","QUEUED","REQUESTED","WAITING","IN_PROGRESS"'
    jq = (
        "[.statusCheckRollup[]] "
        "| group_by("
        '(.context | select(. != null and . != "")) '
        '// (.name | select(. != null and . != "")) '
        '// ""'
        ") "
        "| map(max_by(["
        "((.conclusion == null) and "
        '((.status | if type == "string" then ascii_upcase else "" end) '
        "| IN(" + pending_states + "))), "
        '(.completedAt // .startedAt // "")'
        "])) "
        "| map(.conclusion // empty) "
        '| unique | join(",")'
    )
    result = run_argv(
        ["gh", "pr", "view", str(pr), "--json", "statusCheckRollup", "--jq", jq],
        cwd=cwd,
        **_kw(_run),
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
        '| (.context | select(. != null and . != "")) '
        '// (.name | select(. != null and . != "")) '
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
        '| (.workflowName | select(. != null and . != "")) '
        '// (.context | select(. != null and . != "")) '
        '// (.name | select(. != null and . != "")) '
        "// empty] "
        "| unique | .[]"
    )
    return _rollup_strings(pr, jq, cwd=cwd, _run=_run)


def _rollup_strings(pr, jq: str, *, cwd: str | None, _run) -> list[str] | None:
    result = run_argv(
        ["gh", "pr", "view", str(pr), "--json", "statusCheckRollup", "--jq", jq],
        cwd=cwd,
        **_kw(_run),
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
        "gh",
        "pr",
        "list",
        "--state",
        "all",
        "--limit",
        str(limit),
        "--json",
        "number,headRefName",
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
        cwd=cwd,
        **_kw(_run),
    )
    if not result.ok:
        return None
    raw = result.stdout.strip().lower()
    return raw if raw in ("open", "merged", "closed") else None


def pr_files(pr: int | str, *, cwd: str | None = None, _run=None, _sleep=None) -> list[str] | None:
    """Paths the pull request changed — what it *meant* to change (#561).

    Read from GitHub rather than from a local diff on purpose: after a squash-merge
    the head branch is usually deleted, so the branch tip may not exist locally at
    the moment this check runs. ``None`` when ``gh`` could not be asked.
    """
    return _lines(
        ["gh", "pr", "view", str(pr), "--json", "files", "--jq", ".files[].path"],
        cwd=cwd,
        _run=_run,
        _sleep=_sleep,
    )


def commit_files(sha: str, *, cwd: str | None = None, _run=None, _sleep=None) -> list[str] | None:
    """Paths a commit changed against its first parent — what actually *landed*.

    For a squash-merge the commit has one parent, so this is precisely the set of
    files the merge wrote onto the base branch. ``None`` when ``gh`` could not be
    asked; an empty list means the commit changed nothing, which is itself a fact.
    """
    return _lines(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}", "--jq", ".files[].filename"],
        cwd=cwd,
        _run=_run,
        _sleep=_sleep,
    )


def _lines(argv: list[str], *, cwd: str | None, _run, _sleep=None) -> list[str] | None:
    """Read newline-separated ``gh`` output, retrying transient failures.

    Routed through :func:`run_argv_retry` rather than :func:`run_argv`: the retry
    was written, tested, and called by nothing (#938). These are the reads it was
    written for — ``pr_files`` and ``commit_files`` — and one
    ``keel verify-merge`` run makes ``4 + N`` of
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


#: How far back one `gh pr list` page reaches. A window older than this many
#: merges is reported as unreadable rather than empty — see #937 in the docstring
#: below. Named so the ceiling is one place to read and one place to change.
MERGED_PAGE_LIMIT = 100


def prs_merged_between(
    base: str,
    since: str,
    until: str,
    *,
    cwd: str | None = None,
    _run=None,
    _sleep=None,
    limit: int = MERGED_PAGE_LIMIT,
) -> list[int] | None:
    """Pull request numbers merged into ``base`` in the half-open window (#561).

    ``since``/``until`` are ISO-8601 timestamps. This is the window in which another
    merge can land work that a branch created before ``since`` will not contain — the
    precondition for an update-branch squash reverting it.

    ``None`` when the answer could not be read — including when the page came back
    **truncated**, which is #933's rule reached by a different mechanism (#937).
    ``gh pr list`` returns the *newest* N merges, and the window filter used to run
    inside ``--jq``, after that cut. On a repository where more than ``limit``
    pull requests merged since the window closed, the window's merges are not in
    the page at all, the filter matches nothing, and an empty list reads as
    "nothing overtook this merge" — a successful read that saw none of the answer.

    Live here, not latent: this repo has ~500 merged pull requests and
    ``docs/keel/cli.md`` documents a retrospective ``verify-merge --pr 543`` on a
    pull request from a previous month, which is exactly the shape that trips it.

    Truncation is detectable because the page is newest-first: if it came back
    **full** and its oldest entry still merged at or after ``since``, the page
    never reached back far enough to contain the window. Paginating would also
    work, but on a repo this size a retrospective check would walk hundreds of
    pull requests to answer one question; saying "I could not see that far" is
    honest and cheap. Raising ``limit`` is a separate tuning decision.

    The filter therefore runs in Python rather than ``--jq``: the truncation
    check needs the raw ``mergedAt`` values, which ``--jq`` had already discarded.
    """
    rows = _json_rows(
        [
            "gh",
            "pr",
            "list",
            "--base",
            base,
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "number,mergedAt",
        ],
        cwd=cwd,
        _run=_run,
        _sleep=_sleep,
    )
    if rows is None:
        return None
    merged_at = [str(row.get("mergedAt") or "") for row in rows]
    if len(rows) >= limit and merged_at and min(merged_at) >= since:
        # Full page whose oldest entry is still inside/after the window: the read
        # succeeded and saw only part of the answer, which must not read as clean.
        return None
    return [
        int(row["number"])
        for row in rows
        if isinstance(row.get("number"), int) and since < str(row.get("mergedAt") or "") < until
    ]


def _json_rows(argv: list[str], *, cwd: str | None, _run, _sleep=None) -> list[dict] | None:
    """Parse ``gh --json`` output into rows, or ``None`` when it could not be read."""
    result = run_argv_retry(argv, cwd=cwd, _run=_run, _sleep=_sleep)
    if not result.ok:
        return None
    try:
        data = json.loads(result.stdout or "[]")
    except ValueError:
        return None
    return data if isinstance(data, list) else None


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
    argv = [
        "gh",
        "pr",
        "view",
        str(pr),
        "--json",
        "createdAt,mergedAt,baseRefName,mergeCommit",
        "--jq",
        jq,
    ]
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
            "gh",
            "pr",
            "view",
            str(pr),
            "--json",
            "headRefOid,mergeStateStatus,statusCheckRollup",
        ],
        cwd=cwd,
        **_kw(_run),
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


def issue_facts(
    issue: int | str,
    *,
    cwd: str | None = None,
    fields: str = "title,labels",
    _run=None,
) -> CommandResult:
    """Fetch an issue's ``title`` and ``labels`` as JSON for ``keel guard``.

    Thin I/O for blocker evaluation: the issue facts are read from the host
    rather than trusting agent-supplied args. Fail-soft — the caller inspects
    ``result.ok`` and falls back to offline args when offline.

    ``fields`` widens the same call for the capture sink, which needs the body
    too. It stays a parameter rather than a second function so both readers make
    the identical request and a change to one is a change to both.
    """
    return run_argv(
        ["gh", "issue", "view", str(issue), "--json", fields],
        cwd=cwd,
        **_kw(_run),
    )


#: Labels read in one page. GitHub's default is 30; a policy pack plus keel's
#: attribution vocabulary exceeds that on any real repository, and a truncated
#: listing would report labels as missing that exist.
LABEL_PAGE_LIMIT = 500


def label_list_argv(repo: str | None = None) -> list[str]:
    """The exact ``gh label list`` command ``keel doctor`` runs (pure, for tests)."""
    argv = ["gh", "label", "list", "--limit", str(LABEL_PAGE_LIMIT), "--json", "name"]
    return argv + ["--repo", repo] if repo else argv


def list_labels(*, repo: str | None = None, cwd: str | None = None, _run=None) -> CommandResult:
    """List a repository's labels as JSON. Fail-soft: the caller reads ``result.ok``."""
    return run_argv(label_list_argv(repo), cwd=cwd, **_kw(_run))


def label_create_argv(name: str, repo: str | None = None) -> list[str]:
    """The exact ``gh label create`` command for one label.

    Built here, in the one module that owns keel's ``gh`` command shapes, so the command
    ``keel doctor`` *prints* for an operator to paste and the command ``--fix`` *runs*
    are the same string and cannot drift apart.
    """
    argv = ["gh", "label", "create", name]
    return argv + ["--repo", repo] if repo else argv


def create_label(
    name: str, *, repo: str | None = None, cwd: str | None = None, _run=None
) -> CommandResult:
    """Create one repository label. Mutating — callers gate it on operator consent."""
    return run_argv(label_create_argv(name, repo), cwd=cwd, **_kw(_run))


def _kw(_run):
    return {"_run": _run} if _run is not None else {}


# --- REST transport (#1175) -------------------------------------------------
#
# `gh pr view --json` and `gh pr merge` both go over GitHub's **GraphQL** endpoint.
# On a host whose egress proxy allows the REST API and blocks GraphQL — the remote
# environment #1169/#1170/#1171 were shipped from — every one of them fails before
# `keel merge` reaches its claim, its window re-check, its CI rollup or its evidence
# verification, and the operator is pushed off the only sanctioned merge path onto a
# hand-driven squash. These are the same reads and the same write, asked over REST.
#
# `gh api` expands `{owner}` and `{repo}` from the checkout's own remote, so none of
# this needs the project config — which matters for `keel verify-merge`, whose whole
# contract is that it reads GitHub and nothing else.

#: A query that costs nothing and proves only that the endpoint answers at all.
_GRAPHQL_PROBE = ["gh", "api", "graphql", "-f", "query=query{__typename}"]


def graphql_available(*, cwd: str | None = None, _run=None) -> bool:
    """Can this host reach GitHub's GraphQL endpoint?

    Probed once per run and reused, because the answer is a property of the *host*
    (its proxy) rather than of any pull request. A blocked endpoint fails the probe
    the same way it fails a real query — a non-zero exit, usually carrying a proxy's
    403 or 405 — so the probe needs no special-casing of the reason.

    Deliberately **not** a fallback after a failed call. Reads could retry safely;
    `gh pr merge` cannot. A merge that failed for an unknown reason may or may not
    have landed, and re-driving it over a second transport is how a pull request gets
    merged twice. The transport is chosen before anything is attempted.
    """
    return run_argv(_GRAPHQL_PROBE, cwd=cwd, **_kw(_run)).ok


def rest_pull(pr: int | str, *, cwd: str | None = None, _run=None) -> CommandResult:
    """The pull request as REST returns it (``head.sha``, ``mergeable_state``, …)."""
    return run_argv(["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr}"], cwd=cwd, **_kw(_run))


def rest_check_runs(sha: str, *, cwd: str | None = None, _run=None) -> CommandResult:
    """Check runs for one commit — the Actions half of a status-check rollup."""
    return run_argv(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs?per_page=100",
        ],
        cwd=cwd,
        **_kw(_run),
    )


def rest_commit_statuses(sha: str, *, cwd: str | None = None, _run=None) -> CommandResult:
    """Commit statuses for one commit — the other half, for non-Actions CI."""
    return run_argv(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{{owner}}/{{repo}}/commits/{sha}/statuses?per_page=100",
        ],
        cwd=cwd,
        **_kw(_run),
    )


def rest_json(result: CommandResult) -> object | None:
    """Parse a `gh api` body, tolerating `--paginate`'s concatenated pages."""
    if not result.ok:
        return None
    text = (result.stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # `gh api --paginate` concatenates one JSON document per page rather than merging
    # them, so a two-page read is `[…][…]` and is not a document at all. Decoding them
    # one at a time is what the flag actually returns, and a reader that only tried
    # `json.loads` saw the second page as a syntax error and reported "no checks".
    decoder, index, pages = json.JSONDecoder(), 0, []
    while index < len(text):
        try:
            page, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            return None
        pages.append(page)
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
    # `pages` cannot be empty here: `text` is non-empty and stripped, so the loop ran at
    # least once and either appended a page or returned. A guard for it would be a branch
    # no input can take, which is a claim about the code that the tests cannot check.
    if all(isinstance(page, list) for page in pages):
        return [item for page in pages for item in page]
    return pages[0] if len(pages) == 1 else pages


def _check_run_rows(payload: object) -> list[dict]:
    """The check runs inside a `commits/<sha>/check-runs` body, across every page.

    That endpoint answers with an **object** — ``{"total_count": N, "check_runs": [...]}``
    — not with a bare array, and `gh api --paginate` concatenates one such object per
    page. So a head with more than a hundred checks arrives as ``{…}{…}``, which
    :func:`rest_json` correctly reports as *a list of two page objects*.

    Read as a list of check runs, those two objects became two entries with no ``name``,
    no ``status`` and no ``conclusion`` — neither a failure nor pending, so the reducer
    counted them as checks that had reported and returned **pass**. A merge gate handed
    an all-green rollup for a head whose hundred-odd real checks were never looked at.

    Only the documented shape contributes. A payload that is neither the object nor
    pages of it yields nothing, and the caller treats an empty rollup as *no checks have
    reported*, which refuses a non-docs merge rather than passing it.
    """
    pages = payload if isinstance(payload, list) else [payload]
    rows: list[dict] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        runs = page.get("check_runs")
        if isinstance(runs, list):
            rows.extend(run for run in runs if isinstance(run, dict))
    return rows


def rest_rollup(check_runs: object, statuses: object) -> list[dict]:
    """The two REST payloads in the shape ``statusCheckRollup`` has.

    Pure, and a translation rather than a judgement: the rollup reducer reads
    ``name``/``context``, ``status``, ``conclusion`` and the two timestamps, and
    GraphQL spells those in SCREAMING_CASE where REST spells them in lower. Keeping
    the translation here means the reducer, the dedupe and the recency ordering are
    one implementation asked the same question over either transport, instead of two
    that agree until they do not.

    Commit statuses are carried through with their ``context`` and ``state``, which is
    what the GraphQL rollup returns for a ``StatusContext`` and what the reducer reads
    from one (#1202). They are included rather than dropped for two reasons: a failing
    status has to be able to *fail* a merge, and even a passing one makes the rollup
    non-empty, which is the difference between ``pass`` and ``no-checks``.
    """
    entries: list[dict] = []
    # `_check_run_rows` has already dropped every non-object, so nothing is re-checked
    # here: a guard no input can trip is a claim about the data the tests cannot make.
    for run in _check_run_rows(check_runs):
        entries.append(
            {
                "name": run.get("name"),
                "status": _upper(run.get("status")),
                "conclusion": _upper(run.get("conclusion")),
                "startedAt": run.get("started_at"),
                "completedAt": run.get("completed_at"),
            }
        )
    for row in _status_rows(statuses):
        # **Carried through, not translated.** A commit status keeps its verdict in
        # `state`, exactly as the GraphQL rollup returns one, because the reducer reads
        # that field now (#1202). #1175 translated it here instead, which worked and left
        # the two wires speaking different shapes — and the GraphQL one, which is the
        # default nearly every run takes, still counted a *failing* status as a check
        # that had reported. The timestamps come along because dedupe orders by them.
        entries.append(
            {
                "context": row.get("context"),
                "state": _upper(row.get("state")),
                "completedAt": row.get("updated_at"),
                "startedAt": row.get("created_at"),
            }
        )
    return entries


def _status_rows(payload: object) -> list[dict]:
    """The rows of a `commits/<sha>/statuses` body, across every page.

    That endpoint *does* answer with an array, so `--paginate` yields a list of pages —
    which :func:`rest_json` flattens — or a single page's list. Both arrive here as a
    list of row objects; anything else contributes nothing.
    """
    rows = payload if isinstance(payload, list) else []
    return [row for row in rows if isinstance(row, dict)]


def _upper(value: object) -> str | None:
    """``value`` upper-cased when it is a string; ``None`` otherwise.

    REST says ``in_progress`` and ``success`` where GraphQL says ``IN_PROGRESS`` and
    ``SUCCESS``; the two differ in case alone, so upper-casing *is* the translation.
    """
    return value.upper() if isinstance(value, str) else None


def rest_merge_pr(
    pr: int | str,
    *,
    method: str = "squash",
    head_sha: str | None = None,
    cwd: str | None = None,
    _run=None,
) -> CommandResult:
    """Merge the pull request over REST, pinned to ``head_sha`` when one is known.

    ``sha`` is REST's own head-pin: the merge is refused if the pull request has moved
    since it was read. `gh pr merge` has no equivalent it applies by default, so this
    transport is *stricter* than the one it stands in for — deliberately, because a
    merge is the one operation here that cannot be taken back.
    """
    argv = [
        "gh",
        "api",
        "-X",
        "PUT",
        f"repos/{{owner}}/{{repo}}/pulls/{pr}/merge",
        "-f",
        f"merge_method={method}",
    ]
    if head_sha:
        argv += ["-f", f"sha={head_sha}"]
    return run_argv(argv, cwd=cwd, **_kw(_run))


def rest_pr_files(
    pr: int | str, *, cwd: str | None = None, _run=None, _sleep=None
) -> list[str] | None:
    """Paths the pull request changed, over REST. ``None`` when unreadable."""
    return _lines(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{{owner}}/{{repo}}/pulls/{pr}/files?per_page=100",
            "--jq",
            ".[].filename",
        ],
        cwd=cwd,
        _run=_run,
        _sleep=_sleep,
    )


def _rest_commit_on_branch(sha: str, base: str, *, cwd: str | None, _run) -> bool:
    """Is ``sha`` an ancestor of ``base`` — that is, did this commit actually land?

    ``compare/<base>...<sha>`` answers ``identical`` when they are the same commit and
    ``behind`` when ``sha`` is reachable from ``base``; ``ahead`` and ``diverged`` are
    the speculative test merge, which exists as an object and is on no branch.
    """
    if not sha or not base:
        return False
    result = run_argv(
        [
            "gh",
            "api",
            f"repos/{{owner}}/{{repo}}/compare/{base}...{sha}",
            "--jq",
            ".status",
        ],
        cwd=cwd,
        **_kw(_run),
    )
    return result.ok and result.stdout.strip() in ("identical", "behind")


def rest_pr_merge_window(
    pr: int | str, *, cwd: str | None = None, _run=None, _sleep=None
) -> dict | None:
    """``branched_at`` / ``merged_at`` / ``base`` / ``merge_commit`` over REST.

    The same four fields :func:`pr_merge_window` returns, and the same settling poll:
    a pull request reports ``merged_at`` before ``merge_commit_sha`` is populated, and
    reading it in that instant is how the drift check comes back "no merge commit yet"
    for a merge that had just happened.
    """
    sleep_fn = _sleep or time.sleep
    for attempt in range(1, MERGE_COMMIT_POLL_ATTEMPTS + 1):
        payload = rest_json(
            run_argv_retry(
                ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{pr}"],
                cwd=cwd,
                _run=_run,
                _sleep=_sleep,
            )
        )
        if not isinstance(payload, dict):
            return None
        window = {
            "branched_at": str(payload.get("created_at") or ""),
            "merged_at": str(payload.get("merged_at") or ""),
            "base": str((payload.get("base") or {}).get("ref") or ""),
            "merge_commit": str(payload.get("merge_commit_sha") or ""),
        }
        # **Reachability, not presence.** REST's `merge_commit_sha` is not empty while a
        # pull request is open: GitHub documents it as the *speculative test-merge* SHA
        # (`refs/pull/<n>/merge`) until the merge lands, when it becomes the commit that
        # actually landed. So "merged and the field is filled" is true on the first
        # post-merge read even while the cached test SHA is still being served — and the
        # drift check would then judge the test merge instead of the squash. GraphQL's
        # `mergeCommit.oid` is null until the real commit exists, which is why the same
        # poll is correct there and not here.
        settling = window["merged_at"] and not _rest_commit_on_branch(
            window["merge_commit"], window["base"], cwd=cwd, _run=_run
        )
        if settling:
            if attempt < MERGE_COMMIT_POLL_ATTEMPTS:
                sleep_fn(MERGE_COMMIT_POLL_DELAY_S)
                continue
            # Still unsettled when the budget runs out: the SHA on offer is one this
            # check could not find on the base branch, so it is not an answer. Returning
            # it anyway is how the drift read would judge the speculative test merge.
            return None
        # **All four, exactly as the GraphQL reader requires.** REST answers an
        # *unmerged* pull request with `created_at` and `base.ref` and a null
        # `merged_at`, so "any field present" reported a window for one — and a caller
        # that supplied `--merge-sha` then went on to judge drift on a merge that had
        # not happened, where the same call over GraphQL says `unknown`.
        return window if all(window.values()) else None
    return None  # pragma: no cover - the loop always returns on its last attempt


def rest_prs_merged_between(
    base: str,
    since: str,
    until: str,
    *,
    cwd: str | None = None,
    _run=None,
    _sleep=None,
    limit: int = MERGED_PAGE_LIMIT,
) -> list[int] | None:
    """:func:`prs_merged_between` over REST, with the same truncation honesty.

    REST cannot sort by merge time — ``sort`` takes ``created``, ``updated``,
    ``popularity`` and ``long-running``, and nothing else — so the page is ordered by
    ``updated`` descending and the truncation rule is re-derived on that key rather
    than transplanted. It still holds, and for a reason worth writing down: merging a
    pull request *updates* it, so ``merged_at <= updated_at`` for every row. If the
    oldest row this page reached was updated before ``since``, every row it did not
    reach was updated earlier still, so it was merged earlier still, so it is outside
    the window and the window was seen whole. If it was not, the page may have stopped
    short of the answer — and a partial read must not render as "nothing overtook this
    merge" (#933, #937).

    ``state=closed`` is the narrowest REST offers; a closed-unmerged row has no
    ``merged_at``, is filtered out, and costs only a slot in the page.
    """
    rows = rest_json(
        run_argv_retry(
            [
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/pulls"
                f"?base={base}&state=closed&sort=updated&direction=desc&per_page={limit}",
            ],
            cwd=cwd,
            _run=_run,
            _sleep=_sleep,
        )
    )
    if not isinstance(rows, list):
        return None
    updated = [str(row.get("updated_at") or "") for row in rows if isinstance(row, dict)]
    if len(rows) >= limit and updated and min(updated) >= since:
        return None
    return [
        int(row["number"])
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("number"), int)
        and since < str(row.get("merged_at") or "") < until
    ]
