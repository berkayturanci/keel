"""The keel-progress docs page states timings and read rules; pin them to the mod source."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTER = (ROOT / "mods/keel-progress/hooks/register.js").read_text(encoding="utf-8")
PAGE = " ".join((ROOT / "docs/keel/keel-progress.md").read_text(encoding="utf-8").split())
SITE = (ROOT / "website/content.js").read_text(encoding="utf-8")
VIEW = (ROOT / "mods/keel-progress/hooks/view.js").read_text(encoding="utf-8")


def body(source, name):
    """The source of `function name(...) {...}`: up to the closing brace at column 0."""
    m = re.search(rf"^(?:export )?(?:async )?function {name}\(.*?^\}}", source, re.S | re.M)
    assert m, name
    return m.group(0)


def const(name):
    m = re.search(rf"const {name}\s*=\s*([\d_]+)", REGISTER)
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
        self.assertIn(f"({secs} seconds at the default)", PAGE)
        self.assertIn(f"({secs} seconds at the default)", SITE)

    def test_activity_is_read_from_files(self):
        self.assertIn("read straight from its file", PAGE)
        self.assertIn("read straight from its file", SITE)

    def test_the_mod_reads_activity_files_and_asks_keel_only_while_the_location_is_unknown(self):
        self.assertRegex(REGISTER, r"const ACTIVITY_DIR\s*=\s*'\.keel/activity'")
        files = body(REGISTER, "activityFiles")
        self.assertRegex(files, r"\$\.fs\.list\(")
        self.assertRegex(files, r"\$\.fs\.read\(")
        self.assertNotIn("$.process", files, "reading activity files must not start a process")
        self.assertRegex(body(REGISTER, "activityOf"), r"'keel',\s*'activity'")
        # Every scan reads files once the location is known; keel is asked only before that.
        calls = re.findall(
            r"(\w+)\s*\?\s*await activityFiles\([^)]*\)\s*:\s*await activityOf\(", REGISTER
        )
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(set(calls), {"activityRelKnown"})
        self.assertEqual(len(re.findall(r"await activityOf\(", REGISTER)), len(calls))

    def test_newest_live_record_wins(self):
        self.assertIn("the newest wins", PAGE)
        self.assertIn("the newest of a run's live records", SITE)
        self.assertIn("never replaces an older live checkpoint", PAGE)
        self.assertIn("never replaces an older live checkpoint", SITE)

    def test_the_mod_keeps_only_running_activity_and_the_newest_copy_of_a_run(self):
        # A done / merged / blocked activity record is not a live run: it must not be returned.
        runs = body(VIEW, "activityRuns")
        self.assertRegex(runs, r"status\s*!==\s*'running'[^\n]*continue")
        # One entry per run, the one written last (mtime), the session's own on a tie.
        latest = body(VIEW, "latestPerRun")
        self.assertRegex(latest, r"e\.mtimeMs\s*>\s*prev\.mtimeMs")
        # The dedupe runs before the live filter, so a newer finished checkpoint hides an older
        # running activity record of the same run.
        m = re.search(r"latestPerRun\(([^\n]*)\)\n[^\n]*\.filter\([^\n]*isLive\(", REGISTER)
        self.assertIsNotNone(m, "latestPerRun must run, then the isLive filter on its result")
        self.assertNotIn("isLive", m.group(1), "latestPerRun must see the finished copies too")


class TheSiteArticleMarkupIsWellFormed(unittest.TestCase):
    def test_no_single_quoted_attribute_holds_an_apostrophe(self):
        """content.js builds HTML in single-quoted attributes; an apostrophe ends one early."""
        text = (ROOT / "website" / "content.js").read_text(encoding="utf-8")
        # Every tag's attribute list must parse as name='value', name="value" or a bare name.
        # An apostrophe inside a single-quoted value ends it early and leaves a stray token
        # ("users' runs" -> a token runs'), which this refuses.
        attrs = re.compile(
            r"""(?:\s+[A-Za-z_:][-\w:.]*(?:\s*=\s*(?:'[^']*'|"[^"]*"|[^\s'">=`]+))?)*\s*/?"""
        )
        broken = [
            tag
            for tag in re.findall(r"<[A-Za-z][-\w:]*\b([^<>]*)>", text)
            # A placeholder in prose ("<claude|skills|all>") has no attribute to break.
            if "=" in tag and not attrs.fullmatch(tag)
        ]
        self.assertEqual(broken, [], "an apostrophe inside a single-quoted attribute")


if __name__ == "__main__":
    unittest.main()
