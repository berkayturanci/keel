"""Unit tests for the pure blocker ruleset (:mod:`keel.guard`)."""

import dataclasses
import unittest

from keel import config as cfg
from keel import guard


def _config(policy_pack=None):
    data = {
        "extends": "keel",
        "core_version": "1.0.0",
        "base_branch": "main",
        "knobs": {"build_gate_cmd": "true"},
    }
    if policy_pack is not None:
        pack = dict(policy_pack)
        pack.setdefault("name", "test-pack")
        data["policy_pack"] = pack
    return cfg.parse_config(data)


class TestDefaultRules(unittest.TestCase):
    def setUp(self):
        self.rules = guard.resolve_rules(None)

    def test_blocker_label_matches(self):
        result = guard.evaluate("Routine cleanup", ["blocker"], rules=self.rules)
        self.assertTrue(result.is_blocker)
        self.assertEqual(result.matched, ("blocker-label",))

    def test_hotfix_label_matches_case_insensitive(self):
        result = guard.evaluate("Routine", ["HotFix"], rules=self.rules)
        self.assertEqual(result.matched, ("hotfix-label",))

    def test_security_label_matches(self):
        result = guard.evaluate("Routine", ["security"], rules=self.rules)
        self.assertEqual(result.matched, ("security-label",))

    def test_title_regex_matches_word_boundary(self):
        result = guard.evaluate("hotfix: patch the boot loop", [], rules=self.rules)
        self.assertEqual(result.matched, ("blocker-title-regex",))

    def test_title_regex_respects_word_boundary(self):
        # "securityish" must NOT match \bsecurity\b's standalone word intent.
        result = guard.evaluate("securityishhh refactor", [], rules=self.rules)
        self.assertEqual(result.matched, ())

    def test_no_match_returns_empty(self):
        result = guard.evaluate("Tidy up docs", ["chore"], rules=self.rules)
        self.assertFalse(result.is_blocker)
        self.assertEqual(result.matched, ())

    def test_multiple_rules_fire_in_order(self):
        result = guard.evaluate("security: token leak", ["blocker"], rules=self.rules)
        self.assertEqual(
            result.matched, ("blocker-label", "blocker-title-regex")
        )

    def test_as_dict_is_structured(self):
        result = guard.evaluate("hotfix: x", ["blocker"], rules=self.rules)
        payload = result.as_dict()
        self.assertEqual(payload["schema_version"], guard.GUARD_SCHEMA_VERSION)
        self.assertEqual(payload["is_blocker"], True)
        self.assertEqual(payload["labels"], ["blocker"])
        self.assertEqual(payload["title"], "hotfix: x")
        self.assertEqual(
            payload["rule_ids"],
            ["blocker-label", "hotfix-label", "security-label", "blocker-title-regex"],
        )


class TestResolveRules(unittest.TestCase):
    def test_absent_policy_pack_uses_defaults(self):
        rules = guard.resolve_rules(_config())
        self.assertEqual(rules[0].id, "blocker-label")

    def test_empty_blocker_rules_uses_defaults(self):
        rules = guard.resolve_rules(_config({"blocker_rules": []}))
        self.assertEqual(len(rules), len(guard.DEFAULT_RULES))

    def test_non_list_blocker_rules_uses_defaults(self):
        # A non-list value can't pass the schema, so inject it directly to cover
        # resolve_rules' defensive fallback.
        config = dataclasses.replace(
            _config(), policy_pack={"name": "x", "blocker_rules": "nope"}
        )
        rules = guard.resolve_rules(config)
        self.assertEqual(len(rules), len(guard.DEFAULT_RULES))

    def test_non_dict_policy_pack_uses_defaults(self):
        config = dataclasses.replace(_config(), policy_pack=[])
        self.assertEqual(len(guard.resolve_rules(config)), len(guard.DEFAULT_RULES))

    def test_configured_label_and_regex_rules(self):
        rules = guard.resolve_rules(_config({"blocker_rules": [
            {"id": "p0", "kind": "label", "labels": ["P0"]},
            {"id": "urgent", "kind": "title-regex", "pattern": r"\burgent\b"},
        ]}))
        self.assertEqual([r.id for r in rules], ["p0", "urgent"])
        result = guard.evaluate("an urgent fix", ["other"], rules=rules)
        self.assertEqual(result.matched, ("urgent",))
        result2 = guard.evaluate("calm", ["P0"], rules=rules)
        self.assertEqual(result2.matched, ("p0",))

    def test_evaluate_config_helper(self):
        result = guard.evaluate_config("hotfix: x", [], config=_config())
        self.assertEqual(result.matched, ("blocker-title-regex",))


class TestMalformedRules(unittest.TestCase):
    # Malformed rules can't pass the config schema, so these inject the raw
    # policy_pack directly to cover resolve_rules' fail-closed validation.
    def _config_raw(self, rules):
        return dataclasses.replace(
            _config(), policy_pack={"name": "x", "blocker_rules": rules}
        )

    def _err(self, rule):
        with self.assertRaises(guard.GuardError) as ctx:
            guard.resolve_rules(self._config_raw([rule]))
        return str(ctx.exception)

    def test_non_object_rule(self):
        with self.assertRaises(guard.GuardError):
            guard.resolve_rules(self._config_raw(["nope"]))

    def test_missing_id(self):
        self.assertIn("missing non-empty 'id'", self._err({"kind": "label", "labels": ["x"]}))

    def test_blank_id(self):
        self.assertIn(
            "missing non-empty 'id'", self._err({"id": "  ", "kind": "label", "labels": ["x"]})
        )

    def test_duplicate_id(self):
        with self.assertRaises(guard.GuardError) as ctx:
            guard.resolve_rules(self._config_raw([
                {"id": "dup", "kind": "label", "labels": ["a"]},
                {"id": "dup", "kind": "label", "labels": ["b"]},
            ]))
        self.assertIn("duplicate rule id", str(ctx.exception))

    def test_label_rule_missing_labels(self):
        self.assertIn("non-empty 'labels'", self._err({"id": "x", "kind": "label"}))

    def test_label_rule_empty_labels(self):
        self.assertIn(
            "non-empty 'labels'", self._err({"id": "x", "kind": "label", "labels": []})
        )

    def test_title_regex_missing_pattern(self):
        self.assertIn("non-empty 'pattern'", self._err({"id": "x", "kind": "title-regex"}))

    def test_title_regex_invalid_pattern(self):
        self.assertIn(
            "invalid regex", self._err({"id": "x", "kind": "title-regex", "pattern": "("})
        )

    def test_unknown_kind(self):
        self.assertIn("unknown rule kind", self._err({"id": "x", "kind": "bogus"}))


if __name__ == "__main__":
    unittest.main()
