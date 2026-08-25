"""Tests for the pure consent-boundary reconciliation."""

import unittest

from keel import consentverify


def _ledger_record(*, status="approved", scopes=None):
    record = {
        "schema_version": "keel.run-ledger.v1",
        "record_type": "ship_run",
        "pull_request": {"number": 7},
        "run_context": {
            "consent": {
                "status": status,
                "scopes": list(scopes) if scopes is not None else [],
            },
        },
    }
    return record


class TestRequiredScopesForEffect(unittest.TestCase):
    def test_pr_exists_requires_git_and_github(self):
        self.assertEqual(
            consentverify.required_scopes_for_effect("pr_exists"),
            ("git", "github"),
        )

    def test_comment_merge_label_require_github(self):
        self.assertEqual(consentverify.required_scopes_for_effect("comment"), ("github",))
        self.assertEqual(consentverify.required_scopes_for_effect("merged"), ("github",))
        self.assertEqual(consentverify.required_scopes_for_effect("label"), ("github",))

    def test_pr_created_requires_github(self):
        self.assertEqual(consentverify.required_scopes_for_effect("pr_created"), ("github",))

    def test_unknown_effect_raises(self):
        with self.assertRaises(ValueError) as ctx:
            consentverify.required_scopes_for_effect("teleport")
        self.assertIn("unknown observed effect", str(ctx.exception))


class TestScopeEffectTable(unittest.TestCase):
    def test_table_lists_every_effect_with_scopes(self):
        table = consentverify.scope_effect_table()
        self.assertEqual(table["pr_exists"], ["git", "github"])
        self.assertEqual(table["comment"], ["github"])
        self.assertEqual(table["merged"], ["github"])
        self.assertEqual(table["label"], ["github"])
        self.assertEqual(set(table), set(consentverify.OBSERVED_EFFECT_KINDS))


class TestObservedEffects(unittest.TestCase):
    def test_default_is_empty(self):
        self.assertEqual(consentverify.ObservedEffects().as_kinds(), ())

    def test_all_flags_produce_stable_order(self):
        observed = consentverify.ObservedEffects(
            pr_exists=True, commented=True, merged=True, labeled=True
        )
        self.assertEqual(observed.as_kinds(), ("pr_exists", "comment", "merged", "label"))


class TestReconcile(unittest.TestCase):
    def test_all_effects_within_scopes_passes(self):
        observed = consentverify.ObservedEffects(
            pr_exists=True, commented=True, merged=True, labeled=True
        )
        report = consentverify.reconcile(observed, ["git", "github"], has_consent_record=True)
        self.assertEqual(report["schema_version"], consentverify.SCHEMA_VERSION)
        self.assertEqual(report["verdict"], consentverify.VERDICT_PASS)
        self.assertTrue(report["ok"])
        self.assertEqual(report["uncovered"], [])
        self.assertEqual(report["summary"]["observed"], 4)
        self.assertEqual(report["summary"]["covered"], 4)
        self.assertEqual(report["summary"]["uncovered"], 0)

    def test_merged_without_github_scope_fails_naming_the_effect(self):
        observed = consentverify.ObservedEffects(pr_exists=True, merged=True)
        report = consentverify.reconcile(observed, ["git"], has_consent_record=True)
        self.assertEqual(report["verdict"], consentverify.VERDICT_FAIL)
        self.assertFalse(report["ok"])
        effects = {finding["effect"] for finding in report["uncovered"]}
        self.assertIn("merged", effects)
        self.assertIn("pr_exists", effects)
        merged = next(f for f in report["uncovered"] if f["effect"] == "merged")
        self.assertIn("mutation merged not covered by approved consent scopes", merged["message"])
        self.assertEqual(merged["missing_scopes"], ["github"])

    def test_push_uncovered_when_no_git_scope_fails(self):
        observed = consentverify.ObservedEffects(pr_exists=True)
        report = consentverify.reconcile(observed, ["github"], has_consent_record=True)
        self.assertEqual(report["verdict"], consentverify.VERDICT_FAIL)
        finding = report["uncovered"][0]
        self.assertEqual(finding["effect"], "pr_exists")
        self.assertEqual(finding["missing_scopes"], ["git"])
        self.assertIn("mutation pr_exists not covered", finding["message"])

    def test_comment_uncovered_fails(self):
        observed = consentverify.ObservedEffects(pr_exists=True, commented=True)
        report = consentverify.reconcile(observed, ["git"], has_consent_record=True)
        self.assertEqual(report["verdict"], consentverify.VERDICT_FAIL)
        effects = {finding["effect"] for finding in report["uncovered"]}
        self.assertIn("comment", effects)

    def test_no_consent_record_is_advisory_even_when_uncovered(self):
        observed = consentverify.ObservedEffects(pr_exists=True, merged=True)
        report = consentverify.reconcile(observed, [], has_consent_record=False)
        self.assertEqual(report["verdict"], consentverify.VERDICT_ADVISORY)
        self.assertTrue(report["ok"])
        self.assertEqual(report["uncovered"], [])
        # Effects are still reported as not-covered for visibility.
        self.assertFalse(report["effects"][0]["covered"])

    def test_matching_record_with_full_scopes_passes(self):
        observed = consentverify.ObservedEffects(pr_exists=True, merged=True)
        report = consentverify.reconcile(observed, ["git", "github"], has_consent_record=True)
        self.assertEqual(report["verdict"], consentverify.VERDICT_PASS)
        self.assertTrue(report["ok"])

    def test_none_approved_scopes_normalizes_to_empty(self):
        observed = consentverify.ObservedEffects()
        report = consentverify.reconcile(observed, None, has_consent_record=True)
        self.assertEqual(report["approved_scopes"], [])
        self.assertEqual(report["verdict"], consentverify.VERDICT_PASS)


class TestConsentRecordFromLedger(unittest.TestCase):
    def test_record_with_status_and_scopes(self):
        has_record, scopes = consentverify.consent_record_from_ledger(
            _ledger_record(status="approved", scopes=["git", "github"])
        )
        self.assertTrue(has_record)
        self.assertEqual(scopes, ("git", "github"))

    def test_blank_status_is_no_record(self):
        has_record, scopes = consentverify.consent_record_from_ledger(
            _ledger_record(status="  ", scopes=["git"])
        )
        self.assertFalse(has_record)
        self.assertEqual(scopes, ("git",))

    def test_none_status_is_no_record(self):
        has_record, scopes = consentverify.consent_record_from_ledger(
            _ledger_record(status=None, scopes=[])
        )
        self.assertFalse(has_record)
        self.assertEqual(scopes, ())

    def test_none_record_is_no_record(self):
        has_record, scopes = consentverify.consent_record_from_ledger(None)
        self.assertFalse(has_record)
        self.assertEqual(scopes, ())

    def test_missing_run_context_is_no_record(self):
        has_record, scopes = consentverify.consent_record_from_ledger({"record_type": "ship_run"})
        self.assertFalse(has_record)
        self.assertEqual(scopes, ())

    def test_missing_consent_block_is_no_record(self):
        has_record, scopes = consentverify.consent_record_from_ledger({"run_context": {}})
        self.assertFalse(has_record)
        self.assertEqual(scopes, ())

    def test_malformed_scopes_degrade_to_empty(self):
        has_record, scopes = consentverify.consent_record_from_ledger(
            {"run_context": {"consent": {"status": "approved", "scopes": "git"}}}
        )
        self.assertTrue(has_record)
        self.assertEqual(scopes, ())

    def test_blank_scope_entries_filtered(self):
        has_record, scopes = consentverify.consent_record_from_ledger(
            {"run_context": {"consent": {"status": "approved", "scopes": ["git", "  "]}}}
        )
        self.assertTrue(has_record)
        self.assertEqual(scopes, ("git",))


if __name__ == "__main__":
    unittest.main()
