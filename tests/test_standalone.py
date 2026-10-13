"""Tests for standalone subagent command runner module."""

import argparse
import io
import unittest
from unittest.mock import patch

from keel import config as cfg
from keel import runtime, standalone


class TestStandaloneHelpers(unittest.TestCase):
    def test_issue_labels(self):
        args = argparse.Namespace(issue_label=["bug, backend", "urgent"])
        self.assertEqual(standalone._issue_labels(args), ("bug", "backend", "urgent"))

        empty_args = argparse.Namespace()
        self.assertEqual(standalone._issue_labels(empty_args), ())

    def test_issue_context_provided(self):
        self.assertTrue(standalone._issue_context_provided(argparse.Namespace(issue_title="Bug")))
        self.assertTrue(standalone._issue_context_provided(argparse.Namespace(issue_label=["bug"])))
        self.assertFalse(standalone._issue_context_provided(argparse.Namespace(issue=42)))
        self.assertFalse(standalone._issue_context_provided(argparse.Namespace()))

    def test_standalone_target(self):
        self.assertEqual(standalone._standalone_target(argparse.Namespace(issue=42)), "issue #42")
        self.assertEqual(
            standalone._standalone_target(argparse.Namespace(issue=42, target="auth")),
            "issue #42 (auth)",
        )
        self.assertEqual(standalone._standalone_target(argparse.Namespace(pr=10)), "PR #10")
        self.assertEqual(
            standalone._standalone_target(argparse.Namespace(since="HEAD~1")),
            "since HEAD~1",
        )
        self.assertEqual(
            standalone._standalone_target(argparse.Namespace(since="HEAD~1", target="core")),
            "since HEAD~1 (core)",
        )
        self.assertEqual(
            standalone._standalone_target(argparse.Namespace(scope="backend")),
            "scope backend",
        )
        self.assertEqual(
            standalone._standalone_target(argparse.Namespace(scope="backend", days=7)),
            "7 day scan (scope backend)",
        )
        self.assertEqual(standalone._standalone_target(argparse.Namespace(days=7)), "7 day scan")
        self.assertEqual(
            standalone._standalone_target(argparse.Namespace(issues=[1, 2], max_items=2)),
            "issues #1, #2 (max 2)",
        )
        self.assertEqual(
            standalone._standalone_target(argparse.Namespace(queue="ready", max_items=5)),
            "queue ready (max 5)",
        )
        self.assertEqual(
            standalone._standalone_target(argparse.Namespace(title="Briefing")),
            "Briefing",
        )
        self.assertEqual(
            standalone._standalone_target(argparse.Namespace(hours=4.0, max_items=3)),
            "4h session (max 3)",
        )
        self.assertEqual(
            standalone._standalone_target(argparse.Namespace(hours=4.0)),
            "4h session",
        )
        self.assertIsNone(standalone._standalone_target(argparse.Namespace()))

    def test_has_live_consent_scope(self):
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.8",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="true"),
        )
        req = runtime.CapabilityRequirement()
        # dry run returns False
        args = argparse.Namespace(live=False)
        self.assertFalse(standalone._has_live_consent_scope(args, "implement", config, req, {}))

    def test_cmd_standalone_dry_run_and_live_rejected(self):
        args = argparse.Namespace(dry_run=True, live=True, standalone_command="implement")
        err = io.StringIO()
        with patch("sys.stderr", err):
            code = standalone.cmd_standalone(args)
        self.assertEqual(code, 1)
        self.assertIn("cannot be used together", err.getvalue())

    def test_cmd_standalone_missing_config(self):
        args = argparse.Namespace(
            dry_run=False, live=False, standalone_command="implement", path="nonexistent.yaml"
        )
        err = io.StringIO()
        with patch("sys.stderr", err):
            code = standalone.cmd_standalone(args)
        self.assertEqual(code, 1)

    def test_cmd_standalone_wrap_staged_contamination_warns(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            cfg_path = Path(d) / "project.yaml"
            cfg_path.write_text(
                "extends: keel\nbase_branch: main\ncore_version: '>=0.1'\n"
                "knobs:\n  build_gate_cmd: 'true'\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                dry_run=False,
                live=False,
                standalone_command="wrap",
                path=str(cfg_path),
                root=d,
                pr=None,
                approve_scope=[],
                operator=None,
                target=None,
                json=False,
                delegate=None,
                since=None,
                implementer_file=["src/app.py"],
            )
            with patch(
                "keel.git.staged_modified_or_deleted_files",
                return_value=["src/app.py", "unrelated.py"],
            ):
                err = io.StringIO()
                out = io.StringIO()
                with patch("sys.stderr", err), patch("sys.stdout", out):
                    code = standalone.cmd_standalone(args)
                self.assertEqual(code, 0)
                self.assertIn(
                    "warning: staged change set includes deleted or modified paths",
                    err.getvalue(),
                )
                self.assertIn("staged files  : warning:", out.getvalue())

            args.json = True
            with patch(
                "keel.git.staged_modified_or_deleted_files",
                return_value=["src/app.py", "unrelated.py"],
            ):
                err = io.StringIO()
                out = io.StringIO()
                with patch("sys.stderr", err), patch("sys.stdout", out):
                    code = standalone.cmd_standalone(args)
                self.assertEqual(code, 0)
                data = json.loads(out.getvalue())
                self.assertTrue(data["result"]["staged_contamination"]["has_contamination"])
                self.assertIn(
                    "unrelated.py",
                    data["result"]["staged_contamination"]["suspect_paths"],
                )
                self.assertIn("warnings", data["result"])

            args.json = True
            args.implementer_file = None
            args.since = "HEAD~1"
            with (
                patch(
                    "keel.git.staged_modified_or_deleted_files",
                    return_value=["src/app.py", "unrelated.py"],
                ),
                patch("keel.git.changed_files_since", return_value=["src/app.py"]),
            ):
                err = io.StringIO()
                out = io.StringIO()
                with patch("sys.stderr", err), patch("sys.stdout", out):
                    code = standalone.cmd_standalone(args)
                self.assertEqual(code, 0)
                data = json.loads(out.getvalue())
                self.assertTrue(data["result"]["staged_contamination"]["has_contamination"])
                self.assertEqual(
                    data["result"]["staged_contamination"]["comparison_source"],
                    "files-since-start",
                )

            args.json = False
            args.implementer_file = ["src/app.py"]
            args.since = None
            with patch(
                "keel.git.staged_modified_or_deleted_files",
                return_value=["src/app.py"],
            ):
                err = io.StringIO()
                out = io.StringIO()
                with patch("sys.stderr", err), patch("sys.stdout", out):
                    code = standalone.cmd_standalone(args)
                self.assertEqual(code, 0)
                self.assertNotIn("warning:", err.getvalue())
                self.assertNotIn("staged files  : warning:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
