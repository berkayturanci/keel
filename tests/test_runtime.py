"""Unit tests for runtime capability detection and evaluation."""

from __future__ import annotations

import tempfile
import unittest

from keel import runtime
from keel.runner import CommandResult


def fake_which(name: str) -> str | None:
    return {tool: f"/bin/{tool}" for tool in ("sh", "git", "gh", "adb", "firebase")}.get(name)


def fake_run(argv, **kwargs):  # noqa: ARG001 - signature matches injected runner
    if argv == ["gh", "auth", "status"]:
        return CommandResult(True, 0, "ok")
    return CommandResult(False, 1, "unexpected")


class TestDetect(unittest.TestCase):
    def test_detects_path_env_and_derived_capabilities(self):
        with tempfile.TemporaryDirectory() as d:
            report = runtime.detect(
                d,
                env={
                    "KEEL_GITHUB_MCP": "1",
                    "KEEL_SUBAGENTS": "true",
                    "KEEL_PARALLEL_SUBAGENTS": "yes",
                },
                which=fake_which,
                run=fake_run,
            )
        self.assertTrue(report.available("shell"))
        self.assertTrue(report.available("git"))
        self.assertTrue(report.available("gh-auth"))
        self.assertTrue(report.available("github-mcp"))
        self.assertTrue(report.available("subagents"))
        self.assertTrue(report.available("parallel-subagents"))
        self.assertTrue(report.available("adb"))
        self.assertTrue(report.available("firebase"))
        self.assertTrue(report.available("worktree"))
        self.assertFalse(report.available("release-publish"))

    def test_missing_gh_auth_degrades(self):
        with tempfile.TemporaryDirectory() as d:
            report = runtime.detect(
                d,
                env={},
                which=lambda name: "/bin/sh" if name == "sh" else None,
            )
        self.assertTrue(report.available("shell"))
        self.assertFalse(report.available("gh"))
        self.assertFalse(report.available("gh-auth"))
        self.assertFalse(report.available("adb"))
        self.assertFalse(report.available("firebase"))

    def test_env_overrides_project_command_tool_capabilities(self):
        with tempfile.TemporaryDirectory() as d:
            report = runtime.detect(
                d,
                env={"KEEL_ADB": "1", "KEEL_FIREBASE": "true"},
                which=lambda name: "/bin/sh" if name == "sh" else None,
            )
        self.assertTrue(report.available("adb"))
        self.assertEqual(report.get("adb").source, "environment")
        self.assertTrue(report.available("firebase"))
        self.assertEqual(report.get("firebase").source, "environment")

    def test_default_runner_path(self):
        with tempfile.TemporaryDirectory() as d:
            report = runtime.detect(d, env={}, which=lambda name: None)
        self.assertFalse(report.available("git"))

    def test_missing_root_is_not_writable(self):
        with tempfile.TemporaryDirectory() as d:
            missing = f"{d}/missing"
            report = runtime.detect(missing, env={}, which=lambda name: None, run=fake_run)
        self.assertFalse(report.available("filesystem-write"))


class TestEvaluate(unittest.TestCase):
    def test_required_missing_blocks_optional_missing_degrades(self):
        report = runtime.CapabilityReport((
            runtime.Capability("shell", True, "ok", "test"),
            runtime.Capability("git", False, "missing", "test"),
            runtime.Capability("browser", False, "missing", "test"),
        ))
        req = runtime.CapabilityRequirement(required=("shell", "git"), optional=("browser",))
        evaluation = runtime.evaluate(req, report)
        self.assertFalse(evaluation.ok)
        self.assertEqual(evaluation.missing_required, ("git",))
        self.assertEqual(evaluation.missing_optional, ("browser",))
        self.assertIn("degraded optional", evaluation.render())

    def test_json_shape_is_stable(self):
        report = runtime.CapabilityReport((runtime.Capability("shell", True, "ok", "test"),))
        self.assertIn('"capabilities"', report.to_json())

    def test_unknown_capability_falls_back(self):
        report = runtime.CapabilityReport(())
        self.assertFalse(report.available("unknown"))
        self.assertEqual(report.get("unknown").detail, "unknown capability")

    def test_requirement_merge_and_dict_are_stable(self):
        first = runtime.CapabilityRequirement(required=("shell",), optional=("gh",))
        second = runtime.CapabilityRequirement(required=("shell", "git"), optional=("gh",))
        merged = first.merged(second)
        self.assertEqual(merged.required, ("shell", "git"))
        self.assertEqual(merged.optional, ("gh",))
        self.assertEqual(merged.as_dict()["required"], ["shell", "git"])


class TestValidateNames(unittest.TestCase):
    def test_unknown_capability_reports_error(self):
        errors = runtime.validate_names(("shell", "bogus"), source="x")
        self.assertEqual(len(errors), 1)
        self.assertIn("bogus", errors[0])

    def test_project_command_capabilities_are_known(self):
        self.assertEqual(runtime.validate_names(("adb", "firebase"), source="x"), [])


if __name__ == "__main__":
    unittest.main()
