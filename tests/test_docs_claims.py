"""What the docs and the site *claim* keel is, checked against keel (issue #1019).

`tests/test_documented_commands.py` already asks whether a documented `keel <cmd>`
is a real subcommand. Every defect this module pins slipped past it, because
every one of them is a claim of a different shape:

* Eleven subcommands had no section in `docs/keel/cli.md` at all — `gc`,
  `canary`, `rollback`, `cost-report`, `close-reconcile`, `dryrun-verify`,
  `scratch-dir`, `release`, `adapter-status`, `update-adapter`, `sync`. A
  reference is not a reference if reaching a command means reading `cli.py`.
* `knobs.swarm_review_evidence` — the knob that decides whether swarm landings
  enforce review at all — was in the schema and in no configuration table.
* The website said **16** `/keel` commands in five places and **17** in five
  others. `swarm` made it 17; half the site was never updated.
* `website/integrations.js` promises "100% real Keel CLI commands" in its own
  header and showed `keel ship … --delegate cursor` six times. `keel ship` has
  no `--delegate` flag — it lives on `keel implement` and on the `/keel:ship`
  adapter. `keel cost-report .keel/project.yaml` and a bare
  `keel evidence-verify … --phase pre-merge` do not parse either.
* `keel swarm-plan … 714 715 716 717`, `keel swarm-land … --mode auto` and
  `keel window … --root .` appeared in the docs *and in the adapter bodies
  agents execute*. Every one of those is a real subcommand with flags that do
  not exist, which is exactly the gap `test_documented_commands.py` leaves.

So the checks here compare a claim to the thing it claims about: the argparse
parser, the JSON schema, and the adapter command set — never a list retyped in
a test, which would drift the same way the prose did.

Everything is offline: these are facts about this checkout.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shlex
import unittest
from pathlib import Path

from keel import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE = REPO_ROOT / "website"
CLI_DOC = REPO_ROOT / "docs" / "keel" / "cli.md"
CONFIG_DOC = REPO_ROOT / "docs" / "keel" / "configuration.md"
SCHEMA = REPO_ROOT / "src" / "keel" / "schema" / "project.schema.json"

#: `## `keel <name> …`` / `### `keel <name> …`` — the signature headings cli.md uses.
_CLI_SECTION = re.compile(r"(?m)^#{2,3} `keel ([a-z][a-z0-9-]*)")

#: A `knobs` key as configuration.md spells it: a table row and a detail heading.
_KNOB_ROW = re.compile(r"(?m)^\| `([a-z0-9_]+)` \|")
_KNOB_HEADING = re.compile(r"(?m)^#### `([a-z0-9_]+)`")


def _subcommands() -> set[str]:
    """Every top-level subcommand, read from the parser rather than listed here."""
    parser = cli.build_parser()
    out: set[str] = set()
    for action in parser._subparsers._group_actions:  # noqa: SLF001 - argparse has no public API
        out.update(getattr(action, "choices", {}) or {})
    return out


def _adapter_commands() -> list[Path]:
    return sorted((REPO_ROOT / "src/keel/adapters/commands").glob("*.md"))


class TestEverySubcommandHasAReferenceSection(unittest.TestCase):
    def test_the_parser_exposes_subcommands(self):
        # Guards the guard: an argparse shape change that returned nothing here
        # would make the assertion below pass while checking nothing.
        self.assertGreater(len(_subcommands()), 40)

    def test_cli_md_documents_every_subcommand(self):
        documented = set(_CLI_SECTION.findall(CLI_DOC.read_text(encoding="utf-8")))
        missing = sorted(_subcommands() - documented)
        self.assertEqual(
            [],
            missing,
            "these subcommands have no `## `keel <name> …`` section in docs/keel/cli.md — "
            f"add one (signature, flags, exit codes, one example): {missing}",
        )

    def test_cli_md_documents_nothing_that_is_not_a_subcommand(self):
        """The other direction: a removed command must not keep its section.

        `keel verify-evidence` (#803) was documented for months and never
        existed. That one was caught in a code block; a heading is the more
        prominent place to leave the same lie.
        """
        documented = set(_CLI_SECTION.findall(CLI_DOC.read_text(encoding="utf-8")))
        unknown = sorted(documented - _subcommands())
        self.assertEqual([], unknown, f"cli.md documents non-commands: {unknown}")


class TestEveryKnobIsDocumented(unittest.TestCase):
    """A knob in the schema and in no table is a knob nobody can find.

    `knobs.swarm_review_evidence` decides whether `keel swarm-land` enforces the
    ship s10 review-evidence contract at all (#828). It shipped documented only
    in its own schema `description`.
    """

    def _knobs(self) -> set[str]:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        return set(schema["properties"]["knobs"]["properties"])

    def test_the_schema_declares_knobs(self):
        self.assertGreater(len(self._knobs()), 10)

    def test_configuration_md_lists_every_knob_in_the_table(self):
        rows = set(_KNOB_ROW.findall(CONFIG_DOC.read_text(encoding="utf-8")))
        missing = sorted(self._knobs() - rows)
        self.assertEqual(
            [],
            missing,
            "these `knobs.properties` keys are in project.schema.json but have no row in "
            f"the `## knobs` table of docs/keel/configuration.md: {missing}",
        )

    def test_configuration_md_explains_every_knob(self):
        headings = set(_KNOB_HEADING.findall(CONFIG_DOC.read_text(encoding="utf-8")))
        missing = sorted(self._knobs() - headings)
        self.assertEqual(
            [],
            missing,
            "these knobs have a table row but no `#### `<knob>`` detail section in "
            f"docs/keel/configuration.md: {missing}",
        )


class TestTheSiteStatesTheRealCommandCount(unittest.TestCase):
    """The site said 16 in five places and 17 in five others.

    Derived from `src/keel/adapters/commands/`, the same source
    `TestCommandCountClaims` uses for the README, so the next command to land
    fails here instead of shipping a site that disagrees with itself.
    """

    #: Every place the site spells the count. A glob would sweep SVG path data
    #: and viewBox numbers, so the shapes are written out.
    _CLAIMS = (
        ("llms.txt", re.compile(r"> (\d+) `/keel` commands")),
        ("docs.html", re.compile(r"all (\d+) /keel commands")),
        ("docs.html", re.compile(r"extension slots, (\d+) /keel commands")),
        ("docs.html", re.compile(r'Workflow commands <span class="badge">(\d+)</span>')),
        ("index.html", re.compile(r"extension slots, (\d+) /keel commands")),
        ("index.html", re.compile(r'Workflow commands <span class="badge">(\d+)</span>')),
        ("index.html", re.compile(r"(\d+) /keel commands, stdlib-first")),
        ("index.html", re.compile(r"All (\d+) <code>/keel:")),
        ("index.html", re.compile(r"(\d+) workflows · /keel:")),
        ("content.js", re.compile(r"All (\d+) /keel:")),
        ("content.js", re.compile(r"keel ships <b>(\d+)</b> agentic workflow commands")),
        ("content.js", re.compile(r"Every one of the <b>(\d+)</b> commands")),
        ("home.js", re.compile(r"all (\d+) commands")),
    )

    def test_the_adapters_are_findable(self):
        self.assertGreater(len(_adapter_commands()), 10)

    def test_every_stated_command_count_matches_the_adapters(self):
        expected = str(len(_adapter_commands()))
        wrong: dict[str, list[str]] = {}
        for name, pattern in self._CLAIMS:
            text = (SITE / name).read_text(encoding="utf-8")
            found = pattern.findall(text)
            # A claim that vanished is drift too: the pattern is the record of
            # where the count is stated, so an empty match means the sentence
            # was rewritten and this list needs the new shape.
            self.assertTrue(found, f"website/{name} no longer states {pattern.pattern!r}")
            bad = [n for n in found if n != expected]
            if bad:
                wrong.setdefault(f"website/{name}", []).extend(bad)
        self.assertEqual(
            {},
            wrong,
            f"the site states a /keel command count that is not {expected} "
            f"(src/keel/adapters/commands/): {wrong}",
        )


class TestEveryDocumentedInvocationParses(unittest.TestCase):
    """A documented flag that does not exist fails on the reader's first paste.

    `test_documented_commands.py` asks whether `keel <cmd>` is real and stops
    there, so `keel swarm-land … --mode auto`, `keel swarm-plan … 714 715 716`
    and `keel window … --root .` were documented for months — the last three
    inside adapter bodies an agent executes verbatim.

    The parser is asked, not called: `parse_args` runs no command and touches
    nothing. Lines carrying a `<placeholder>`, a `$VAR` or a glob are skipped —
    they are deliberately not runnable as written.
    """

    #: Only fenced blocks tagged as a shell — what a reader pastes. Prose and
    #: inline backticks mark emphasis as often as commands.
    _FENCE = re.compile(r"^```(?:bash|sh|shell|console|zsh)[^\n]*\n(.*?)^```", re.DOTALL | re.M)
    #: Shell continuations join into one command before parsing.
    _CONTINUATION = re.compile(r"\\\n\s*")
    #: Everything from the first pipe, redirect or comment is the shell's, not keel's.
    _SHELL_TAIL = re.compile(r"\s+[|>#]")

    _GLOBS = (
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "docs/**/*.md",
        "keel-visual/*.md",
        "src/keel/adapters/commands/*.md",
    )

    def _invocations(self) -> list[tuple[str, str]]:
        """``[(file, command), …]`` for every runnable `keel …` line."""
        found: list[tuple[str, str]] = []
        for pattern in self._GLOBS:
            for path in sorted(REPO_ROOT.glob(pattern)):
                # Relative, not absolute: this checkout may itself *be* a
                # worktree under .keel/worktrees, and an absolute-path filter
                # would then skip every file (the #803 empty-sweep trap).
                relative = path.relative_to(REPO_ROOT)
                if not path.is_file() or relative.parts[0] == ".keel":
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for block in self._FENCE.findall(text):
                    for line in self._CONTINUATION.sub(" ", block).splitlines():
                        command = line.strip().removeprefix("$ ").strip()
                        if not command.startswith("keel "):
                            continue
                        command = self._SHELL_TAIL.split(f" {command}")[0].strip()
                        found.append((str(relative), command))
        return found

    @staticmethod
    def _runnable(argv: list[str]) -> bool:
        return not any(a.startswith(("<", "$")) or "*" in a for a in argv)

    def test_the_docs_contain_invocations(self):
        # Guards the guard: a regex matching nothing would pass vacuously.
        self.assertGreater(len(self._invocations()), 50)

    def test_every_invocation_parses(self):
        parser = cli.build_parser()
        failures: list[str] = []
        for where, command in self._invocations():
            try:
                argv = shlex.split(command)[1:]
            except ValueError:  # pragma: no cover - unbalanced quotes in prose
                continue
            if not self._runnable(argv):
                continue
            errors = io.StringIO()
            try:
                with (
                    contextlib.redirect_stderr(errors),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    parser.parse_args(argv)
            except SystemExit as exit_:
                if exit_.code == 0:  # `keel --version` prints and exits 0
                    continue
                detail = errors.getvalue().strip().splitlines()
                failures.append(f"{where}: {command} -> {detail[-1] if detail else 'rejected'}")
        self.assertEqual(
            [],
            failures,
            "documented `keel …` invocations the CLI would reject:\n" + "\n".join(failures),
        )


class TestTheIntegrationsCatalogRunsWhatItShows(unittest.TestCase):
    """`website/integrations.js` says "100% real Keel CLI commands" in its header.

    It showed `keel ship … --delegate <name>` on six cards; `--delegate` is a
    `keel implement` / `/keel:ship` flag and `keel ship` rejects it. The card
    text is the whole product for a reader who has not installed keel yet, so a
    command that cannot run is the worst place for a typo.

    Only `keel …` cards are parsed: the catalog legitimately shows `brew`,
    `pipx`, `curl`, `uses:` and `export` lines that are not keel's to validate.
    """

    _CMD = re.compile(r'cmd: "([^"]+)"')

    def _keel_commands(self) -> list[str]:
        text = (SITE / "integrations.js").read_text(encoding="utf-8")
        return [c for c in self._CMD.findall(text) if c.startswith("keel ")]

    def test_the_catalog_shows_keel_commands(self):
        self.assertGreater(len(self._keel_commands()), 5)

    def test_every_catalog_command_parses(self):
        parser = cli.build_parser()
        failures: list[str] = []
        for command in self._keel_commands():
            errors = io.StringIO()
            try:
                with (
                    contextlib.redirect_stderr(errors),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    parser.parse_args(shlex.split(command)[1:])
            except SystemExit as exit_:
                if exit_.code == 0:
                    continue
                detail = errors.getvalue().strip().splitlines()
                failures.append(f"{command} -> {detail[-1] if detail else 'rejected'}")
        self.assertEqual(
            [],
            failures,
            "website/integrations.js promises real CLI commands and shows these, "
            "which the CLI rejects:\n" + "\n".join(failures),
        )

    def test_the_python_distribution_is_named_correctly(self):
        """`pipx install keel` installs an unrelated PyPI project.

        The bare `keel` name was taken, which is why this project publishes as
        `keel-workflow` (docs/keel/release.md). The card said `keel`.
        """
        text = (SITE / "integrations.js").read_text(encoding="utf-8")
        self.assertNotRegex(
            text,
            r"(?:pipx|pip) install keel(?![-\w])",
            "the PyPI distribution is `keel-workflow`; a bare `keel` installs someone else's",
        )


if __name__ == "__main__":
    unittest.main()
