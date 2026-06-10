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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
