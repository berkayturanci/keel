"""A profile or registry entry can name the vendor its label reports (#1129).

`knobs.delegate_profiles` and `~/.keel/providers.yaml` both restrict `vendor` to the
**transport** the schema knows — `cli` for every local coding-agent CLI. So two entries
driving two different makers through one binary were the same `agent:cli`:

    providers:
      grok:           {transport: cli, command: cursor-agent, model: cursor-grok-4.6-high-fast}
      gpt-via-cursor: {transport: cli, command: cursor-agent, model: gpt-5.3-codex-high}

Both reported `vendor: cli` in the delegate contract and `agent:cli` in attribution. The
identity was never lost — `provider` and `attribution.delegate_profile` carry the entry
name — it was simply not in the field the two rules that matter read.

Those two rules are why this is not cosmetic, and they are what this file measures
against rather than asserting the field in isolation:

* `evidence.distinct_vendor_check` refuses a merge unless the required review verdicts
  declare **distinct** vendors. Fed `agent:cli` twice it cannot tell Grok from GPT, and
  would equally refuse two genuinely different reviewers.
* `evidence.attribution_check` cross-checks a PR's `agent:*` labels against the ledger's
  implementer. Both halves come from `agents.attribution`, so a label that says less than
  the truth still *passes* — which is exactly why a test on the label alone is not enough.

Unset is unchanged: every built-in keeps naming itself, and a project that never writes
`vendor_label` gets the same labels, the same contract, and the same `config_hash`.
"""

from __future__ import annotations

import unittest

from keel import agents, delegate, evidence, providers
from keel import config as cfg

REGISTRY = "/tmp/providers.yaml"


def _registry(**entries):
    return providers.parse_registry({"providers": entries}, path=REGISTRY)


def _entry(registry, name):
    for provider in registry.providers:
        if provider.name == name:
            return provider
    raise AssertionError(f"{name!r} was not registered: {registry.warnings}")


def _config(**profiles):
    return cfg.ProjectConfig(
        extends="keel",
        core_version="^1.0",
        base_branch="main",
        knobs=cfg.Knobs(build_gate_cmd="true", delegate_profiles=profiles),
    )


CURSOR = {"transport": "cli", "command": "cursor-agent"}


class TwoEntriesOnOneBinaryAreTwoVendors(unittest.TestCase):
    """The defect's own shape: same command, different makers, one label."""

    def setUp(self):
        self.registry = _registry(
            grok={**CURSOR, "model": "cursor-grok-4.6-high-fast", "vendor_label": "xai"},
            gpt={**CURSOR, "model": "gpt-5.3-codex-high", "vendor_label": "openai"},
        )

    def _plan(self, name):
        return delegate.plan_run(_entry(self.registry, name), "review", "/tmp/brief.md")

    def test_the_contract_names_the_maker_not_the_transport(self):
        self.assertEqual(self._plan("grok").vendor, "xai")
        self.assertEqual(self._plan("gpt").vendor, "openai")

    def test_which_executor_runs_it_is_still_reported_in_its_own_field(self):
        """`vendor` stopped saying `cli`; the contract did not stop saying how it runs.

        `transport` is the field a caller branches on — `api` has no process and no
        tools, `profile` is a configured binary. What a declared label costs is the
        finer `cli`-vs-`local` split, and `_transport_of` runs both the same way.
        """
        self.assertEqual(self._plan("grok").transport, "profile")
        self.assertEqual(_entry(self.registry, "grok").transport, "cli")

    def test_the_entry_name_is_still_recorded(self):
        record = self._plan("grok").attribution
        self.assertEqual(record["delegate_profile"], "grok")
        self.assertEqual(record["system"], "xai:cursor-grok-4.6-high-fast")

    def test_the_distinctness_rule_can_now_tell_them_apart(self):
        """`require_distinct_vendors` reads one vendor per accepted verdict."""
        vendors = [self._plan("grok").vendor, self._plan("gpt").vendor]
        result = evidence.distinct_vendor_check(vendors, required_count=2)

        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["duplicated"], [])

    def test_without_the_field_that_rule_reads_one_vendor_twice(self):
        """The state before this change, asserted so the fix cannot be undone quietly."""
        registry = _registry(
            grok={**CURSOR, "model": "cursor-grok-4.6-high-fast"},
            gpt={**CURSOR, "model": "gpt-5.3-codex-high"},
        )
        vendors = [
            delegate.plan_run(_entry(registry, name), "review", "/tmp/brief.md").vendor
            for name in ("grok", "gpt")
        ]
        result = evidence.distinct_vendor_check(vendors, required_count=2)

        self.assertEqual(vendors, ["cli", "cli"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["duplicated"], ["cli"])

    def test_the_attribution_cross_check_matches_the_label_it_writes(self):
        """Both halves of `attribution_check` come from `agents.attribution`, so this
        asserts they still agree once the vendor is the declared one."""
        record = self._plan("grok").attribution
        # The ledger stores `system`; the check takes the vendor half, split the way
        # `agents.attribution_from_implementer` splits it. Going through that helper is
        # the point — it is the seam where a hand-written vocabulary would drift.
        implementer = agents.attribution_from_implementer(record["system"])
        result = evidence.attribution_check(
            [record["agent_label"]],
            implementer_vendor=agents.split_delegate(record["system"])[0],
        )

        self.assertEqual(implementer["agent_label"], record["agent_label"])
        self.assertTrue(result["ok"], result["reason"])
        self.assertEqual(result["label_vendors"], ["xai"])

    def test_a_label_from_one_entry_does_not_satisfy_the_other(self):
        result = evidence.attribution_check(
            [self._plan("gpt").attribution["agent_label"]],
            implementer_vendor=agents.split_delegate(self._plan("grok").attribution["system"])[0],
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "vendor-mismatch")


class UnsetIsUnchanged(unittest.TestCase):
    def test_a_registry_entry_without_the_field_keeps_the_generic_label(self):
        provider = _entry(_registry(plain=CURSOR), "plain")

        self.assertIsNone(provider.vendor_label)
        self.assertEqual(provider.label_vendor(), "cli")

    def test_a_builtin_still_names_itself(self):
        for provider in providers.builtin_providers():
            with self.subTest(provider=provider.name):
                self.assertIsNone(provider.vendor_label)
                self.assertEqual(provider.label_vendor(), provider.vendor)

    def test_the_hashed_config_does_not_grow_a_key_for_a_project_that_never_set_it(self):
        """An added optional field must not rotate `config_hash`."""
        config = _config(cursor=cfg.DelegateProfile(vendor="cli", command="cursor-agent"))
        serialised = cfg.delegate_profiles_dict(config)["delegate_profiles"]["cursor"]

        self.assertNotIn("vendor_label", serialised)

    def test_and_does_appear_once_it_is_set(self):
        config = _config(
            grok=cfg.DelegateProfile(vendor="cli", command="cursor-agent", vendor_label="xai")
        )
        serialised = cfg.delegate_profiles_dict(config)["delegate_profiles"]["grok"]

        self.assertEqual(serialised["vendor_label"], "xai")


class TheVocabularyContainsTheLabelItWrites(unittest.TestCase):
    """`known_vendors` is what refuses a vendor keel could never have produced (#1129).

    Adding `vendor_label` without adding it here split the vocabulary from the labels, and
    the split surfaced in three places both gate seats traced independently: `keel doctor`
    demanding `agent:xai` while `keel attribution --vendor xai` answered *unknown vendor*;
    the same command refusing `--vendor xai --profile grok` as contradicting a profile
    whose own attribution says `agent:xai`; and `ship --live --append-ledger` warning that
    `xai:grok-4.6` — the implementer it had just recorded — was not one of keel's delegate
    vendors.

    So this asserts the *agreement* rather than the membership: everything
    `attribution_labels` can write must be something `known_vendors` accepts.
    """

    def _config(self, **profiles):
        return _config(**profiles)

    def _labelled(self):
        return self._config(
            grok=cfg.DelegateProfile(
                vendor="cli", command="cursor-agent", model="grok-4.6", vendor_label="xai"
            )
        )

    def test_the_declared_label_is_a_known_vendor(self):
        self.assertIn("xai", agents.known_vendors(self._labelled()))

    def test_the_generic_vendor_and_the_profile_name_still_are(self):
        """Both are spellings a caller may legitimately use: `--delegate grok` names the
        entry, and an unlabelled profile still reports `cli`."""
        known = agents.known_vendors(self._labelled())

        self.assertIn("cli", known)
        self.assertIn("grok", known)

    def test_every_label_keel_can_write_is_a_vendor_it_will_accept(self):
        """The agreement, stated once. A label in `attribution_labels` that
        `known_vendors` refuses is a contradiction keel cannot act on."""
        config = self._config(
            grok=cfg.DelegateProfile(vendor="cli", command="cursor-agent", vendor_label="xai"),
            gpt=cfg.DelegateProfile(vendor="cli", command="cursor-agent", vendor_label="openai"),
            plain=cfg.DelegateProfile(vendor="cli", command="aider"),
        )
        known = agents.known_vendors(config)
        written = [
            label[len("agent:") :]
            for label in agents.attribution_labels(config)
            if label.startswith("agent:")
        ]

        self.assertTrue(written)
        self.assertEqual([], sorted(set(written) - known))


class TheLabelVocabularyStaysEnumerable(unittest.TestCase):
    """`keel doctor` checks that every label keel may apply exists on the repository."""

    def test_a_profile_label_is_listed_among_the_labels_keel_can_write(self):
        config = _config(
            grok=cfg.DelegateProfile(
                vendor="cli", command="cursor-agent", model="grok-4.6", vendor_label="xai"
            )
        )
        labels = agents.attribution_labels(config)

        self.assertIn("agent:xai", labels)

    def test_the_generic_token_is_not_listed_when_every_profile_declares_one(self):
        """Otherwise the check would ask for a label nothing writes any more."""
        config = _config(
            grok=cfg.DelegateProfile(vendor="cli", command="cursor-agent", vendor_label="xai"),
            gpt=cfg.DelegateProfile(vendor="cli", command="cursor-agent", vendor_label="openai"),
        )
        labels = agents.attribution_labels(config)

        self.assertNotIn("agent:cli", labels)
        self.assertIn("agent:openai", labels)


class TheValueIsCheckedBecauseItBecomesALabel(unittest.TestCase):
    """`agent:<vendor>` is applied by name and read back by `attribution_check`."""

    def _errors(self, label):
        return cfg.vendor_label_errors(label, where="knobs.delegate_profiles.x")

    def test_it_may_not_shadow_a_builtin_vendor(self):
        """A built-in writes the same label from a different provider — the ambiguity
        this field removes, inverted."""
        self.assertTrue(self._errors("agy"))
        self.assertIn("built-in delegate vendor", self._errors("agy")[0])

    def test_it_may_not_restate_the_generic_vendor(self):
        self.assertTrue(self._errors("cli"))
        self.assertIn("leave it unset", self._errors("cli")[0])

    def test_a_character_outside_the_label_vocabulary_is_refused(self):
        for value in ("x ai", "XAI", "xai:grok", "x/ai"):
            with self.subTest(value=value):
                self.assertTrue(self._errors(value), f"{value!r} was accepted")

    def test_a_plain_maker_name_is_accepted_and_unset_is_silent(self):
        self.assertEqual(self._errors("xai"), [])
        self.assertEqual(self._errors(None), [])

    def test_a_value_that_is_not_a_name_at_all_is_refused(self):
        """A YAML mapping value is not necessarily a string: an unquoted `on:`/`2:`
        resolves to a bool/int, and the schema validates the *property* rather than
        the type of every leaf. Blank is the same case — it would write `agent:`."""
        for value in (123, True, ["xai"], "", "   "):
            with self.subTest(value=value):
                errors = self._errors(value)
                self.assertTrue(errors, f"{value!r} was accepted")
                self.assertIn("non-empty string", errors[0])

    def test_a_project_config_reports_it_through_keel_validate(self):
        errors = cfg._validate_delegate_profiles(
            {"grok": {"vendor": "cli", "command": "cursor-agent", "vendor_label": "agy"}},
            source="knobs.delegate_profiles",
        )

        self.assertTrue(any("vendor_label" in error for error in errors), errors)

    def test_the_registry_drops_a_bad_label_rather_than_the_whole_entry(self):
        """Fail-soft, like every other registry rule: the provider still works, it just
        keeps the label it had before."""
        registry = _registry(bad={**CURSOR, "vendor_label": "agy"})
        provider = _entry(registry, "bad")

        self.assertIsNone(provider.vendor_label)
        self.assertEqual(provider.label_vendor(), "cli")
        self.assertTrue(any("ignoring it" in warning for warning in registry.warnings))

    def test_the_registry_warns_about_a_value_that_is_not_a_string(self):
        """`_text()` returns None for a bool, an int or a list, and an unset label is
        silent — so exactly the unquoted-YAML case the project validator reports was
        swallowed here without a warning. Both gate seats found it; the registry now
        validates the raw value."""
        for value in (123, True, ["xai"], "", "   "):
            with self.subTest(value=value):
                registry = _registry(bad={**CURSOR, "vendor_label": value})
                provider = _entry(registry, "bad")

                self.assertIsNone(provider.vendor_label)
                self.assertEqual(provider.label_vendor(), "cli")
                self.assertTrue(
                    any("vendor_label" in warning for warning in registry.warnings),
                    f"{value!r} was dropped silently: {registry.warnings}",
                )


if __name__ == "__main__":
    unittest.main()
