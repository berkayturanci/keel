"""The keel-progress docs page states timings and read rules; pin them to the mod source."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTER = (ROOT / "mods/keel-progress/hooks/register.js").read_text(encoding="utf-8")
PAGE = " ".join((ROOT / "docs/keel/keel-progress.md").read_text(encoding="utf-8").split())
SITE = (ROOT / "website/content.js").read_text(encoding="utf-8")


def const(name):
    m = re.search(r"const %s\s*=\s*([\d_]+)" % name, REGISTER)
    assert m, name
    return int(m.group(1).replace("_", ""))


class TestKeelProgressDocs(unittest.TestCase):
    def test_status_ttl_is_thirty_seconds(self):
        self.assertEqual(const("STATUS_TTL_MS"), 30_000)
        self.assertIn("30 seconds", PAGE)
        self.assertIn("every 30 seconds", SITE)

    def test_idle_scans_are_five_times_less_often(self):
        self.assertEqual(const("IDLE_EVERY"), 5)
        self.assertIn("five times less often", PAGE)
        self.assertIn("five times less often", SITE)

    def test_default_poll_is_two_seconds(self):
        self.assertEqual(const("POLL_MS"), 2_000)
        self.assertIn("every two seconds", PAGE)
        self.assertIn("every two seconds", SITE)

    def test_idle_interval_in_seconds_is_stated(self):
        secs = const("POLL_MS") * const("IDLE_EVERY") // 1000
        self.assertIn("(%d seconds at the default)" % secs, PAGE)
        self.assertIn("(%d seconds at the default)" % secs, SITE)

    def test_activity_is_read_from_files(self):
        self.assertIn("read straight from its file", PAGE)
        self.assertIn("read straight from its file", SITE)

    def test_newest_live_record_wins(self):
        self.assertIn("the newest wins", PAGE)
        self.assertIn("the newest of a run's live records", SITE)
        self.assertIn("never replaces an older live checkpoint", PAGE)
        self.assertIn("never replaces an older live checkpoint", SITE)


if __name__ == "__main__":
    unittest.main()
