"""Tests for pure branch-contract verification (base ancestry + worktree isolation)."""

import unittest

from keel import branchscope


def _verify(**overrides):
    facts = {
        "base_branch": "main",
        "head_sha": "head",
        "merge_base_sha": "tip",
        "base_tip_sha": "tip",
        "base_distance": 0,
        "worktree_path": "/repo/worktrees/issue-1",
        "repo_root": "/repo",
        "is_linked_worktree": True,
    }
    facts.update(overrides)
    return branchscope.verify(**facts)


class TestAncestry(unittest.TestCase):
    def test_up_to_date_base_passes(self):
        report = _verify(merge_base_sha="tip", base_tip_sha="tip", base_distance=0)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["verdict"], "ok")
        self.assertIsNone(report["note"])
        self.assertFalse(report["ancestry"]["advisory"])

    def test_within_tolerance_passes_even_if_behind(self):
        report = _verify(merge_base_sha="old", base_tip_sha="tip", base_distance=3, tolerance=5)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["ancestry"]["verdict"], "ok")

    def test_at_tolerance_boundary_passes(self):
        report = _verify(merge_base_sha="old", base_tip_sha="tip", base_distance=5, tolerance=5)
        self.assertEqual(report["status"], "pass")

    def test_stale_base_beyond_tolerance_fails(self):
        report = _verify(merge_base_sha="old", base_tip_sha="tip", base_distance=9, tolerance=5)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["verdict"], "stale")
        self.assertIn("base is stale", report["note"])
        self.assertIn("9 commit(s)", report["note"])

    def test_strict_tolerance_zero_fails_one_behind(self):
        report = _verify(merge_base_sha="old", base_tip_sha="tip", base_distance=1, tolerance=0)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["verdict"], "stale")

    def test_allow_stale_base_downgrades_to_advisory_pass(self):
        report = _verify(
            merge_base_sha="old", base_tip_sha="tip", base_distance=9,
            tolerance=5, allow_stale_base=True,
        )
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["verdict"], "stale")
        self.assertTrue(report["ancestry"]["advisory"])
        self.assertIn("downgraded to advisory by --allow-stale-base", report["note"])

    def test_unresolved_base_facts_skip_gracefully(self):
        for missing in ("merge_base_sha", "base_tip_sha", "base_distance"):
            report = _verify(**{missing: None})
            self.assertEqual(report["status"], "pass", missing)
            self.assertEqual(report["ancestry"]["verdict"], "unknown")
            self.assertTrue(report["ancestry"]["advisory"])
            self.assertIn("not resolved", report["ancestry"]["note"])


class TestIsolation(unittest.TestCase):
    def test_nested_linked_worktree_passes(self):
        report = _verify(
            worktree_path="/repo/worktrees/issue-1",
            repo_root="/repo",
            is_linked_worktree=True,
        )
        self.assertEqual(report["isolation"]["verdict"], "ok")
        self.assertEqual(report["status"], "pass")

    def test_primary_checkout_edit_is_contaminated(self):
        report = _verify(
            worktree_path="/repo", repo_root="/repo", is_linked_worktree=False,
        )
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["verdict"], "contaminated")
        self.assertIn("primary checkout", report["note"])

    def test_linked_but_outside_repo_root_is_contaminated(self):
        report = _verify(
            worktree_path="/elsewhere/wt", repo_root="/repo", is_linked_worktree=True,
        )
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["verdict"], "contaminated")
        self.assertIn("not nested under the repo root", report["note"])

    def test_allow_stale_base_does_not_rescue_contamination(self):
        # --allow-stale-base only downgrades a stale ancestry; contamination must
        # still fail (precedence contamination > stale), even with the escape on.
        report = _verify(
            merge_base_sha="old", base_tip_sha="tip", base_distance=9, tolerance=5,
            worktree_path="/repo", repo_root="/repo", is_linked_worktree=False,
            allow_stale_base=True,
        )
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["verdict"], "contaminated")

    def test_no_local_worktree_is_skipped(self):
        report = _verify(worktree_path=None, repo_root=None, is_linked_worktree=None)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["isolation"]["verdict"], "n/a")
        self.assertTrue(report["isolation"]["advisory"])

    def test_partial_worktree_facts_skip(self):
        report = _verify(worktree_path="/repo/wt", repo_root=None, is_linked_worktree=True)
        self.assertEqual(report["isolation"]["verdict"], "n/a")

    def test_path_equal_to_root_is_not_nested(self):
        # A worktree path equal to the root is the primary checkout, not nested.
        self.assertFalse(branchscope._is_nested("/repo", "/repo"))

    def test_windows_style_paths_normalize(self):
        self.assertTrue(branchscope._is_nested("C:\\repo\\wt", "C:\\repo"))

    def test_trailing_and_dot_segments_ignored(self):
        self.assertTrue(branchscope._is_nested("/repo/./wt/", "/repo/"))


class TestCombinedPrecedence(unittest.TestCase):
    def test_contaminated_overrides_stale(self):
        # When both checks fail, contamination is the headline verdict.
        report = _verify(
            merge_base_sha="old", base_tip_sha="tip", base_distance=9, tolerance=5,
            worktree_path="/repo", repo_root="/repo", is_linked_worktree=False,
        )
        self.assertEqual(report["verdict"], "contaminated")
        self.assertEqual(report["status"], "fail")
        self.assertIn("primary checkout", report["note"])

    def test_stale_note_surfaced_over_isolation_skip(self):
        report = _verify(
            merge_base_sha="old", base_tip_sha="tip", base_distance=9, tolerance=5,
            worktree_path=None, repo_root=None, is_linked_worktree=None,
        )
        self.assertEqual(report["verdict"], "stale")
        self.assertIn("base is stale", report["note"])

    def test_clean_report_shape(self):
        report = _verify()
        self.assertEqual(report["schema_version"], branchscope.SCHEMA_VERSION)
        self.assertEqual(report["base_branch"], "main")
        self.assertFalse(report["allow_stale_base"])
        self.assertEqual(report["tolerance"], branchscope.DEFAULT_BASE_DISTANCE)
        self.assertEqual(report["verdict"], "ok")


if __name__ == "__main__":
    unittest.main()
