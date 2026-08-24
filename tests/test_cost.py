"""Unit tests for Keel analytics, token tracking, and USD cost estimation engine."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

from keel import cost
from keel.cli import main
from keel.cost import (
    _PRICING_KEYS_BY_LENGTH,
    MODEL_PRICING,
    calculate_cost_report,
    estimate_benchmark_cost,
    estimate_token_cost,
    generate_cost_report,
    normalize_model_name,
    render_cost_report,
)


class TestPricingKeyOrderIsComputedOnce(unittest.TestCase):
    """The match order is a property of the table, not of the string matched.

    #898 put ``sorted(MODEL_PRICING, key=len, reverse=True)`` inside the match
    loop, so every call re-sorted the whole table — and ``keel cost-report``
    calls :func:`normalize_model_name` twice per ledger record (#930). Measured:
    1.14 us/call before, 0.46 us after.
    """

    def test_longest_key_wins_so_a_prefix_cannot_shadow_a_specific_model(self):
        """The reason the order exists at all, asserted on behaviour.

        Eleven pricing keys contain a shorter one — ``claude`` inside four
        Sonnet/Haiku/Opus entries, ``gemini`` inside four, ``gpt-4o`` inside
        ``gpt-4o-mini``, ``deepseek`` inside two. Match shortest-first and every
        one of them silently prices as the generic entry, which for
        ``gpt-4o-mini`` means billing a cheap model at the expensive rate.

        Derived from the table rather than hardcoded, so a new nested key is
        covered the day it lands.
        """
        nested = [
            (short, long)
            for short in MODEL_PRICING
            for long in MODEL_PRICING
            if short != long and short in long
        ]
        self.assertTrue(nested, "no nested keys left; this guard would be vacuous")
        for short, long in nested:
            with self.subTest(short=short, long=long):
                self.assertEqual(normalize_model_name(f"vendor:{long}"), long)
                self.assertEqual(normalize_model_name(f"vendor:{short}"), short)

    def test_the_precomputed_order_is_longest_first(self):
        """Asserts the property, not the expression that currently produces it.

        Pinning ``sorted(MODEL_PRICING, key=len, reverse=True)`` verbatim would
        fail a behaviour-identical refactor — adding an alphabetical tie-break,
        say — while the three behavioural guards around it stayed green. The
        property that matters is only that no key precedes a longer one.
        """
        lengths = [len(key) for key in _PRICING_KEYS_BY_LENGTH]
        self.assertEqual(lengths, sorted(lengths, reverse=True))

    def test_a_new_pricing_key_cannot_be_left_out_of_the_order(self):
        self.assertEqual(set(_PRICING_KEYS_BY_LENGTH), set(MODEL_PRICING))

    def test_the_loop_reads_the_precomputed_order_and_nothing_else(self):
        """Behavioural, because an AST check only ever catches one spelling.

        The first version of this guard walked the AST for a call to the *name*
        ``sorted``. Three ways of re-sorting per call slipped past it: a helper
        that sorts, ``list.sort`` (an attribute call, not a name), and
        ``builtins.sorted``. One of them was measured at 3.2x the hoisted cost
        with the guard still green — a smoke alarm wired to the word "fire".

        Patching the precomputed tuple to the *wrong* order settles it: any
        implementation that re-derives the order inside the call ignores the
        patch and returns the right answer anyway, which fails here.
        """
        # A dated identifier, deliberately *not* itself a pricing key: an exact-key
        # fast path (`if raw in MODEL_PRICING: return raw`) is a legitimate future
        # improvement that does no re-sorting, and picking a bare key as the input
        # would make this guard fail on it and report the wrong reason.
        shortest_first = tuple(sorted(MODEL_PRICING, key=len))
        with unittest.mock.patch.object(cost, "_PRICING_KEYS_BY_LENGTH", shortest_first):
            self.assertEqual(
                cost.normalize_model_name("anthropic:claude-3-5-sonnet-20241022"),
                "claude",
                "the loop re-derived the key order instead of reading the module constant",
            )


class TestCostPureLogic(unittest.TestCase):
    def test_normalize_model_name(self):
        self.assertEqual(normalize_model_name("google:gemini-2.5-pro"), "gemini-2.5-pro")
        self.assertEqual(normalize_model_name("openai:gpt-4o"), "gpt-4o")
        self.assertEqual(normalize_model_name("openai:gpt-4o-mini"), "gpt-4o-mini")
        self.assertEqual(normalize_model_name("anthropic:claude-3-7-sonnet"), "claude-3-7-sonnet")
        self.assertEqual(normalize_model_name("ollama:deepseek-r1"), "ollama")
        self.assertEqual(normalize_model_name("ollama:llama3"), "ollama")
        self.assertEqual(normalize_model_name("local:qwen"), "local")
        self.assertEqual(normalize_model_name("custom-unknown-model"), "custom-unknown-model")
        self.assertEqual(normalize_model_name(""), "default")

    def test_estimate_token_cost_and_benchmark(self):
        # Gemini 2.5 Flash: 0.15 / 0.60 per 1M
        cost_flash = estimate_token_cost(1_000_000, 1_000_000, "gemini-2.5-flash")
        self.assertAlmostEqual(cost_flash, 0.75, places=4)

        # Claude 3.5 Sonnet: 3.00 / 15.00 per 1M
        cost_sonnet = estimate_token_cost(1_000_000, 1_000_000, "claude-3-5-sonnet")
        self.assertAlmostEqual(cost_sonnet, 18.00, places=4)

        # Benchmark cost
        bench = estimate_benchmark_cost(1_000_000, 1_000_000)
        self.assertAlmostEqual(bench, 90.00, places=4)

    def test_calculate_cost_report_empty_and_populated(self):
        # Empty records
        rep_empty = calculate_cost_report([])
        self.assertEqual(rep_empty.total_runs, 0)
        self.assertEqual(rep_empty.total_tokens, 0)
        self.assertEqual(rep_empty.total_cost_usd, 0.0)
        self.assertIsNone(rep_empty.top_performer)
        rendered_empty = render_cost_report(rep_empty)
        self.assertIn("Total Runs Tracked    : 0", rendered_empty)
        self.assertNotIn("Model Breakdown:", rendered_empty)

        # Populated records with explicit and fallback token counts
        records = [
            {
                "run_id": "r1",
                "model": "google:gemini-2.5-flash",
                "prompt_tokens": 10_000,
                "completion_tokens": 2_000,
            },
            {
                "run_id": "r2",
                "model": "openai:gpt-4o",
                "prompt_tokens": 5_000,
                "completion_tokens": 1_000,
            },
            {
                "run_id": "r3",
                "model": "google:gemini-2.5-flash",
                # Synthetic token fallback
            },
        ]
        rep = calculate_cost_report(records)
        self.assertEqual(rep.total_runs, 3)
        self.assertGreater(rep.total_tokens, 15_000)
        self.assertGreater(rep.total_cost_usd, 0.0)
        self.assertEqual(rep.top_performer, "gemini-2.5-flash")

        # Check serialization and rendering
        d = rep.to_dict()
        self.assertEqual(d["total_runs"], 3)
        self.assertIn("model_breakdown", d)

        rendered = render_cost_report(rep)
        self.assertIn("Keel Efficiency & Cost Ledger", rendered)
        self.assertIn("Total Runs Tracked    : 3", rendered)
        self.assertIn("Top Dispatched Model  : gemini-2.5-flash", rendered)
        self.assertIn("Model Breakdown:", rendered)


class TestCostThinIOAndCLI(unittest.TestCase):
    def test_generate_cost_report_and_cli(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            act_dir = Path(tmpdir) / ".keel" / "activity"
            act_dir.mkdir(parents=True, exist_ok=True)

            # Valid activity record
            r1 = {
                "schema_version": "keel.activity.v1",
                "record_type": "command_activity",
                "command": "ship",
                "run_id": "run-001",
                "phase": "s10",
                "status": "merged",
                "prompt_tokens": 4000,
                "completion_tokens": 1200,
                "model": "google:gemini-2.5-flash",
            }
            (act_dir / "run-001.json").write_text(json.dumps(r1), encoding="utf-8")

            # Corrupt activity record (should fail soft and be ignored)
            (act_dir / "corrupt.json").write_text("{bad json", encoding="utf-8")

            rep = generate_cost_report(root=tmpdir)
            self.assertEqual(rep.total_runs, 1)
            self.assertEqual(rep.total_prompt_tokens, 4000)
            self.assertEqual(rep.total_completion_tokens, 1200)

            # Test CLI text output
            buf_text = io.StringIO()
            with redirect_stdout(buf_text):
                code = main(["cost-report", "--root", tmpdir])
            self.assertEqual(code, 0)
            self.assertIn("Keel Efficiency & Cost Ledger", buf_text.getvalue())

            # Test CLI JSON output
            buf_json = io.StringIO()
            with redirect_stdout(buf_json):
                code_json = main(["cost-report", "--root", tmpdir, "--json"])
            self.assertEqual(code_json, 0)
            data = json.loads(buf_json.getvalue())
            self.assertEqual(data["total_runs"], 1)
            self.assertEqual(data["total_prompt_tokens"], 4000)

            # Test generate on directory with no .keel/activity
            with tempfile.TemporaryDirectory() as empty_tmp:
                rep_empty_dir = generate_cost_report(root=empty_tmp)
                self.assertEqual(rep_empty_dir.total_runs, 0)


if __name__ == "__main__":
    unittest.main()
