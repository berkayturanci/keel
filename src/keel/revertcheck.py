"""The opt-in ``revert-check`` gate (#1289): does any test notice when a change is undone?

Coverage proves that a line *ran*, not that an assertion *depends* on it. With
``fail_under = 100`` enforced, "maintained 100 % coverage" is true of every merged pull
request before anyone writes it, and an audit of 14 closed fixes found three whose tests
passed with the fix removed. This gate asks the question coverage cannot: for each
production change on the branch, revert **that change alone** in a scratch worktree, run
the project's tests, and require a test to **fail as an assertion**.

It is mutation testing scoped to one mutation per change — the change itself.

This module is the pure half, and deterministic:

* :func:`resolve` — ``knobs.revert_check`` (+ ``build_gate_cmd``, ``gate_timeout_s``) ->
  :class:`Settings`;
* :func:`test_paths` — where the tests live, from ``policy_pack.test_groups.*.test_paths``;
* :func:`parse_diff` / :func:`plan_changes` — the branch's unified diff -> the production
  changes to revert, each with the patch that undoes it;
* :func:`read_output` / :func:`classify` — a test run's output -> *caught* (a test failed as
  an assertion), *errored*, *unnoticed*, *timed out* or *unreadable*;
* :func:`execute` — the bounded loop (``max_changes``, ``budget_s``), driven through an
  injected ``run`` callable and ``clock``, so the loop is tested offline;
* :func:`judge` — the gate's verdict and findings.

The git and subprocess work — the scratch worktree, ``git apply -R``, the test command —
lives in :mod:`keel.cli` through :mod:`keel.git` and :mod:`keel.runner`; nothing here
touches the file system.

**What it does not do.** The unit is a git hunk (or a file), not a *behaviour*: #871's
guarded and unguarded arms shared one hunk, so a per-hunk check passes while half that fix
is unpinned. And a passing check is necessary, not sufficient: #873's test fails without
its fix and still could not see the regression the fix shipped (#1268), because its fixture
made the fix and the bug agree. Both remain a reviewer's questions.
"""

from __future__ import annotations

import ast
import fnmatch
import posixpath
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .findings import Finding

#: The gate id, as ``gates:`` lists it.
GATE_ID = "revert-check"

#: ``knobs.revert_check.unit``: revert each hunk alone (the default), or each file.
UNIT_HUNK = "hunk"
UNIT_FILE = "file"
UNITS = (UNIT_HUNK, UNIT_FILE)

#: At most this many changes are reverted per run unless ``max_changes`` says otherwise.
DEFAULT_MAX_CHANGES = 10
#: Wall-clock seconds the whole check — the baseline run and every revert — may spend.
DEFAULT_BUDGET_S = 1800

#: What counts as production when ``knobs.revert_check.paths`` is not set: a changed file
#: with a source-code suffix that is not under the project's test paths. Docs, config,
#: lockfiles and data are left out — reverting a README cannot make a test fail, and
#: reporting it as unnoticed would be noise, not evidence.
SOURCE_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".scala",
    ".rb",
    ".php",
    ".cs",
    ".fs",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".hpp",
    ".m",
    ".mm",
    ".swift",
    ".dart",
    ".ex",
    ".exs",
    ".erl",
    ".clj",
    ".lua",
    ".pl",
    ".pm",
    ".r",
    ".sh",
    ".bash",
)

# Per-change results.
CAUGHT = "caught"  # a test failed as an assertion
ERRORED = "errored"  # tests failed, none as an assertion (an exception, an import error)
UNNOTICED = "unnoticed"  # the test command passed with the change reverted
TIMED_OUT = "timed-out"
UNREADABLE = "unreadable"  # the command failed and its output says no test failed
NOT_APPLIED = "not-applied"  # the reverse patch did not apply on HEAD
INERT = "inert"  # only comments, docstrings or formatting change: nothing to run

_BLOCK = "major"


@dataclass(frozen=True)
class Settings:
    """The resolved ``knobs.revert_check`` for one run."""

    #: The test command each revert runs; ``None`` when neither knob sets one.
    cmd: str | None
    #: The knob that supplied :attr:`cmd` — named in the finding when it is missing.
    cmd_source: str
    #: Globs that select production files; empty means :data:`SOURCE_SUFFIXES`.
    paths: tuple[str, ...]
    unit: str
    max_changes: int
    budget_s: int
    #: The wall-clock limit of one test run (``knobs.gate_timeout_s``).
    run_timeout_s: int


def resolve(
    knob: Mapping[str, Any] | None, *, build_cmd: str | None, gate_timeout_s: int
) -> Settings:
    """``knobs.revert_check`` -> :class:`Settings`, defaults filled in.

    The test command is ``knobs.revert_check.cmd`` when set, else ``knobs.build_gate_cmd``:
    a project whose suite is slow can point the check at the tests that matter without
    changing what its ``build`` gate runs. The schema owns the value shapes; anything it
    would refuse reads as the default here, because this resolver runs on every gate run.
    """
    raw = knob if isinstance(knob, Mapping) else {}
    own = raw.get("cmd")
    if isinstance(own, str) and own.strip():
        cmd: str | None = own
        source = "knobs.revert_check.cmd"
    else:
        cmd = build_cmd if isinstance(build_cmd, str) and build_cmd.strip() else None
        source = "knobs.build_gate_cmd"
    paths = raw.get("paths")
    unit = raw.get("unit")
    return Settings(
        cmd=cmd,
        cmd_source=source,
        paths=tuple(_strings(paths)),
        unit=unit if unit in UNITS else UNIT_HUNK,
        max_changes=_positive(raw.get("max_changes"), DEFAULT_MAX_CHANGES),
        budget_s=_positive(raw.get("budget_s"), DEFAULT_BUDGET_S),
        run_timeout_s=max(1, int(gate_timeout_s)),
    )


def _positive(value: Any, default: int) -> int:
    """A positive integer knob, or ``default`` (``bool`` is not a number here)."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return default


def _strings(raw: Any) -> list[str]:
    """Non-blank string entries of a list (anything else contributes nothing)."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [entry.strip() for entry in raw if isinstance(entry, str) and entry.strip()]


def test_paths(policy_pack: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Where the project's tests live: every ``policy_pack.test_groups.*.test_paths`` glob.

    Only the **declared** ``test_paths`` count. A group's ``paths`` are selectors and
    routinely include the implementation surface (keel's own ``unit`` group selects
    ``src/**``); read as test paths they would classify every production file as a test,
    and the gate would skip every change it exists to check.
    """
    pack = policy_pack if isinstance(policy_pack, Mapping) else {}
    groups = pack.get("test_groups")
    if not isinstance(groups, Mapping):
        return ()
    globs: list[str] = []
    for _name, group in sorted(groups.items(), key=lambda item: str(item[0])):
        if isinstance(group, Mapping):
            globs.extend(_strings(group.get("test_paths")))
    return tuple(dict.fromkeys(globs))


def is_production(path: str, *, tests: Sequence[str], paths: Sequence[str]) -> bool:
    """Is ``path`` production code this gate reverts?

    Never a test path. Then ``paths`` (``fnmatch`` globs, the matcher ``tier3_globs`` and
    ``test_paths`` use) when the project set it, else a source-code suffix.
    """
    if any(fnmatch.fnmatch(path, glob) for glob in tests):
        return False
    if paths:
        return any(fnmatch.fnmatch(path, glob) for glob in paths)
    return path.lower().endswith(SOURCE_SUFFIXES)


# --- the diff ------------------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class Hunk:
    """One ``@@`` hunk: its header line and body, verbatim."""

    header: str
    lines: tuple[str, ...]
    added: int
    removed: int
    #: Context lines. :func:`keel.git.revert_diff` asks for none, so any here means the
    #: diff is not the shape the plan needs (:func:`context_problem`).
    context: int = 0

    @property
    def pure_addition(self) -> bool:
        """Only adds lines: reverting it removes code rather than restoring old code."""
        return self.removed == 0


@dataclass(frozen=True)
class FileDiff:
    """One file's part of a unified diff."""

    path: str
    #: The lines from ``diff --git`` up to the first hunk — what ``git apply`` needs to
    #: know which file, and whether it is new or deleted.
    header: tuple[str, ...]
    hunks: tuple[Hunk, ...]
    status: str  # added | deleted | modified


def _path_from(line: str, prefix: str) -> str | None:
    """The path on a ``--- a/x`` / ``+++ b/x`` line; ``None`` for ``/dev/null``.

    git ends the line with a tab when the name contains a space, and wraps a name in
    double quotes when it holds a control character, a quote or a backslash; both are
    stripped so the name matches the project's globs.
    """
    name = line[4:].rstrip("\t")
    if name == "/dev/null":
        return None
    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return name[len(prefix) :] if name.startswith(prefix) else name


def parse_diff(text: str) -> tuple[FileDiff, ...]:
    """Split ``git diff --no-renames --src-prefix=a/ --dst-prefix=b/`` output into files.

    Hunk bodies are consumed by the counts in their ``@@`` header, not by their first
    character, so a blank context line (``diff.suppressBlankEmpty``) cannot end a hunk
    early. A file with no hunk — binary, or a mode change — is kept with ``hunks=()``, so
    the plan can say it was not checked rather than drop it.
    """
    files: list[FileDiff] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        if not lines[i].startswith("diff --git "):
            i += 1
            continue
        header = [lines[i]]
        # `diff --git a/x b/x`: the fallback name for a file with no ---/+++ lines.
        fallback = lines[i].rsplit(" b/", 1)[-1]
        old = new = None
        i += 1
        while i < len(lines) and not lines[i].startswith(("@@", "diff --git ")):
            line = lines[i]
            if line.startswith("--- "):
                old = _path_from(line, "a/")
            elif line.startswith("+++ "):
                new = _path_from(line, "b/")
            header.append(line)
            i += 1
        hunks: list[Hunk] = []
        while i < len(lines) and lines[i].startswith("@@"):
            hunk, i = _read_hunk(lines, i)
            hunks.append(hunk)
        text_header = "\n".join(header)
        if "\nnew file mode " in text_header:
            status = "added"
        elif "\ndeleted file mode " in text_header:
            status = "deleted"
        else:
            status = "modified"
        files.append(FileDiff(new or old or fallback, tuple(header), tuple(hunks), status))
    return tuple(files)


def _read_hunk(lines: list[str], i: int) -> tuple[Hunk, int]:
    """Read the hunk whose header is ``lines[i]``; return it and the index after it."""
    header = lines[i]
    match = _HUNK_RE.match(header)
    old_left = int(match.group(2) or "1") if match else 0
    new_left = int(match.group(4) or "1") if match else 0
    body: list[str] = []
    added = removed = context = 0
    i += 1
    while i < len(lines) and (old_left > 0 or new_left > 0):
        line = lines[i]
        tag = line[:1]
        if tag == "+":
            added += 1
            new_left -= 1
        elif tag == "-":
            removed += 1
            old_left -= 1
        elif tag == "\\":
            pass  # "\ No newline at end of file" belongs to the line before it
        else:  # context, including an empty line git wrote for a blank one
            old_left -= 1
            new_left -= 1
            context += 1
        body.append(line)
        i += 1
    # A trailing "\ No newline at end of file" marker after the last counted line.
    while i < len(lines) and lines[i].startswith("\\"):
        body.append(lines[i])
        i += 1
    return Hunk(header, tuple(body), added, removed, context), i


def context_problem(files: Sequence[FileDiff]) -> str | None:
    """Why the diff cannot be split into independent changes, or ``None`` when it can.

    Every hunk must carry **no context line**. :func:`keel.git.revert_diff` pins
    ``--unified=0`` and ``--inter-hunk-context=0``, but ``GIT_DIFF_OPTS`` or a setting keel
    does not know could still widen a hunk — and a hunk that carries context is either
    two edits merged into one change, where one caught edit passes the other, or a patch
    whose context ``--unidiff-zero`` would misplace. Checked here, on what git printed.
    """
    widened = [f.path for f in files if any(h.context for h in f.hunks)]
    if not widened:
        return None
    return (
        f"the diff carries context lines (in {', '.join(widened[:3])}), so its hunks may merge "
        "independent edits; check GIT_DIFF_OPTS and the diff settings in your git config"
    )


@dataclass(frozen=True)
class Change:
    """One production change to revert: a label for the report and its patch."""

    label: str
    path: str
    #: A unified diff that ``git apply -R`` undoes on HEAD.
    patch: str
    #: Every hunk in it only adds lines — see :func:`judge` for why that matters.
    pure_addition: bool


@dataclass(frozen=True)
class Plan:
    """What the gate will revert, and what it had to leave out."""

    changes: tuple[Change, ...]
    #: Production files with no textual hunk (binary, mode-only): not checked.
    unrevertable: tuple[str, ...]
    #: Every path the diff touched, for the finding that says none was production.
    touched: tuple[str, ...]


def plan_changes(
    files: Sequence[FileDiff], *, tests: Sequence[str], paths: Sequence[str], unit: str
) -> Plan:
    """The production changes to revert, one per hunk (or per file), ordered by path.

    Ordered here, not by git: ``diff.orderFile`` reorders git's output, and the order
    decides which changes ``max_changes`` reaches.
    """
    changes: list[Change] = []
    unrevertable: list[str] = []
    for f in sorted(files, key=lambda f: f.path):
        if not is_production(f.path, tests=tests, paths=paths):
            continue
        if not f.hunks:
            unrevertable.append(f.path)
            continue
        if unit == UNIT_FILE or f.status != "modified":
            # A new or deleted file is one change whatever the unit: half a file's
            # removal is not a state the branch ever had.
            suffix = {"added": " (new file)", "deleted": " (deleted file)"}.get(f.status, "")
            changes.append(
                Change(
                    f"{f.path}{suffix}",
                    f.path,
                    _patch(f.header, f.hunks),
                    all(h.pure_addition for h in f.hunks),
                )
            )
            continue
        for hunk in f.hunks:
            label = f"{f.path} {hunk.header.split(' @@', 1)[0]} @@"
            changes.append(Change(label, f.path, _patch(f.header, (hunk,)), hunk.pure_addition))
    return Plan(tuple(changes), tuple(unrevertable), tuple(f.path for f in files))


def _patch(header: Sequence[str], hunks: Sequence[Hunk]) -> str:
    lines = list(header)
    for hunk in hunks:
        lines.append(hunk.header)
        lines.extend(hunk.lines)
    return "\n".join(lines) + "\n"


# --- changes with no behaviour ------------------------------------------------------

#: Line-comment markers by suffix. Used **only to word a finding** — a change whose every
#: line looks like a comment but that keel cannot prove inert is still tested, and its
#: "no test notices" finding says why. ``#`` is listed only where it starts a comment; in
#: C it starts ``#include``/``#define``, which are code.
_COMMENT_MARKERS: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(
        (".py", ".pyi", ".sh", ".bash", ".rb", ".pl", ".pm", ".r", ".ex", ".exs"), ("#",)
    ),
    **dict.fromkeys(
        (
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
            ".ts",
            ".tsx",
            ".go",
            ".rs",
            ".java",
            ".kt",
            ".kts",
            ".scala",
            ".php",
            ".cs",
            ".fs",
            ".c",
            ".h",
            ".cc",
            ".cpp",
            ".cxx",
            ".hpp",
            ".m",
            ".mm",
            ".swift",
            ".dart",
        ),
        ("//", "/*", "*"),
    ),
    ".lua": ("--",),
    ".clj": (";",),
    ".erl": ("%",),
}

#: The C-family languages whose whole files :func:`_strip_c_comments` reads safely, so a
#: comment-only change in them may skip its run: their only literals are ``"…"`` and
#: ``'…'`` with backslash escapes, plus Go's backtick raw string, which has none.
#:
#: Left out, because a literal the stripper cannot read could hide code inside what it
#: takes for a comment — and the rule is that inertness is *proven*, never guessed:
#: C++ (``.cc``/``.cpp``/``.cxx``/``.hpp``, and ``.h``/``.mm``, which may be C++) for
#: ``R"(…)"`` raw strings; Rust for ``r#"…"#`` and lifetimes (``'a``); C# for ``@"…"`` and
#: ``"""…"""``; Java, Kotlin, Scala, Swift and Dart for ``"""…"""`` text blocks (and Dart's
#: ``r'…'``); PHP for heredoc/nowdoc and ``#`` comments; F# for ``(* … *)`` and
#: ``"""…"""``; and JavaScript/TypeScript for regex literals (``/\/*/``) and ``${…}``
#: nesting inside template literals. Every other language — the ``#``-comment scripting
#: languages, Lua, Clojure, Erlang — has no reader at all here.
_C_INERT_SUFFIXES: dict[str, str] = {".c": "c", ".m": "c", ".go": "go"}

#: C text the reader will not vouch for, so a change in such a file is never inert: a
#: ``??/`` trigraph (a backslash before C23, so it can splice a comment's next line into
#: it), a backslash followed only by blanks at the end of a line (GCC and Clang splice
#: that too, with a warning), and a ``//`` or ``/*`` inside an ``#include``/``#import``
#: header name, which is not a comment there.
_C_UNSAFE = re.compile(
    r"\?\?/|\\[ \t]+\r?$|^[ \t]*#[ \t]*(?:include|import)[ \t]*<[^>\n]*(?://|/\*)",
    re.MULTILINE,
)
#: The languages :func:`behaviour_free` can prove inert, as a finding names them.
INERT_LANGUAGES = "Python (.py, .pyi), C (.c), Objective-C (.m) and Go (.go)"


class _DropStrings(ast.NodeTransformer):
    """Remove every bare string statement — docstrings and attribute docstrings alike."""

    def visit_Expr(self, node: ast.Expr) -> ast.AST | None:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return None
        return node


class _DropImports(_DropStrings):
    """…and every ``import`` / ``from … import`` statement too."""

    def visit_Import(self, node: ast.Import) -> None:
        return None

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        return None


def _python_shape(text: str, *, imports: bool = True) -> str | None:
    """The file's syntax tree without positions or bare strings (and, with ``imports``
    false, without import statements); ``None`` if unparseable."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    return ast.dump((_DropStrings() if imports else _DropImports()).visit(tree))


def _strip_c_comments(text: str, *, raw_backticks: bool = False) -> str:
    """``text`` without ``//`` and ``/* … */`` comments, whitespace outside strings collapsed.

    String and character literals (``"…"`` and ``'…'``, with backslash escapes) are kept
    verbatim, so a comment marker inside one is text, not a comment. With
    ``raw_backticks`` (Go) a backtick string is kept verbatim too, and a backslash inside it
    escapes nothing. Only the languages in :data:`_C_INERT_SUFFIXES` are read this way.
    """
    out: list[str] = []
    i, n = 0, len(text)
    pending_space = False
    quotes = "\"'`" if raw_backticks else "\"'"
    while i < n:
        ch = text[i]
        if ch in quotes:
            end = i + 1
            while end < n and text[end] != ch:
                end += 2 if text[end] == "\\" and ch != "`" else 1
            if pending_space and out:
                out.append(" ")
            pending_space = False
            out.append(text[i : end + 1])
            i = end + 1
        elif text.startswith("//", i):
            newline = text.find("\n", i)
            i = n if newline == -1 else newline
            pending_space = True
        elif text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = n if close == -1 else close + 2
            pending_space = True
        elif ch.isspace():
            pending_space = True
            i += 1
        else:
            if pending_space and out:
                out.append(" ")
            pending_space = False
            out.append(ch)
            i += 1
    return "".join(out)


def _changed_lines(patch: str) -> list[str]:
    """The ``+``/``-`` lines of a change's hunks, without their marker."""
    _header, _sep, body = patch.partition("\n@@")
    return [line[1:] for line in body.split("\n") if line[:1] in ("+", "-")]


def behaviour_free(change: Change, before: str | None, after: str | None) -> bool:
    """Does reverting ``change`` leave the program's behaviour as it was?

    ``before`` is the file on ``HEAD`` and ``after`` the file with the change undone (``None``
    when the file does not exist on that side). No test can notice such a change as an
    assertion, so it is not run — and not reported as unnoticed:

    * **Python** — the two files parse to the same syntax tree once positions and bare
      string statements (docstrings) are set aside: a change to comments, docstrings or
      formatting. A file that does not parse on either side is never behaviour-free.
    * **C, Objective-C and Go** (:data:`_C_INERT_SUFFIXES`) — every changed line looks like
      a comment, *and* the two whole files are equal once :func:`_strip_c_comments` has
      removed their comments with every string literal kept.

    Inertness has to be proven by a string-aware reading of both whole files; a changed
    line that merely *looks* like a comment proves nothing (it may sit inside a multi-line
    string). So every other language — and a file added or deleted by the change — is
    never behaviour-free: the tests run.
    """
    if before is None or after is None:
        return False
    suffix = posixpath.splitext(change.path)[1].lower()
    if suffix in (".py", ".pyi"):
        shape = _python_shape(before)
        return shape is not None and shape == _python_shape(after)
    if suffix not in _C_INERT_SUFFIXES or not looks_comment_only(change):
        return False
    # A leading ``*`` is a block-comment continuation only inside ``/* … */`` — ``*p = 1;``
    # is a pointer write. So the files must also match once their comments are stripped
    # (strings kept verbatim), or the change is not inert.
    mode = _C_INERT_SUFFIXES[suffix]
    head, undone = _c_source(before, mode), _c_source(after, mode)
    return head is not None and head == undone


def _c_source(text: str, mode: str) -> str | None:
    """``text`` as the compiler's lexer sees it, comments removed; ``None`` if unreadable.

    For C and Objective-C, backslash-newline splicing (translation phase 2) comes first:
    a ``// note\\`` comment swallows the next line, so ``return 1;`` under it is comment
    and not code. Go has no splicing and no trigraphs; its backtick strings are raw.
    """
    if mode == "go":
        return _strip_c_comments(text, raw_backticks=True)
    if _C_UNSAFE.search(text):
        return None
    return _strip_c_comments(text.replace("\\\r\n", "").replace("\\\n", ""))


def looks_comment_only(change: Change) -> bool:
    """Is every changed line blank or led by its language's comment marker?

    A *look*, not a proof: :func:`behaviour_free` requires more, and this alone only words
    the finding for a change that looks comment-only yet was tested and not noticed.
    """
    markers = _COMMENT_MARKERS.get(posixpath.splitext(change.path)[1].lower())
    if markers is None:
        return False
    return all(
        not line.strip() or line.strip().startswith(markers)
        for line in _changed_lines(change.patch)
    )


def imports_only(change: Change, before: str | None, after: str | None) -> bool:
    """Does reverting ``change`` alter nothing in a Python file but its imports?

    Reverting an import that the branch *added a name to* removes the name, and every test
    that reaches it can then only raise ``NameError`` — as with a hunk that only adds lines.
    :func:`judge` treats both the same way. ``False`` for any file that is not Python,
    added, deleted or unparseable.
    """
    if before is None or after is None:
        return False
    if posixpath.splitext(change.path)[1].lower() not in (".py", ".pyi"):
        return False
    shape = _python_shape(before, imports=False)
    return shape is not None and shape == _python_shape(after, imports=False)


# --- reading a test run --------------------------------------------------------------

_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
_UNITTEST_FAILED = re.compile(r"^FAILED \(([^)]*)\)\s*$", re.MULTILINE)
_PYTEST_SUMMARY = re.compile(
    r"^=*\s*((?:\d+ [a-z]+(?:, )?)+) in [\d.]+s\b.*$|^=*\s*no tests ran in [\d.]+s\b.*$",
    re.MULTILINE,
)
_PYTEST_COUNT = re.compile(r"(\d+) ([a-z]+)")
#: A pytest short-summary line. The node id never starts with ``(``, which is what keeps
#: unittest's own ``FAILED (failures=1)`` from being read as a failed pytest test.
#:
#: The node id may contain spaces — a parametrized id (``test_v[hello world]``) or a file
#: name — so it is read as: a bracketed id up to its ``]``, else one word, else the
#: shortest text before `` - ``; the reason is what follows `` - `` and may be absent.
_PYTEST_LINE = re.compile(
    r"^(FAILED|ERROR) (?!\()(?:\S*?\[.*?\]|\S+|.+?)(?: - (.*))?$", re.MULTILINE
)
#: A pytest short-summary reason that is an assertion: a rewritten ``assert``, an
#: ``AssertionError`` (unittest-style assertions under pytest), or ``pytest.fail``.
_ASSERTION_REASON = re.compile(r"^(?:assert\b|AssertionError\b|Failed:)")


@dataclass(frozen=True)
class Tally:
    """What a test run's output says happened."""

    #: The output carries a unittest or pytest summary at all.
    recognized: bool = False
    #: Tests that ran.
    ran: int = 0
    #: Tests that failed as an assertion.
    assertions: int = 0
    #: Tests that failed any other way: an exception, an import or collection error, a
    #: strict unexpected success.
    errors: int = 0
    #: pytest counted a failure its short summary does not describe (``-rN``, truncation).
    unclassified: int = 0


def read_output(output: str) -> Tally:
    """Read unittest's and pytest's summaries out of a test command's combined output.

    **unittest** separates the two cleanly: ``failures`` are the test's
    ``failureException`` (an ``AssertionError``), ``errors`` are anything else, a test
    module that failed to import included.

    **pytest** does not: its ``failed`` count includes a ``NameError`` raised in the test
    body. The short test summary (``-rfE``, pytest's default since 6.0) names each
    failure's exception, and only an assertion reason counts; a failure the summary does
    not describe is *unclassified*, never an assertion.

    Every summary in the output is summed, so a command running two suites is read whole.
    """
    ran = assertions = errors = unclassified = 0
    recognized = False
    for match in _UNITTEST_RAN.finditer(output):
        recognized = True
        ran += int(match.group(1))
    for match in _UNITTEST_FAILED.finditer(output):
        for part in match.group(1).split(","):
            key, _, value = part.strip().partition("=")
            count = int(value) if value.isdigit() else 0
            if key == "failures":
                assertions += count
            elif key in ("errors", "unexpected successes"):
                errors += count
    pytest_failed = 0
    for match in _PYTEST_SUMMARY.finditer(output):
        recognized = True
        for count, word in _PYTEST_COUNT.findall(match.group(1) or ""):
            if word in ("passed", "failed", "xfailed", "xpassed"):
                ran += int(count)
            if word == "failed":
                pytest_failed += int(count)
            elif word in ("error", "errors"):
                errors += int(count)
    described = 0
    for match in _PYTEST_LINE.finditer(output):
        if match.group(1) != "FAILED":
            continue  # ERROR lines are already in the summary's error count
        described += 1
        if _ASSERTION_REASON.match(match.group(2) or ""):
            assertions += 1
        else:
            errors += 1
    unclassified = max(0, pytest_failed - described)
    return Tally(recognized, ran, assertions, errors, unclassified)


def classify(*, exit_ok: bool, timed_out: bool, output: str) -> tuple[str, str]:
    """One reverted run -> ``(result, why)``. Only :data:`CAUGHT` is a pass.

    The exit code decides pass/fail; the output decides *how* it failed. A test that
    errored is not a test that asserted — reverting half a fix can raise ``NameError``
    in every test that imports it, and that proves the import, not the behaviour.
    """
    if timed_out:
        return TIMED_OUT, "the test command timed out with the change reverted"
    if exit_ok:
        return UNNOTICED, "the test command passed with the change reverted"
    tally = read_output(output)
    if tally.assertions:
        return CAUGHT, f"{tally.assertions} test(s) failed as an assertion"
    if not tally.recognized:
        return UNREADABLE, (
            "the test command failed, and its output carries no unittest or pytest summary "
            "to say whether a test failed"
        )
    if tally.errors:
        return ERRORED, f"{tally.errors} test(s) errored and none failed as an assertion"
    if tally.unclassified:
        return UNREADABLE, (
            f"pytest reported {tally.unclassified} failure(s) its short test summary does not "
            "describe, so an assertion cannot be told from an error (keep pytest's -rf)"
        )
    return UNREADABLE, "the test command failed, but no test did"


def baseline_problem(*, exit_ok: bool, timed_out: bool, output: str) -> str | None:
    """Why the unreverted run cannot anchor the check, or ``None`` when it can.

    A failure after a revert means something only if the same command passes — and is
    readable — on HEAD itself, in the same kind of clean checkout.
    """
    if timed_out:
        return "the test command timed out on a clean checkout of HEAD"
    if not exit_ok:
        return (
            "the test command fails on a clean checkout of HEAD, so a failure with a change "
            "reverted would prove nothing"
        )
    tally = read_output(output)
    if not tally.recognized:
        return (
            "the test command's output carries no unittest or pytest summary; revert-check "
            "reads those two to tell an assertion from an error"
        )
    if tally.ran == 0:
        return "the test command ran no tests on a clean checkout of HEAD"
    return None


# --- the bounded loop ----------------------------------------------------------------


@dataclass(frozen=True)
class RunResult:
    """One execution of the test command, as the I/O layer observed it."""

    exit_ok: bool = False
    timed_out: bool = False
    output: str = ""


@dataclass(frozen=True)
class Reverted:
    """One change undone in the scratch tree, as the I/O layer observed it."""

    #: The tree was reset and the reverse patch applied.
    applied: bool
    #: The changed file on ``HEAD`` and with the change undone; ``None`` where it is absent.
    before: str | None = None
    after: str | None = None


@dataclass(frozen=True)
class ChangeResult:
    change: Change
    result: str
    why: str
    #: The change only adds code or names to an import (:attr:`Change.pure_addition`, or
    #: :func:`imports_only`), so undoing it can only make a test error, never assert.
    adds_only: bool = False


@dataclass(frozen=True)
class Report:
    """What :func:`execute` observed."""

    #: Set when the baseline run cannot anchor the check; nothing was reverted then.
    baseline: str | None
    results: tuple[ChangeResult, ...] = ()
    #: Changes left unchecked by ``max_changes`` or ``budget_s``, with the reason.
    not_checked: tuple[tuple[Change, str], ...] = ()


#: ``revert(change)``: reset the scratch tree to ``HEAD`` and undo ``change`` in it.
Reverter = Callable[[Change], Reverted]
#: ``test(timeout_s)``: run the test command in the scratch tree as it stands.
Tester = Callable[[int], RunResult]


def execute(
    changes: Sequence[Change],
    settings: Settings,
    *,
    revert: Reverter,
    test: Tester,
    clock: Callable[[], float],
) -> Report:
    """Run the baseline, then revert each change alone, within the cost bounds.

    ``max_changes`` caps how many changes are reverted; ``budget_s`` caps the wall clock
    of the baseline and every revert together, and each run's limit is the smaller of
    ``knobs.gate_timeout_s`` and what is left of it. A change either bound leaves out is
    reported **not checked** — never as caught. A change :func:`behaviour_free` says has no
    behaviour is reported **inert** without a run.
    """
    start = clock()

    def left() -> float:
        return settings.budget_s - (clock() - start)

    def limit(remaining: float) -> int:
        return max(1, min(settings.run_timeout_s, int(remaining)))

    base = test(limit(left()))
    problem = baseline_problem(exit_ok=base.exit_ok, timed_out=base.timed_out, output=base.output)
    if problem is not None:
        return Report(problem)
    results: list[ChangeResult] = []
    skipped: list[tuple[Change, str]] = []
    for index, change in enumerate(changes):
        if index >= settings.max_changes:
            skipped.append(
                (change, f"over knobs.revert_check.max_changes ({settings.max_changes})")
            )
            continue
        remaining = left()
        if remaining < 1:
            skipped.append(
                (change, f"the knobs.revert_check.budget_s budget ({settings.budget_s}s) ran out")
            )
            continue
        undone = revert(change)
        if not undone.applied:
            results.append(
                ChangeResult(change, NOT_APPLIED, "git apply -R could not undo it on HEAD")
            )
            continue
        if behaviour_free(change, undone.before, undone.after):
            results.append(
                ChangeResult(change, INERT, "only comments, docstrings or formatting change")
            )
            continue
        outcome = test(limit(remaining))
        result, why = classify(
            exit_ok=outcome.exit_ok, timed_out=outcome.timed_out, output=outcome.output
        )
        adds_only = change.pure_addition or imports_only(change, undone.before, undone.after)
        results.append(ChangeResult(change, result, why, adds_only))
    return Report(None, tuple(results), tuple(skipped))


# --- the verdict ---------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """The gate's outcome, in :class:`keel.gates.GateOutcome` terms."""

    ok: bool
    findings: tuple[Finding, ...]
    #: The gate cannot judge — only the project's config or environment can fix it.
    unconfigured: bool = False
    #: Nothing to check: the diff has no production change. Reported ``SKIPPED``.
    skipped: bool = False


def cannot_judge(why: str) -> Verdict:
    """A verdict for a check that could not run: failed, never a pass (#1364)."""
    return Verdict(False, (Finding(_BLOCK, f"cannot judge: {why}", GATE_ID),), unconfigured=True)


def precheck(settings: Settings, *, tests: Sequence[str], gates_green: bool) -> Verdict | None:
    """The verdict when the check must not start, else ``None``.

    Order matters: a missing command or test layout is a config problem no run can fix,
    so it is reported before the red-gates case an implementer can.
    """
    if settings.cmd is None:
        return cannot_judge(
            "no test command: set knobs.revert_check.cmd or knobs.build_gate_cmd "
            "in .keel/project.yaml"
        )
    if not tests:
        return cannot_judge(
            "revert-check cannot tell tests from production: declare where the tests live "
            "in policy_pack.test_groups.<group>.test_paths"
        )
    if not gates_green:
        return Verdict(
            False,
            (
                Finding(
                    _BLOCK,
                    "not run: the guard and test gates are red, and a revert check against a "
                    "red suite cannot tell which failures the revert caused",
                    GATE_ID,
                ),
            ),
        )
    return None


def judge(plan: Plan, report: Report | None) -> Verdict:
    """The gate's verdict: every production change must make a test fail as an assertion.

    * **unnoticed**, **timed out**, **unreadable**, **not applied** and **not checked**
      each block, naming the change: a change the gate did not see fail is not a pass.
    * **errored** — and **unreadable**, where the suite crashed before its summary — block,
      except for a change that only *adds* lines or only changes imports. Reverting an
      addition removes a name or a branch, and a test that calls it can then only error:
      no assertion can fail against code that is not there. The error does prove a test
      depends on the addition, so it is reported as a ``nit`` rather than a pass or a
      block. Reverting a *modification* restores code that ran before; a test that only
      errors against it (the ``NameError`` of a half-reverted fix) proves nothing.
    * A production file with no textual hunk (binary, mode-only) is a ``minor``: nothing
      to revert, so nothing checked, and said so.
    * An **inert** change (:func:`behaviour_free`) is counted in the closing ``nit``, not
      reported alone: a comment has no behaviour for a test to notice.
    * A diff with no production change is ``SKIPPED`` — judged, with nothing to check.

    ``report`` is ``None`` only when there was nothing to execute.
    """
    findings: list[Finding] = []
    for path in plan.unrevertable:
        findings.append(
            Finding(
                "minor",
                f"{path}: not checked — no textual hunk to revert (a binary or mode-only change)",
                GATE_ID,
            )
        )
    if report is None:
        if findings:
            return Verdict(True, tuple(findings))
        touched = len(plan.touched)
        return Verdict(
            True,
            (
                Finding(
                    "nit",
                    f"nothing to revert: none of the {touched} changed file(s) is production "
                    "code (knobs.revert_check.paths, or a source suffix outside the test paths)",
                    GATE_ID,
                ),
            ),
            skipped=True,
        )
    if report.baseline is not None:
        return cannot_judge(report.baseline)
    caught = inert = 0
    for item in report.results:
        label = item.change.label
        if item.result == CAUGHT:
            caught += 1
        elif item.result == INERT:
            inert += 1
        elif item.result in (ERRORED, UNREADABLE) and item.adds_only:
            findings.append(
                Finding(
                    "nit",
                    f"{label}: noticed only as an error — {item.why}; the change only adds "
                    "code or imported names, so no assertion can fail without it",
                    GATE_ID,
                )
            )
        elif item.result == UNNOTICED:
            hint = (
                "; it looks comment-only, but keel proves a change inert only for "
                f"{INERT_LANGUAGES} — test it, or leave such files out with "
                "knobs.revert_check.paths"
                if looks_comment_only(item.change)
                else ""
            )
            findings.append(
                Finding(_BLOCK, f"{label}: no test notices this change — {item.why}{hint}", GATE_ID)
            )
        else:
            findings.append(
                Finding(_BLOCK, f"{label}: no test failed as an assertion — {item.why}", GATE_ID)
            )
    for change, why in report.not_checked:
        findings.append(Finding(_BLOCK, f"{change.label}: not checked — {why}", GATE_ID))
    total = len(report.results) + len(report.not_checked) - inert
    summary = (
        f"{caught} of {total} production change(s) made a test fail as an assertion when "
        "reverted alone"
    )
    if inert:
        summary += f"; {inert} changed only comments, docstrings or formatting and were not run"
    findings.append(Finding("nit", summary, GATE_ID))
    blocked = any(f.severity == _BLOCK for f in findings)
    return Verdict(not blocked, tuple(findings))
