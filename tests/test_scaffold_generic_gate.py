"""A project keel cannot find a test command for is told so, not handed `make test` (#1328).

`keel setup` on a repository with no recognised stack wrote
`build_gate_cmd: "make test"` whether or not a Makefile existed. The first
`keel run-gates` then failed with ``make: *** No rule to make target `test'`` and a dry
`keel ship` said BLOCK — for a reason that pointed at `make`, not at the missing
configuration. #1297 fixed the same thing for detected Python projects.

The generic path now writes `make test` only when the Makefile has a `test` rule.
Otherwise it writes **no** `build_gate_cmd`, says so in the file and on the terminal,
and keeps `build` in `gates:` — so the gate is still planned and still blocks, with a
finding that names the knob to set. A missing gate never reads as a pass.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import yaml

from keel import cli, gates, scaffold
from keel import config as cfg


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class _Root(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write(self, name: str, text: str) -> None:
        (self.root / name).write_text(text, encoding="utf-8")

    def parsed(self, text: str) -> cfg.ProjectConfig:
        return cfg.parse_config(yaml.safe_load(text), source="<scaffold>")


class TheGenericTemplateHasNoGuessedCommand(_Root):
    def test_no_makefile_scaffolds_no_build_command(self):
        text = scaffold.default_config("generic", repo="demo", root=self.root)
        config = self.parsed(text)
        self.assertIsNone(config.knobs.build_gate_cmd)
        self.assertNotIn("make test", text)
        # The gate stays planned: it is unconfigured, not absent.
        self.assertEqual(config.gates, ("build",))

    def test_the_file_says_what_to_set(self):
        text = scaffold.default_config("generic", repo="demo", root=self.root)
        self.assertIn("# build_gate_cmd is not set", text)
        self.assertIn("knobs: {}", text)

    def test_a_makefile_with_a_test_rule_is_used(self):
        self.write("Makefile", "test:\n\tpytest\n")
        config = self.parsed(scaffold.default_config("generic", root=self.root))
        self.assertEqual(config.knobs.build_gate_cmd, "make test")

    def test_a_makefile_without_a_test_rule_is_not(self):
        self.write("Makefile", "build:\n\tcc main.c\n")
        config = self.parsed(scaffold.default_config("generic", root=self.root))
        self.assertIsNone(config.knobs.build_gate_cmd)

    def test_an_unknown_stack_falls_back_to_the_same_rule(self):
        config = self.parsed(scaffold.default_config("cobol", root=self.root))
        self.assertIsNone(config.knobs.build_gate_cmd)

    def test_auto_detect_reports_it_unset(self):
        text, meta = scaffold.auto_detect_config(self.root, repo="demo")
        self.assertIsNone(meta["build_cmd"])
        self.assertIsNone(self.parsed(text).knobs.build_gate_cmd)

    def test_other_knobs_keep_their_block(self):
        text = scaffold.render_config(build_cmd=None, lint_cmd="lint", tier3_globs=("x/**",))
        config = self.parsed(text)
        self.assertIsNone(config.knobs.build_gate_cmd)
        self.assertEqual(config.knobs.lint_cmd, "lint")
        self.assertIn("# build_gate_cmd is not set", text)
        self.assertNotIn("knobs: {}", text)


class TheWizardOffersNoDefault(_Root):
    def test_enter_leaves_it_unset_and_the_prompt_says_so(self):
        asked = {}

        def ask(prompt, default):
            asked[prompt] = default
            return "n" if "merge window" in prompt else default

        text = scaffold.wizard("generic", ask, repo="demo", root=self.root)
        self.assertIsNone(self.parsed(text).knobs.build_gate_cmd)
        prompt = next(p for p in asked if p.startswith("Build/test command"))
        self.assertEqual(asked[prompt], "")
        self.assertIn("blank leaves it unset", prompt)

    def test_an_answer_is_written(self):
        def ask(prompt, default):
            if "merge window" in prompt:
                return "n"
            return "./run-tests" if prompt.startswith("Build/test command") else default

        text = scaffold.wizard("generic", ask, repo="demo", root=self.root)
        self.assertEqual(self.parsed(text).knobs.build_gate_cmd, "./run-tests")

    def test_a_stack_with_a_default_keeps_the_plain_prompt(self):
        asked = {}

        def ask(prompt, default):
            asked[prompt] = default
            return "n" if "merge window" in prompt else default

        scaffold.wizard("rust", ask, repo="demo", root=self.root)
        self.assertEqual(asked["Build/test command"], "cargo test")


class TheMissingGateHelper(unittest.TestCase):
    def test_reads_the_rendered_text(self):
        self.assertTrue(scaffold.missing_build_gate(scaffold.render_config(build_cmd=None)))
        self.assertFalse(scaffold.missing_build_gate(scaffold.render_config(build_cmd="x")))


class TheCommandsSayWhatIsMissing(_Root):
    """End to end: the first-run commands name the knob instead of failing in `make`."""

    def test_init_prints_the_note(self):
        rc, out, err = run(["init", "--root", str(self.root)])
        self.assertEqual(rc, 0, err)
        self.assertIn("build gate   : not configured — set knobs.build_gate_cmd", out)

    def test_init_auto_prints_the_note(self):
        rc, out, err = run(["init", "--root", str(self.root), "--auto"])
        self.assertEqual(rc, 0, err)
        self.assertIn("build gate   : not configured — set knobs.build_gate_cmd", out)

    def test_init_with_a_known_stack_prints_no_note(self):
        self.write("Cargo.toml", "[package]\nname = 'x'\n")
        rc, out, err = run(["init", "--root", str(self.root)])
        self.assertEqual(rc, 0, err)
        self.assertNotIn("not configured", out)

    def test_setup_prints_the_note_and_the_plan_marks_the_gate(self):
        rc, out, err = run(["setup", "--root", str(self.root), "--adapter-target", "claude"])
        self.assertEqual(rc, 0, err)
        self.assertIn("build gate   : not configured", out)
        self.assertIn("- gate: build (not configured", out)

    def test_setup_with_a_configured_gate_prints_no_note(self):
        self.write("Makefile", "test:\n\ttrue\n")
        rc, out, err = run(["setup", "--root", str(self.root), "--adapter-target", "claude"])
        self.assertEqual(rc, 0, err)
        self.assertNotIn("not configured", out)

    def test_run_gates_blocks_with_the_knob_named_and_never_runs_make(self):
        rc, _, err = run(["init", "--root", str(self.root)])
        self.assertEqual(rc, 0, err)
        project = str(self.root / ".keel" / "project.yaml")
        rc, out, _ = run(["run-gates", project, "--root", str(self.root)])
        self.assertNotEqual(rc, 0)
        self.assertIn("FAIL  build", out)
        self.assertIn(gates.UNCONFIGURED_BUILD_GATE, out)
        self.assertNotIn("No rule to make target", out)

    def test_a_dry_ship_blocks_on_it_rather_than_reporting_merge(self):
        rc, _, err = run(["init", "--root", str(self.root)])
        self.assertEqual(rc, 0, err)
        project = str(self.root / ".keel" / "project.yaml")
        _, out, _ = run(["ship", project, "--root", str(self.root)])
        self.assertIn("decision      : BLOCK", out)
        self.assertIn("gate(s): build", out)


if __name__ == "__main__":
    unittest.main()
