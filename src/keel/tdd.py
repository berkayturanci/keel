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
* :func:`check_order` — the gate: the first non-merge commit on the branch touches only
  test paths, an implementation commit follows it, and the gate run is green.

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


@dataclass(frozen=True)
class Commit:
    """One commit on the branch: what it is, and which paths it touched."""

    sha: str
    subject: str = ""
    files: tuple[str, ...] = ()
    merge: bool = False

    @property
    def short(self) -> str:
        """The 7-character sha operators read in a gate message."""
        return self.sha[:7]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "subject": self.subject,
            "files": list(self.files),
            "merge": self.merge,
        }


def parse_commits(text: str | None) -> tuple[Commit, ...] | None:
    """Parse :data:`LOG_FORMAT` + ``--name-only`` output, oldest commit first.

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
                files=tuple(line.strip() for line in body.splitlines() if line.strip()),
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
    #: Non-test paths in the first commit, in commit order — what to move out of it.
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
    4. a later commit touches a non-test path — the implementation the tests were
       written for;
    5. the gate run is green, when the caller measured one.

    ``gates_green`` is tri-state. ``None`` means the caller made no gate observation, so
    only the commit order is judged; ``False`` blocks, because a branch whose tests were
    committed first and are still red has not finished phase B.
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
    if not first.files:
        return OrderResult(
            False,
            EMPTY_FIRST_COMMIT,
            f"the first commit {first.short} touches no file, so it cannot be the "
            "test-first commit; commit the failing tests first",
            tests_commit=first.sha,
            test_globs=globs,
        )
    offending = tuple(path for path in first.files if not is_test_path(path, globs))
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
    implementation = next(
        (
            commit
            for commit in work[1:]
            if any(not is_test_path(path, globs) for path in commit.files)
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


def phase_records(result: OrderResult | None) -> list[dict[str, Any]] | None:
    """The two s4 phases as ledger records, or ``None`` when the run had no TDD phases.

    A phase whose commit could not be identified records ``commit: null`` rather than
    being dropped: the ledger says a TDD run happened and which half of it is missing,
    which is the question a closure comment is read for.
    """
    if result is None:
        return None
    return [
        {"phase": PHASE_TESTS, "commit": result.tests_commit},
        {"phase": PHASE_IMPLEMENTATION, "commit": result.implementation_commit},
    ]
