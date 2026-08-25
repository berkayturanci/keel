"""The ``model:<base>`` label must name the model, not the transport.

#955's finding: `agents.model_base` read the first colon by *position*, taking
everything before it as the model. That is right for Ollama (``qwen2.5:7b``) and
backwards for every hosted-API delegate, so `anthropic-api:claude-opus-4-5`,
`openai-api:gpt-5` and `google-api:gemini-2.5-pro` all landed on their PRs as
`model:<vendor>` — the transport, identically, whichever model actually ran.

Invisible in the obvious place: the label was well-formed, non-empty and stable.
The only signal was that `cost.normalize_model_name` resolved the same ids
correctly, so keel's two readings of one string disagreed.

Both now go through `agents.strip_transport`, which reads the colon by what is
on either side of it. The Ollama cases are here in the same file because they
are the form the old rule was *right* about — a fix that lost them would trade
one wrong label for another.
"""

from __future__ import annotations

import unittest

from keel import agents

#: The hosted-API forms, derived from `API_VENDORS` rather than listed, so a
#: vendor added there is covered on the day it lands. Each entry is the model id
#: keel would be given after the `<vendor>:` prefix.
HOSTED_MODELS = {
    "anthropic-api": "claude-opus-4-5",
    "openai-api": "gpt-5",
    "google-api": "gemini-2.5-pro",
}


class TheLabelNamesTheModelNotTheTransport(unittest.TestCase):
    def test_every_hosted_api_vendor_is_covered_by_this_test(self):
        """Vacuity: the sweep below is only meaningful if it sweeps all of them."""
        self.assertEqual(
            sorted(HOSTED_MODELS),
            sorted(agents.API_VENDORS),
            "a hosted-API vendor was added to API_VENDORS without a case here, "
            "so the sweep would pass while the new vendor mislabels",
        )
        self.assertGreater(len(agents.API_VENDORS), 1)

    def test_no_hosted_api_delegate_is_labelled_with_its_vendor(self):
        for vendor, model in HOSTED_MODELS.items():
            with self.subTest(vendor=vendor):
                label = agents.model_label(f"{vendor}:{model}")
                self.assertIsNotNone(label)
                self.assertNotIn(
                    vendor,
                    label,
                    f"`{vendor}:{model}` is labelled with the transport",
                )

    def test_two_models_on_one_vendor_get_different_labels(self):
        """The failure #955 describes: an audit cannot tell the runs apart.

        Asserted directly rather than inferred from the two labels being
        correct — 'they differ' is the property that was actually lost.
        """
        opus = agents.model_label("anthropic-api:claude-opus-4-5")
        haiku = agents.model_label("anthropic-api:claude-haiku-4-5")
        self.assertNotEqual(opus, haiku)

    def test_the_hosted_label_matches_the_undecorated_model(self):
        """Prefixing a model with its transport must not change what it is."""
        for vendor, model in HOSTED_MODELS.items():
            with self.subTest(vendor=vendor):
                self.assertEqual(
                    agents.model_label(f"{vendor}:{model}"),
                    agents.model_label(model),
                )


class TheOllamaFormsStillWork(unittest.TestCase):
    """The form the old positional rule was right about."""

    def test_a_bare_ollama_tag_keeps_naming_the_family(self):
        self.assertEqual(agents.model_label("qwen2.5:7b"), "model:qwen")

    def test_a_prefixed_ollama_id_names_the_family_not_the_transport(self):
        # `ollama:qwen2.5:7b` has two colons: the first is transport, the
        # second the model's own tag.
        self.assertEqual(agents.model_label("ollama:qwen2.5:7b"), "model:qwen")

    def test_the_documented_bases_are_unchanged(self):
        for model, expected in (
            ("gemma2", "gemma"),
            ("llama3.1", "llama"),
            ("gpt-5.5", "gpt-5"),
            ("gpt-4o", "gpt-4o"),
        ):
            with self.subTest(model=model):
                self.assertEqual(agents.model_base(model), expected)


class StripTransportReadsTheColonBySides(unittest.TestCase):
    def test_a_transport_prefix_is_dropped(self):
        self.assertEqual(
            agents.strip_transport("anthropic-api:claude-opus-4-5"),
            "claude-opus-4-5",
        )
        self.assertEqual(agents.strip_transport("ollama:llama3"), "llama3")

    def test_a_colon_the_model_owns_is_kept(self):
        # An Ollama tag and a Bedrock revision are the model's own suffix; the
        # caller decides whether to drop them.
        self.assertEqual(agents.strip_transport("qwen2.5:7b"), "qwen2.5:7b")
        self.assertEqual(agents.strip_transport("x-v1:0"), "x-v1:0")

    def test_an_unknown_prefix_is_not_treated_as_transport(self):
        """Fail-closed: only names keel knows as transports are stripped."""
        self.assertEqual(agents.strip_transport("acme:model"), "acme:model")

    def test_the_prefix_set_is_derived_from_the_vendor_tuples(self):
        """Not a hand-kept list — that is the bug one level down."""
        for vendor in agents.API_VENDORS + agents.LOCAL_VENDORS:
            with self.subTest(vendor=vendor):
                self.assertEqual(agents.strip_transport(f"{vendor}:m"), "m")

    def test_a_transport_with_no_model_is_left_alone(self):
        """Rejected upstream when parsing `--delegate`; not silently reinterpreted."""
        self.assertEqual(agents.strip_transport("anthropic-api:"), "anthropic-api:")

    def test_case_and_padding_are_normalised(self):
        self.assertEqual(
            agents.strip_transport("  Anthropic-API:Claude-Opus-4-5 "),
            "claude-opus-4-5",
        )
