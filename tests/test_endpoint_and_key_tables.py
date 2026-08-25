"""The accepted/refused tables #865 and #866 specified, as tests (#929).

Both issues asked for two measures and both closed with one shipped. A table is
the shape that makes a partial implementation *fail* rather than close: every
name and host below was measured against the code as it stood, and each row that
read `ACCEPTED` was a hole.
"""

from __future__ import annotations

import unittest

from keel import config as cfg

REMOTE_OK = {cfg.ALLOW_REMOTE_ENDPOINT_ENV: "1"}
INTERNAL_OK = {**REMOTE_OK, cfg.ALLOW_INTERNAL_ENDPOINT_ENV: "1"}


class TheApiKeyAllowlist(unittest.TestCase):
    """#865 req 1. Only the denylist shipped, so these were all accepted."""

    #: (name, may a delegate profile read its key from it?)
    NAMES = (
        ("OPENAI_API_KEY", True),
        ("GROQ_API_KEY", True),
        ("DEEPSEEK_API_KEY", True),
        ("TOGETHER_API_KEY", True),
        ("OPENROUTER_API_KEY", True),
        ("LITELLM_API_KEY", True),
        ("VLLM_API_KEY", True),
        ("KEEL_DELEGATE_KEY_ACME", True),
        ("keel_delegate_key_acme", True),  # case-insensitive, like the denylist
        # Measured as ACCEPTED before this change:
        ("VAULT_TOKEN", False),
        ("AZURE_CLIENT_SECRET", False),
        ("KUBECONFIG", False),
        ("GITLAB_TOKEN", False),
        ("DATABASE_URL", False),
        ("STRIPE_SECRET_KEY", False),
        # Already refused by the denylist; must stay refused.
        ("GITHUB_TOKEN", False),
        ("AWS_SECRET_ACCESS_KEY", False),
    )

    def test_the_table(self):
        for name, allowed in self.NAMES:
            with self.subTest(name=name):
                self.assertEqual(allowed, cfg._is_allowed_api_key_env(name))

    def test_the_denylist_still_wins_over_the_allowlist(self):
        """Defence in depth, and it must be the *order* that says so.

        A name on both lists has to be refused. Today none is, but the allowlist
        is the thing that will grow.
        """
        overlap = cfg.ALLOWED_ENV_KEY_NAMES & cfg.BLOCKED_ENV_KEY_NAMES
        self.assertEqual(frozenset(), overlap, "a name is on both lists")

        for allowed in cfg.ALLOWED_ENV_KEY_NAMES:
            with self.subTest(name=allowed):
                self.assertFalse(
                    allowed.startswith(cfg.BLOCKED_ENV_PREFIXES),
                    "an allowlisted name is also denied by prefix",
                )

    def test_the_table_has_rows_of_both_kinds(self):
        kinds = {allowed for _, allowed in self.NAMES}
        self.assertEqual({True, False}, kinds)


class TheEndpointHostTable(unittest.TestCase):
    """#866 req 2 plus a bypass of req 1 that shipped."""

    #: (url, env, refused?)
    HOSTS = (
        ("https://api.openai.com/v1", REMOTE_OK, False),
        ("http://localhost:1234/v1", {}, False),
        # req 1, shipped and working:
        ("http://169.254.169.254/latest/meta-data/", REMOTE_OK, True),
        # req 1, bypassed: alternate spellings of 169.254.169.254 that a C
        # resolver — and therefore the HTTP client — dials just the same.
        ("http://2852039166/latest/meta-data/", REMOTE_OK, True),
        ("http://0251.0376.0251.0376/", REMOTE_OK, True),
        ("http://0xA9FEA9FE/", REMOTE_OK, True),
        # req 2, never implemented:
        ("http://10.0.0.5/v1/chat/completions", REMOTE_OK, True),
        ("http://172.16.5.9/v1", REMOTE_OK, True),
        ("http://192.168.1.10/v1", REMOTE_OK, True),
    )

    def test_the_table(self):
        for url, env, refused in self.HOSTS:
            with self.subTest(url=url):
                issues = cfg.endpoint_issues(url, where="profile", env=env)
                self.assertEqual(refused, bool(issues), issues)

    def test_the_internal_opt_out_opens_rfc1918_and_nothing_else(self):
        """The narrow opt-out must not also unlock the metadata address.

        A self-hosted model server on the same subnet is a real deployment;
        `169.254.169.254` never is. Checked in that order so the metadata guard
        runs first.
        """
        self.assertEqual([], cfg.endpoint_issues("http://10.0.0.5/v1", where="p", env=INTERNAL_OK))
        for url in ("http://169.254.169.254/", "http://2852039166/"):
            with self.subTest(url=url):
                self.assertTrue(
                    cfg.endpoint_issues(url, where="p", env=INTERNAL_OK),
                    "the internal opt-out unlocked a metadata address",
                )

    def test_reaching_in_needs_its_own_opt_in(self):
        """`ALLOW_REMOTE` permits reaching out; it must not permit reaching in."""
        issues = cfg.endpoint_issues("http://10.0.0.5/v1", where="p", env=REMOTE_OK)

        self.assertTrue(issues)
        self.assertIn(
            cfg.ALLOW_INTERNAL_ENDPOINT_ENV,
            issues[0],
            "the error should name the opt-out the operator needs",
        )

    def test_a_hostname_is_not_mistaken_for_an_address(self):
        # `_as_ip` must return None for names, or every hostname would be
        # classified by whatever inet_aton makes of it.
        self.assertIsNone(cfg._as_ip("api.openai.com"))
        self.assertIsNone(cfg._as_ip(""))

    def test_the_table_has_rows_of_both_kinds(self):
        kinds = {refused for _, _, refused in self.HOSTS}
        self.assertEqual({True, False}, kinds)


if __name__ == "__main__":
    unittest.main()
