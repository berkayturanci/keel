"""Unit tests for the merge-window logic (fixed clock)."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from keel import window

TZ = "Europe/Istanbul"
WRAP = "07:00-01:30"  # open 07:00 -> 01:30 (wraps midnight)
PLAIN = "09:00-17:00"  # same-day window


def at(h, m=0, tz=TZ):
    return datetime(2026, 6, 5, h, m, tzinfo=ZoneInfo(tz))


class TestParse(unittest.TestCase):
    def test_parse_window(self):
        o, c = window.parse_window(WRAP)
        self.assertEqual((o.hour, o.minute), (7, 0))
        self.assertEqual((c.hour, c.minute), (1, 30))


class TestWrappingWindow(unittest.TestCase):
    def test_open_daytime(self):
        self.assertTrue(window.is_merge_open(TZ, WRAP, now=at(12)))

    def test_open_just_after_open(self):
        self.assertTrue(window.is_merge_open(TZ, WRAP, now=at(7, 0)))

    def test_open_after_midnight_before_close(self):
        self.assertTrue(window.is_merge_open(TZ, WRAP, now=at(1, 0)))

    def test_closed_at_close_boundary(self):
        self.assertFalse(window.is_merge_open(TZ, WRAP, now=at(1, 30)))

    def test_closed_in_night(self):
        self.assertFalse(window.is_merge_open(TZ, WRAP, now=at(3)))
        self.assertFalse(window.is_merge_open(TZ, WRAP, now=at(6, 59)))


class TestPlainWindow(unittest.TestCase):
    def test_open(self):
        self.assertTrue(window.is_merge_open(TZ, PLAIN, now=at(12)))

    def test_closed_before_and_after(self):
        self.assertFalse(window.is_merge_open(TZ, PLAIN, now=at(8)))
        self.assertFalse(window.is_merge_open(TZ, PLAIN, now=at(18)))


class TestClockHandling(unittest.TestCase):
    def test_naive_now_is_localized(self):
        naive = datetime(2026, 6, 5, 12, 0)  # no tzinfo
        self.assertTrue(window.is_merge_open(TZ, WRAP, now=naive))

    def test_other_zone_resolves(self):
        # Etc/GMT-3 == UTC+3; just assert it resolves and evaluates.
        self.assertIsInstance(window.is_merge_open("Etc/GMT-3", WRAP, now=at(12)), bool)

    def test_default_now_runs(self):
        self.assertIsInstance(window.is_merge_open(TZ, WRAP), bool)


if __name__ == "__main__":
    unittest.main()
