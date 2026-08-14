"""Tests for the default capture-redaction policy.

These focus on the ``credential-url`` rule, whose scheme quantifier is bounded
to ``{0,64}`` to prevent catastrophic backtracking (ReDoS) on large malformed
payloads. Assertions are deterministic/functional rather than timing-based so
they do not flake under CI load.
"""

from __future__ import annotations

import unittest

from keel import redaction


def _credential_url_rules(audit: dict) -> list[dict]:
    return [rule for rule in audit["rules"] if rule["id"] == "credential-url"]


def _rule_count(audit: dict, rule_id: str) -> int:
    return sum(rule["count"] for rule in audit["rules"] if rule["id"] == rule_id)


class CredentialUrlRedactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = redaction.policy_from_config()

    def test_redacts_credentials_in_url(self) -> None:
        """A standard credential URL is redacted and audited (no regression)."""
        result = redaction.sanitize("http://user:pass@host", self.policy)

        self.assertEqual(result.value, "http://[REDACTED:credentials]@host")
        self.assertNotIn("user", result.value)
        self.assertNotIn("pass", result.value)

        credential_rules = _credential_url_rules(result.audit)
        self.assertEqual(len(credential_rules), 1)
        self.assertEqual(credential_rules[0]["source"], "default")
        self.assertEqual(credential_rules[0]["count"], 1)
        self.assertEqual(result.audit["status"], "applied")

    def test_scheme_quantifier_is_bounded(self) -> None:
        """The compiled scheme quantifier is bounded to {0,64}, not unbounded.

        Proves the ReDoS fix is in effect: an unbounded ``*`` after the scheme
        character class is what enabled catastrophic backtracking. This asserts
        directly on the compiled pattern so it cannot silently regress.
        """
        rule = next(r for r in self.policy.rules if r.id == "credential-url")
        pattern = rule.pattern.pattern

        self.assertIn("[A-Za-z0-9+.-]{0,64}://", pattern)
        self.assertNotIn("[A-Za-z0-9+.-]*://", pattern)

    def test_oversized_unbroken_scheme_run_is_not_redacted(self) -> None:
        """A scheme run separated from ``://`` so no 1+64 window reaches it.

        Inserting a non-scheme separator (a space) more than 64 chars before
        ``://`` means the bounded rule cannot slide a valid scheme window onto
        the ``://``, so nothing is redacted. The unbounded original would have
        matched the long run. Deterministic functional proof of the bound.
        """
        # 70 scheme chars, a space (non-scheme), then '://': the nearest valid
        # scheme start before '://' is after the space, leaving 0 chars — but
        # the URL host shape still requires a scheme char immediately preceding.
        value = ("a" * 70) + " ://user:pass@host"

        result = redaction.sanitize(value, self.policy)

        self.assertEqual(result.value, value)
        self.assertEqual(_credential_url_rules(result.audit), [])

    def test_scheme_within_bound_is_still_redacted(self) -> None:
        """A scheme exactly within the bound (1 + 64 chars) is still redacted."""
        scheme = "a" + ("b" * 63)  # 64 chars total, within [A-Za-z...]{0,64}
        value = f"{scheme}://user:pass@host"

        result = redaction.sanitize(value, self.policy)

        self.assertEqual(result.value, f"{scheme}://[REDACTED:credentials]@host")
        self.assertEqual(len(_credential_url_rules(result.audit)), 1)

    def test_malformed_payload_without_at_is_unchanged(self) -> None:
        """A long malformed payload with no ``@`` is returned unchanged.

        This is the ReDoS-shaped input (large run after the scheme with no
        terminating ``@``); the bounded rule short-circuits and the value is
        returned verbatim. Deterministic functional assertion, not timing.
        """
        value = "http://A:" + ("B" * 50000) + "!"

        result = redaction.sanitize(value, self.policy)

        self.assertEqual(result.value, value)
        self.assertEqual(_credential_url_rules(result.audit), [])

    def test_malformed_payload_completes_quickly(self) -> None:
        """Generous timing safety-net only; primary guarantees are functional."""
        import time

        value = ("z" * 200) + "://A:" + ("B" * 50000) + "!"

        start = time.perf_counter()
        redaction.sanitize(value, self.policy)
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 2.0)


class CredentialAssignmentRedactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = redaction.policy_from_config()

    def test_unquoted_api_key_assignment_is_redacted(self) -> None:
        result = redaction.sanitize("ANTHROPIC_API_KEY=sk-ant-api03-secretvalue123", self.policy)

        self.assertEqual(result.value, "ANTHROPIC_API_KEY=[REDACTED:credential]")
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 1)
        self.assertNotIn("sk-ant-api03-secretvalue123", result.value)

    def test_unquoted_openai_key_assignment_is_redacted(self) -> None:
        result = redaction.sanitize("OPENAI_API_KEY=sk-proj-secretvalue1234567890", self.policy)

        self.assertEqual(result.value, "OPENAI_API_KEY=[REDACTED:credential]")
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 1)
        self.assertNotIn("sk-proj-secretvalue1234567890", result.value)

    def test_unquoted_aws_secret_access_key_assignment_is_redacted(self) -> None:
        result = redaction.sanitize(
            "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/example",
            self.policy,
        )

        self.assertEqual(result.value, "AWS_SECRET_ACCESS_KEY=[REDACTED:credential]")
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 1)
        self.assertNotIn("wJalrXUtnFEMI", result.value)

    def test_unquoted_secret_key_assignment_is_redacted(self) -> None:
        result = redaction.sanitize("SECRET_KEY=django-insecure-examplevalue", self.policy)

        self.assertEqual(result.value, "SECRET_KEY=[REDACTED:credential]")
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 1)
        self.assertNotIn("django-insecure", result.value)

    def test_quoted_assignment_still_redacts(self) -> None:
        result = redaction.sanitize("PASSWORD='supersecretvalue'", self.policy)

        self.assertEqual(result.value, "PASSWORD=[REDACTED:credential]")
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 1)

    def test_standalone_llm_api_key_is_redacted(self) -> None:
        result = redaction.sanitize("token sk-proj-abcdefghijklmnopqrstuvwxyz", self.policy)

        self.assertEqual(result.value, "token [REDACTED:llm-api-key]")
        self.assertEqual(_rule_count(result.audit, "llm-api-key"), 1)

    def test_unquoted_assignment_redaction_handles_large_input(self) -> None:
        value = "API_KEY=" + ("A" * 50000)

        result = redaction.sanitize(value, self.policy)

        self.assertEqual(result.value, "API_KEY=[REDACTED:credential]")
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 1)

    def test_unquoted_assignment_with_semicolon_is_redacted(self) -> None:
        result = redaction.sanitize("api_key=abc;def12345", self.policy)

        self.assertEqual(result.value, "api_key=[REDACTED:credential]")
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 1)
        self.assertNotIn("abc;def12345", result.value)

    def test_unquoted_assignment_with_commas_is_redacted(self) -> None:
        result = redaction.sanitize("API_KEY=abcd,efgh,ijkl", self.policy)

        self.assertEqual(result.value, "API_KEY=[REDACTED:credential]")
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 1)
        self.assertNotIn("abcd,efgh,ijkl", result.value)

    def test_comma_joined_credential_assignments_are_counted_separately(self) -> None:
        result = redaction.sanitize(
            "password=s3cr3t_value,api_key=my_real_api_key_here",
            self.policy,
        )

        self.assertEqual(
            result.value,
            "password=[REDACTED:credential],api_key=[REDACTED:credential]",
        )
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 2)
        self.assertEqual(result.audit["redaction_count"], 2)
        self.assertNotIn("s3cr3t_value", result.value)
        self.assertNotIn("my_real_api_key_here", result.value)

    def test_semicolon_joined_credential_assignments_are_counted_separately(self) -> None:
        result = redaction.sanitize(
            "token=firstsecret123;refresh_token=secondsecret456",
            self.policy,
        )

        self.assertEqual(
            result.value,
            "token=[REDACTED:credential];refresh_token=[REDACTED:credential]",
        )
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 2)

    def test_json_quoted_key_is_redacted(self) -> None:
        """A JSON-quoted key redacts cleanly, with no orphaned surrounding quote."""
        result = redaction.sanitize('{"api_key": "abcdef123456"}', self.policy)

        self.assertEqual(result.value, "{api_key=[REDACTED:credential]}")
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 1)

    def test_compact_json_redacts_only_the_secret_field(self) -> None:
        """A compact JSON object keeps sibling fields; only the secret is removed."""
        result = redaction.sanitize('{"api_key":"secret1234","x":"y"}', self.policy)

        self.assertEqual(result.value, '{api_key=[REDACTED:credential],"x":"y"}')
        self.assertNotIn("secret1234", result.value)
        self.assertIn('"x":"y"', result.value)

    def test_compact_object_sibling_is_not_swallowed_by_unquoted_secret(self) -> None:
        result = redaction.sanitize("{api_key:secretvalue1234,x:y}", self.policy)

        self.assertEqual(result.value, "{api_key=[REDACTED:credential],x:y}")
        self.assertEqual(_rule_count(result.audit, "credential-assignment"), 1)

    def test_short_quoted_status_values_are_not_redacted(self) -> None:
        """Short quoted values (status strings, not secrets) keep the 8-char floor."""
        for line in ('token="none"', 'token=""', 'api_key="n/a"',
                     'password="test"', 'token: "ok"'):
            result = redaction.sanitize(line, self.policy)
            self.assertEqual(result.value, line)
            self.assertEqual(_rule_count(result.audit, "credential-assignment"), 0)

    def test_unbalanced_double_quote_value_is_redacted(self) -> None:
        """An unbalanced opening quote no longer defeats redaction (leak fix)."""
        result = redaction.sanitize('password="hunter2secretvalue', self.policy)

        self.assertEqual(result.value, "password=[REDACTED:credential]")
        self.assertNotIn("hunter2secretvalue", result.value)

    def test_unbalanced_single_quote_value_is_redacted(self) -> None:
        result = redaction.sanitize("token='abcdefghij", self.policy)

        self.assertEqual(result.value, "token=[REDACTED:credential]")
        self.assertNotIn("abcdefghij", result.value)

    def test_yaml_style_colon_assignment_is_redacted(self) -> None:
        result = redaction.sanitize("password: longsecretvalue", self.policy)

        self.assertEqual(result.value, "password=[REDACTED:credential]")

    def test_secret_value_under_token_suffixed_key_is_redacted(self) -> None:
        """A genuine opaque value keeps matching even with a prefixed key."""
        result = redaction.sanitize("my_access_token=ya29.A0ARrdaMexamplevalue", self.policy)

        self.assertEqual(result.value, "my_access_token=[REDACTED:credential]")

    def test_function_call_value_is_left_intact(self) -> None:
        """A call expression assigned to a credential-named var is not mangled."""
        for code in (
            "token = get_token()",
            "access_token = response.json()",
            "pwd = os.getcwd()",
        ):
            result = redaction.sanitize(code, self.policy)
            self.assertEqual(result.value, code)
            self.assertEqual(_rule_count(result.audit, "credential-assignment"), 0)

    def test_subscript_expression_value_is_left_intact(self) -> None:
        """A subscript expression is not redacted or mangled mid-string."""
        code = "csrf_token = request.headers['X-CSRF']"
        result = redaction.sanitize(code, self.policy)

        self.assertEqual(result.value, code)

    def test_env_and_command_references_are_left_intact(self) -> None:
        """``${...}`` / ``$(...)`` references are not literal secrets — keep them."""
        for ref in ("password: ${DB_PASSWORD}", "token=$(cat secret)"):
            result = redaction.sanitize(ref, self.policy)
            self.assertEqual(result.value, ref)

    def test_unbalanced_quote_value_completes_quickly(self) -> None:
        """The possessive value run cannot backtrack catastrophically."""
        import time

        value = 'password="' + ("a" * 500000)
        start = time.perf_counter()
        redaction.sanitize(value, self.policy)
        self.assertLess(time.perf_counter() - start, 2.0)

    def test_adversarial_dense_json_payloads_do_not_reDoS_or_leak(self) -> None:
        """Dense JSON with hundreds of mixed assignments is processed in bounded time."""
        import time

        items = [f'"item_{i}_token": "secret_token_value_{i:04d}"' for i in range(200)]
        dense = "{" + ",".join(items) + "}"
        start = time.perf_counter()
        result = redaction.sanitize(dense, self.policy)
        duration = time.perf_counter() - start
        self.assertLess(duration, 0.5)
        self.assertNotIn("secret_token_value_0001", result.value)

    def test_unicode_and_null_byte_injections_handled_safely(self) -> None:
        """Unicode lookalikes, null bytes, and control chars do not crash the sanitizer."""
        injections = [
            "token=secret\x00hidden",
            "password: \u200bsecret_long_value\u200b",
            "api_key: 'öçşğıü-secret-key-12345'",
        ]
        for item in injections:
            result = redaction.sanitize(item, self.policy)
            self.assertIsInstance(result.value, str)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

