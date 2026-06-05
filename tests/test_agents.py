"""Unit tests for agent dispatch + attribution."""

import unittest

from keel import agents
from keel import config as cfg

CONFIG = cfg.parse_config({
    "extends": "keel",
    "core_version": "^0.1",
    "base_branch": "main",
    "knobs": {
        "build_gate_cmd": "make test",
        "implementer_agents": {"mobile": "flutter-developer", "backend": "supabase-developer"},
    },
})


class TestSplitDelegate(unittest.TestCase):
    def test_with_model(self):
        self.assertEqual(agents.split_delegate("ollama:qwen2.5"), ("ollama", "qwen2.5"))

    def test_without_model(self):
        self.assertEqual(agents.split_delegate("codex"), ("codex", None))

    def test_empty_model_after_colon(self):
        self.assertEqual(agents.split_delegate("ollama:"), ("ollama", None))


class TestResolveAgent(unittest.TestCase):
    def test_delegate_wins(self):
        self.assertEqual(
            agents.resolve_agent(CONFIG, role="mobile", delegate="codex"), "codex"
        )

    def test_role_mapping(self):
        self.assertEqual(agents.resolve_agent(CONFIG, role="mobile"), "flutter-developer")

    def test_unknown_role_falls_back_to_host(self):
        self.assertEqual(
            agents.resolve_agent(CONFIG, role="desktop", host_agent="agy"), "agy"
        )

    def test_default_host(self):
        self.assertEqual(agents.resolve_agent(CONFIG), "claude")


class TestModelBase(unittest.TestCase):
    def test_examples(self):
        cases = {
            "qwen2.5:7b": "qwen",
            "gemma2": "gemma",
            "llama3.1": "llama",
            "gpt-5.5": "gpt-5",
            "gpt-4o": "gpt-4o",
            "claude": "claude",
            "Qwen2.5": "qwen",  # lowercased
        }
        for model, expected in cases.items():
            self.assertEqual(agents.model_base(model), expected, model)

    def test_empty(self):
        self.assertEqual(agents.model_base(""), "")
        self.assertEqual(agents.model_base("   "), "")


class TestLabels(unittest.TestCase):
    def test_agent_label(self):
        self.assertEqual(agents.agent_label("ollama"), "agent:ollama")

    def test_model_label(self):
        self.assertEqual(agents.model_label("qwen2.5"), "model:qwen")

    def test_model_label_none_when_no_base(self):
        self.assertIsNone(agents.model_label("2.5"))  # strips to empty


class TestAttribution(unittest.TestCase):
    def test_vendor_only(self):
        a = agents.attribution("codex")
        self.assertEqual(a["agent_label"], "agent:codex")
        self.assertIsNone(a["model_label"])
        self.assertEqual(a["system"], "codex")

    def test_vendor_and_model(self):
        a = agents.attribution("ollama", "qwen2.5")
        self.assertEqual(a["agent_label"], "agent:ollama")
        self.assertEqual(a["model_label"], "model:qwen")
        self.assertEqual(a["system"], "ollama:qwen2.5")


if __name__ == "__main__":
    unittest.main()
