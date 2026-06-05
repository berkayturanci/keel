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


if __name__ == "__main__":
    unittest.main()
