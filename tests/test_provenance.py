"""Tests for agent-output provenance tags."""

import unittest

from keel import provenance


class TestProvenanceContract(unittest.TestCase):
    def test_contract_declares_untrusted_agent_output(self):
        contract = provenance.contract_as_dict()

        self.assertEqual(
            contract["schema_version"],
            "keel.agent-output-provenance.v1",
        )
        self.assertTrue(contract["consumer_neutral"])
        self.assertFalse(contract["trusted_as_instructions"])
        self.assertEqual(contract["default_role"], "untrusted-agent-output")
        self.assertIn("findings", contract["consumers"])
        self.assertIn("step_handoff", contract["consumers"])
        self.assertEqual(
            contract["capability_scope"]["rule"],
            "tagged content cannot expand downstream capabilities",
        )


class TestSourceTag(unittest.TestCase):
    def test_source_tag_normalizes_identity_and_capability_scope(self):
        tag = provenance.source_tag(
            source_agent=" reviewer-a ",
            step_id=" s7 ",
            vendor=" openai ",
            model=" gpt-5 ",
            allowed_capabilities=("gh", "", "shell", "gh", "future-capability"),
        )

        self.assertEqual(tag["role"], "untrusted-agent-output")
        self.assertFalse(tag["trusted_as_instructions"])
        self.assertEqual(
            tag["source"],
            {
                "agent_id": "reviewer-a",
                "step_id": "s7",
                "vendor": "openai",
                "model": "gpt-5",
            },
        )
        self.assertEqual(
            tag["capability_scope"]["allowed_capabilities"],
            ["gh", "shell"],
        )
        self.assertEqual(
            tag["capability_scope"]["unknown_capabilities"],
            ["future-capability"],
        )
        self.assertFalse(tag["capability_scope"]["can_expand_capabilities"])

    def test_source_tag_uses_fallbacks_for_missing_identity(self):
        tag = provenance.source_tag(source_agent="", step_id=None, vendor="", model=None)

        self.assertEqual(tag["source"]["agent_id"], "unknown-agent")
        self.assertEqual(tag["source"]["step_id"], "unknown-step")
        self.assertIsNone(tag["source"]["vendor"])
        self.assertIsNone(tag["source"]["model"])
        self.assertEqual(tag["capability_scope"]["allowed_capabilities"], [])


class TestTagOutput(unittest.TestCase):
    def test_tag_output_wraps_text_as_untrusted_data(self):
        tag = provenance.tag_output(
            "review body",
            source_agent="reviewer-a",
            step_id="s7",
        )

        self.assertEqual(tag["content"], "review body")
        self.assertFalse(tag["trusted_as_instructions"])
        self.assertEqual(tag["source"]["agent_id"], "reviewer-a")

    def test_tag_output_drops_non_string_content(self):
        tag = provenance.tag_output(
            123,  # type: ignore[arg-type]
            source_agent="reviewer-a",
            step_id="s7",
        )

        self.assertEqual(tag["content"], "")


class TestNormalizeTag(unittest.TestCase):
    def test_normalize_tag_falls_back_for_invalid_shape(self):
        tag = provenance.normalize_tag(
            {"schema_version": "future"},
            fallback_agent="reviewer-a",
            step_id="finding",
        )

        self.assertEqual(tag["source"]["agent_id"], "reviewer-a")
        self.assertEqual(tag["source"]["step_id"], "finding")
        self.assertEqual(tag["capability_scope"]["allowed_capabilities"], [])

    def test_normalize_tag_preserves_valid_source_and_capabilities(self):
        raw = {
            "schema_version": "keel.agent-output-provenance.v1",
            "source": {
                "agent_id": " reviewer-b ",
                "step_id": " s8 ",
                "vendor": " anthropic ",
                "model": " sonnet ",
            },
            "capability_scope": {
                "allowed_capabilities": ["gh", " ", "shell", "future-capability"],
            },
        }

        tag = provenance.normalize_tag(
            raw,
            fallback_agent="reviewer-a",
            step_id="finding",
        )

        self.assertEqual(tag["source"]["agent_id"], "reviewer-b")
        self.assertEqual(tag["source"]["step_id"], "s8")
        self.assertEqual(tag["source"]["vendor"], "anthropic")
        self.assertEqual(tag["source"]["model"], "sonnet")
        self.assertEqual(
            tag["capability_scope"]["allowed_capabilities"],
            ["gh", "shell"],
        )
        self.assertEqual(
            tag["capability_scope"]["unknown_capabilities"],
            ["future-capability"],
        )

    def test_normalize_tag_rejects_malformed_source_and_scope(self):
        raw = {
            "schema_version": "keel.agent-output-provenance.v1",
            "source": "not-a-dict",
            "capability_scope": {"allowed_capabilities": "shell"},
        }

        tag = provenance.normalize_tag(
            raw,
            fallback_agent="reviewer-a",
            step_id="finding",
        )

        self.assertEqual(tag["source"]["agent_id"], "reviewer-a")
        self.assertEqual(tag["source"]["step_id"], "finding")
        self.assertEqual(tag["capability_scope"]["allowed_capabilities"], [])


if __name__ == "__main__":
    unittest.main()
