"""Tests for shared signal-driven work creation policy."""

import unittest

from keel import workcreation


class TestWorkCreationContract(unittest.TestCase):
    def test_contract_exposes_shared_policy(self):
        contract = workcreation.contract_as_dict()

        self.assertEqual(contract["schema_version"], "keel.work-creation.v1")
        self.assertTrue(contract["consumer_neutral"])
        self.assertEqual(contract["transient_filter"]["transient_outcome"],
                         "suppress-transient")
        self.assertEqual(contract["dedupe"]["duplicate_outcome"], "suppress-duplicate")
        self.assertEqual(contract["cycle_limit"]["limit_outcome"], "limit-reached")
        self.assertIn("regression", contract["consumers"])
        self.assertIn("flake-audit", contract["consumers"])


class TestWorkCreationEvaluation(unittest.TestCase):
    def test_clean_candidate_creates_work(self):
        report = workcreation.evaluate_candidates([
            {
                "id": "signal-1",
                "title": "Fix payment flow crash",
                "body": "Crash reproduced twice.",
                "occurrences": 2,
                "confidence": 0.9,
            }
        ])

        self.assertEqual(report["summary"]["create"], 1)
        self.assertEqual(report["decisions"][0]["decision"], "create")
        self.assertTrue(report["decisions"][0]["creates_issue"])

    def test_transient_signal_is_suppressed_by_occurrence_floor(self):
        report = workcreation.evaluate_candidates([
            {
                "id": "blip",
                "title": "One-off CI failure",
                "occurrences": 1,
                "confidence": 1.0,
            }
        ])

        self.assertEqual(report["summary"]["suppress_transient"], 1)
        self.assertEqual(report["decisions"][0]["reason"], "transient-signal")

    def test_low_confidence_signal_is_transient(self):
        report = workcreation.evaluate_candidates([
            {
                "id": "weak",
                "title": "Possible UI drift",
                "occurrences": 5,
                "confidence": 0.2,
            }
        ])

        self.assertEqual(report["decisions"][0]["decision"], "suppress-transient")

    def test_duplicate_by_dedupe_key_is_suppressed(self):
        report = workcreation.evaluate_candidates(
            [
                {
                    "id": "cve",
                    "title": "Upgrade package for CVE-1",
                    "dedupe_key": "cve:CVE-1",
                    "occurrences": 3,
                    "confidence": 1.0,
                }
            ],
            existing_work=[
                {
                    "number": 10,
                    "state": "open",
                    "title": "Existing security issue",
                    "dedupe_key": "cve:CVE-1",
                }
            ],
        )

        self.assertEqual(report["decisions"][0]["decision"], "suppress-duplicate")
        self.assertEqual(report["decisions"][0]["duplicate_of"], 10)

    def test_duplicate_by_normalized_title_is_suppressed(self):
        report = workcreation.evaluate_candidates(
            [
                {
                    "title": "Fix   checkout   crash!",
                    "occurrences": 2,
                    "confidence": 1.0,
                }
            ],
            existing_work=[
                {
                    "number": 11,
                    "state": "open",
                    "title": "fix checkout crash",
                }
            ],
        )

        self.assertEqual(report["decisions"][0]["duplicate_of"], 11)

    def test_duplicate_by_near_text_is_suppressed(self):
        report = workcreation.evaluate_candidates(
            [
                {
                    "title": "Regression in save flow",
                    "body": "save button fails after sync completes",
                    "occurrences": 2,
                    "confidence": 1.0,
                }
            ],
            existing_work=[
                {
                    "number": 12,
                    "state": "open",
                    "title": "Save flow regression",
                    "body": "save button fails after sync completes",
                }
            ],
        )

        self.assertEqual(report["decisions"][0]["decision"], "suppress-duplicate")
        self.assertEqual(report["decisions"][0]["duplicate_of"], 12)

    def test_closed_existing_work_does_not_suppress_new_work(self):
        report = workcreation.evaluate_candidates(
            [
                {
                    "title": "Fix closed issue recurrence",
                    "dedupe_key": "bug:closed",
                    "occurrences": 2,
                    "confidence": 1.0,
                }
            ],
            existing_work=[
                {
                    "number": 13,
                    "state": "closed",
                    "title": "Fix closed issue recurrence",
                    "dedupe_key": "bug:closed",
                }
            ],
        )

        self.assertEqual(report["decisions"][0]["decision"], "create")

    def test_missing_existing_state_suppresses_duplicates_conservatively(self):
        for existing in (
            {"number": 14, "title": "Fix duplicate signal"},
            {"number": 15, "state": None, "title": "Fix duplicate signal"},
        ):
            with self.subTest(existing=existing):
                report = workcreation.evaluate_candidates(
                    [
                        {
                            "title": "Fix duplicate signal",
                            "occurrences": 2,
                            "confidence": 1.0,
                        }
                    ],
                    existing_work=[existing],
                )

                self.assertEqual(report["decisions"][0]["decision"], "suppress-duplicate")
                self.assertEqual(report["decisions"][0]["duplicate_of"], existing["number"])

    def test_per_cycle_limit_is_enforced(self):
        report = workcreation.evaluate_candidates(
            [
                {"id": "one", "title": "One", "occurrences": 2, "confidence": 1.0},
                {"id": "two", "title": "Two", "occurrences": 2, "confidence": 1.0},
                {"id": "three", "title": "Three", "occurrences": 2, "confidence": 1.0},
            ],
            max_creations=2,
        )

        self.assertEqual([item["decision"] for item in report["decisions"]], [
            "create",
            "create",
            "limit-reached",
        ])
        self.assertEqual(report["summary"]["limit_reached"], 1)

    def test_created_items_dedupe_later_candidates_in_same_cycle(self):
        report = workcreation.evaluate_candidates([
            {
                "id": "first",
                "title": "Duplicate within cycle",
                "dedupe_key": "same",
                "occurrences": 2,
                "confidence": 1.0,
            },
            {
                "id": "second",
                "title": "Duplicate within cycle again",
                "dedupe_key": "same",
                "occurrences": 2,
                "confidence": 1.0,
            },
        ])

        self.assertEqual(report["decisions"][0]["decision"], "create")
        self.assertEqual(report["decisions"][1]["decision"], "suppress-duplicate")
        self.assertEqual(report["decisions"][1]["duplicate_of"], -1)

    def test_invalid_policy_inputs_fall_back_to_defaults(self):
        report = workcreation.evaluate_candidates(
            [
                {"title": "", "occurrences": 0, "confidence": 2.0},
            ],
            min_occurrences=0,
            min_confidence=2.0,
            max_creations=0,
            near_text_similarity=2.0,
        )

        self.assertEqual(report["policy"]["min_occurrences"],
                         workcreation.DEFAULT_MIN_OCCURRENCES)
        self.assertEqual(report["policy"]["min_confidence"],
                         workcreation.DEFAULT_MIN_CONFIDENCE)
        self.assertEqual(report["policy"]["max_creations"],
                         workcreation.DEFAULT_MAX_CREATIONS)
        self.assertEqual(report["policy"]["near_text_similarity"],
                         workcreation.DEFAULT_NEAR_TEXT_SIMILARITY)
        self.assertEqual(report["decisions"][0]["candidate_id"], "candidate-1")

    def test_non_dict_candidates_are_ignored(self):
        report = workcreation.evaluate_candidates([
            "not-a-candidate",
            {"title": "Valid", "occurrences": 2, "confidence": 1.0},
        ])

        self.assertEqual(report["summary"]["candidates"], 1)
        self.assertEqual(report["summary"]["create"], 1)

    def test_empty_text_never_near_text_duplicates(self):
        report = workcreation.evaluate_candidates(
            [
                {"title": "", "body": "", "occurrences": 2, "confidence": 1.0},
            ],
            existing_work=[
                {"number": 14, "state": "open", "title": "", "body": ""},
            ],
        )

        self.assertEqual(report["decisions"][0]["decision"], "create")


if __name__ == "__main__":
    unittest.main()
