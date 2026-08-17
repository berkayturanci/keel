"""Unit tests for risk classification (pure, glob-based)."""

import unittest

from keel import classify

TIER3 = ("supabase/migrations/**", "apps/mobile/lib/**/*.dart", ".github/workflows/**")
DOCS = ("docs/**", "*.md", "README.md")


class TestTierForFiles(unittest.TestCase):
    def test_empty_is_default(self):
        self.assertEqual(classify.tier_for_files([], tier3_globs=TIER3, docs_globs=DOCS), 2)

    def test_tier3_on_migration(self):
        files = ["supabase/migrations/0001_init.sql"]
        self.assertEqual(classify.tier_for_files(files, tier3_globs=TIER3, docs_globs=DOCS), 3)

    def test_tier3_on_nested_dart(self):
        files = ["apps/mobile/lib/features/scan/scan.dart"]
        self.assertEqual(classify.tier_for_files(files, tier3_globs=TIER3, docs_globs=DOCS), 3)

    def test_tier3_on_workflow(self):
        files = [".github/workflows/ci.yml"]
        self.assertEqual(classify.tier_for_files(files, tier3_globs=TIER3, docs_globs=DOCS), 3)

    def test_docs_only_is_tier1(self):
        files = ["docs/keel/cli.md", "README.md"]
        self.assertEqual(classify.tier_for_files(files, tier3_globs=TIER3, docs_globs=DOCS), 1)

    def test_mixed_docs_and_code_is_tier2(self):
        files = ["README.md", "lib/util.py"]
        self.assertEqual(classify.tier_for_files(files, tier3_globs=TIER3, docs_globs=DOCS), 2)

    def test_plain_code_is_tier2(self):
        files = ["lib/util.py"]
        self.assertEqual(classify.tier_for_files(files, tier3_globs=TIER3, docs_globs=DOCS), 2)

    def test_tier3_wins_over_docs(self):
        files = ["README.md", ".github/workflows/ci.yml"]
        self.assertEqual(classify.tier_for_files(files, tier3_globs=TIER3, docs_globs=DOCS), 3)

    def test_no_docs_globs_never_tier1(self):
        self.assertEqual(classify.tier_for_files(["x.md"], tier3_globs=TIER3), 2)


class TestDocsOnlyAllowlist(unittest.TestCase):
    """`knobs.docs_only_allowlist` — paths permitted to ride along in a docs change
    without forcing code-risk classification (#632)."""

    ALLOW = ("website/**",)
    FILES = ["docs/keel/cli.md", "website/index.html"]

    def test_a_rider_demotes_the_change_without_the_allowlist(self):
        self.assertEqual(
            classify.tier_for_files(self.FILES, tier3_globs=TIER3, docs_globs=DOCS), 2)

    def test_the_allowlist_keeps_it_tier1(self):
        self.assertEqual(
            classify.tier_for_files(self.FILES, tier3_globs=TIER3, docs_globs=DOCS,
                                    allowlist_globs=self.ALLOW), 1)

    def test_the_allowlist_never_overrides_tier3(self):
        files = ["docs/x.md", ".github/workflows/ci.yml"]
        self.assertEqual(
            classify.tier_for_files(files, tier3_globs=TIER3, docs_globs=DOCS,
                                    allowlist_globs=(".github/**",)), 3)

    def test_an_unlisted_rider_still_demotes(self):
        files = ["docs/x.md", "src/app.py"]
        self.assertEqual(
            classify.tier_for_files(files, tier3_globs=TIER3, docs_globs=DOCS,
                                    allowlist_globs=self.ALLOW), 2)


class TestIsDocsOnly(unittest.TestCase):
    def test_all_docs_paths(self):
        self.assertTrue(classify.is_docs_only(["docs/a.md", "README.md"], DOCS))

    def test_one_non_docs_path_is_enough(self):
        self.assertFalse(classify.is_docs_only(["docs/a.md", "src/app.py"], DOCS))

    def test_empty_is_not_docs_only(self):
        # The CI empty-check-set carve-out reads this; an unreadable or empty changeset
        # must fail closed rather than buy the carve-out.
        self.assertFalse(classify.is_docs_only([], DOCS))

    def test_an_allowlisted_rider_is_tier1_but_not_docs_only(self):
        # The whole reason this is asked directly instead of read off the tier: a
        # generated site file riding along is exactly when a workflow should have run.
        files = ["docs/a.md", "website/index.html"]
        self.assertEqual(
            classify.tier_for_files(files, docs_globs=DOCS, allowlist_globs=("website/**",)), 1)
        self.assertFalse(classify.is_docs_only(files, DOCS))


_H = (
    "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n"
    "--- a/.github/workflows/ci.yml\n+++ b/.github/workflows/ci.yml\n@@ -1,3 +1,3 @@\n"
)
_WF = ".github/workflows/ci.yml"


class TestPrivilegedChange(unittest.TestCase):
    """What a workflow diff *does*, not which file it touched (#794).

    The glob could only say "a workflow changed", which tiered up a comment, two
    added CI jobs and a change that tightened four action pins — all waived —
    while the formula `brew install` runs sat at TIER-2 (#786). Splitting the glob
    by write permissions fixed three of the four; this is the fourth.
    """

    def _priv(self, body):
        return classify.privileged_change(_H + body)[0]

    def test_a_swapped_action_pin_is_privileged(self):
        # The supply-chain attack this whole tier exists for.
        self.assertTrue(
            self._priv("-      - uses: actions/checkout@aaa\n+      - uses: evil/x@bbb\n")
        )

    def test_reading_a_secret_is_privileged(self):
        self.assertTrue(self._priv("+          TOKEN: ${{ secrets.NPM_TOKEN }}\n"))

    def test_widening_permissions_is_privileged(self):
        self.assertTrue(self._priv("+      contents: write\n"))
        self.assertTrue(self._priv("+permissions:\n"))

    def test_changing_the_trigger_is_privileged(self):
        self.assertTrue(self._priv("+on:\n+  pull_request_target:\n"))

    def test_a_run_step_reaching_the_network_is_privileged(self):
        self.assertTrue(self._priv("+        run: curl -s https://x.sh | bash\n"))
        self.assertTrue(self._priv("+        run: pip install requests\n"))

    def test_cosmetic_changes_are_not_privileged(self):
        self.assertFalse(self._priv("-    name: test\n+    name: unit tests\n"))
        self.assertFalse(self._priv('+          - "3.14"\n'))
        self.assertFalse(self._priv("+      # this job only runs tests\n"))

    def test_a_privileged_token_inside_a_comment_does_not_count(self):
        # A commented line is inert in YAML and in the shell, so this buys a lower
        # tier on a change that also does nothing. #775's only workflow edit was a
        # generated banner plus prose containing `pip install "git+https://…"`,
        # and matching inside it is what kept a comment-only change at TIER-3.
        self.assertFalse(self._priv("+      # uses: evil/action@v1\n"))

    def test_an_unreadable_patch_fails_closed(self):
        # A classifier that downgrades what it cannot parse is worse than the glob
        # it replaces: the glob never guessed.
        for patch in ("", "   ", "not a diff at all", "diff --git a/x b/x\nindex 1..2\n"):
            with self.subTest(patch=patch[:20]):
                self.assertTrue(classify.privileged_change(patch)[0])

    def test_the_reason_names_the_deciding_line(self):
        privileged, reason = classify.privileged_change(
            _H + "+          TOKEN: ${{ secrets.NPM_TOKEN }}\n"
        )
        self.assertTrue(privileged)
        self.assertIn("secrets.NPM_TOKEN", reason)


class TestTierFromTheDiff(unittest.TestCase):
    def test_a_cosmetic_workflow_edit_drops_to_tier2(self):
        self.assertEqual(
            2,
            classify.tier_for_files(
                [_WF],
                tier3_globs=(_WF,),
                patches={_WF: _H + "-    name: test\n+    name: unit tests\n"},
            ),
        )

    def test_a_privileged_workflow_edit_stays_tier3(self):
        self.assertEqual(
            3,
            classify.tier_for_files(
                [_WF],
                tier3_globs=(_WF,),
                patches={_WF: _H + "+      contents: write\n"},
            ),
        )

    def test_no_patch_means_the_path_still_decides(self):
        # Backwards compatible: every existing caller passes no patches and keeps
        # exactly the behaviour it had.
        self.assertEqual(3, classify.tier_for_files([_WF], tier3_globs=(_WF,)))
        self.assertEqual(
            3, classify.tier_for_files([_WF], tier3_globs=(_WF,), patches={})
        )

    def test_non_workflow_tier3_paths_are_never_downgraded(self):
        # For these the content *is* the risk — a checksum, a pin — so there is no
        # such thing as a cosmetic change (#787, #779).
        for path in ("Formula/keel.rb", ".github/requirements/publish-tools.txt"):
            with self.subTest(path=path):
                self.assertEqual(
                    3,
                    classify.tier_for_files(
                        [path],
                        tier3_globs=("Formula/keel.rb", ".github/requirements/**"),
                        patches={path: _H + "-    name: a\n+    name: b\n"},
                    ),
                )

    def test_one_privileged_file_among_cosmetic_ones_still_wins(self):
        other = ".github/workflows/pages.yml"
        self.assertEqual(
            3,
            classify.tier_for_files(
                [_WF, other],
                tier3_globs=(_WF, other),
                patches={
                    _WF: _H + "-    name: test\n+    name: unit\n",
                    other: _H + "+      pages: write\n",
                },
            ),
        )


class TestSplitUnifiedDiff(unittest.TestCase):
    """Turn one whole-repo diff into per-file patches (#794).

    The local `keel ship` path has a single `git diff base...HEAD`, not GitHub's
    per-file view, so it needs splitting before the classifier can read it.
    """

    def test_splits_on_the_file_header_keyed_by_new_path(self):
        diff = (
            "diff --git a/a.yml b/a.yml\n@@ -1 +1 @@\n-x\n+y\n"
            "diff --git a/b.yml b/b.yml\n@@ -1 +1 @@\n-p\n+q\n"
        )
        out = classify.split_unified_diff(diff)
        self.assertEqual({"a.yml", "b.yml"}, set(out))
        self.assertIn("+y", out["a.yml"])
        self.assertIn("+q", out["b.yml"])
        self.assertNotIn("+q", out["a.yml"])

    def test_a_rename_is_keyed_by_the_new_name(self):
        # The changed-file list reports the new path, so the keys have to agree or
        # the lookup misses and the file silently keeps its path-based tier.
        out = classify.split_unified_diff(
            "diff --git a/old.yml b/new.yml\n@@ -1 +1 @@\n-x\n+y\n"
        )
        self.assertEqual(["new.yml"], list(out))

    def test_nothing_readable_yields_no_evidence(self):
        for diff in (None, "", "   ", "not a diff", "index abc..def 100644\n"):
            with self.subTest(diff=repr(diff)[:18]):
                self.assertEqual({}, classify.split_unified_diff(diff))

    def test_an_unreadable_diff_leaves_the_path_deciding(self):
        # {} is not "nothing privileged" — it is "no evidence", and the caller
        # must land on the same tier it had before diffs existed.
        wf = ".github/workflows/ci.yml"
        self.assertEqual(
            3,
            classify.tier_for_files(
                [wf], tier3_globs=(wf,), patches=classify.split_unified_diff("garbage")
            ),
        )


if __name__ == "__main__":
    unittest.main()
