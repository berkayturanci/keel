"""Unit tests for ``keel attribution`` (issue #1013).

The command is the only sanctioned way for an adapter to obtain the ``agent:`` /
``model:`` labels, so these tests pin two things: the labels it prints are exactly
``keel.agents.attribution()``'s, and the validation it applies is the same vocabulary
``evidence-verify`` and ``ship --append-ledger`` use.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from keel import agents, cli

BASE_CONFIG = {
    "extends": "keel",
    "core_version": "^0.1",
    "base_branch": "main",
    "owner": "acme",
    "repo": "widgets",
    "knobs": {
        "build_gate_cmd": "make test",
        "delegate_profiles": {
            "cursor": {
                "vendor": "cli",
                "command": "cursor-agent",
                "prompt_mode": "arg",
                "model": "composer-1",
            }
        },
    },
}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


class AttributionCommandCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = Path(self._tmp.name) / "project.yaml"
        self.config_path.write_text(yaml.safe_dump(BASE_CONFIG), encoding="utf-8")

    def config(self):
        return str(self.config_path)


class TestHumanOutput(AttributionCommandCase):
    def test_prints_the_three_fields(self):
        rc, out, _ = run(["attribution", "--vendor", "agy", "--model", "gemini-3.8-flash-high"])
        self.assertEqual(rc, 0)
        self.assertIn("agent_label   : agent:agy", out)
        self.assertIn("model_label   : model:gemini-3", out)
        self.assertIn("system        : agy:gemini-3.8-flash-high", out)

    def test_prints_the_labels_core_produces_not_the_hand_written_ones(self):
        # The live-run defect: the host wrote agent:gemini / model:gemini for this
        # exact pair. The CLI must not be able to agree with it.
        _, out, _ = run(["attribution", "--vendor", "agy", "--model", "gemini-3.8-flash-high"])
        self.assertNotIn("agent:gemini\n", out)
        self.assertNotIn("model:gemini\n", out)

    def test_no_model_reads_as_not_recorded(self):
        rc, out, _ = run(["attribution", "--vendor", "codex"])
        self.assertEqual(rc, 0)
        self.assertIn("model_label   : not recorded", out)
        self.assertIn("system        : codex", out)

    def test_vendor_is_normalized(self):
        rc, out, _ = run(["attribution", "--vendor", "  AGY  "])
        self.assertEqual(rc, 0)
        self.assertIn("agent_label   : agent:agy", out)


class TestJsonOutput(AttributionCommandCase):
    def test_json_is_the_attribution_record(self):
        rc, out, _ = run(
            ["attribution", "--vendor", "agy", "--model", "gemini-3.8-flash-high", "--json"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            json.loads(out),
            agents.attribution("agy", "gemini-3.8-flash-high"),
        )

    def test_json_model_label_is_null_without_a_model(self):
        _, out, _ = run(["attribution", "--vendor", "ollama", "--json"])
        self.assertIsNone(json.loads(out)["model_label"])


class TestVendorValidation(AttributionCommandCase):
    def test_unknown_vendor_is_refused_with_a_config(self):
        rc, _, err = run(["attribution", "--vendor", "gemini", "--config", self.config()])
        self.assertEqual(rc, 1)
        self.assertIn("unknown vendor 'gemini'", err)
        # The message has to name the vocabulary, or the operator cannot act on it.
        self.assertIn("agy", err)

    def test_any_vendor_is_accepted_without_a_config(self):
        # The ledger carries values written before this check existed; without a
        # config keel cannot tell a legacy value from a typo, so it does not guess.
        rc, out, _ = run(["attribution", "--vendor", "gemini", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["agent_label"], "agent:gemini")

    def test_configured_profile_name_is_a_known_vendor(self):
        rc, _, err = run(["attribution", "--vendor", "cursor", "--config", self.config()])
        self.assertEqual(rc, 0, err)

    def test_builtin_vendor_passes_validation(self):
        rc, _, err = run(["attribution", "--vendor", "codex", "--config", self.config()])
        self.assertEqual(rc, 0, err)

    def test_missing_config_file(self):
        rc, _, err = run(["attribution", "--vendor", "agy", "--config", "no/such.yaml"])
        self.assertEqual(rc, 1)
        self.assertIn("no such config", err)

    def test_invalid_config_file(self):
        broken = Path(self._tmp.name) / "broken.yaml"
        broken.write_text("extends: keel\ncore_version: 9\n", encoding="utf-8")
        rc, _, err = run(["attribution", "--vendor", "agy", "--config", str(broken)])
        self.assertEqual(rc, 1)
        self.assertTrue(err.strip())


class TestProfileAttribution(AttributionCommandCase):
    def test_profile_supplies_vendor_model_and_name(self):
        rc, out, err = run(
            [
                "attribution",
                "--vendor",
                "cli",
                "--profile",
                "cursor",
                "--config",
                self.config(),
                "--json",
            ]
        )
        self.assertEqual(rc, 0, err)
        record = json.loads(out)
        self.assertEqual(record["agent_label"], "agent:cli")
        self.assertEqual(record["model_label"], "model:composer-1")
        self.assertEqual(record["delegate_profile"], "cursor")

    def test_per_run_model_wins_over_the_profile_model(self):
        _, out, _ = run(
            [
                "attribution",
                "--vendor",
                "cli",
                "--profile",
                "cursor",
                "--model",
                "cursor-grok-4.5-high",
                "--config",
                self.config(),
                "--json",
            ]
        )
        self.assertEqual(json.loads(out)["model_label"], "model:cursor-grok-4")

    def test_human_output_names_the_profile(self):
        rc, out, _ = run(
            ["attribution", "--vendor", "cli", "--profile", "cursor", "--config", self.config()]
        )
        self.assertEqual(rc, 0)
        self.assertIn("delegate_profile: cursor", out)

    def test_profile_requires_a_config(self):
        rc, _, err = run(["attribution", "--vendor", "cli", "--profile", "cursor"])
        self.assertEqual(rc, 1)
        self.assertIn("--profile requires --config", err)

    def test_unknown_profile_is_refused(self):
        rc, _, err = run(
            ["attribution", "--vendor", "cli", "--profile", "nope", "--config", self.config()]
        )
        self.assertEqual(rc, 1)
        self.assertIn("no delegate profile named 'nope'", err)

    def test_vendor_contradicting_the_profile_is_refused(self):
        # One of the two would have to lose silently, and attribution exists to stop
        # a guessed value being recorded.
        rc, _, err = run(
            ["attribution", "--vendor", "codex", "--profile", "cursor", "--config", self.config()]
        )
        self.assertEqual(rc, 1)
        self.assertIn("contradicts profile 'cursor'", err)


class ADeclaredVendorLabelIsAcceptedHere(unittest.TestCase):
    """`keel attribution` is the sanctioned way to get the labels, so it has to accept the
    vendor it produces (#1129).

    `vendor_label` made `agents.attribution` report `xai` for a `cli` profile, and this
    command validates against `agents.known_vendors`, which did not contain it. The result
    was a command that refused the only spelling ever written into a label — `--vendor xai`
    answered *unknown vendor* while `keel doctor` demanded `agent:xai` — and, with
    `--profile`, rejected it a second time as contradicting a profile whose own attribution
    says exactly that. Both gate seats traced this independently.
    """

    LABELLED = {
        **BASE_CONFIG,
        "knobs": {
            **BASE_CONFIG["knobs"],
            "delegate_profiles": {
                "grok": {
                    "vendor": "cli",
                    "command": "cursor-agent",
                    "model": "cursor-grok-4.6-high-fast",
                    "vendor_label": "xai",
                }
            },
        },
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "project.yaml"
        self.path.write_text(yaml.safe_dump(self.LABELLED), encoding="utf-8")

    def test_the_declared_label_is_not_an_unknown_vendor(self):
        rc, out, err = run(["attribution", "--vendor", "xai", "--config", str(self.path), "--json"])

        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["agent_label"], "agent:xai")

    def test_it_does_not_contradict_the_profile_that_declares_it(self):
        rc, out, err = run(
            [
                "attribution",
                "--vendor",
                "xai",
                "--profile",
                "grok",
                "--config",
                str(self.path),
                "--json",
            ]
        )

        self.assertEqual(rc, 0, err)
        record = json.loads(out)
        self.assertEqual(record["agent_label"], "agent:xai")
        self.assertEqual(record["delegate_profile"], "grok")

    def test_a_genuinely_different_vendor_is_still_a_contradiction(self):
        """The check still does its job — it compares against the declared label rather
        than accepting anything."""
        rc, _out, err = run(
            ["attribution", "--vendor", "codex", "--profile", "grok", "--config", str(self.path)]
        )

        self.assertEqual(rc, 1)
        self.assertIn("contradicts profile", err)

    def test_what_the_profile_reports_is_what_this_command_accepts(self):
        """Stated as the agreement rather than as two constants: the profile's own
        attribution and this command's vocabulary come from the same place or they will
        drift again."""
        config = cli.cfg.load_config(str(self.path))
        profile = agents.resolve_delegate_profile(config, "grok")
        produced = agents.profile_attribution("grok", profile)["agent_label"]

        self.assertIn(produced[len("agent:") :], agents.known_vendors(config))


if __name__ == "__main__":
    unittest.main()
