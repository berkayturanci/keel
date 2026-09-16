"""Thin, fail-soft ``git`` wrappers (argv, no shell).

These build the exact git command for each backbone operation and run it via the
injectable ``_run`` seam, so the command construction is unit-tested offline; live
behaviour is exercised opt-in against a real repo. Each returns a
:class:`keel.runner.CommandResult` (or a parsed value), never raising.
"""

from __future__ import annotations

import re

from . import tdd
from .runner import CommandResult, run_argv


def fetch(remote: str, ref: str, *, cwd: str | None = None, _run=None) -> CommandResult:
    """Fetch one branch of ``remote``.

    The branch goes as ``refs/heads/<ref>``, never bare: a positional argument that begins
    with ``-`` is an *option* to git, and ``--upload-pack=<program>`` among those runs a
    program. The landing plan already refuses such a name; this keeps the wrapper from being
    the thing that makes a stray value dangerous. git still updates ``<remote>/<ref>`` for a
    fully qualified ref, so nothing downstream reads a different name.
    """
    return run_argv(["git", "fetch", "--quiet", remote, f"refs/heads/{ref}"], cwd=cwd, **_kw(_run))


def worktree_add(
    base: str, branch: str, path: str, *, cwd: str | None = None, _run=None
) -> CommandResult:
    return run_argv(["git", "worktree", "add", "-b", branch, path, base], cwd=cwd, **_kw(_run))


def worktree_remove(path: str, *, cwd: str | None = None, _run=None) -> CommandResult:
    return run_argv(["git", "worktree", "remove", path, "--force"], cwd=cwd, **_kw(_run))


def worktree_list(*, cwd: str | None = None, _run=None) -> CommandResult:
    return run_argv(["git", "worktree", "list", "--porcelain"], cwd=cwd, **_kw(_run))


def current_branch(*, cwd: str | None = None, _run=None) -> str | None:
    result = run_argv(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, **_kw(_run))
    return result.stdout.strip() if result.ok else None


def list_branches(*, cwd: str | None = None, _run=None) -> CommandResult:
    """List local + remote branch short names (one per line) as a ``CommandResult``.

    Returns the raw result (like :func:`worktree_list`) rather than a parsed
    fail-soft list, so a caller that needs to *distinguish a git error from an
    empty repo* — e.g. dry-run integrity verification, which must fail closed
    when it cannot observe — can inspect ``result.ok``. Parsing is the caller's.
    """
    return run_argv(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"],
        cwd=cwd,
        **_kw(_run),
    )


#: A 40- or 64-hex object name (SHA-1 / SHA-256). git may print a ``warning:`` to
#: stderr while still succeeding; reading ``stdout`` avoids the contamination, and
#: validating the shape is a second line of defence so a stray token never poses as a SHA.
_SHA_RE = re.compile(r"\A[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def rev_parse(ref: str, *, cwd: str | None = None, _run=None) -> str | None:
    """Resolve ``ref`` to a full commit SHA; ``None`` when it cannot be resolved."""
    result = run_argv(["git", "rev-parse", "--verify", "--quiet", ref], cwd=cwd, **_kw(_run))
    output = result.stdout.strip()
    return output if result.ok and _SHA_RE.match(output) else None


def merge_base(a: str, b: str, *, cwd: str | None = None, _run=None) -> str | None:
    """Best common ancestor of ``a`` and ``b``; ``None`` when there is none/on error."""
    result = run_argv(["git", "merge-base", a, b], cwd=cwd, **_kw(_run))
    output = result.stdout.strip()
    return output if result.ok and _SHA_RE.match(output) else None


def rev_count(base: str, head: str, *, cwd: str | None = None, _run=None) -> int | None:
    """Commits in ``base..head`` (how far ``head`` is ahead of ``base``); ``None`` on error."""
    result = run_argv(["git", "rev-list", "--count", f"{base}..{head}"], cwd=cwd, **_kw(_run))
    if not result.ok:
        return None
    output = result.stdout.strip()
    if not output.isdigit():
        return None
    return int(output)


def changed_files(base: str, head: str, *, cwd: str | None = None, _run=None) -> list[str] | None:
    """Files changed between ``base`` and ``head`` (``base...head``).

    ``None`` when the git command failed — deliberately distinct from ``[]`` (the
    command ran and there were no changes), so a caller classifying risk or checking
    scope can tell "could not read the diff" apart from "the diff is empty" instead of
    treating an unreadable diff as a clean, empty one.
    """
    result = run_argv(["git", "diff", "--name-only", f"{base}...{head}"], cwd=cwd, **_kw(_run))
    if not result.ok:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def commit_log(base: str, head: str, *, cwd: str | None = None, _run=None) -> str | None:
    """Raw ``git log`` for ``base..head``: this branch's own commits, oldest first.

    Returns git's stdout verbatim — :func:`keel.tdd.parse_commits` turns it into records,
    so the parsing is pure and unit-tested rather than living behind a subprocess. ``None``
    when the command failed, deliberately distinct from ``""`` (the range is empty): the
    ``tdd-order`` gate must be able to block on "we could not read the history" instead of
    reading it as "this branch has no commits".

    Four flags carry the whole meaning of "this implementer's commit order", and each is
    load-bearing:

    ``base..head``
        not ``base...head`` — the symmetric form would also list the base's side.
    ``--topo-order``
        git's default is *commit-date* order. Once the branch integrates its base (ship
        s10), a base commit dated before the tests commit sorts ahead of it and becomes
        the "first commit" — so the same topology passed or blocked depending on nothing
        but timestamps. Topological order asks about ancestry, which is what was meant.
    ``--first-parent``
        follows only this branch's own line through its merges, dropping the commits a
        base merge brought in. Without it a stale local ``base`` ref leaves those commits
        inside the range, and one of them can be judged as this implementer's first
        commit. The merge commits themselves stay on the chain and
        :func:`keel.tdd.check_order` skips them.
    ``--name-status``
        not ``--name-only`` — a name alone cannot tell an addition from a deletion, and
        the gate has to separate "wrote the failing tests" from ``git rm`` over them.

    ``--reverse`` is applied after ordering and selection, so the output is oldest-first
    within the topological order.
    """
    result = run_argv(
        [
            "git",
            "log",
            "--topo-order",
            "--first-parent",
            "--reverse",
            "--no-color",
            f"--format={tdd.LOG_FORMAT}",
            "--name-status",
            f"{base}..{head}",
        ],
        cwd=cwd,
        **_kw(_run),
    )
    return result.stdout if result.ok else None


def diff(base: str, head: str, *, cwd: str | None = None, _run=None) -> str | None:
    """The unified diff between ``base`` and ``head`` (``base...head``).

    ``None`` when the git command failed — distinct from ``""`` (the command ran and
    the diff is empty), so a review/gate caller can refuse to treat an unreadable diff
    as "nothing to review".
    """
    result = run_argv(["git", "diff", f"{base}...{head}"], cwd=cwd, **_kw(_run))
    return result.stdout if result.ok else None


def hash_object(path: str, *, cwd: str | None = None, _run=None) -> str | None:
    """Write ``path``'s content into the object database; return its blob SHA.

    ``-w`` is what makes the landing possible without a checkout: the blob exists in
    the repository before any tree references it, so the commit can be assembled with
    plumbing and pushed, and a failed push leaves nothing but an unreferenced object
    that ``git gc`` collects.
    """
    result = run_argv(["git", "hash-object", "-w", "--", path], cwd=cwd, **_kw(_run))
    output = result.stdout.strip()
    return output if result.ok and _SHA_RE.match(output) else None


def ls_tree(treeish: str, *, cwd: str | None = None, _run=None) -> str | None:
    """List one tree's own entries (not recursive); ``None`` when it cannot be read.

    ``None`` and ``""`` are different answers and both are ordinary here: a sink
    directory that does not exist on the base branch yet cannot be read (``None``),
    and an existing but empty one reads as no entries. The caller treats the first as
    "start a new directory" rather than as an error, which is what makes the very
    first lesson land as cleanly as the hundredth.

    ``-z`` for the same reason :func:`mktree` takes it: entries are NUL-terminated,
    so a name is returned raw instead of C-quoted, and the round trip back through
    ``mktree`` cannot re-encode one.
    """
    result = run_argv(["git", "ls-tree", "-z", treeish], cwd=cwd, **_kw(_run))
    return result.stdout if result.ok else None


def mktree(listing: str, *, cwd: str | None = None, _run=None) -> str | None:
    """Write a tree object from NUL-terminated ``ls-tree``-shaped ``listing``.

    The listing arrives on **stdin**, never in an argv: it carries object names and
    file names, and an argv is world-readable in ``ps`` for the life of the process.

    **``-z``, because a text-mode pipe rewrites newlines on Windows.** Python opens a
    subprocess's stdin with ``newline=None`` under ``text=True``, which translates
    every ``\n`` to ``os.linesep`` — so a LF-terminated listing reaches git as CRLF
    there, and `mktree` does not complain: it writes a tree whose entry is named
    ``<name>\r``, exits 0, and hands back a different SHA (measured). NUL-terminated
    input has no newline to translate, so the same bytes arrive on every platform.
    """
    result = run_argv(["git", "mktree", "-z"], cwd=cwd, stdin_text=listing, **_kw(_run))
    output = result.stdout.strip()
    return output if result.ok and _SHA_RE.match(output) else None


def commit_tree(
    tree: str, *, parent: str, message: str, cwd: str | None = None, _run=None
) -> str | None:
    """Commit ``tree`` with a single ``parent``; return the new commit SHA.

    The message goes in the **argv**, not on stdin, for the newline reason in
    :func:`mktree`: a text-mode pipe turns every ``\n`` into CRLF on Windows, and a
    commit message is content — it would land on the base branch carrying stray
    carriage returns and stop being byte-identical across platforms. It is safe
    there in a way a tree listing is not: this message is a fixed subject plus the
    artifact path, which is about to be published on the base branch anyway.
    """
    result = run_argv(
        ["git", "commit-tree", tree, "-p", parent, "-m", message],
        cwd=cwd,
        **_kw(_run),
    )
    output = result.stdout.strip()
    return output if result.ok and _SHA_RE.match(output) else None


def diff_names(a: str, b: str, *, cwd: str | None = None, _run=None) -> list[str] | None:
    """Paths differing between two tree-ish objects (two-dot); ``None`` on error.

    Two-dot on purpose, unlike :func:`changed_files`: the landing compares a commit
    against the parent it was *just built on*, so "what did this commit add" is the
    literal difference between the two trees and not a merge-base question. ``None``
    stays distinct from ``[]`` so a caller that must fail closed when it cannot
    observe — the landing's own "this commit changes exactly one file" check — can
    tell an unreadable diff from an empty one.
    """
    # `-z` with `core.quotePath=false`, for the same reason `ls_tree`/`mktree` use it.
    # Under the default `quotePath=true` git renders a non-ASCII name as a C-quoted
    # escape — `".keel/learning/caf\\303\\251.md"` — which can never equal the raw path
    # the landing planned, so the one live safety check refused every such artifact
    # permanently and blamed the commit for changing a file nobody asked for.
    result = run_argv(
        ["git", "-c", "core.quotePath=false", "diff", "--name-only", "-z", a, b],
        cwd=cwd,
        **_kw(_run),
    )
    if not result.ok:
        return None
    return [name for name in result.stdout.split("\0") if name.strip()]


def push_commit(
    remote: str, commit: str, ref: str, *, cwd: str | None = None, _run=None
) -> CommandResult:
    """Fast-forward ``ref`` on ``remote`` to ``commit``.

    Deliberately **not** forced. A rejected push is the concurrency signal the
    landing is built around: another ship pushed its own lesson first, so this one
    re-reads the branch and rebuilds its commit on top. Forcing here would discard
    that ship's lesson — and, on a base branch, whatever else arrived with it.
    """
    return run_argv(["git", "push", remote, f"{commit}:{ref}"], cwd=cwd, **_kw(_run))


def _kw(_run):
    """Pass ``_run`` through only when provided (so the default subprocess is used otherwise)."""
    return {"_run": _run} if _run is not None else {}
