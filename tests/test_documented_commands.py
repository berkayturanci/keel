"""Every `keel <cmd>` a reader can copy must be a real subcommand (issue #803).

`docs/keel/evidence.md` carried a whole section titled "Offline Verification:
`keel verify-evidence`", with a runnable example. The command is
`evidence-verify`; the documented one has never existed. It sat in the document
that explains the feature keel is positioned on, so an auditor following it
failed on the first line.

Nothing could have caught it. The link checkers verify that files resolve, and
the adapter drift check verifies that generated surfaces match their source —
neither asks whether a command in a code block is a command.

The subcommand list is read from the parser rather than hard-coded here: a list
in a test drifts exactly the way the prose did.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from keel import cli

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Where a reader looks for something to copy.
_DOC_GLOBS = ("README.md", "AGENTS.md", "CLAUDE.md", "docs/**/*.md", "keel-visual/*.md")

#: Only fenced blocks *tagged as a shell* — what a reader will paste.
#:
#: Prose says "keel blocks the merge" and "keel runs its own gates", and inline
#: backticks mark both commands and emphasis, so sweeping either is mostly noise.
#: Untagged fences are excluded for a sharper reason than noise: they hold things
#: that are deliberately not runnable. `docs/proposals/keel-architecture.md`
#: sketches a CLI surface that was designed and never built (`keel next-issue`,
#: `keel run-step`), and `docs/keel/cutover.md` has a legend line reading
#: `keel core (pip, pinned)`. Failing those would be wrong, and a check that
#: cries wolf on design docs is one people switch off.
_FENCE = re.compile(r"^```(?:bash|sh|shell|console|zsh)[^\n]*\n(.*?)^```", re.DOTALL | re.MULTILINE)

#: A command invocation at the start of a line, optionally after a shell prompt.
_INVOCATION = re.compile(r"(?m)^\s*(?:\$\s+)?keel\s+([a-z][a-z0-9-]*)")


def _real_subcommands() -> set[str]:
    parser = cli.build_parser()
    out: set[str] = set()
    for action in parser._subparsers._group_actions:  # noqa: SLF001 - argparse has no public API
        out.update(getattr(action, "choices", {}) or {})
    return out


def _documented() -> dict[str, set[str]]:
    """``{command: {file, …}}`` for every `keel <cmd>` inside a fenced block."""
    found: dict[str, set[str]] = {}
    for pattern in _DOC_GLOBS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            # Relative, not absolute: the checkout itself may *be* a worktree under
            # .keel/worktrees, and an absolute-path filter skips every file when it
            # is — which is exactly the empty-sweep case the guard above caught.
            if not path.is_file() or path.relative_to(REPO_ROOT).parts[0] == ".keel":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for block in _FENCE.findall(text):
                for name in _INVOCATION.findall(block):
                    found.setdefault(name, set()).add(str(path.relative_to(REPO_ROOT)))
    return found


class TestDocumentedCommandsExist(unittest.TestCase):
    def test_the_parser_exposes_subcommands(self):
        # Guards the guard: if the parser shape changes and this returns nothing,
        # every assertion below passes while checking nothing.
        self.assertGreater(len(_real_subcommands()), 10)

    def test_the_docs_contain_command_examples(self):
        # Same reason, from the other side — a regex that matches nothing would
        # make "every documented command exists" vacuously true.
        self.assertGreater(len(_documented()), 5)

    def test_every_documented_command_is_real(self):
        real = _real_subcommands()
        unknown = {name: sorted(files) for name, files in _documented().items() if name not in real}
        self.assertEqual(
            {},
            unknown,
            "these appear as `keel <cmd>` in a code block but are not subcommands",
        )


class TestCommandCountClaims(unittest.TestCase):
    """The README counts the command flows in prose; the count went stale.

    `swarm` made it 17 and the sentence stayed at 16. Derived from the adapter
    sources so the next command to land fails here rather than in the README.
    """

    def test_the_readme_command_count_matches_the_adapters(self):
        adapters = sorted((REPO_ROOT / "src/keel/adapters/commands").glob("*.md"))
        self.assertTrue(adapters, "no adapter command sources found")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        claimed = re.search(r"any of the (\d+) command flows", readme)
        self.assertIsNotNone(claimed, "the README no longer states a command count")
        self.assertEqual(
            len(adapters),
            int(claimed.group(1)),
            "the README's command count disagrees with src/keel/adapters/commands/",
        )


class TestAgentMemoryFilesDoNotAccreteDuplicates(unittest.TestCase):
    """A memory file that restates a lesson it already holds stops preventing it.

    `.jules/palette.md` is a learning log: each entry exists so the same defect is
    not re-learned. Issue #815 found the 2026-08-18 entry restating 2026-06-15
    verbatim in substance, with a clause already covered by 2026-08-10 — while the
    bug being filed was a *third* variant none of them covered. Re-stating a lesson
    reads as coverage and crowds out the increment, so the file grows while its
    value falls.

    Titles are the cheap, mechanical signal: two entries sharing one is either a
    duplicate to merge or an entry that needs a title saying what is new.
    """

    def test_palette_entry_titles_are_unique(self):
        palette = REPO_ROOT / ".jules" / "palette.md"
        titles: dict[str, list[str]] = {}
        for line in palette.read_text(encoding="utf-8").splitlines():
            if not line.startswith("## "):
                continue
            heading = line[3:].strip()
            # Entries are "<date> - <title>"; compare the title only.
            title = heading.split(" - ", 1)[1] if " - " in heading else heading
            titles.setdefault(title.casefold(), []).append(heading)
        repeated = {t: h for t, h in titles.items() if len(h) > 1}
        self.assertEqual(
            repeated,
            {},
            "duplicate palette entry titles — merge them, or retitle the newer one "
            f"to say what it adds: {repeated}",
        )


class TestModuleInvocationsCarryThePathPrefix(unittest.TestCase):
    """`python3 -m keel` without `PYTHONPATH=src` does not run this checkout (issue #831).

    `-m` resolves the package in the *invoking* interpreter. From a fresh checkout
    that is nothing, so the command fails; and if a global editable install happens
    to be registered it silently runs whichever checkout installed that — the #825
    failure mode. A pipx-installed `keel` is not a substitute: it has its own
    interpreter, so `-m` never reaches it, and it runs the released version anyway.

    `AGENTS.md:60` had the prefix for tests while the next line omitted it for the
    CLI. That was harmless only while a global install papered over it. The sweep in
    :class:`TestDocumentedCommandsExist` reads shell-tagged fences only, and this
    instruction lives in prose with inline backticks, so nothing else covers it.

    `python3 -m keel.cli` is exempt: it appears only as a named counter-example.
    """

    #: `python -m keel` / `python3 -m keel` not followed by a dotted submodule.
    _INVOCATION = re.compile(r"python3? -m keel(?![.\w])")

    def test_agents_md_prefixes_every_module_invocation(self):
        text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        unprefixed = []
        for match in self._INVOCATION.finditer(text):
            before = text[: match.start()]
            if not before.endswith("PYTHONPATH=src "):
                line = text.count("\n", 0, match.start()) + 1
                unprefixed.append(f"AGENTS.md:{line}")
        self.assertEqual(
            unprefixed,
            [],
            "`python3 -m keel` documented without the `PYTHONPATH=src` prefix — it "
            "would run the released package or another checkout, not this one: "
            f"{unprefixed}",
        )


if __name__ == "__main__":
    unittest.main()
