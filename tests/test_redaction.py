"""Unit tests for the redaction helpers."""

import time
import unittest

from keel import redaction


class TestRedaction(unittest.TestCase):
    def test_default_rules_redact_credential_url(self):
        policy = redaction.policy_from_config(None)

        # Test standard URL
        val1 = "Connecting to http://user:pass@example.com"
        res1 = redaction.sanitize(val1, policy)
        self.assertEqual(res1.value, "Connecting to http://[REDACTED:credentials]@example.com")
        self.assertTrue(any(r["id"] == "credential-url" for r in res1.audit["rules"]))

        # Test slightly complex URL (using standard chars to avoid @ splitting the group)
        val2 = "db://user_name:myP%40ssw0rd@my.database.host.local:5432/db"
        res2 = redaction.sanitize(val2, policy)
        self.assertEqual(res2.value, "db://[REDACTED:credentials]@my.database.host.local:5432/db")

    def test_default_rules_prevent_redos_credential_url(self):
        policy = redaction.policy_from_config(None)

        # Malformed payload to attempt catastrophic backtracking
        val1 = "http://A:" + "B" * 50000 + "!"

        start = time.time()
        res1 = redaction.sanitize(val1, policy)
        elapsed = time.time() - start

        self.assertEqual(res1.value, val1)
        self.assertLess(elapsed, 0.5, "ReDoS detected! Parsing took more than 0.5 seconds.")


if __name__ == "__main__":
    unittest.main()
