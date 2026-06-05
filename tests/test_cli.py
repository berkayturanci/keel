"""Unit tests for the keel CLI."""

import contextlib
import io
import unittest
from pathlib import Path

from keel import cli

PROJECTS = Path(__file__).resolve().parent.parent / "projects"
REPO_ROOT = PROJECTS.parent


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestVersion(unittest.TestCase):
    def test_version_subcommand(self):
        rc, out, _ = run(["version"])
        self.assertEqual(rc, 0)
        self.assertIn("keel", out)


class TestNoCommand(unittest.TestCase):
    def test_prints_help_and_returns_2(self):
        rc, out, _ = run([])
        self.assertEqual(rc, 2)
        self.assertIn("usage", out.lower())


class TestValidate(unittest.TestCase):
    def test_valid_configs(self):
        rc, out, _ = run(["validate", str(PROJECTS / "keel.yaml"),
                          str(PROJECTS / "smartinventory.yaml")])
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("OK"), 2)

    def test_missing_file(self):
        rc, out, _ = run(["validate", str(PROJECTS / "nope.yaml")])
        self.assertEqual(rc, 1)
        self.assertIn("MISSING", out)

    def test_invalid_config(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("extends: keel\n")  # missing required keys
            bad = f.name
        rc, out, _ = run(["validate", bad])
        self.assertEqual(rc, 1)
        self.assertIn("INVALID", out)

    def test_strict_extensions_missing_root(self):
        # ingreview references extension files not present in this repo -> strict fail.
        rc, out, _ = run(["validate", str(PROJECTS / "ingreview.yaml"), "--root", str(REPO_ROOT)])
        self.assertEqual(rc, 1)
        self.assertIn("extensions", out)


class TestPlan(unittest.TestCase):
    def test_plan_renders_backbone(self):
        rc, out, err = run(
            ["plan", str(PROJECTS / "smartinventory.yaml"), "--root", str(REPO_ROOT)]
        )
        self.assertEqual(rc, 0)
        self.assertIn("s10  merge", out)
        self.assertIn("gate: build", out)

    def test_plan_missing_config(self):
        rc, _, err = run(["plan", str(PROJECTS / "nope.yaml")])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_plan_reports_extension_problems_on_stderr(self):
        # ingreview's extension files are not in this repo -> fail-soft warnings.
        rc, out, err = run(["plan", str(PROJECTS / "ingreview.yaml"), "--root", str(REPO_ROOT)])
        self.assertEqual(rc, 0)
        self.assertIn("extension not loaded", err)

    def test_plan_invalid_config(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("extends: keel\n")
            bad = f.name
        rc, _, err = run(["plan", bad])
        self.assertEqual(rc, 1)
        self.assertIn("invalid keel config", err)


class TestParser(unittest.TestCase):
    def test_subcommands_present(self):
        parser = cli.build_parser()
        # argparse stores subparser choices on the subparsers action.
        actions = [a for a in parser._actions if a.dest == "command"]
        self.assertTrue(actions)
        self.assertEqual(set(actions[0].choices), {"version", "validate", "plan"})


if __name__ == "__main__":
    unittest.main()
