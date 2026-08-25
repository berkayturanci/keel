"""The pricing table and the attribution convention must share one vocabulary.

#942's finding: `src/keel/adapters/commands/ship.md` specifies a versionless
``model:<base>`` label and `MODEL_PRICING` is keyed on 2024-era product names.
Nothing connected them, so **keel priced its own runs at the fallback** — roughly
5 % of true Opus spend, with the difference then claimed as savings (#944).

The test #942 asked for is the one that would have caught the divergence: take
the model names keel's own attribution actually emits and assert each resolves
to a real key rather than the fallback.
"""

from __future__ import annotations

import unittest

from keel import agents, cost

#: Model ids keel dispatches or records today, in the spellings they arrive in:
#: a vendor CLI's own id, the label `agents.model_base` derives from it, and the
#: re-hosted forms a delegate can be pointed at.
DISPATCHED_MODELS = (
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "opus-4-8",
    "sonnet-4-5",
    "haiku-4-5",
    "anthropic-api:claude-opus-4-5",
    "anthropic/claude-opus-4",
    "anthropic.claude-3-opus-20240229-v1:0",
    "ollama:qwen2.5-coder",
    "local:whatever",
)


class KeelPricesItsOwnRuns(unittest.TestCase):
    def test_every_dispatched_model_resolves_to_a_real_key(self):
        unpriced = [
            model for model in DISPATCHED_MODELS
            if cost.normalize_model_name(model) == "default"
        ]

        self.assertEqual(
            [], unpriced,
            "keel emits these model labels and cannot price them, so its own "
            "cost report falls back to a guess and claims the difference as "
            "savings (#942)",
        )

    def test_the_attribution_label_resolves_as_well_as_the_raw_id(self):
        """The label is what lands on the PR, so it is what a later audit reads."""
        # The `-api` exemption this loop used to carry is gone: #955 fixed the
        # attribution algorithm, so `anthropic-api:claude-opus-4-5` now labels as
        # `model:claude-opus-4-5` and prices like its raw id.
        for model in DISPATCHED_MODELS:
            base = agents.model_base(model)
            if not base or model.startswith(tuple(f"{p}:" for p in agents.LOCAL_TRANSPORTS)):
                # A local run is the one case where the label and the raw id
                # answer different questions, and both answers are right. The
                # label names the model that ran (`model:qwen`); the price comes
                # from the transport, because `MODEL_PRICING` prices local
                # inference at zero and has no entry for the model itself.
                # Pinning them equal would mean either pricing an open-weight
                # family at zero everywhere — it is also sold hosted — or going
                # back to labelling every local run `model:ollama`.
                # `test_a_local_run_is_still_priced_free` covers the half that
                # matters, on the id the report actually reads.
                continue
            with self.subTest(model=model):
                self.assertEqual(
                    cost.normalize_model_name(model),
                    cost.normalize_model_name(base),
                    "the raw id and the label keel records for it price differently",
                )

    def test_a_local_run_is_still_priced_free(self):
        """#955 made the label name the model; the price must stay at the tier.

        `calculate_cost_report` reads `rec["model"]` — the raw id — so this is
        the value that decides whether a local run counts as free or as an
        unpriced guess. Asserted on the price, not on the key, because the key
        being `ollama` is an implementation detail and free is the promise.
        """
        for model in ("ollama:qwen2.5-coder", "local:whatever", "ollama:mistral:7b"):
            with self.subTest(model=model):
                self.assertEqual(cost.estimate_token_cost(1_000_000, 1_000_000, model), 0.0)
                self.assertNotEqual(cost.normalize_model_name(model), "default")

    def test_every_alias_points_at_a_key_that_exists(self):
        dangling = {
            alias: target for alias, target in cost.MODEL_ALIASES.items()
            if target not in cost.MODEL_PRICING
        }

        self.assertEqual({}, dangling, "an alias names a pricing key that is not there")

    def test_no_alias_shadows_a_real_key(self):
        """An alias must add a name, never redirect one the table already prices."""
        shadowed = sorted(set(cost.MODEL_ALIASES) & set(cost.MODEL_PRICING))

        self.assertEqual([], shadowed)

    def test_the_sweep_has_models_to_sweep(self):
        """Keeps the assertions above from passing on an empty list."""
        self.assertGreaterEqual(len(DISPATCHED_MODELS), 8)


class AnUnpricedModelIsReportedAsUnpriced(unittest.TestCase):
    """#944: understating a cost inflates the saving, so an unknown must not
    quietly borrow a cheap price."""

    def test_a_missing_model_is_not_treated_as_the_cheapest_entry(self):
        report = cost.calculate_cost_report([{"prompt_tokens": 1_000_000}])

        self.assertEqual(1, report.unpriced_runs)
        self.assertEqual(
            0.0, report.estimated_savings_usd,
            "a run with no attribution cannot evidence a saving",
        )

    def test_savings_compare_the_same_set_on_both_sides(self):
        priced = {"model": "claude-3-5-haiku", "prompt_tokens": 1_000_000}
        unpriced = {"model": "some-model-nobody-listed", "prompt_tokens": 1_000_000}

        alone = cost.calculate_cost_report([priced])
        together = cost.calculate_cost_report([priced, unpriced])

        self.assertEqual(alone.estimated_savings_usd, together.estimated_savings_usd,
                         "an unpriced run moved the savings figure")
        self.assertEqual(1, together.unpriced_runs)
        self.assertGreater(together.total_cost_usd, alone.total_cost_usd,
                           "its tokens were still spent and must still be counted")

    def test_the_render_says_how_much_of_the_report_is_a_guess(self):
        report = cost.calculate_cost_report([{"prompt_tokens": 10}])

        text = cost.render_cost_report(report)

        self.assertIn("Unpriced Runs", text)
        self.assertIn("excluded from savings", text)

    def test_a_fully_priced_report_does_not_mention_unpriced_runs(self):
        report = cost.calculate_cost_report(
            [{"model": "claude-3-5-haiku", "prompt_tokens": 10}]
        )

        self.assertNotIn("Unpriced Runs", cost.render_cost_report(report))


if __name__ == "__main__":
    unittest.main()
