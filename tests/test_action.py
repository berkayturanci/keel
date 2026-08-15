"""Unit tests for the official GitHub Action metadata (action.yml)."""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


class TestGitHubAction(unittest.TestCase):
    def setUp(self):
        self.action_path = ROOT / "action.yml"
        self.assertTrue(self.action_path.exists(), "action.yml must exist in repository root")
        self.data = yaml.safe_load(self.action_path.read_text(encoding="utf-8"))

    def test_metadata_fields(self):
        self.assertEqual(self.data["name"], "Keel Autonomous Delivery Action")
        self.assertEqual(self.data["author"], "Berkay Turancı")
        self.assertIn("description", self.data)
        self.assertIn("branding", self.data)
        self.assertEqual(self.data["branding"]["icon"], "anchor")

    def test_inputs_declared(self):
        inputs = self.data.get("inputs", {})
        expected_inputs = [
            "command",
            "config-path",
            "issue",
            "issues",
            "delegate",
            "reviewers",
            "jury",
            "live",
            "hotfix",
            "github-token",
            "gemini-api-key",
            "openai-api-key",
            "anthropic-api-key",
            "version",
        ]
        for key in expected_inputs:
            self.assertIn(key, inputs, f"input {key!r} must be declared in action.yml")

    def test_outputs_declared(self):
        outputs = self.data.get("outputs", {})
        self.assertIn("status", outputs)
        self.assertIn("decision", outputs)

    def test_composite_runs_block(self):
        runs = self.data.get("runs", {})
        self.assertEqual(runs.get("using"), "composite")
        steps = runs.get("steps", [])
        self.assertGreaterEqual(len(steps), 3)
        step_names = [s.get("name") for s in steps]
        self.assertIn("Set up Python", step_names)
        self.assertIn("Install Keel", step_names)
        self.assertIn("Run Keel Command", step_names)


if __name__ == "__main__":
    unittest.main()
