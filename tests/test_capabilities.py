"""Tests for capabilities and runtime requirement resolution."""

import unittest

from keel import capabilities, runtime
from keel import config as cfg


class TestCapabilities(unittest.TestCase):
    def test_validate_names_valid(self):
        errors = capabilities.validate_names(["git", "shell"], source="test")
        self.assertEqual(errors, [])

    def test_validate_names_invalid(self):
        errors = capabilities.validate_names(["unknown-cap"], source="test")
        self.assertEqual(len(errors), 1)
        self.assertIn("unknown capability 'unknown-cap'", errors[0])

    def test_ci_check_capability_requirement(self):
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.8",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="true", ci_workflows=["ci.yml"]),
        )
        req = runtime.ci_check_capability_requirement(config)
        self.assertIn("raw-actions-logs", req.optional)

    def test_morning_capability_requirement(self):
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.8",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="true"),
            policy_pack={
                "name": "test",
                "health_providers": {
                    "datadog": {
                        "required_capabilities": ["api-token"],
                        "optional_capabilities": ["browser"],
                    },
                    "ignored": "not-a-dict",
                },
            },
        )
        req = runtime.morning_capability_requirement(config)
        self.assertIn("api-token", req.required)
        self.assertIn("browser", req.optional)

    def test_scan_capability_requirement(self):
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.8",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="true"),
        )
        reg_req = runtime.scan_capability_requirement("regression", config)
        self.assertIn("worktree", reg_req.required)

        rad_req = runtime.scan_capability_requirement("review-all-day", config)
        self.assertNotIn("worktree", rad_req.required)
        self.assertIn("git", rad_req.required)

    def test_build_capability_requirement_gate_error(self):
        # When gates fail to plan, returns base requirement
        config = cfg.ProjectConfig(
            extends="keel",
            core_version="^0.8",
            base_branch="main",
            knobs=cfg.Knobs(build_gate_cmd="true"),
            gates={"invalid": {"kind": "unknown-kind"}},
        )
        req = runtime.build_capability_requirement("ship", config, {})
        self.assertEqual(req.required, ())


if __name__ == "__main__":
    unittest.main()
