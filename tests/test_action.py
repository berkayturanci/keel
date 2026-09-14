"""The published GitHub Action's contract, checked against keel's own parser.

`action.yml` is what GitHub serves as the Marketplace listing
(`uses: berkayturanci/keel@vX.Y.Z`), so it is the one file in this repository a
stranger runs before they run anything else.

The previous version of this file listed the declared input names and stopped.
Every defect #1153 measured was invisible to it, because a list of key names says
nothing about whether the file *works*:

* `command: swarm` named a subcommand that does not exist — `keel swarm` exits 2,
  `invalid choice`. Only `swarm-plan/run/land/status` are real.
* `command: swarm-plan` built an argv the parser refused: the run block always
  appends `--root .`, and `swarm-plan` was the one sibling whose parser lacked it.
* The declared outputs `status` and `decision` were never written to
  `$GITHUB_OUTPUT`, so both were always empty.
* The default invocation forced `--live --jury` and stopped on keel's consent
  gate — the Marketplace action failed on its own defaults.

So this file builds the argv the run block builds and hands it to
`build_parser()`. A command that cannot be parsed is a command that cannot run.
"""

import contextlib
import os
import re
import unittest
from pathlib import Path

import yaml

from keel.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent


def _subcommands() -> set[str]:
    """Every subcommand `keel` actually declares, read from the parser itself."""
    parser = build_parser()
    subparsers = [
        action for action in parser._subparsers._group_actions if hasattr(action, "choices")
    ]
    return set(subparsers[0].choices)


class TheActionMetadataTheListingDependsOn(unittest.TestCase):
    """`name` and `branding` are the Marketplace listing; they do not drift."""

    def setUp(self):
        self.path = ROOT / "action.yml"
        self.assertTrue(self.path.exists(), "action.yml must exist in the repository root")
        self.data = yaml.safe_load(self.path.read_text(encoding="utf-8"))

    def test_the_published_identity_is_unchanged(self):
        # GitHub keys the listing on the name; changing it unpublishes the action.
        self.assertEqual(self.data["name"], "Keel Autonomous Delivery Action")
        self.assertEqual(self.data["author"], "Berkay Turancı")
        self.assertEqual(self.data["branding"]["icon"], "anchor")
        self.assertTrue(str(self.data.get("description", "")).strip())

    def test_it_is_a_composite_action(self):
        self.assertEqual(self.data["runs"]["using"], "composite")


class TheDocumentedCommandsAreRealSubcommands(unittest.TestCase):
    """Every command the input advertises must exist in keel's parser."""

    def setUp(self):
        self.data = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
        described = self.data["inputs"]["command"]["description"]
        # "keel subcommand to run: a, b, c." -> {a, b, c}
        listed = described.split(":", 1)[1]
        self.documented = {
            part.strip().rstrip(".") for part in listed.split(",") if part.strip().rstrip(".")
        }

    def test_the_description_actually_lists_commands(self):
        # Guard the extraction itself: a description that stopped naming commands
        # would otherwise make every assertion below vacuously true.
        self.assertGreaterEqual(len(self.documented), 5)

    def test_every_documented_command_exists(self):
        real = _subcommands()
        for command in sorted(self.documented):
            with self.subTest(command=command):
                self.assertIn(
                    command, real, f"action.yml advertises {command!r}, keel has no such subcommand"
                )

    def test_the_default_command_is_one_of_them(self):
        self.assertIn(self.data["inputs"]["command"]["default"], self.documented)


class TheArgvTheRunBlockBuildsIsAccepted(unittest.TestCase):
    """The contract that matters: the parser accepts what the Action sends it.

    The run block composes one shape for every command — the config, then
    ``--root .``, then the optional ``--pr`` / ``--issue`` / free-form ``args``.
    These tests assemble that shape and parse it, which is what would have caught
    `swarm-plan` and `swarm` before either reached a runner.
    """

    def setUp(self):
        self.text = (ROOT / "action.yml").read_text(encoding="utf-8")
        self.data = yaml.safe_load(self.text)
        described = self.data["inputs"]["command"]["description"]
        self.documented = sorted(
            part.strip().rstrip(".")
            for part in described.split(":", 1)[1].split(",")
            if part.strip().rstrip(".")
        )
        self.config = self.data["inputs"]["config"]["default"]

    def _run_step(self) -> str:
        for step in self.data["runs"]["steps"]:
            if step.get("id") == "run":
                return step["run"]
        raise AssertionError("action.yml has no step with id 'run'")

    def test_the_run_block_still_builds_the_shape_these_tests_assume(self):
        # Built from parts so this assertion cannot be satisfied by its own text.
        root_flag = "--" + "root"
        opener = "ARGS=(" + '"$KEEL_CONFIG"'
        body = self._run_step()
        self.assertIn(opener + " " + root_flag + " .", body)

    def _needs_pr(self, command: str) -> bool:
        """Does this command's parser refuse the base argv without ``--pr``?"""
        parser = build_parser()
        with open(os.devnull, "w", encoding="utf-8") as sink, contextlib.redirect_stderr(sink):
            try:
                parser.parse_args([command, self.config, "--root", "."])
            except SystemExit:
                return True
        return False

    def test_every_documented_command_parses_with_the_argv_the_action_sends(self):
        parser = build_parser()
        for command in self.documented:
            with self.subTest(command=command):
                argv = [command, self.config, "--root", "."]
                if self._needs_pr(command):
                    # The Action refuses these up front rather than letting argparse
                    # exit 2 about a flag the caller never wrote; asserted below.
                    argv += ["--pr", "7"]
                parsed = parser.parse_args(argv)
                self.assertEqual(parsed.root, ".")

    def test_the_action_guards_exactly_the_commands_whose_parser_requires_a_pr(self):
        # Derived from the parser, not restated, so a command that gains or loses
        # the requirement moves this assertion instead of silently passing.
        required = {command for command in self.documented if self._needs_pr(command)}
        self.assertTrue(required, "expected at least one PR-scoped command")
        body = self._run_step()
        guard = re.search(r"case\s+\"\$KEEL_COMMAND\"\s+in\s*\n\s*(?P<arms>[^)]+)\)", body)
        self.assertIsNotNone(guard, "the run block has no command guard")
        guarded = {arm.strip() for arm in guard.group("arms").split("|")}
        self.assertEqual(guarded, required)

    def _forwarded(self, flag: str) -> set[str]:
        """Commands the run block appends ``flag`` to, read from its `case` arms."""
        body = self._run_step()
        pattern = rf"case \"\$KEEL_COMMAND\" in\s*\n\s*([^)]+)\)\s*\n[^;]*{re.escape(flag)} "
        arms = re.findall(pattern, body)
        self.assertTrue(arms, f"no guard forwards {flag}")
        return {name.strip() for name in arms[0].split("|")}

    def _accepts(self, command: str, flag: str) -> bool:
        parser = build_parser()
        with open(os.devnull, "w", encoding="utf-8") as sink, contextlib.redirect_stderr(sink):
            try:
                parser.parse_args([command, self.config, "--root", ".", flag, "7"])
            except SystemExit:
                return False
        return True

    def test_pr_is_forwarded_only_where_the_parser_takes_it(self):
        """Appending `--pr` to every command made most of them exit 2 (#1153).

        `--pr` exists on three subcommands. `command: validate` with a `pr` input died on
        a flag the caller never wrote, and `command: plan` died more confusingly still —
        argparse abbreviation bound `--pr` to `--profile` and reported
        `invalid choice: '7'`. With `fail-on-block: false` that stderr became the action's
        output and, with `comment: true`, was posted to the pull request.
        """
        forwarded = self._forwarded("--pr")
        for command in self.documented:
            with self.subTest(command=command):
                self.assertEqual(
                    command in forwarded,
                    self._accepts(command, "--pr"),
                    f"{command}: the action and the parser disagree about --pr",
                )

    def test_issue_is_forwarded_only_where_it_records_rather_than_selects(self):
        """`issue` is documented as recorded on the run, and that is not universal.

        On `swarm-plan` / `swarm-run` / `swarm-land` the same flag **selects the work**:
        forwarding it there adds an issue to the plan, which is a wrong answer rather than
        an error. Those take their issues through `args`.
        """
        forwarded = self._forwarded("--issue")
        self.assertEqual(forwarded, {"ship", "run-gates", "plan"})
        for command in forwarded:
            with self.subTest(command=command):
                self.assertTrue(self._accepts(command, "--issue"))
        for command in ("swarm-plan", "swarm-run", "swarm-land"):
            with self.subTest(command=command):
                self.assertNotIn(command, forwarded)

    def test_the_run_block_disables_errexit(self):
        """`fail-on-block: false` is a promise `-e` would break.

        GitHub runs a composite `shell: bash` step as
        `bash --noprofile --norc -e -o pipefail`, so the step dies the moment keel exits
        non-zero — before the outputs are written, the summary is appended, or the
        `fail-on-block` branch is reached. The `action-smoke` job caught it: `keel
        validate` answered "unknown property" against the released keel and the step
        failed, rather than reporting an informational run.
        """
        body = self._run_step()
        # Built from parts so this cannot be satisfied by its own text.
        self.assertIn("set " + "+e", body)

    def test_the_default_run_does_not_force_live_flags(self):
        # The consent gate is keel's, not this action's, to satisfy: a default that
        # forces `--live` fails on it every time.
        body = self._run_step()
        for forced in ("--" + "live", "--" + "jury"):
            self.assertNotIn(forced, body, f"the run block must not inject {forced}")


class TheDeclaredOutputsAreActuallyWritten(unittest.TestCase):
    """A declared output nothing writes is an empty string with a docstring."""

    def setUp(self):
        self.data = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
        self.outputs = self.data.get("outputs", {})
        self.steps = self.data["runs"]["steps"]

    def test_there_are_outputs(self):
        self.assertTrue(self.outputs, "action.yml declares no outputs")

    def test_each_output_is_bound_to_a_step_that_writes_it(self):
        sink = "$GITHUB" + "_OUTPUT"
        bodies = "\n".join(step.get("run", "") for step in self.steps)
        for name, spec in self.outputs.items():
            with self.subTest(output=name):
                value = str(spec.get("value", ""))
                match = re.search(r"steps\.(?P<step>[\w-]+)\.outputs\.(?P<key>[\w-]+)", value)
                self.assertIsNotNone(match, f"output {name!r} is not bound to a step output")
                key = match.group("key")
                self.assertRegex(
                    bodies,
                    rf"{re.escape(key)}=[^\n]*>>\s*\"?{re.escape(sink)}",
                    f"output {name!r} is declared but never written to the step output file",
                )

    def test_the_step_the_outputs_name_exists(self):
        ids = {step.get("id") for step in self.steps}
        for name, spec in self.outputs.items():
            match = re.search(r"steps\.(?P<step>[\w-]+)\.outputs\.", str(spec.get("value", "")))
            with self.subTest(output=name):
                self.assertIn(match.group("step"), ids)


if __name__ == "__main__":
    unittest.main()
