"""Unit tests for GitHub transport resolution."""

import unittest

from keel import github_transport, runtime


def report(*caps: tuple[str, bool]) -> runtime.CapabilityReport:
    return runtime.CapabilityReport(
        tuple(runtime.Capability(name, available, "test", "test") for name, available in caps)
    )


class TestResolve(unittest.TestCase):
    def test_prefers_authenticated_gh(self):
        transport = github_transport.resolve(report(("gh", True), ("gh-auth", True)))
        self.assertEqual(transport.name, "gh")
        self.assertTrue(transport.available)
        self.assertTrue(transport.supports("pr_merge"))
        self.assertTrue(transport.supports("raw_actions_logs"))
        self.assertEqual(transport.degraded, ())

    def test_mcp_fallback_degrades_known_gaps(self):
        transport = github_transport.resolve(
            report(
                ("gh", False),
                ("gh-auth", False),
                ("github-mcp", True),
            )
        )
        self.assertEqual(transport.name, "mcp")
        self.assertTrue(transport.supports("issue_read"))
        self.assertTrue(transport.supports("comments"))
        self.assertFalse(transport.supports("pr_merge"))
        self.assertFalse(transport.supports("check_runs"))
        self.assertFalse(transport.supports("raw_actions_logs"))
        self.assertIn("check_runs", transport.degraded)
        self.assertIn("raw_actions_logs", transport.degraded)

    def test_no_transport_is_explicit(self):
        transport = github_transport.resolve(report(("gh", False), ("github-mcp", False)))
        self.assertEqual(transport.name, "none")
        self.assertFalse(transport.available)
        self.assertFalse(transport.supports("check_runs"))
        self.assertIn("no authenticated", transport.reason)

    def test_dict_shape_is_stable(self):
        data = github_transport.resolve(report(("gh", False))).as_dict()
        self.assertIn("transport", data)
        self.assertIn("capabilities", data)
        self.assertIn("check_runs", data["capabilities"])
        self.assertIn("normalized_fields", data)
        self.assertIn("mergeable_state", data["normalized_fields"])

    def test_render_omits_empty_reason(self):
        transport = github_transport.GitHubTransport("custom", True, {}, reason="")
        rendered = transport.render()
        self.assertNotIn("reason:", rendered)


if __name__ == "__main__":
    unittest.main()
