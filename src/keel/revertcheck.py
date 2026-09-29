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

_BLOCK = "major"


@dataclass(frozen=True)
class Settings:
    """The resolved ``knobs.revert_check`` for one run."""

    #: The test command each revert runs; ``None`` when neither knob sets one.
    cmd: str | None
    #: The knob :attr:`cmd` came from (``knobs.build_gate_cmd`` when neither sets one).
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

    @property
    def mode(self) -> tuple[str, str] | None:
        """``(old, new)`` when the diff changes the file's mode (``old mode`` / ``new mode``)."""
        old = next((line[9:] for line in self.header if line.startswith("old mode ")), None)
        new = next((line[9:] for line in self.header if line.startswith("new mode ")), None)
        return (old, new) if old and new else None

    @property
    def binary(self) -> bool:
        """git printed no text for it (``Binary files … differ``)."""
        return any(line.startswith(("Binary files ", "GIT binary patch")) for line in self.header)

    @property
    def content_header(self) -> tuple[str, ...]:
        """The header without its mode lines: a content hunk must not carry the mode along."""
        return tuple(
            line for line in self.header if not line.startswith(("old mode ", "new mode "))
        )


#: git's C-style escapes in a quoted path (``quote_c_style``), beside ``\\ooo`` octal bytes.
_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def _unquote(name: str) -> str:
    """A path as git wrote it -> the path itself.

    git wraps a name in double quotes when it holds a control character, a quote or a
    backslash, and escapes those inside (``"a/q\\"x.py"``); a byte is ``\\ooo`` octal. An
    unquoted name is returned as it is.
    """
    if len(name) < 2 or not (name.startswith('"') and name.endswith('"')):
        return name
    body, out, i = name[1:-1], bytearray(), 0
    while i < len(body):
        char = body[i]
        octal = body[i + 1 : i + 4]
        if char == "\\" and body[i + 1 : i + 2] in _C_ESCAPES:
            out.append(_C_ESCAPES[body[i + 1]])
            i += 2
        elif char == "\\" and len(octal) == 3 and all(c in "01234567" for c in octal):
            out.append(int(octal, 8) & 0xFF)
            i += 4
        else:
            out += char.encode("utf-8", "surrogateescape")
            i += 1
    return out.decode("utf-8", "surrogateescape")


def _closing_quote(text: str) -> int:
    """The index of the quote that closes the quoted name opening ``text``; ``-1`` if none."""
    i = 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i
        i += 1
    return -1


def _strip(name: str, prefix: str) -> str:
    return name[len(prefix) :] if name.startswith(prefix) else name


def _path_from(line: str, prefix: str) -> str | None:
    """The path on a ``--- a/x`` / ``+++ b/x`` line; ``None`` for ``/dev/null``.

    git ends the line with a tab when the name contains a space, and quotes a name that
    holds a control character, a quote or a backslash (:func:`_unquote`); the path is read
    back so it matches the project's globs.
    """
    name = line[4:].rstrip("\t")
    if name == "/dev/null":
        return None
    return _strip(_unquote(name), prefix)


def _header_path(line: str) -> str:
    """The path on a ``diff --git a/x b/x`` line — the only name a file with no ``---`` /
    ``+++`` lines (a mode change, a binary file) carries.

    With renames off both names are the same file, so an unquoted pair splits in the middle
    (a name may hold ``" b/"``), and a quoted one ends at its closing quote. A header that
    reads neither way falls back to the text after the last ``" b/"``.
    """
    rest = line[len("diff --git ") :]
    if rest.startswith('"'):
        end = _closing_quote(rest)
        if end > 0:
            return _strip(_unquote(rest[: end + 1]), "a/")
    half = (len(rest) - 5) // 2
    name = rest[2 : 2 + half]
    if rest.startswith("a/") and rest[2 + half :] == f" b/{name}":
        return name
    return rest.rsplit(" b/", 1)[-1]


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
        fallback = _header_path(lines[i])
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
    """The production changes to revert, ordered by path.

    Ordered here, not by git: ``diff.orderFile`` reorders git's output, and the order
    decides which changes ``max_changes`` reaches. Each change is reverted alone, so each
    must carry only itself:

    * **A mode change is its own change** (``old mode``/``new mode``). Copied into every
      content hunk's patch, it was undone with each of them, and one test of the
      executable bit "caught" every hunk of the file.
    * **Added Python code is split at its top-level definitions** (:func:`_blocks`) — a
      hunk that only adds lines, and a new file's content, whatever ``unit`` says for a
      new file. Undone whole, three added functions where a test calls one read as
      "noticed" for all three; undone one at a time, each must be noticed on its own. A
      new file that is one block stays one change, which deletes it. Other languages, and
      added lines that do not parse on their own, are not split.
    * Otherwise one change per hunk (``unit: hunk``) or per file (``unit: file``); a
      deleted file is one change, which restores it.
    """
    changes: list[Change] = []
    unrevertable: list[str] = []
    for f in sorted(files, key=lambda f: f.path):
        if not is_production(f.path, tests=tests, paths=paths):
            continue
        if f.mode is not None and f.status == "modified":
            old, new = f.mode
            mode_patch = "\n".join((f.header[0], f"old mode {old}", f"new mode {new}")) + "\n"
            changes.append(Change(f"{f.path} (mode {old} -> {new})", f.path, mode_patch, False))
        if f.binary or not f.hunks:
            if f.binary or f.mode is None:
                unrevertable.append(f.path)
            continue
        header = f.content_header
        if f.status == "added":
            blocks = _blocks(f.hunks[0], f.path) if len(f.hunks) == 1 else []
            if len(blocks) > 1:
                plain = (header[0], _old_side(header), *(h for h in header if h[:4] == "+++ "))
                changes.extend(_block_change(f.path, plain, block) for block in blocks)
            else:
                changes.append(
                    Change(f"{f.path} (new file)", f.path, _patch(header, f.hunks), True)
                )
            continue
        if unit == UNIT_FILE or f.status == "deleted":
            suffix = " (deleted file)" if f.status == "deleted" else ""
            changes.append(
                Change(
                    f"{f.path}{suffix}",
                    f.path,
                    _patch(header, f.hunks),
                    all(h.pure_addition for h in f.hunks),
                )
            )
            continue
        for hunk in f.hunks:
            blocks = _blocks(hunk, f.path) if hunk.pure_addition else []
            if len(blocks) > 1:
                changes.extend(_block_change(f.path, header, block) for block in blocks)
                continue
            label = f"{f.path} {hunk.header.split(' @@', 1)[0]} @@"
            changes.append(Change(label, f.path, _patch(header, (hunk,)), hunk.pure_addition))
    return Plan(tuple(changes), tuple(unrevertable), tuple(f.path for f in files))


#: Files whose added code :func:`_blocks` can split: it parses them.
_PYTHON_SUFFIXES = (".py", ".pyi")
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _old_side(header: Sequence[str]) -> str:
    """A new file's ``--- a/x`` line, from its ``+++ b/x`` line as git wrote it (quoting and
    all), so a patch that removes part of the file names the file that stays."""
    new = next(h for h in header if h[:4] == "+++ ")
    name = new[4:]
    return "--- " + ('"a/' + name[3:] if name.startswith('"b/') else "a/" + name[2:])


def _blocks(hunk: Hunk, path: str) -> list[tuple[int, tuple[str, ...]]]:
    """A pure-addition hunk's lines split at its top-level definitions, as ``(first line, lines)``.

    Only Python is split, and only when the added lines parse on their own
    (:func:`_block_starts`): a boundary read from the text alone was wrong both ways — two
    functions with no blank line between them stayed one change, so a test of one vouched
    for the other, and a blank line inside a string literal split it, so each half's revert
    was a syntax error. ``[]`` (keep the hunk whole) otherwise, and for a header that cannot
    be read. Lines between blocks ride with the block before them (leading ones with the
    first); a ``\\ No newline`` marker stays with its line.
    """
    match = _HUNK_RE.match(hunk.header)
    if not match or not path.endswith(_PYTHON_SUFFIXES):
        return []
    starts = _block_starts([line[1:] for line in hunk.lines if line.startswith("+")])
    if not starts:
        return []
    first = int(match.group(3))
    blocks: list[tuple[int, list[str]]] = []
    offset = 0
    for line in hunk.lines:
        if line.startswith("+"):
            if not blocks or offset in starts:
                blocks.append((first + offset, []))
            offset += 1
        blocks[-1][1].append(line)
    return [(start, tuple(lines)) for start, lines in blocks]


def _block_starts(added: Sequence[str]) -> set[int]:
    """The 0-based added lines where a new block starts; empty when there is one block.

    The lines are dedented by their common leading whitespace and parsed. Each ``def`` and
    ``class`` (from its first decorator) is a block, and so is each run of other statements
    between them. Empty when the lines do not parse on their own — part of an expression,
    a string whose continuation sits left of the rest — or do not share one indent.
    """
    texts = [text for text in added if text.strip()]
    if not texts:
        return set()
    width = min(len(text) - len(text.lstrip()) for text in texts)
    indent = texts[0][:width]
    if any(not text.startswith(indent) for text in texts):
        return set()
    source = "\n".join(text[width:] if text.strip() else "" for text in added) + "\n"
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    starts: set[int] = set()
    previous: bool | None = None
    for node in tree.body:
        definition = isinstance(node, _DEFINITIONS)
        if previous is not None and (definition or previous):
            decorators = getattr(node, "decorator_list", [])
            starts.add(min([node.lineno, *(d.lineno for d in decorators)]) - 1)
        previous = definition
    return starts


def _block_change(path: str, header: Sequence[str], block: tuple[int, tuple[str, ...]]) -> Change:
    """One block of added lines as its own change: a patch that removes just those lines."""
    first, lines = block
    count = sum(1 for line in lines if line.startswith("+"))
    hunk_header = f"@@ -{first - 1},0 +{first},{count} @@"
    patch = "\n".join((*header, hunk_header, *lines)) + "\n"
    return Change(f"{path} {hunk_header}", path, patch, True)


def _patch(header: Sequence[str], hunks: Sequence[Hunk]) -> str:
    lines = list(header)
    for hunk in hunks:
        lines.append(hunk.header)
        lines.extend(hunk.lines)
    return "\n".join(lines) + "\n"


# --- what a change looks like ------------------------------------------------------
#
# Nothing here skips a run. Every production change in scope is reverted and tested: an
# earlier version skipped changes it judged "inert" (comments, docstrings, formatting),
# and each proof of inertness had a hole — a Go ``//go:embed`` directive, a Python
# ``# coding:`` cookie, a comment that moves ``__LINE__`` or ``f_lineno`` — because "no
# test can observe this change" is not provable in general (#1289 review rounds).

#: Leading text that makes a line **look** like a comment in some language. A wording
#: heuristic only (:func:`looks_comment_only`): it never decides whether a change is run.
_COMMENT_LOOKS: tuple[str, ...] = ("#", "//", "/*", "*/", "* ", "--", ";", "%")


def _changed_lines(patch: str) -> list[str]:
    """The ``+``/``-`` lines of a change's hunks, without their marker."""
    _header, _sep, body = patch.partition("\n@@")
    return [line[1:] for line in body.split("\n") if line[:1] in ("+", "-")]


def looks_comment_only(change: Change) -> bool:
    """Does every changed line look blank or like a comment? **Wording only.**

    Used to explain a blocking "no test notices this change" finding, never to skip a
    run: a comment can still change behaviour.
    """
    lines = _changed_lines(change.patch)
    return bool(lines) and all(
        not line.strip() or line.strip() == "*" or line.strip().startswith(_COMMENT_LOOKS)
        for line in lines
    )


_NAMES = r"[A-Za-z_]\w*(?:\s+as\s+[A-Za-z_]\w*)?(?:\s*,\s*[A-Za-z_]\w*(?:\s+as\s+[A-Za-z_]\w*)?)*"
_DOTTED = r"[A-Za-z_][\w.]*(?:\s+as\s+[A-Za-z_]\w*)?"
#: One complete import statement on one line, and nothing else on it — no trailing
#: comment, no ``;``, no open parenthesis left for a continuation line.
_IMPORT_LINE = re.compile(
    rf"^\s*(?:import\s+{_DOTTED}(?:\s*,\s*{_DOTTED})*"
    rf"|from\s+(?:\.+[\w.]*|[A-Za-z_][\w.]*)\s+import\s+"
    rf"(?:\*|{_NAMES}|\(\s*{_NAMES}\s*,?\s*\)))\s*$"
)


def imports_only(change: Change) -> bool:
    """Is ``change`` a Python change whose every changed line is a whole import statement?

    Read from the change's **own** lines, never from a comparison of the two files: an
    AST cannot see an encoding cookie, and ``# coding: latin-1`` beside an import once
    passed as "imports only" while it changed what a string literal decodes to. Blank
    lines are ignored; a comment, a cookie, a continuation line of a multi-line import or
    anything else makes the answer ``False`` — that change's errors then block, which is
    the safe side. It classifies a result *after* its run; it never skips one.
    """
    if posixpath.splitext(change.path)[1].lower() not in (".py", ".pyi"):
        return False
    lines = [line for line in _changed_lines(change.patch) if line.strip()]
    return bool(lines) and all(_IMPORT_LINE.match(line) for line in lines)


#: The exceptions that say "a name the tests reach is gone" — all a revert of pure
#: added code (or of an imported name) can produce. ``AttributeError`` counts only for a
#: *module* or *class* attribute: ``'NoneType' object has no attribute`` is behaviour.
_MISSING_NAME = re.compile(
    r"^(?:NameError|UnboundLocalError|ImportError|ModuleNotFoundError)\b"
    r"|^AttributeError: (?:(?:partially initialized )?module '[^']*'|type object '[^']*') "
    r"has no attribute\b"
)
#: The start of a Python traceback; its exception line is the first unindented line after it.
_TRACEBACK = "Traceback (most recent call last):"
#: A pytest short-summary line's reason (`` - <Exception>: message``), any exception name.
_PYTEST_REASON = re.compile(r"^(?:FAILED|ERROR) .*? - ([A-Za-z_][\w.]*(?::.*)?)$", re.MULTILINE)


def _raised(output: str) -> list[str]:
    """Every exception the output reports, as ``Name: message`` with the module dropped.

    Read from each traceback's final line — whatever the exception is called, so a
    custom ``Boom`` or a ``StopIteration`` is seen too — and from pytest's short summary.
    """
    found: list[str] = []
    lines = output.split("\n")
    for i, line in enumerate(lines):
        if line.strip() != _TRACEBACK:
            continue
        rest = (text for text in lines[i + 1 :] if text.strip() and not text[:1].isspace())
        found.append(next(rest, ""))
    found.extend(match.group(1) for match in _PYTEST_REASON.finditer(output))
    return [
        name.rsplit(".", 1)[-1] + sep + message
        for name, sep, message in (text.partition(":") for text in found)
    ]


def missing_names_only(output: str) -> bool:
    """Does every exception the run reports say only that a name is missing?

    The evidence a lenient reading needs: the error-only result of undoing an addition is
    a ``nit`` only when the errors are the ones removing code can cause. A ``KeyError``, a
    ``TypeError``, an attribute missing from an *instance*, any exception keel cannot
    name, and output with no traceback at all are behaviour, and block.
    """
    found = _raised(output)
    return bool(found) and all(_MISSING_NAME.match(text) for text in found)


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
_PYTEST_LINE = re.compile(r"^(FAILED|ERROR) (?!\()(\S*?\[.*?\]|\S+|.+?)(?: - (.*))?$", re.MULTILINE)
#: A unittest failure section's header: ``FAIL: test_x (pkg.T.test_x)`` is an assertion,
#: ``ERROR: …`` anything else. What the differential reading compares.
_UNITTEST_HEADER = re.compile(r"^(FAIL|ERROR): (.+?)\s*$", re.MULTILINE)
#: A pytest short-summary reason that is an assertion: a rewritten ``assert``, an
#: ``AssertionError`` (unittest-style assertions under pytest), or ``pytest.fail``.
#: ``Failed: Timeout`` is excluded: pytest-timeout reports a timed-out test that way, and
#: a hang is not an assertion.
_ASSERTION_REASON = re.compile(r"^(?:assert\b|AssertionError\b|Failed:(?! Timeout\b))")


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
    #: The ids of the tests that failed as an assertion, where the runner names them.
    assertion_ids: frozenset[str] = frozenset()
    #: The ids of every failing test — assertion or not — where the runner names them.
    failure_ids: frozenset[str] = frozenset()

    @property
    def failed(self) -> bool:
        """Does the output report any failing or erroring test at all?"""
        return bool(self.assertions or self.errors or self.unclassified or self.failure_ids)


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
    asserted: set[str] = set()
    failing: set[str] = set()
    for kind, test_id in _UNITTEST_HEADER.findall(output):
        failing.add(test_id)
        if kind == "FAIL":
            asserted.add(test_id)
    for match in _PYTEST_LINE.finditer(output):
        failing.add(match.group(2))
        if match.group(1) != "FAILED":
            continue  # ERROR lines are already in the summary's error count
        described += 1
        if _ASSERTION_REASON.match(match.group(3) or ""):
            assertions += 1
            asserted.add(match.group(2))
        else:
            errors += 1
    unclassified = max(0, pytest_failed - described)
    return Tally(
        recognized,
        ran,
        assertions,
        errors,
        unclassified,
        frozenset(asserted),
        frozenset(failing),
    )


def classify(
    *, exit_ok: bool, timed_out: bool, output: str, baseline_failed: frozenset[str] = frozenset()
) -> tuple[str, str]:
    """One reverted run -> ``(result, why)``. Only :data:`CAUGHT` is a pass.

    The exit code decides pass/fail; the output decides *how* it failed. A test that
    errored is not a test that asserted — reverting half a fix can raise ``NameError``
    in every test that imports it, and that proves the import, not the behaviour.

    **Differential.** ``baseline_failed`` names the tests that failed without the revert.
    Where the runner names the assertion failures, the change is caught only by one
    *not* among them, so a failure that was already there cannot certify it.
    (:func:`baseline_problem` already refuses a baseline with any failure; this holds the
    rule on its own too.)
    """
    if timed_out:
        return TIMED_OUT, "the test command timed out with the change reverted"
    if exit_ok:
        return UNNOTICED, "the test command passed with the change reverted"
    tally = read_output(output)
    if tally.assertion_ids and tally.assertion_ids <= baseline_failed:
        return UNNOTICED, (
            "the only tests that failed as an assertion also fail without the revert"
        )
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
    if tally.failed:
        # `suite1; suite2` exits with suite2's status: a suite already failing an
        # assertion would otherwise lend that assertion to every revert.
        return (
            "the test command exits 0 on a clean checkout of HEAD but its output reports "
            "failing tests, so a failure with a change reverted would prove nothing"
        )
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


@dataclass(frozen=True)
class ChangeResult:
    change: Change
    result: str
    why: str
    #: The change only adds code or import lines (:attr:`Change.pure_addition`, or
    #: :func:`imports_only`) **and** every error its run reports is a missing name
    #: (:func:`missing_names_only`) — the one error-only result :func:`judge` reads as a
    #: ``nit``.
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
    reported **not checked** — never as caught. Every change that is reverted is tested:
    nothing is skipped for looking harmless.
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
    baseline_failed = read_output(base.output).failure_ids
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
        # Re-read the clock: resetting, cleaning, applying and reading took time too, and a
        # run must never start with a limit the budget no longer has.
        remaining = left()
        if remaining < 1:
            skipped.append(
                (change, f"the knobs.revert_check.budget_s budget ({settings.budget_s}s) ran out")
            )
            continue
        outcome = test(limit(remaining))
        result, why = classify(
            exit_ok=outcome.exit_ok,
            timed_out=outcome.timed_out,
            output=outcome.output,
            baseline_failed=baseline_failed,
        )
        adds_only = (change.pure_addition or imports_only(change)) and missing_names_only(
            outcome.output
        )
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
      except for a change that only *adds* lines or only changes import lines **and**
      whose run reported nothing but missing names (``NameError``, ``ImportError``, a
      module or class attribute). Reverting such an addition removes a name, and a test
      that calls it can then only error: no assertion can fail against code that is not
      there. The error does prove a test depends on it, so it is a ``nit``. Any other
      error — a ``KeyError``, a ``TypeError``, an instance attribute — is behaviour a test
      should assert on, and blocks, as does any error after reverting a modification.
    * A production file with no textual hunk (binary, mode-only) blocks as **not
      checked**: nothing could be reverted, and the gate never certifies what it did not
      check.
    * A diff with no production change is ``SKIPPED`` — judged, with nothing to check.

    ``report`` is ``None`` only when there was nothing to execute.
    """
    findings: list[Finding] = []
    for path in plan.unrevertable:
        findings.append(
            Finding(
                _BLOCK,
                f"{path}: not checked — no textual hunk to revert (a binary or mode-only change)",
                GATE_ID,
            )
        )
    if report is None:
        if findings:
            return Verdict(False, tuple(findings))
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
    caught = 0
    for item in report.results:
        label = item.change.label
        if item.result == CAUGHT:
            caught += 1
        elif item.result in (ERRORED, UNREADABLE) and item.adds_only:
            findings.append(
                Finding(
                    "nit",
                    f"{label}: noticed only as a missing name — {item.why}; the change only "
                    "adds code or import lines, so no assertion can fail without it",
                    GATE_ID,
                )
            )
        elif item.result == UNNOTICED:
            hint = (
                "; it looks comment-only, and keel tests every change, because a comment can "
                "still change behaviour (encoding cookies, compiler directives, line "
                "numbers) — add a test that notices it, or scope such files out with "
                "knobs.revert_check.paths if you do not want them checked"
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
    total = len(report.results) + len(report.not_checked)
    summary = (
        f"{caught} of {total} production change(s) made a test fail as an assertion when "
        "reverted alone"
    )
    findings.append(Finding("nit", summary, GATE_ID))
    blocked = any(f.severity == _BLOCK for f in findings)
    return Verdict(not blocked, tuple(findings))
