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


if __name__ == "__main__":
    unittest.main()
