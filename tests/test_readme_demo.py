"""The README's demo is a recording of what keel prints, and cannot drift from it (#1330).

`docs/assets/demo.svg` (and the `demo.cast` beside it) are written by
`scripts/record_demo.py`, which runs this checkout's keel in a scratch git repository:
`keel run-gates` fails a change whose test breaks, then passes once it is fixed. These
tests run the same capture again — about a second — and require the committed files to
be exactly what it renders today. Change keel's output and the demo has to be
regenerated (`python3 scripts/record_demo.py`); hand-edit a line of the demo and the
suite fails. `scripts/` is outside the coverage gate, so this file is what holds the
script.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "record_demo.py"
_spec = importlib.util.spec_from_file_location("record_demo", _SCRIPT)
record_demo = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = record_demo  # dataclasses resolve their module by name
_spec.loader.exec_module(record_demo)

_REGENERATE = "the demo drifted from keel's output: run `python3 scripts/record_demo.py`"


def _fresh_capture() -> list:
    with tempfile.TemporaryDirectory(prefix="keel-demo-test-", ignore_cleanup_errors=True) as tmp:
        return record_demo.capture(Path(tmp))


class TheDemoIsWhatKeelPrintsToday(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = _fresh_capture()
        cls.svg = record_demo.SVG_PATH.read_text(encoding="utf-8")
        cls.cast = record_demo.CAST_PATH.read_text(encoding="utf-8")

    def test_every_line_of_the_svg_is_a_line_of_a_fresh_capture(self):
        shown = record_demo.svg_lines(self.svg)
        self.assertGreater(len(shown), 20)
        self.assertEqual(record_demo.display_lines(self.steps), shown, _REGENERATE)

    def test_the_svg_is_the_rendering_of_a_fresh_capture(self):
        self.assertEqual(record_demo.render_svg(self.steps), self.svg, _REGENERATE)

    def test_the_cast_is_the_rendering_of_a_fresh_capture(self):
        self.assertEqual(record_demo.render_cast(self.steps), self.cast, _REGENERATE)

    def test_the_recording_stops_the_change_and_then_clears_it(self):
        runs = [s for s in self.steps if s.text == "keel run-gates .keel/project.yaml"]
        self.assertEqual(2, len(runs), [s.text for s in self.steps])
        stopped, cleared = runs
        self.assertEqual(1, stopped.returncode, stopped.output)
        self.assertIn("     FAIL  build", stopped.output)
        self.assertTrue(stopped.output[-1].startswith("BLOCKED"), stopped.output)
        self.assertEqual(0, cleared.returncode, cleared.output)
        self.assertEqual(("       ok  build", "       ok  lint"), cleared.output)

    def test_the_fix_between_the_two_runs_is_shown_by_git(self):
        diff = next(s for s in self.steps if s.text == "git diff")
        self.assertEqual(0, diff.returncode)
        self.assertIn("-    return a - b", diff.output)
        self.assertIn("+    return a + b", diff.output)

    def test_a_hand_edited_output_line_fails_the_comparison(self):
        """The comparison above is sensitive to one line: the guard of the guard."""
        line = "test_add: add(2, 3) returned -1, expected 5"
        self.assertIn(line, self.svg)
        edited = self.svg.replace(line, "test_add: add(2, 3) returned 5, expected 5", 1)
        self.assertNotEqual(record_demo.display_lines(self.steps), record_demo.svg_lines(edited))


class TheRecordingIsMachineIndependent(unittest.TestCase):
    def test_the_scratch_path_is_replaced_by_the_placeholder(self):
        paths = ["/tmp/x/calc", "/tmp/x"]
        self.assertEqual(
            "~/calc/a.py and ~/calc", record_demo._scrub("/tmp/x/calc/a.py and /tmp/x", paths)
        )

    def test_the_gate_commands_write_no_bytecode(self):
        """A `.pyc` from the failing run would be loaded by the passing one (same size)."""
        project = record_demo._project_yaml()
        self.assertNotIn("py_compile", project)
        self.assertEqual(2, project.count('" -B '), project)


class TheSvgIsSmallSelfContainedAndNeutral(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svg = record_demo.SVG_PATH.read_text(encoding="utf-8")

    def test_it_is_under_sixty_kilobytes(self):
        self.assertLess(record_demo.SVG_PATH.stat().st_size, 60_000)

    def test_it_loads_nothing_and_runs_nothing(self):
        for needle in ("<script", "@import", "url(http", "href=", "<image", "@font-face"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, self.svg)

    def test_it_paints_its_own_background_for_light_and_dark_pages(self):
        self.assertRegex(self.svg, r'<rect x="0\.5" y="0\.5" [^>]*fill="#0d1117"')

    def test_it_holds_still_for_readers_who_ask_for_reduced_motion(self):
        self.assertIn("@media (prefers-reduced-motion:reduce)", self.svg)

    def test_it_names_no_vendor(self):
        text = (self.svg + record_demo.CAST_PATH.read_text(encoding="utf-8")).lower()
        for vendor in ("claude", "anthropic", "openai", "codex", "gemini", "cursor", "copilot"):
            with self.subTest(vendor=vendor):
                self.assertNotIn(vendor, text)


class TheReadmeShowsItOnTheFirstScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")

    def test_it_is_embedded_before_the_first_section_and_before_install(self):
        first_screen = self.readme[: self.readme.index("\n## ")]
        match = re.search(r"!\[([^\]]+)\]\(docs/assets/demo\.svg\)", first_screen)
        self.assertIsNotNone(match, "the README's first screen does not embed the demo")
        self.assertGreater(len(match.group(1)), 60, "the demo's alt text says too little")
        self.assertLess(match.start(), self.readme.index("\n## Install"))

    def test_its_caption_says_it_is_a_real_recording_and_how_it_is_made(self):
        first_screen = self.readme[: self.readme.index("\n## ")]
        self.assertIn("A real recording", first_screen)
        self.assertIn("scripts/record_demo.py", first_screen)


if __name__ == "__main__":
    unittest.main()
