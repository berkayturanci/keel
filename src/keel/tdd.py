"""``implement_mode: tdd`` — the test-first s4 profile and its commit-order gate (#1020).

Some implementers skip parts of an issue, and nothing catches it until a reviewer reads
the diff. A test-first contract catches it at s8 instead: the acceptance criteria are
committed as *failing tests* before a line of implementation exists, so the gates
themselves say whether the criteria were met.

``tdd`` is an **s4 profile**, exactly as ``compound`` is — the backbone step ids do not
change. In ``tdd`` mode s4 runs in two phases against the same provider, one
``keel delegate run`` call and one commit each:

``tests``           the failing tests derived from the issue's acceptance criteria, as a
                    test-only diff (the gates are expected red here);
``implementation``  the change that turns them green.

This module is the pure half — the mode resolution, the commit parser, and the
``tdd-order`` verifier that decides whether the branch really was written that way:

* :func:`resolve_mode` — ``knobs.implement_mode`` + the per-run ``--tdd`` flag -> a
  :class:`Mode`, rendered by ``keel plan``/``keel ship --json`` so every host runs the
  same profile;
* :func:`test_globs` — where a project says its tests live, read off
  ``policy_pack.test_groups``;
* :func:`parse_commits` — ``git log`` output -> :class:`Commit` records;
* :func:`check_order` — the gate: the first non-merge commit on the branch adds or
  modifies test paths and touches nothing else, an implementation commit follows it, no
  later commit deletes a test, and the gate run is green.

**What this gate does not do.** It reads *commit order and paths*, and nothing else. It
does not run phase A's tests, does not verify they were red, and cannot tell whether the
committed tests assert anything at all. Three residuals follow from that, each a
reviewer's catch rather than a gate's:

* a first commit adding an empty file under ``tests/`` satisfies the "adds a test" rule,
  and so does one that merely *renames* an existing test within the test paths;
* a test deleted inside a **merge from a side branch** is not judged, because merges are
  skipped so that a deletion made on the base is not blamed on this implementer (see the
  comment at the merge skip in :func:`check_order`);
* nothing here says the tests are good, only that the branch has the shape of a
  test-first run.

The red-then-green half is the implementer's brief and its PR body, not this gate.

Pure and deterministic: no wall-clock, no randomness, no I/O, and — at module scope — no
keel imports at all. The one git read the gate needs is :func:`keel.git.commit_log`, the
same thin seam every other command reads git through; core is handed its *output*.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: ``knobs.implement_mode`` values. ``default`` is the single-pass s4 keel has always run.
DEFAULT_MODE = "default"
TDD_MODE = "tdd"
MODES = (DEFAULT_MODE, TDD_MODE)

#: The gate id ``tdd`` mode adds at the s8 test phase.
GATE_ID = "tdd-order"

#: The two s4 phases, in the only order that is TDD.
PHASE_TESTS = "tests"
PHASE_IMPLEMENTATION = "implementation"
PHASES = (PHASE_TESTS, PHASE_IMPLEMENTATION)

#: Record separator git writes before each commit's format line, and the field separator
#: inside it. Both are control characters that cannot occur in a path or a subject line,
#: so a commit message containing a newline — or a filename containing one — cannot be
#: read as another commit.
RECORD_SEP = "\x1e"
FIELD_SEP = "\x1f"

#: The ``--format`` :func:`parse_commits` reads. Kept here, next to the parser, so the
#: argv in :func:`keel.git.commit_log` and the parser cannot drift apart.
LOG_FORMAT = f"{RECORD_SEP}%H{FIELD_SEP}%P{FIELD_SEP}%s"


@dataclass(frozen=True)
class Mode:
    """The resolved s4 implement profile and where it came from."""

    name: str
    source: str

    @property
    def is_tdd(self) -> bool:
        return self.name == TDD_MODE

    def as_dict(self) -> dict[str, Any]:
        """JSON-stable record for ``keel plan`` / ``keel ship --json``."""
        return {
            "mode": self.name,
            "tdd": self.is_tdd,
            "source": self.source,
            "phases": list(PHASES) if self.is_tdd else [],
            "gate": GATE_ID if self.is_tdd else None,
        }


def resolve_mode(configured: Any = None, *, flag: bool = False) -> Mode:
    """The s4 profile for this run: ``--tdd`` > ``knobs.implement_mode`` > ``default``.

    A per-run flag can only *select* the stricter profile — there is no ``--no-tdd``,
    because a project that configured a test-first contract has said the contract is the
    policy, and a flag that switched it off from the command line would make the policy
    advisory. Unknown values are impossible here (the schema owns the vocabulary) and
    read as ``default`` rather than raising: this resolver runs on every ship.
    """
    if flag:
        return Mode(TDD_MODE, "flag:--tdd")
    value = configured.strip() if isinstance(configured, str) else ""
    if value == TDD_MODE:
        return Mode(TDD_MODE, "knobs.implement_mode")
    return Mode(DEFAULT_MODE, "default")


def test_globs(policy_pack: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Where this project's tests live, from ``policy_pack.test_groups``.

    A group's ``paths`` are *selectors* — the paths that make the group relevant — and on
    a real project they routinely include the implementation surface (keel's own ``unit``
    group selects ``src/**`` as well as ``tests/**``). Read as test paths they would make
    this gate vacuous: a first commit touching only ``src/`` would pass as "tests", and
    the implementation commit that must follow it would then have nowhere to land.

    So a group may declare ``test_paths`` — the paths its *tests* occupy — and **when any
    group declares one, only the declared ones count**. Mixing the remaining groups'
    selectors back in would re-import exactly the implementation surface ``test_paths``
    exists to exclude. A project that declares none keeps the plain reading of
    ``paths``, and :func:`check_order` fails closed when that leaves nothing at all.
    """
    pack = policy_pack if isinstance(policy_pack, Mapping) else {}
    groups = pack.get("test_groups")
    if not isinstance(groups, Mapping):
        return ()
    declared: list[str] = []
    selectors: list[str] = []
    for _name, group in sorted(groups.items(), key=lambda item: str(item[0])):
        if not isinstance(group, Mapping):
            continue
        declared.extend(_globs(group.get("test_paths")))
        selectors.extend(_globs(group.get("paths")))
    return _unique(declared) if declared else _unique(selectors)


def _globs(raw: Any) -> list[str]:
    """Non-blank string entries of a path list (anything else contributes nothing)."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [entry.strip() for entry in raw if isinstance(entry, str) and entry.strip()]


def _unique(globs: Iterable[str]) -> tuple[str, ...]:
    """De-duplicated, order-preserving — the gate's message lists these verbatim."""
    return tuple(dict.fromkeys(globs))


def is_test_path(path: str, globs: Sequence[str]) -> bool:
    """Does ``path`` sit under one of the project's test globs?

    ``fnmatch`` semantics, the same matcher :mod:`keel.classify` uses for
    ``tier3_globs`` and ``docs_gate_paths``, so one project writes one kind of glob.
    """
    return any(fnmatch.fnmatch(path, glob) for glob in globs)


#: ``--name-status`` letters this module reasons about. Only ``D`` is load-bearing: every
#: other status leaves the path present in the tree after the commit.
ADDED = "A"
MODIFIED = "M"
DELETED = "D"
RENAMED = "R"
COPIED = "C"


@dataclass(frozen=True)
class Change:
    """One path a commit touched, and what it did to it.

    ``--name-only`` cannot tell an addition from a deletion, which made a first commit
    that only ran ``git rm`` over the test suite look exactly like one that wrote it. The
    status is what separates "wrote the failing tests" from "removed the failing tests".
    """

    status: str
    path: str
    #: Where a rename or copy came *from*; ``None`` for every other status. Recorded
    #: because a path that left the test tree is missing from ``path`` by construction:
    #: after ``git mv tests/test_a.py src/legacy.py`` the only mention of the test is
    #: here. :func:`check_order` needs it to tell a move-out from an ordinary rename.
    source: str | None = None

    @property
    def deleted(self) -> bool:
        return self.status == DELETED

    @property
    def present(self) -> bool:
        """Does the path exist in the tree after this commit? (Everything but ``D``.)"""
        return not self.deleted


@dataclass(frozen=True)
class Commit:
    """One commit on the branch: what it is, and what it did to which paths."""

    sha: str
    subject: str = ""
    changes: tuple[Change, ...] = ()
    merge: bool = False

    @property
    def short(self) -> str:
        """The 7-character sha operators read in a gate message."""
        return self.sha[:7]

    @property
    def files(self) -> tuple[str, ...]:
        """Every path the commit touched, in commit order, deletions included."""
        return tuple(change.path for change in self.changes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "subject": self.subject,
            "changes": [
                {"status": c.status, "path": c.path, "source": c.source} for c in self.changes
            ],
            "files": list(self.files),
            "merge": self.merge,
        }


def _change(line: str) -> Change | None:
    """One ``--name-status`` line -> a :class:`Change` (``None`` when it is not one).

    A rename or copy prints ``R100<TAB>old<TAB>new``. The destination is the path that
    exists afterwards, so that is ``path``; the origin is kept as ``source``, because a
    rename *out of* the test tree removes a test as surely as ``git rm`` does and the
    destination alone cannot show it.

    Deliberately glob-free: this turns git's output into records, and *which* paths are
    tests is :func:`check_order`'s question against the project's policy.
    """
    fields = line.split("\t")
    if len(fields) < 2 or not fields[0].strip():
        return None
    status = fields[0].strip()[0].upper()
    renamed = status in (RENAMED, COPIED) and len(fields) > 2
    path = (fields[2] if renamed else fields[1]).strip()
    source = fields[1].strip() if renamed else None
    if not path:
        return None
    return Change(status, path, source or None)


def parse_commits(text: str | None) -> tuple[Commit, ...] | None:
    """Parse :data:`LOG_FORMAT` + ``--name-status`` output, oldest commit first.

    ``None`` in, ``None`` out — :func:`keel.git.commit_log` reports an unreadable
    history as ``None``, and that must stay distinct from "the branch has no commits"
    all the way into :func:`check_order`, which blocks on the first and can say so.
    """
    if text is None:
        return None
    commits: list[Commit] = []
    for chunk in text.split(RECORD_SEP):
        if not chunk.strip():
            continue
        header, _, body = chunk.partition("\n")
        sha, _, rest = header.partition(FIELD_SEP)
        parents, _, subject = rest.partition(FIELD_SEP)
        sha = sha.strip()
        if not sha:
            continue
        commits.append(
            Commit(
                sha=sha,
                subject=subject.strip(),
                changes=tuple(
                    change
                    for change in (_change(line) for line in body.splitlines() if line.strip())
                    if change is not None
                ),
                # A merge has more than one parent. Merges are skipped rather than
                # judged: a merge from the base branch carries every path the base
                # moved, which is not this implementer's commit order.
                merge=len(parents.split()) > 1,
            )
        )
    return tuple(commits)


#: Machine-readable outcomes of :func:`check_order`. Callers branch on the code; the
#: message is for the operator reading the gate line.
OK = "ok"
UNREADABLE_HISTORY = "unreadable-history"
NO_TEST_PATHS = "no-test-paths"
NO_COMMITS = "no-commits"
EMPTY_FIRST_COMMIT = "empty-first-commit"
IMPLEMENTATION_FIRST = "implementation-first"
NO_TESTS_ADDED = "no-tests-added"
TESTS_DELETED = "tests-deleted"
NO_IMPLEMENTATION_COMMIT = "no-implementation-commit"
GATES_RED = "gates-red"


@dataclass(frozen=True)
class OrderResult:
    """The ``tdd-order`` verdict: did this branch put its tests first?"""

    ok: bool
    code: str
    message: str
    tests_commit: str | None = None
    implementation_commit: str | None = None
    #: The paths that decided a failure, in commit order: the non-test paths to move out
    #: of the first commit, or the deleted tests to restore.
    offending: tuple[str, ...] = ()
    test_globs: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "tests_commit": self.tests_commit,
            "implementation_commit": self.implementation_commit,
            "offending": list(self.offending),
            "test_globs": list(self.test_globs),
        }


def removed_test(change: Change, globs: Sequence[str]) -> str | None:
    """The test path this change *removed*, or ``None`` — deletions and moves-out.

    Two spellings of the same act, and the gate has to see both. ``git rm
    tests/test_a.py`` is the obvious one. ``git mv tests/test_a.py src/legacy_test_a.py``
    is the same act wearing a rename: the test stops being collected by the suite that
    was red in phase A, and the destination path alone cannot show it because the only
    mention of the test is the rename's *source*.

    A rename **within** the test tree is an ordinary move and stays fine. A **copy**
    (``C``) is not a removal at all — its source still exists after the commit — so it is
    excluded even when the destination lands outside the test tree. That exclusion is
    **defensive only**: :func:`keel.git.commit_log` never passes ``-C``/``--find-copies``,
    so git does not emit a ``C`` status for this gate's input and the branch is unreachable
    from the real argv. It stays because the vocabulary is git's, not keel's.
    """
    if change.deleted:
        return change.path if is_test_path(change.path, globs) else None
    if change.status != RENAMED or change.source is None:
        return None
    if is_test_path(change.source, globs) and not is_test_path(change.path, globs):
        return change.source
    return None


def check_order(
    commits: Sequence[Commit] | None,
    *,
    test_globs: Sequence[str] = (),
    gates_green: bool | None = None,
) -> OrderResult:
    """Was this branch written test-first? A pure function of the commits and the policy.

    The contract, in the order it is checked:

    1. the history is readable at all (``None`` is git failing, never an empty branch);
    2. the project says where its tests live (see :func:`test_globs`) — without that
       there is nothing to check against, and a gate that cannot look must not pass;
    3. the first non-merge commit touches at least one path, and **only** test paths;
    4. that commit *adds or modifies* at least one test — a first commit that only
       deletes tests is the opposite of writing them;
    5. no later commit *removes* a test — deleted outright, or renamed out of the test
       paths, which stops the suite collecting it just as surely. Making the failing
       tests go away is the cheapest way to make phase B "pass", and it is the move this
       gate exists to refuse;
    6. a later commit touches a non-test path — the implementation the tests were
       written for;
    7. the gate run is green, when the caller measured one.

    ``gates_green`` is tri-state. ``None`` means the caller made no gate observation, so
    only the commit order is judged; ``False`` blocks, because a branch whose tests were
    committed first and are still red has not finished phase B.

    **The boundary.** This reads commit order and paths, and nothing else. It never runs
    phase A's tests, so it cannot report that they were red, and it cannot tell whether
    the committed tests assert anything — an empty file under ``tests/`` satisfies rule 4,
    and so does a rename of an existing test within the test paths. Rule 5 has its own
    residual: a test deleted inside a merge from a side branch is never judged, because
    merges are skipped (the comment at the skip says why, and what that costs). Those
    remain a reviewer's questions; the gate makes the *shape* of a test-first run
    machine-checkable, not the quality of the tests.
    """
    globs = tuple(test_globs)
    if commits is None:
        return OrderResult(
            False,
            UNREADABLE_HISTORY,
            "could not read the branch history, so the test-first commit order cannot be "
            "verified; run this where the branch and its base are both present",
            test_globs=globs,
        )
    if not globs:
        return OrderResult(
            False,
            NO_TEST_PATHS,
            "implement_mode: tdd needs to know which paths are tests, and this project "
            "declares none; add policy_pack.test_groups.<group>.test_paths (or .paths) "
            "naming the paths the tests live in",
            test_globs=globs,
        )
    # Merges are skipped throughout, so the deletion scan below never judges what a merge
    # commit brought in. That is a deliberate trade-off, not a proof of safety, and the
    # earlier claim here — "the only paths a merge carries are the base's" — was simply
    # wrong. It holds for a merge *from the base*, which is the common case and the reason
    # to skip: a test legitimately deleted on the base arrives through every branch that
    # integrates it, and judging merges would block this implementer for someone else's
    # work, on a change they did not make.
    #
    # The residual, named because it is real: a merge from a *side* branch carries that
    # branch's paths, not the base's. `git rm tests/test_a.py` on a side branch merged
    # with `--no-ff` removes the test with no non-merge commit recording the deletion, so
    # rule 5 does not see it. A reviewer reading the diff does. Closing it would mean
    # telling a base merge from a side merge, which the commit list alone cannot do —
    # both are a second parent — so the gate declares the gap instead of guessing.
    work = [commit for commit in commits if not commit.merge]
    if not work:
        return OrderResult(
            False,
            NO_COMMITS,
            "the branch carries no non-merge commit, so there is no test-first commit to "
            "verify; commit the failing tests from the issue's acceptance criteria first",
            test_globs=globs,
        )
    first = work[0]
    if not first.changes:
        return OrderResult(
            False,
            EMPTY_FIRST_COMMIT,
            f"the first commit {first.short} touches no file, so it cannot be the "
            "test-first commit; commit the failing tests first",
            tests_commit=first.sha,
            test_globs=globs,
        )
    offending = tuple(
        change.path for change in first.changes if not is_test_path(change.path, globs)
    )
    if offending:
        return OrderResult(
            False,
            IMPLEMENTATION_FIRST,
            f"the first commit {first.short} touches implementation paths, so this "
            f"branch was not written test-first: {', '.join(offending)} "
            f"(test paths: {', '.join(globs)})",
            tests_commit=first.sha,
            offending=offending,
            test_globs=globs,
        )
    if not any(change.present for change in first.changes):
        # Every path in the first commit is a test path *and* every one of them is a
        # deletion: `git rm` over the suite, which reads as "tests first" to anything
        # that only looks at names. Writing the failing tests is the phase; removing
        # them is its inverse.
        removed = tuple(change.path for change in first.changes)
        return OrderResult(
            False,
            NO_TESTS_ADDED,
            f"the first commit {first.short} only deletes tests and adds none, so there "
            f"are no failing tests for phase B to satisfy: {', '.join(removed)}",
            tests_commit=first.sha,
            offending=removed,
            test_globs=globs,
        )
    deleted = tuple(
        removed
        for commit in work[1:]
        for change in commit.changes
        if (removed := removed_test(change, globs)) is not None
    )
    if deleted:
        return OrderResult(
            False,
            TESTS_DELETED,
            f"a commit after the tests commit {first.short} removes tests (deleted, or "
            "renamed out of the test paths), which is the cheapest way to make phase B "
            f"pass without implementing anything: {', '.join(deleted)}",
            tests_commit=first.sha,
            offending=deleted,
            test_globs=globs,
        )
    implementation = next(
        (
            commit
            for commit in work[1:]
            if any(not is_test_path(change.path, globs) for change in commit.changes)
        ),
        None,
    )
    if implementation is None:
        return OrderResult(
            False,
            NO_IMPLEMENTATION_COMMIT,
            f"the tests commit {first.short} is the whole branch: no later commit touches "
            "an implementation path, so phase B never ran",
            tests_commit=first.sha,
            test_globs=globs,
        )
    if gates_green is False:
        return OrderResult(
            False,
            GATES_RED,
            f"the tests commit {first.short} came first, but the gates are red — phase B "
            "runs until the tests it was written against pass",
            tests_commit=first.sha,
            implementation_commit=implementation.sha,
            test_globs=globs,
        )
    return OrderResult(
        True,
        OK,
        f"tests committed first in {first.short}, implementation in {implementation.short}",
        tests_commit=first.sha,
        implementation_commit=implementation.sha,
        test_globs=globs,
    )


def phase_implementers(
    pairs: Iterable[tuple[str, str]] = (),
    *,
    default: str | None = None,
) -> dict[str, str | None]:
    """``--phase-implementer <phase>=<label>`` pairs -> one label per phase.

    ``default`` is the run's ``--implementer``, used for any phase not named explicitly —
    which is the ordinary case, because the profile requires both phases to run on the
    same provider. The point of allowing them to differ is that a run where they *did*
    differ records the fact instead of quietly presenting a single label for both.
    Unknown phase names are dropped here; the CLI rejects them at parse time.
    """
    resolved: dict[str, str | None] = {phase: default for phase in PHASES}
    for phase, label in pairs:
        if phase in resolved:
            resolved[phase] = label
    return resolved


def phase_records(
    result: OrderResult | None,
    *,
    implementers: Mapping[str, str | None] | None = None,
) -> list[dict[str, Any]] | None:
    """The two s4 phases as ledger records, or ``None`` when the run had no TDD phases.

    A phase whose commit could not be identified records ``commit: null`` rather than
    being dropped: the ledger says a TDD run happened and which half of it is missing,
    which is the question a closure comment is read for.

    Each record also carries the ``implementer`` that ran that phase, so *"the same
    provider wrote the tests and the implementation"* — the rule the profile rests on and
    which nothing else in the record could show — is auditable after the fact rather than
    assumed. It is emit-only, like the gate seat in ``knobs.team``: keel records what the
    orchestrator reports, and two different labels are a finding for a reader, not
    something core can independently prove.
    """
    if result is None:
        return None
    seats = implementers or {}
    return [
        {
            "phase": PHASE_TESTS,
            "commit": result.tests_commit,
            "implementer": seats.get(PHASE_TESTS),
        },
        {
            "phase": PHASE_IMPLEMENTATION,
            "commit": result.implementation_commit,
            "implementer": seats.get(PHASE_IMPLEMENTATION),
        },
    ]
