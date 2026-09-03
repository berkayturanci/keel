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

    def test_api_token_present_names_env_vars_not_values(self):
        with tempfile.TemporaryDirectory() as d:
            report = runtime.detect(
                d,
                env={"ANTHROPIC_API_KEY": "sk-secret-value"},
                which=fake_which,
                run=fake_run,
            )
        self.assertTrue(report.available("api-token"))
        cap = next(c for c in report.capabilities if c.name == "api-token")
        self.assertIn("ANTHROPIC_API_KEY", cap.detail)
        self.assertNotIn("sk-secret-value", cap.detail)

    def test_api_token_absent_and_blank_key_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            report = runtime.detect(
                d, env={"OPENAI_API_KEY": "   "}, which=fake_which, run=fake_run
            )
        self.assertFalse(report.available("api-token"))
        cap = next(c for c in report.capabilities if c.name == "api-token")
        self.assertIn("GEMINI_API_KEY", cap.detail)

    def test_api_token_gemini_detected(self):
        with tempfile.TemporaryDirectory() as d:
            report = runtime.detect(
                d,
                env={"GEMINI_API_KEY": "AIzaSySecret"},
                which=fake_which,
                run=fake_run,
            )
        self.assertTrue(report.available("api-token"))
        cap = next(c for c in report.capabilities if c.name == "api-token")
        self.assertIn("GEMINI_API_KEY", cap.detail)
        self.assertNotIn("AIzaSySecret", cap.detail)

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
        report = runtime.CapabilityReport(
            (
                runtime.Capability("shell", True, "ok", "test"),
                runtime.Capability("git", False, "missing", "test"),
                runtime.Capability("browser", False, "missing", "test"),
            )
        )
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


class TestProviderCapabilities(unittest.TestCase):
    """`providers` and `review-vendors` — the cheap half of the #1011 probe."""

    def _detect(self, present, env=None):
        with tempfile.TemporaryDirectory() as d:
            return runtime.detect(
                d,
                env=env or {},
                which=lambda name: f"/bin/{name}" if name in present else None,
                run=fake_run,
            )

    def test_a_tool_capable_cli_on_path_satisfies_providers(self):
        report = self._detect({"sh", "claude"})
        self.assertTrue(report.available("providers"))
        self.assertEqual(report.get("providers").detail, "claude")
        self.assertEqual(report.get("providers").source, "PATH")

    def test_no_agent_cli_means_no_tool_capable_implementer(self):
        report = self._detect({"sh"}, env={"ANTHROPIC_API_KEY": "sk-secret"})
        # A hosted key is not a tool-capable implementer: an api delegate runs under
        # the no-tools contract, so it can never drive the git/PR steps itself.
        self.assertFalse(report.available("providers"))
        self.assertIn("no tool-capable agent CLI", report.get("providers").detail)
        self.assertIn("claude, codex, agy", report.get("providers").detail)

    def test_review_vendors_counts_distinct_vendors_across_transports(self):
        report = self._detect({"sh", "claude", "ollama"}, env={"GEMINI_API_KEY": "AIza-secret"})
        cap = report.get("review-vendors")
        self.assertTrue(cap.available)
        self.assertIn("3 distinct vendor(s)", cap.detail)
        self.assertIn("claude, ollama, google-api", cap.detail)
        self.assertNotIn("AIza-secret", cap.detail)

    def test_one_vendor_is_not_a_cross_vendor_panel(self):
        report = self._detect({"sh", "claude"})
        self.assertFalse(report.available("review-vendors"))
        self.assertIn("1 distinct vendor(s)", report.get("review-vendors").detail)

    def test_no_vendor_at_all_names_none(self):
        report = self._detect({"sh"}, env={"OPENAI_API_KEY": "   "})
        self.assertFalse(report.available("review-vendors"))
        self.assertEqual(report.get("review-vendors").detail, "0 distinct vendor(s)")

    def test_both_names_are_part_of_the_capability_vocabulary(self):
        self.assertEqual(runtime.validate_names(("providers", "review-vendors"), source="x"), [])


if __name__ == "__main__":
    unittest.main()
